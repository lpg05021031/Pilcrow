#!/usr/bin/env python3
"""Tie a specification to the cases the audit runs.

    python3 spec.py split <system> <prd file> [--workspace DIR]
    python3 spec.py brief <system> <section> [--check] [--workspace DIR]
    python3 spec.py join  <system> [--workspace DIR]

`split` cuts the document into sections at its headings and writes spec/sections.json (each section
with its line numbers) and spec/vocabulary.json: the names of the commands, their variables and the
values of each, and nothing about what the code does with them. That vocabulary is all a spec analyst
is given besides the section's text, so what it writes down comes from the document alone.

`brief` prints what one analyst is given for one section: the section's text with its line numbers,
the vocabulary, and the form of the reply. The reply is saved unchanged as
spec/raw/clauses-<section>.json:

    { "clauses": [ { "quote": "the exact words", "lines": [12, 13], "status": "testable" | "ambiguous" | "untestable",
                     "note": "...", "expectations": [ { "command": "CMD-x", "when": { "var": "value" },
                                                       "outcome": "allowed" | "rejected" | "ignored" | "not-allowed",
                                                       "code": "..." } ] } ] }

("not-allowed": the words say it must not happen and do not say whether the caller is told.)

`brief --check` prints what a second analyst is given: the same section, and every expectation the
first one wrote, numbered. Its reply says of each whether the quoted words really say it, and is saved
unchanged as spec/raw/check-<section>.json:

    { "verdicts": [ { "ref": "2.1", "verdict": "faithful" | "not-faithful", "reason": "..." } ] }

`join` keeps a clause only if its quote stands, word for word, in the lines it names, and sets aside an
expectation the second analyst called not faithful to the words. Each remaining expectation
is tied to the case of that command whose variables have the values it names. Where the specification
and the observed behaviour agree, the pinned case records which clause it satisfies. Where they differ,
the case from then on expects what the specification says (source "spec"), so the difference shows as a
mismatch in every verify run until the code or the specification changes; a person's decision about the
same case still comes first (apply_decisions.py lays the three over each other). Writes
spec/clauses.json, spec/expectations.json and SPEC_TRACE.md, with the reverse list too: executed cases
no clause speaks about, and cases about which two clauses say different things.
"""
import argparse
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402

SCHEMA = 1
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
OUTCOMES = ("allowed", "rejected", "ignored", "not-allowed")


def squeeze(text):
    return " ".join((text or "").split())


def split(workspace, system, prd):
    adir = _lib.audit_dir(workspace, system)
    path = os.path.normpath(os.path.join(workspace, prd))
    if not _lib.inside(workspace, path) or not os.path.isfile(path):
        _lib.die("the specification must be a file inside the workspace: %s" % prd)
    text = _lib.read_text(path)
    if text is None:
        _lib.die("cannot read %s (too large, or not text)" % prd)
    lines = text.split("\n")
    sections, current = [], None
    for n, line in enumerate(lines, 1):
        m = HEADING.match(line)
        if m or current is None:
            current = {"title": m.group(2) if m else "(before the first heading)", "first_line": n, "last_line": n}
            sections.append(current)
        current["last_line"] = n
    sections = [s for s in sections if squeeze("\n".join(lines[s["first_line"] - 1:s["last_line"]]).lstrip("#"))]
    for i, s in enumerate(sections, 1):
        s["id"] = "SEC-%02d" % i
        s["text"] = "\n".join("%d: %s" % (n, lines[n - 1]) for n in range(s["first_line"], s["last_line"] + 1))
    commands = (_lib.read_json(os.path.join(adir, "model", "commands.json"), {}) or {}).get("commands", [])
    # The names only: what can be asked of the system and what each variable can be. Nothing about which
    # value leads to which result, so an analyst cannot copy the code's behaviour into an expectation.
    vocabulary = []
    for c in commands:
        variables = []
        for v in c.get("vars", []):
            entry = {"name": v["name"], "values": [str(x) for x in v.get("domain", [])],
                     "usual": str(c.get("base", {}).get(v["name"]))}
            if v.get("meaning"):
                entry["meaning"] = v["meaning"]
            if v.get("sql"):
                entry["is_read_by"] = v["sql"]
            variables.append(entry)
        vocabulary.append({"id": c["id"], "title": c.get("title"), "about": c.get("target"), "variables": variables})
    _lib.write_json(os.path.join(adir, "spec", "sections.json"),
                    {"schema": SCHEMA, "file": os.path.relpath(path, workspace).replace(os.sep, "/"),
                     "sha256": _lib.sha256_file(path), "sections": sections})
    _lib.write_json(os.path.join(adir, "spec", "vocabulary.json"), {"schema": SCHEMA, "commands": vocabulary})
    return sections


