#!/usr/bin/env python3
"""Check the audit's own work: hand a finding to a reader who was told nothing else, and sum up.

    python3 assure.py brief <system> <finding id> [--workspace DIR]
    python3 assure.py summary <system> [--workspace DIR]

`brief` prints what one adversary is given about one finding: the statement, up to three of the cases
that are said to show it with where their result and evidence files are, how each case's state was
reached, and the places in the code the finding cites. It leaves out everything that would tell the
reader what to conclude: the class, the grade, why the question was asked, and what a person answered.
The reply is saved unchanged as assure/recheck/<finding id>.json:

    { "finding": "F-007", "fingerprint": "...",
      "evidence_shows_it": "yes" | "partly" | "no", "evidence_note": "...",
      "state_by_commands_only": "yes" | "no" | "cannot-tell", "state_note": "...",
      "cited_code": "says-it" | "says-otherwise" | "not-found" | "nothing-cited", "code_note": "...",
      "signs_of_intent": [ { "where": "path:lines", "what": "..." } ],
      "other_explanation": "none, or what else could produce this",
      "verdict": "stands" | "weakened" | "refuted", "reason": "..." }

`judge.py findings` reads these replies: a reply that belongs to the finding as it is now (same
fingerprint) is shown with it, and "weakened" or "refuted" becomes a reservation that lowers the grade.
A finding is never removed because a reader disagreed.

`summary` writes ASSURANCE.md from what exists: the rechecks, the canary (canary.py) and the seeded
defects (seed_run.py). It computes nothing new.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402
import judge  # noqa: E402

CASES_SHOWN = 3
KIND_OF_CASE = (("FAULT-", "faults"), ("RACE-", "races"), ("INV-", "sequences"), ("SEQERR-", "sequences"))
REPLY = {
    "finding": "the id from the brief", "fingerprint": "the fingerprint from the brief, copied",
    "evidence_shows_it": "yes | partly | no", "evidence_note": "what the result and evidence files show, in your words",
    "state_by_commands_only": "yes | no | cannot-tell", "state_note": "what in the way the state was reached made you say so",
    "cited_code": "says-it | says-otherwise | not-found | nothing-cited", "code_note": "path:lines and what the code there does",
    "signs_of_intent": [{"where": "path:lines", "what": "a test that asserts it, a comment or a document that says so"}],
    "other_explanation": "none, or what in the test setup could produce this without the application doing it",
    "verdict": "stands | weakened | refuted", "reason": "one or two sentences",
}


def rel(workspace, path):
    return os.path.relpath(path, workspace).replace(os.sep, "/")


def brief(workspace, system, finding_id):
    adir = _lib.audit_dir(workspace, system)
    found = _lib.read_json(os.path.join(adir, "findings.json"), {}) or {}
    f = next((x for x in found.get("findings", []) + found.get("unconfirmed", []) if x["id"] == finding_id), None)
    if f is None:
        _lib.die("no finding %s in audit/%s/findings.json" % (finding_id, system))
    cells = {c["id"]: c for c in (_lib.read_json(os.path.join(adir, "model", "matrix.json"), {}) or {}).get("cells", [])}
    cases = []
    runs = judge.matrix_runs(adir)
    for cell_id in f.get("cells", [])[:CASES_SHOWN]:
        cell = cells.get(cell_id, {})
        entry = {"case": cell_id, "command": cell.get("command"), "situation": cell.get("deviation") or "nothing unusual",
                 "state_reached_by": {"fixture": cell.get("fixture"), "steps": cell.get("steps")}, "called_with": cell.get("with")}
        for name, _run, rows in reversed(runs):
            if cell_id in rows:
                entry["result_line"] = "%s, the line whose id is %s" % (rel(workspace, os.path.join(adir, "runs", name, "results.jsonl")), cell_id)
                evidence = os.path.join(adir, "runs", name, "evidence", cell_id + ".json")
                if os.path.isfile(evidence):
                    entry["evidence_file"] = rel(workspace, evidence)
                break
        cases.append(entry)
    for case in f.get("cases", [])[:CASES_SHOWN]:
        kind = next((k for prefix, k in KIND_OF_CASE if case.startswith(prefix)), None)
        run_dir = judge.latest_run(adir, kind) if kind else None
        entry = {"case": case, "kind_of_run": kind}
        if run_dir:
            entry["result_line"] = "%s, the line whose id is %s" % (rel(workspace, os.path.join(run_dir, "results.jsonl")), case)
            evidence = os.path.join(run_dir, "evidence", case + ".json")
            if os.path.isfile(evidence):
                entry["evidence_file"] = rel(workspace, evidence)
        cases.append(entry)
    more = len(f.get("cells", [])) + len(f.get("cases", [])) - len(cases)
    return {
        "task": "recheck",
        "finding": {"id": f["id"], "fingerprint": f["fingerprint"], "statement": f["statement"]},
        "cases": cases, "cases_not_shown": more,
        "cited_code": f.get("cites", []),
        "how_states_are_built": [rel(workspace, os.path.join(adir, "model", name)) for name in ("fixtures.json", "recipes.json")],
        "reply": REPLY,
        "save_reply_as": rel(workspace, os.path.join(adir, "assure", "recheck", f["id"] + ".json")),
    }


def summary(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    found = _lib.read_json(os.path.join(adir, "findings.json"), {}) or {}
    canary = _lib.read_json(os.path.join(adir, "assure", "canary.json"))
    seeds = _lib.read_json(os.path.join(adir, "assure", "seeds.json"))
    fs = found.get("findings", [])
    lines = ["# Assurance: %s" % system, "",
             "Written by `assure.py summary` from the files under `audit/%s/assure/`. Three checks of the audit's" % system,
             "own work; none of them changes a finding's status, which rests on the runs alone.", "",
             "## Independent recheck of each finding", ""]
    checked = [f for f in fs if f.get("recheck")]
    count = {}
    for f in checked:
        count[f["recheck"]["verdict"]] = count.get(f["recheck"]["verdict"], 0) + 1
    if not fs:
        lines.append("There is no finding to recheck.")
    else:
        lines += ["%d of %d findings were given to a reader who saw only the statement, the evidence files and the cited code: %s." % (
            len(checked), len(fs), ", ".join("%d %s" % (count[k], k) for k in ("stands", "weakened", "refuted") if count.get(k)) or "none yet"), "",
            "| Finding | Reader's verdict | Evidence shows it | State by commands only | Cited code | Reason |", "|---|---|---|---|---|---|"]
        for f in fs:
            r = f.get("recheck")
            cell = lambda v: str(v or "").replace("|", "\\|").replace("\n", " ")       # noqa: E731
            lines.append("| %s | %s |" % (f["id"], "not rechecked | | | | ") if not r else "| %s | **%s** | %s | %s | %s | %s |" % (
                f["id"], r["verdict"], cell(r.get("evidence_shows_it")), cell(r.get("state_by_commands_only")),
                cell(r.get("cited_code")), cell(r.get("reason"))))
        signs = [(f["id"], s) for f in checked for s in f["recheck"].get("signs_of_intent") or []]
        if signs:
            lines += ["", "Signs that a behaviour may be intended, for the person who answers the question:", ""]
            lines += ["- %s: `%s` — %s" % (fid, s.get("where"), s.get("what")) for fid, s in signs]
    lines += ["", "## Guard canary", ""]
    if not isinstance(canary, dict):
        lines.append("Not run.")
    else:
        c = canary.get("counts") or {}
        lines += ["Each cited check was switched off in a copy of the code, one at a time%s: %s." % (
            "" if canary.get("complete") else " (only some of them: the run was limited)",
            ", ".join("%d %s" % (c[k], k) for k in ("killed", "survived", "expected", "invalid", "no-mutant") if c.get(k)) or "nothing to switch off"), ""]
        for word, title in (("survived", "Checks whose removal no case notices"), ("invalid", "Checks that could not be switched off"),
                            ("no-mutant", "Cited places with nothing this check can switch off")):
            rows = [m for m in canary.get("mutants", []) if m["result"] == word]
            if rows:
                lines += ["%s:" % title, ""] + ["- `%s:%s` (%s)" % (m["file"], m["lines"], ", ".join(sorted(
                    {"%s %s" % (g["command"], g.get("code") or g["id"]) for g in m["guards"]}))) for m in rows] + [""]
    lines += ["## Seeded defects", ""]
    if not isinstance(seeds, dict):
        lines += ["Not run: no defects were given to seed.", ""]
    else:
        s = seeds.get("summary") or {}
        lines += ["%d known defects were put into a copy of the code one at a time; the checks built from the unchanged code noticed %d, "
                  "%d of them by something other than a changed pin." % (s.get("defects", 0), s.get("noticed", 0), s.get("noticed_without_pins", 0)), "",
                  "| Defect | Category | Noticed by |", "|---|---|---|"]
        lines += ["| %s | %s | %s |" % (r["id"], r["category"], ", ".join("%s %d" % (k, r["counts"][k]) for k in r["routes"]) or "**not noticed**")
                  for r in seeds.get("results", [])]
        lines.append("")
    lines += ["## What these do not show", "",
              "- A reader who agrees with a finding has checked the evidence, not the business: whether the behaviour is wanted is still a person's answer.",
              "- The canary says the cases notice when a check disappears. It says nothing about a check that is missing from the code.",
              "- Seeded defects measure what the audit's own tests catch when the code changes afterwards, not what a fresh audit of the changed code would find.", ""]
    path = os.path.join(adir, "ASSURANCE.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return {"findings": len(fs), "rechecked": len(checked), "verdicts": count,
            "canary": (canary or {}).get("counts") if isinstance(canary, dict) else None,
            "seeds": (seeds or {}).get("summary") if isinstance(seeds, dict) else None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("brief")
    b.add_argument("system")
    b.add_argument("finding")
    b.add_argument("--workspace", default=".")
    s = sub.add_parser("summary")
    s.add_argument("system")
    s.add_argument("--workspace", default=".")
    args = ap.parse_args(argv)
    workspace = os.path.abspath(args.workspace)
    if args.cmd == "brief":
        print(json.dumps(brief(workspace, args.system, args.finding), indent=1, ensure_ascii=False))
        return 0
    r = summary(workspace, args.system)
    v = r["verdicts"]
    print("rechecked %d of %d findings%s; canary: %s; seeded defects: %s" % (
        r["rechecked"], r["findings"], " (%s)" % ", ".join("%d %s" % (v[k], k) for k in sorted(v)) if v else "",
        ", ".join("%d %s" % (n, k) for k, n in sorted(r["canary"].items())) if r["canary"] else "not run",
        "%d of %d noticed" % (r["seeds"].get("noticed", 0), r["seeds"].get("defects", 0)) if r["seeds"] else "not run"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
