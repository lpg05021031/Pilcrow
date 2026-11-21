#!/usr/bin/env python3
"""Write the one-page report from the files the audit already holds.

    python3 build_report.py <system> [--workspace DIR]

Reads, under audit/<system>/: INTENT.md, profile.json, VERDICT.json, findings.json, questions.json,
DECISIONS.json, model/reconciliation.json, model/invariants.json, assure/canary.json and the run.json
of the latest run of each kind. Writes REPORT.html: one file, no scripts, no outside resources, so it
opens anywhere and can be attached to a ticket.

Nothing is computed here that a judge has not computed: this script only lays out. Every piece of text
that comes from the audited repository or from a model is escaped; none of it is treated as markup.
"""
import argparse
import html
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402
import judge  # noqa: E402
import status as status_mod  # noqa: E402

WORD = {"complete": "reviewed completely", "partial": "reviewed in part", "insufficient": "reviewed insufficiently"}
MARK = {"pass": "pass", "fail": "FAIL", "could-not-pass": "could not pass"}
CSS = """
body{font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;max-width:1000px;margin:2em auto;padding:0 1em;color:#1b1f23}
h1{font-size:1.6em;margin-bottom:.2em}h2{margin-top:2em;border-bottom:1px solid #d0d7de;padding-bottom:.2em}h3{margin-top:1.4em}
table{border-collapse:collapse;width:100%;margin:.6em 0}th,td{border:1px solid #d0d7de;padding:.35em .6em;text-align:left;vertical-align:top}
th{background:#f6f8fa}code{background:#f6f8fa;padding:.1em .3em;border-radius:3px;font-size:.92em}
.pass{color:#1a7f37}.fail{color:#cf222e;font-weight:600}.could-not-pass{color:#9a6700}
.tag{display:inline-block;padding:0 .5em;border-radius:1em;border:1px solid #d0d7de;font-size:.85em;margin-right:.3em}
.defect{border-color:#cf222e;color:#cf222e}.anomaly,.inconsistency{border-color:#9a6700;color:#9a6700}
.muted{color:#57606a}.box{border:1px solid #d0d7de;border-radius:6px;padding:.6em 1em;margin:.8em 0}
"""


def e(value):
    return html.escape("" if value is None else str(value))


def table(header, rows):
    out = ["<table><tr>" + "".join("<th>%s</th>" % e(h) for h in header) + "</tr>"]
    for row in rows:
        out.append("<tr>" + "".join("<td>%s</td>" % cell for cell in row) + "</tr>")
    return "".join(out) + "</table>"


def latest_note(adir, kind):
    run_dir = judge.latest_run(adir, kind)
    return (_lib.read_json(os.path.join(run_dir, "run.json"), {}) or {}) if run_dir else None