REPLY_CLAUSES = {
    "clauses": [{"quote": "the exact words of the document, copied character for character",
                 "lines": ["first line number", "last line number"],
                 "status": "testable | ambiguous | untestable",
                 "note": "for ambiguous or untestable: what cannot be told, in one sentence",
                 "expectations": [{"command": "an id from the vocabulary", "when": {"variable name": "one of its values"},
                                   "outcome": "allowed | rejected | ignored | not-allowed",
                                   "code": "the error code, only if the document itself names one"}]}]}
REPLY_CHECK = {"verdicts": [{"ref": "as numbered in the brief", "verdict": "faithful | not-faithful",
                             "reason": "for not-faithful: what the words do not say, in one sentence"}]}


def raw_clauses(spec_dir, section):
    body = _lib.read_json(os.path.join(spec_dir, "raw", "clauses-%s.json" % section))
    return body.get("clauses", []) if isinstance(body, dict) and isinstance(body.get("clauses"), list) else None


def brief(workspace, system, section, check=False):
    spec_dir = os.path.join(_lib.audit_dir(workspace, system), "spec")
    sections = (_lib.read_json(os.path.join(spec_dir, "sections.json"), {}) or {}).get("sections")
    vocabulary = (_lib.read_json(os.path.join(spec_dir, "vocabulary.json"), {}) or {}).get("commands")
    if sections is None or vocabulary is None:
        _lib.die("audit/%s/spec/sections.json not found: run `spec.py split` first" % system)
    sec = next((s for s in sections if s["id"] == section), None)
    if sec is None:
        _lib.die("no section %s; the sections are %s" % (section, ", ".join(s["id"] for s in sections)))
    out = {"section": {"id": sec["id"], "title": sec["title"], "lines": [sec["first_line"], sec["last_line"]], "text": sec["text"]},
           "vocabulary": vocabulary}
    if not check:
        out.update({"task": "clauses", "reply": REPLY_CLAUSES,
                    "save_reply_as": "audit/%s/spec/raw/clauses-%s.json" % (system, section)})
        return out
    clauses = raw_clauses(spec_dir, section)
    if clauses is None:
        _lib.die("audit/%s/spec/raw/clauses-%s.json not found or not in the agreed form" % (system, section))
    items = []
    for n, c in enumerate(clauses, 1):
        for m, x in enumerate(c.get("expectations") or [], 1):
            items.append({"ref": "%d.%d" % (n, m), "quote": c.get("quote"), "lines": c.get("lines"), "expectation": x})
    out.update({"task": "check", "expectations": items, "reply": REPLY_CHECK,
                "save_reply_as": "audit/%s/spec/raw/check-%s.json" % (system, section)})
    return out


def cell_for(cells, command, when):
    """The executable case of a command in which the variables have the named values and nothing else is unusual.

    That is the case that departs from the usual one in a variable the clause names, or the usual case
    itself when every value named is the usual one. A case that departs in a variable the clause does
    not mention is never taken: the clause says nothing about it.
    """
    base = None
    for c in cells:
        if c["command"] != command or "with" not in c or "unreachable" in c:
            continue
        given = c.get("given", {})
        if not all(k in given and str(given[k]) == str(v) for k, v in when.items()):
            continue
        if c.get("deviation") is None:
            base = c
        elif c["deviation"].split("=")[0] in when:
            return c
    return base


