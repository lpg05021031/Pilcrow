#!/usr/bin/env python3
"""Build the command model from what the readers returned and what the referees said.

    python3 render_model.py <system> [--workspace DIR]

Reads audit/<system>/model_result.json (the raw result of the model step: the commands the readers
proposed, the referees' verdicts on each guard and branch, later corrections) and audit/<system>/map.json,
and writes model/commands.json and MODEL.md.

What a reader returns is a claim. This script keeps only what stands up mechanically:

- a command must name a handler the map has; its HTTP method and path are taken from the map, and a
  bean and method must be ones the map lists for that entry point;
- a guard or branch the referee refuted is dropped; one the referee re-located gets the right lines;
  one no referee looked at is kept and marked unverified;
- a citation must point at lines that exist in a file of the workspace;
- a variable's query must be a single SELECT;
- a command that is not coherent (a guard about a variable it does not have, a base case a guard
  refuses) is left out and listed with what is wrong.

Whether the claims are *true* is not settled here: the matrix run compares each one with what the
application does.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402
import build_matrix  # noqa: E402

SCHEMA = 1
ID_RE = re.compile(r"^CMD-[A-Za-z0-9_][A-Za-z0-9_.-]*$")
CITE_RE = re.compile(r"^([^:\s][^:]*):(\d+)(?:-(\d+))?$")
FORBIDDEN_SQL = re.compile(r"(?i)\b(insert|update|delete|merge|drop|alter|truncate|create|grant|revoke|call|execute|copy|into)\b")
VERDICTS = ("confirmed", "wrong-citation", "refuted")


def check_cite(workspace, cite):
    """None when the citation points at existing lines of a file inside the workspace, else what is wrong."""
    m = CITE_RE.match(cite or "")
    if not m:
        return "is not of the form path/File.java:120-134"
    path = os.path.normpath(os.path.join(workspace, m.group(1)))
    if not _lib.inside(workspace, path) or not os.path.isfile(path):
        return "names a file that does not exist in the workspace"
    first, last = int(m.group(2)), int(m.group(3) or m.group(2))
    text = _lib.read_text(path)
    if text is None:
        return "names a file that cannot be read"
    if not 1 <= first <= last <= text.count("\n") + 1:
        return "names lines outside the file"
    return None


def check_sql(sql):
    s = (sql or "").strip()
    if not s.lower().startswith("select"):
        return "is not a SELECT"
    if ";" in s:
        return "holds more than one statement"
    if FORBIDDEN_SQL.search(s):
        return "holds a word that writes or runs something"
    return None


def path_shape(path):
    return re.sub(r"\{[^}/]+\}", "{}", path or "")


def bind(command, entry):
    """Make the binding agree with the entry point the map found. Returns a list of problems."""
    binding = command.get("binding")
    if not isinstance(binding, dict):
        return ["has no binding"]
    if entry.get("kind") == "http":
        if binding.get("kind") != "http":
            return ["is bound as %r but its entry point is an HTTP endpoint" % binding.get("kind")]
        if path_shape(binding.get("path")) != path_shape(entry.get("path")):
            return ["is bound to %s but its entry point is %s" % (binding.get("path"), entry.get("path"))]
        binding["method"] = entry.get("method")
        return []
    if binding.get("kind") != "bean":
        return ["is bound as %r but its entry point is not an HTTP endpoint" % binding.get("kind")]
    if entry.get("bean") and binding.get("bean") != entry["bean"]:
        return ["calls bean %r but its entry point is on bean %r" % (binding.get("bean"), entry["bean"])]
    known = set()
    for key in ("handler_methods", "public_methods"):
        for name in entry.get(key) or []:
            known.add(re.split(r"[\[(]", name.split("#")[-1])[0])
    known.add((entry.get("handler") or "#").split("#")[-1])
    if binding.get("method") not in known:
        return ["calls method %r, which the map does not list for this entry point" % binding.get("method")]
    return []


def render(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    result = _lib.read_json(os.path.join(adir, "model_result.json"))
    themap = _lib.read_json(os.path.join(adir, "map.json"))
    if not isinstance(result, dict) or not isinstance(themap, dict):
        _lib.die("audit/%s/model_result.json or map.json not found" % system)
    entries = {e.get("handler"): e for e in themap.get("entries", [])}

    proposed = {}
    for c in result.get("commands", []):
        if isinstance(c, dict) and c.get("id"):
            proposed[c["id"]] = c
    for fix in result.get("corrections", []):
        if fix.get("remove"):
            proposed.pop(fix["remove"], None)
        elif isinstance(fix.get("command"), dict) and fix["command"].get("id"):
            proposed[fix["command"]["id"]] = fix["command"]
    verdicts = {}
    for v in result.get("referee", []):
        if v.get("verdict") in VERDICTS:
            verdicts[(v.get("command"), v.get("id"))] = v
    # A verdict is about the claim as it was. A command a fix replaced must be refereed again:
    # verdicts given before the fix do not carry over, unless they came after it (referee-fix-...).
    corrected = {fix["command"]["id"] for fix in result.get("corrections", []) if isinstance(fix.get("command"), dict)}
    regiven = {(v.get("command"), v.get("id")) for v in result.get("referee", []) if v.get("after_fix")}
    for key in [k for k in verdicts if k[0] in corrected and k not in regiven]:
        del verdicts[key]

    kept, left_out, dropped = [], [], []
    counts = {"confirmed": 0, "relocated": 0, "refuted": 0, "unverified": 0, "bad_citation": 0}
    for cid in sorted(proposed):
        c = proposed[cid]
        problems = []
        if not ID_RE.match(cid):
            problems.append("its id is not of the form CMD-name")
        entry = entries.get(c.get("handler"))
        if entry is None:
            problems.append("names the handler %r, which the map does not have" % c.get("handler"))
        else:
            problems += bind(c, entry)
        for v in c.get("vars", []):
            if v.get("sql"):
                bad = check_sql(v["sql"])
                if bad:
                    problems.append("the query of variable %s %s" % (v.get("name"), bad))
        for group in ("guards", "branches"):
            survivors = []
            for g in c.get(group, []):
                verdict = verdicts.get((cid, g.get("id")))
                if verdict and verdict["verdict"] == "refuted":
                    counts["refuted"] += 1
                    dropped.append({"command": cid, "id": g.get("id"), "why": "refuted by the referee: " + (verdict.get("reason") or ""),
                                    "claim": g})
                    continue
                if verdict and verdict["verdict"] == "wrong-citation" and verdict.get("cite"):
                    g["cite"] = verdict["cite"]
                    counts["relocated"] += 1
                    g["verified"] = True
                elif verdict and verdict["verdict"] == "confirmed":
                    counts["confirmed"] += 1
                    g["verified"] = True
                else:
                    counts["unverified"] += 1
                    g["verified"] = False
                bad = check_cite(workspace, g.get("cite"))
                if bad:
                    counts["bad_citation"] += 1
                    dropped.append({"command": cid, "id": g.get("id"), "why": "its citation %s" % bad, "claim": g})
                    continue
                survivors.append(g)
            if group in c:
                c[group] = survivors
        if not problems:
            problems += [p.split(": ", 1)[-1] for p in build_matrix.check_command(c)]
        if problems:
            left_out.append({"command": cid, "problems": problems})
        else:
            c.pop("shard", None)
            kept.append(c)

    covered = {c.get("handler") for c in kept}
    uncovered = sorted(e.get("id") + " " + (e.get("handler") or "") for e in themap.get("entries", [])
                       if e.get("handler") not in covered)
    stats = {"commands": len(kept), "left_out": len(left_out), "guards": dict(counts),
             "entries": len(themap.get("entries", [])), "entries_without_command": len(uncovered)}
    _lib.write_json(os.path.join(adir, "model", "commands.json"), {"schema": SCHEMA, "commands": kept})
    report = {"schema": SCHEMA, "stats": stats, "left_out": left_out, "dropped": dropped,
              "entries_without_command": uncovered, "gaps": result.get("gaps", []),
              "instruction_shaped": result.get("instruction_shaped", [])}
    _lib.write_json(os.path.join(adir, "model", "render.json"), report)
    with open(os.path.join(adir, "MODEL.md"), "w", encoding="utf-8") as fh:
        fh.write(markdown(system, kept, report, entries))
    return report


def when_text(cond):
    if "all" in cond:
        return " and ".join("(%s)" % when_text(c) for c in cond["all"])
    if "any" in cond:
        return " or ".join("(%s)" % when_text(c) for c in cond["any"])
    if "not" in cond:
        return "not (%s)" % when_text(cond["not"])
    if "is" in cond:
        return "%s is %s" % (cond["var"], build_matrix.text(cond["is"]))
    if "in" in cond:
        return "%s is one of %s" % (cond["var"], ", ".join(build_matrix.text(v) for v in cond["in"]))
    return "%s is none of %s" % (cond["var"], ", ".join(build_matrix.text(v) for v in cond.get("not_in", [])))


def cell_safe(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def markdown(system, commands, report, entries):
    s = report["stats"]
    lines = ["# Model: %s" % system, "",
             "Written by `render_model.py`. Each command is what a reader claims the code does at one entry point;",
             "the matrix run compares every claim with what the application does.", "",
             "%d commands for %d entry points. Guards and branches: %d confirmed by a referee, %d re-located, %d dropped as"
             " refuted, %d dropped for a citation that leads nowhere, %d not looked at by a referee." % (
                 s["commands"], s["entries"], s["guards"]["confirmed"], s["guards"]["relocated"], s["guards"]["refuted"],
                 s["guards"]["bad_citation"], s["guards"]["unverified"])]
    if report["entries_without_command"]:
        lines += ["", "**Entry points without a command:** " + "; ".join(report["entries_without_command"])]
    if report["left_out"]:
        lines += ["", "**Commands left out as incoherent:**"]
        lines += ["- %s: %s" % (x["command"], "; ".join(x["problems"])) for x in report["left_out"]]
    if report["gaps"]:
        lines += ["", "**Gaps:**"] + ["- %s" % cell_safe(g) for g in report["gaps"]]
    for c in commands:
        entry = entries.get(c.get("handler"), {})
        b = c["binding"]
        how = "%s %s" % (b.get("method"), b.get("path")) if b.get("kind") == "http" else "%s.%s" % (b.get("bean"), b.get("method"))
        lines += ["", "## %s — %s" % (c["id"], cell_safe(c.get("title", ""))), "",
                  "Entry point %s `%s`, called as `%s`%s. Changes: `%s`." % (
                      entry.get("id", "?"), c.get("handler"), how, " (a read)" if c.get("reads_only") else "",
                      c.get("target", "?")), "",
                  "| Variable | Kind | Values (base first) |", "|---|---|---|"]
        for v in c.get("vars", []):
            base = build_matrix.text(c["base"][v["name"]])
            values = [base] + [build_matrix.text(x) for x in v["domain"] if build_matrix.text(x) != base]
            lines.append("| `%s` | %s | %s |" % (v["name"], v.get("kind", ""), cell_safe(", ".join(values))))
        if c.get("guards"):
            lines += ["", "| Check | When | Then | Code | Referee |", "|---|---|---|---|---|"]
            for g in c["guards"]:
                lines.append("| %s | %s | %s%s | `%s` | %s |" % (
                    g["id"], cell_safe(when_text(g["when"])), g["outcome"], " " + g["code"] if g.get("code") else "",
                    g.get("cite"), "confirmed" if g.get("verified") else "not looked at"))
        if c.get("branches"):
            lines += ["", "| Branch | When | Also writes | Code | Referee |", "|---|---|---|---|---|"]
            for g in c["branches"]:
                lines.append("| %s | %s | %s | `%s` | %s |" % (
                    g["id"], cell_safe(when_text(g["when"])), cell_safe(", ".join(g.get("writes", []))), g.get("cite"),
                    "confirmed" if g.get("verified") else "not looked at"))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("system")
    ap.add_argument("--workspace", default=".")
    args = ap.parse_args(argv)
    r = render(os.path.abspath(args.workspace), args.system)
    s = r["stats"]
    print("model written: %d commands for %d entry points (%d entry points without a command, %d commands left out); "
          "guards and branches: %d confirmed, %d re-located, %d refuted, %d with a citation that leads nowhere, %d not refereed" % (
              s["commands"], s["entries"], s["entries_without_command"], s["left_out"], s["guards"]["confirmed"],
              s["guards"]["relocated"], s["guards"]["refuted"], s["guards"]["bad_citation"], s["guards"]["unverified"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