def build(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    load = lambda rel: _lib.read_json(os.path.join(adir, rel), {}) or {}        # noqa: E731
    verdict, findings, questions = load("VERDICT.json"), load("findings.json"), load("questions.json")
    if not verdict.get("entities"):
        _lib.die("audit/%s/VERDICT.json not found: run `judge.py verdict` first" % system)
    intent = status_mod.parse_intent(os.path.join(adir, "INTENT.md"))
    caps = load("profile.json").get("capabilities") or {}
    rec, rules, canary = load("model/reconciliation.json"), load("model/invariants.json"), load("assure/canary.json")
    decisions = load("DECISIONS.json").get("decisions", [])
    qs = questions.get("questions", [])
    parts = ["<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Audit report: %s</title><style>%s</style></head><body>" % (e(system), CSS)]
    parts.append("<h1>Audit of the business logic: %s</h1>" % e(system))
    parts.append("<p class='muted'>Computed %s from the result files. Goal: %s. Source as of commit <code>%s</code>.</p>" % (
        e(verdict.get("computed")), e(intent.get("goal") or "audit"), e(intent.get("base_commit") or "not recorded")))

    # ---- the verdict
    parts.append("<h2>How far \"nothing found\" can be trusted</h2>")
    parts.append(table(["Entity", "Verdict", "Cases"],
                       [["<code>%s</code>" % e(x["entity"]), e(WORD.get(x["verdict"], x["verdict"])), e(x["cells"])] for x in verdict["entities"]]))
    waiting = verdict.get("waiting_for_people") or {}
    parts.append("<div class='box'><b>Waiting for people.</b> Open flagged questions: %s. Sign-off: not signed.</div>" % (
        e(", ".join(waiting.get("open_flagged_questions") or []) or "none")))

    # ---- findings
    fs = findings.get("findings", [])
    parts.append("<h2>Findings (%d)</h2>" % len(fs))
    if not fs:
        parts.append("<p>No finding has execution evidence.</p>")
    for f in fs:
        parts.append("<h3>%s — %s</h3>" % (e(f["id"]), e(f["title"])))
        parts.append("<p><span class='tag %s'>%s</span><span class='tag'>basis %s</span><span class='tag'>%s%s</span></p>" % (
            e(f["class"]), e(f["class"]), e(", ".join(f.get("basis") or [])), e(f["status"]),
            ", grade " + e(f["grade"]) if f.get("grade") else ""))
        parts.append("<p>%s</p>" % e(f["statement"]))
        if f.get("answer") == "not-intended":
            parts.append("<p class='muted'>A person answered: not intended (%s).</p>" % e(f.get("decision")))
        elif f.get("kind") == "against-spec":
            parts.append("<p class='muted'>Rests on the specification. A person can still answer %s: the document may be the one "
                         "that is out of date.</p>" % e(f.get("question")))
        else:
            parts.append("<p class='muted'>Waiting for a person's answer to %s.</p>" % e(f.get("question")))
        if f.get("reservations"):
            parts.append("<p>Reservations: %s</p>" % e("; ".join(judge.RESERVATIONS.get(m, m) for m in f["reservations"])))
        where = [("Code", ", ".join(f.get("cites") or [])), ("Cases", ", ".join((f.get("cells") or []) + (f.get("cases") or [])[:12]))]
        parts.append("<p class='muted'>%s</p>" % " &nbsp; ".join("%s: <code>%s</code>" % (k, e(v)) for k, v in where if v))
    if findings.get("unconfirmed"):
        parts.append("<h3>Unconfirmed candidates</h3><ul>%s</ul>" % "".join(
            "<li>%s: %s</li>" % (e(f.get("question")), e(f.get("statement"))) for f in findings["unconfirmed"]))
    if findings.get("accepted"):
        parts.append("<h3>Accepted as intended</h3><ul>%s</ul>" % "".join(
            "<li>%s, %s: %s%s</li>" % (e(a["question"]), e(a["decision"]), e(a["observed"]),
                                       " — <i>%s</i>" % e(a["note"]) if a.get("note") else "") for a in findings["accepted"]))

    # ---- per entity
    parts.append("<h2>The seven checks, entity by entity</h2>")
    for x in verdict["entities"]:
        parts.append("<h3><code>%s</code> — %s</h3>" % (e(x["entity"]), e(WORD.get(x["verdict"], x["verdict"]))))
        parts.append(table(["Check", "Result", "Detail"],
                           [[e(i["label"]), "<span class='%s'>%s</span>" % (e(i["result"]), e(MARK.get(i["result"], i["result"]))), e(i["detail"])]
                            for i in x["items"]]))

    # ---- what was run
    parts.append("<h2>What was run</h2>")
    counts = rec.get("counts") or {}
    rows = [["Cases of the matrix", e(", ".join("%s %s" % (v, k) for k, v in counts.items() if k != "total")) + " of %s" % e(counts.get("total"))]]
    for kind, label, keys in (("faults", "Failures injected", ("total", "commands")), ("races", "Overlapping calls", ("total", "pairs")),
                              ("sequences", "Random sequences", ("sequences", "steps", "states", "seed"))):
        note = latest_note(adir, kind)
        rows.append([label, "not run" if note is None else e(", ".join("%s %s" % (note.get(k), k) for k in keys if note.get(k) is not None))
                     + (" — " + e(", ".join("%s %s" % (v, k) for k, v in (note.get("counts") or {}).items())) if kind != "sequences" else "")])
    by_status = {}
    for r in rules.get("invariants", []):
        by_status[r.get("status")] = by_status.get(r.get("status"), 0) + 1
    rows.append(["Rules of the data", e(", ".join("%s %s" % (v, k) for k, v in sorted(by_status.items())) or "none proposed")])
    rows.append(["Guard canary", e(", ".join("%s %s" % (v, k) for k, v in (canary.get("counts") or {}).items()) or "not run")])
    spec = load("spec/expectations.json").get("summary")
    if spec is None:
        rows.append(["Specification", "not traced: no finding or verdict here rests on a specification"])
    else:
        silent = sum((spec.get("cases_no_clause_speaks_about") or {}).values())
        rows.append(["Specification", e("%s clauses kept, %s dropped for a quote that is not in the document; expectations: %s; "
                                        "%s executed cases no clause speaks about (SPEC_TRACE.md)" % (
                                            spec.get("clauses"), len(spec.get("dropped") or []),
                                            ", ".join("%s %s" % (v, k) for k, v in sorted((spec.get("expectations") or {}).items())) or "none",
                                            silent))])
    db = caps.get("database") or {}
    rows.append(["Database", e("%s %s, %s; matches production: %s" % (db.get("engine"), db.get("major"), "real engine" if db.get("real_engine") else
                                                                    "stand-in", caps.get("matches_production")))])
    parts.append(table(["", ""], rows))
    confirmed = [r for r in rules.get("invariants", []) if r.get("status") == "confirmed"]
    if confirmed:
        parts.append("<h3>Rules a person confirmed</h3><ul>%s</ul>" % "".join("<li>%s <span class='muted'>(%s)</span></li>" % (
            e(r["statement"]), e(r["id"])) for r in confirmed))

    # ---- questions and decisions
    open_qs = [q for q in qs if q.get("status") == "open"]
    parts.append("<h2>Open questions (%d flagged, %d not flagged)</h2>" % (
        sum(1 for q in open_qs if q.get("flagged")), sum(1 for q in open_qs if not q.get("flagged"))))
    parts.append(table(["Id", "Kind", "Observed"], [[e(q["id"]), e(q["kind"]) + ("" if q.get("flagged") else " <span class='muted'>(not flagged)</span>"),
                                                     e(q["observed"])] for q in open_qs]) if open_qs else "<p>None.</p>")
    parts.append("<h2>Decisions (%d)</h2>" % len(decisions))
    parts.append(table(["Id", "Question", "Answer", "In the person's words", "When"],
                       [[e(d["id"]), e(d["question"]), e(d["answer"]), e(d.get("note")), e(d.get("decided_at"))] for d in decisions])
                 if decisions else "<p>None recorded.</p>")

    parts.append("<h2>What this report does not say</h2><ul>"
                 "<li>Everything here is a sample: cases that were run, sequences that were drawn, failures that were placed. "
                 "The verdict says how much was sampled, not that the rest is free of defects.</li>"
                 "<li>A rule that every part of the code implements consistently and wrongly shows up only against a specification, "
                 "a person's decision, or a confirmed rule.</li>"
                 "<li>An open question is an observation with evidence, not a defect, until a person answers it.</li></ul>")
    parts.append("<h2>Sign-off</h2>" + table(["Name", "Role", "Date", "Scope signed"], [["&nbsp;", "", "", ""]]))
    parts.append("</body></html>")
    path = os.path.join(adir, "REPORT.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("system")
    ap.add_argument("--workspace", default=".")
    args = ap.parse_args(argv)
    path = build(os.path.abspath(args.workspace), args.system)
    print("report written: %s (%d bytes)" % (os.path.relpath(path, os.path.abspath(args.workspace)), os.path.getsize(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