def join(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    spec_dir = os.path.join(adir, "spec")
    sections = _lib.read_json(os.path.join(spec_dir, "sections.json"))
    matrix = _lib.read_json(os.path.join(adir, "model", "matrix.json"))
    if not isinstance(sections, dict) or not isinstance(matrix, dict):
        _lib.die("audit/%s/spec/sections.json or model/matrix.json not found: run `spec.py split` after audit-characterize" % system)
    source = _lib.read_text(os.path.join(workspace, sections["file"])) or ""
    lines = source.split("\n")
    commands = {c["id"]: c for c in (_lib.read_json(os.path.join(adir, "model", "commands.json"), {}) or {}).get("commands", [])}
    import apply_decisions
    pins = (_lib.read_json(os.path.join(adir, "model", "expected.json"), {}) or {}).get("cells", {})

    clauses, expectations, dropped, unchecked, unread, times = [], [], [], [], [], {}
    for sec in sections["sections"]:
        name = "clauses-%s.json" % sec["id"]
        found = raw_clauses(spec_dir, sec["id"])
        if found is None:
            unread.append(sec["id"])
            continue
        checked = _lib.read_json(os.path.join(spec_dir, "raw", "check-%s.json" % sec["id"]))
        verdicts = {str(v.get("ref")): v for v in checked.get("verdicts", [])} if isinstance(checked, dict) else None
        if verdicts is None:
            unchecked.append(sec["id"])
        for n, c in enumerate(found, 1):
            quote = c.get("quote") or ""
            span = c.get("lines") or []
            first, last = (int(span[0]), int(span[-1])) if span else (0, 0)
            where = squeeze("\n".join(lines[max(first, 1) - 1:last]))
            if not squeeze(quote) or squeeze(quote) not in where:
                dropped.append({"from": name, "quote": quote[:200], "lines": span, "why": "the quote is not in the lines it names"})
                continue
            # The id follows from the words, so a clause keeps it from one reading to the next; a sentence that
            # states two requirements gives two clauses with the same quote, told apart by their order.
            cid = "PRD-" + hashlib.sha1(squeeze(quote).encode("utf-8")).hexdigest()[:8]
            times[cid] = times.get(cid, 0) + 1
            if times[cid] > 1:
                cid = "%s-%d" % (cid, times[cid])
            clause = {"id": cid, "quote": quote, "lines": [first, last], "status": c.get("status") or "testable",
                      "note": c.get("note") or "", "from": name}
            clauses.append(clause)
            for m, x in enumerate(c.get("expectations") or [], 1) if clause["status"] == "testable" else []:
                item = {"clause": cid, "command": x.get("command"), "when": x.get("when") or {}, "outcome": x.get("outcome"),
                        "code": x.get("code") or None}
                verdict = (verdicts or {}).get("%d.%d" % (n, m))
                item["checked"] = verdict.get("verdict") if verdict else None
                if verdict and verdict.get("verdict") == "not-faithful":
                    item["result"] = "set-aside"
                    item["why"] = "a second reader: the words do not say this (%s)" % (verdict.get("reason") or "no reason given")
                    expectations.append(item)
                    continue
                if item["command"] not in commands or item["outcome"] not in OUTCOMES:
                    item["result"] = "not-usable"
                    item["why"] = "names a command or an outcome the model does not have"
                    expectations.append(item)
                    continue
                cell = cell_for(matrix["cells"], item["command"], item["when"])
                entry = pins.get(cell["key"]) if cell else None
                if entry is None:
                    item["result"] = "no-case"
                    item["why"] = "no executed case of %s has these values" % item["command"]
                    expectations.append(item)
                    continue
                item["cell"], item["key"] = cell["id"], cell["key"]
                seen = apply_decisions.seen(entry)
                if item["outcome"] == "not-allowed":
                    agrees = seen.get("outcome") != "allowed"
                else:
                    agrees = seen.get("outcome") == item["outcome"]
                agrees = agrees and (not item["code"] or seen.get("code") == item["code"])
                item["observed"] = {"outcome": seen.get("outcome"), "code": seen.get("code")}
                item["result"] = "agrees" if agrees else "differs"
                expectations.append(item)

    # Two clauses that want different things of one case: the specification disagrees with itself there.
    wants, contradictions = {}, []
    for x in expectations:
        if x.get("key"):
            wants.setdefault(x["key"], []).append(x)
    for key, group in sorted(wants.items()):
        if any(a["outcome"] != b["outcome"] or (a["code"] and b["code"] and a["code"] != b["code"])
               for a in group for b in group):
            contradictions.append({"cell": group[0]["cell"], "clauses": sorted({x["clause"] for x in group})})

    spoken = {x.get("key") for x in expectations if x.get("key")}
    silent = {}
    for c in matrix["cells"]:
        if "with" in c and "unreachable" not in c and c["key"] not in spoken:
            silent[c["command"]] = silent.get(c["command"], 0) + 1
    counts = {}
    for x in expectations:
        counts[x["result"]] = counts.get(x["result"], 0) + 1
    out = {"schema": SCHEMA, "clauses": len(clauses), "dropped": dropped, "expectations": counts,
           "contradictions": contradictions, "cases_no_clause_speaks_about": silent,
           "sections_not_read": unread, "sections_not_checked_by_a_second_reader": unchecked}
    _lib.write_json(os.path.join(spec_dir, "clauses.json"), {"schema": SCHEMA, "file": sections["file"], "clauses": clauses, "dropped": dropped})
    _lib.write_json(os.path.join(spec_dir, "expectations.json"), {"schema": SCHEMA, "summary": out, "expectations": expectations})
    with open(os.path.join(adir, "SPEC_TRACE.md"), "w", encoding="utf-8") as fh:
        fh.write(trace(system, sections["file"], clauses, expectations, dropped, silent, contradictions, unread, unchecked))
    apply_decisions.apply(workspace, system)        # lays what the clauses say over the pinned observations
    return out


def trace(system, file, clauses, expectations, dropped, silent, contradictions=(), unread=(), unchecked=()):
    by = {}
    for x in expectations:
        by.setdefault(x["clause"], []).append(x)
    counts = {}
    for x in expectations:
        counts[x["result"]] = counts.get(x["result"], 0) + 1
    by_status = {}
    for c in clauses:
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1
    lines = ["# Specification trace: %s" % system, "",
             "%d clauses (%s); expectations: %s." % (
                 len(clauses), ", ".join("%d %s" % (n, k) for k, n in sorted(by_status.items())) or "none",
                 ", ".join("%d %s" % (n, k) for k, n in sorted(counts.items())) or "none"), "",
             "Written by `spec.py join` from `%s`. A clause is kept only if its quote stands word for word in the" % file,
             "lines it names. \"differs\" means the code was observed to do something else than the clause says.", ""]
    if unread:
        lines += ["**Sections no analyst has read yet:** %s. Nothing below speaks for them." % ", ".join(unread), ""]
    if unchecked:
        lines += ["**Sections whose expectations no second reader checked against the words:** %s." % ", ".join(unchecked), ""]
    lines += [
             "| Clause | Lines | Says | Case | Result |", "|---|---|---|---|---|"]
    for c in clauses:
        rows = by.get(c["id"]) or [None]
        for x in rows:
            says = c["quote"].replace("|", "\\|").replace("\n", " ")
            if x is None:
                lines.append("| %s | %d-%d | %s | — | %s%s |" % (c["id"], c["lines"][0], c["lines"][1], says, c["status"],
                                                                 ": " + c["note"].replace("|", "\\|") if c["note"] else ""))
            else:
                want = "%s%s when %s" % (x["outcome"], " " + x["code"] if x.get("code") else "",
                                         ", ".join("%s=%s" % kv for kv in sorted(x["when"].items())) or "nothing unusual")
                got = " (observed: %s%s)" % (x["observed"]["outcome"], " " + x["observed"]["code"] if x["observed"].get("code") else "") \
                    if x.get("observed") and x["result"] == "differs" else ""
                lines.append("| %s | %d-%d | %s | %s: %s | **%s**%s |" % (
                    c["id"], c["lines"][0], c["lines"][1], says, x.get("cell") or x["command"], want, x["result"],
                    got or (": " + x["why"] if x.get("why") else "")))
    if contradictions:
        lines += ["", "## Cases about which two clauses say different things", ""]
        lines += ["- %s: %s" % (c["cell"], ", ".join(c["clauses"])) for c in contradictions]
    if dropped:
        lines += ["", "## Clauses dropped", ""] + ["- %s (lines %s): %s" % (d["quote"], d["lines"], d["why"]) for d in dropped]
    if silent:
        lines += ["", "## Executed cases no clause speaks about", ""]
        lines += ["- %s: %d cases" % (k, v) for k, v in sorted(silent.items())]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("split")
    s.add_argument("system")
    s.add_argument("prd")
    s.add_argument("--workspace", default=".")
    b = sub.add_parser("brief")
    b.add_argument("system")
    b.add_argument("section")
    b.add_argument("--check", action="store_true")
    b.add_argument("--workspace", default=".")
    j = sub.add_parser("join")
    j.add_argument("system")
    j.add_argument("--workspace", default=".")
    args = ap.parse_args(argv)
    workspace = os.path.abspath(args.workspace)
    if args.cmd == "split":
        sections = split(workspace, args.system, args.prd)
        print("%d sections: %s" % (len(sections), ", ".join("%s (lines %d-%d)" % (x["id"], x["first_line"], x["last_line"]) for x in sections)))
    elif args.cmd == "brief":
        import json
        # One line: the vocabulary is long, and an analyst reads it as well without the white space.
        print(json.dumps(brief(workspace, args.system, args.section, args.check), ensure_ascii=False, separators=(",", ":")))
    else:
        out = join(workspace, args.system)
        e = out["expectations"]
        print("%d clauses kept, %d dropped for a quote that is not in the document; expectations: %s; "
              "%d executed cases no clause speaks about%s" % (
                  out["clauses"], len(out["dropped"]), ", ".join("%d %s" % (e[k], k) for k in sorted(e)) or "none",
                  sum(out["cases_no_clause_speaks_about"].values()),
                  "; %d cases about which two clauses say different things" % len(out["contradictions"]) if out["contradictions"] else ""))
        if out["sections_not_read"]:
            print("sections no analyst has read yet: " + ", ".join(out["sections_not_read"]))
        if out["sections_not_checked_by_a_second_reader"]:
            print("sections not checked by a second reader: " + ", ".join(out["sections_not_checked_by_a_second_reader"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
