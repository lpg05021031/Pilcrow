#!/usr/bin/env python3
"""Compute what the audit may claim, from result files only.

    python3 judge.py capabilities <system> [--workspace DIR]
    python3 judge.py findings <system> [--workspace DIR]
    python3 judge.py verdict <system> [--workspace DIR]

`capabilities` reads the newest smoke run under audit/<system>/runs/ (written by the harness itself
while it ran), the facts it extracted and the person's answer about the production database, and
writes the `capabilities` block of profile.json: what works on this application, whether the test
database matches production, and the highest evidence grade each kind of check can reach
(design sections 5.4 and 12.1).

`findings` reads questions.json, DECISIONS.json and every believable matrix run, and writes
findings.json and FINDINGS.md: for each flagged question that a person has not accepted as intended,
whether the runs reproduce it, and with what grade (section 12.2). `verdict` writes VERDICT.json and
VERDICT.md: for each entity, how far "nothing found" can be trusted (section 12.3).

Every rule is in this file; nothing here is decided by a model.

A result file is believed only if run.json agrees with it: the number of lines and the SHA-256 of
results.jsonl must match what the harness recorded when the run ended.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402

SUBSTITUTE_ENGINES = ("h2", "hsql", "derby")
ENGINE_WORDS = (
    ("postgresql", r"postgres(?:ql)?|\bpg\b"),
    ("mysql", r"mysql"),
    ("mariadb", r"mariadb"),
    ("oracle", r"oracle"),
    ("sqlserver", r"sql\s*server|mssql"),
    ("db2", r"\bdb2\b"),
)
ISOLATION_WORDS = (
    ("READ_UNCOMMITTED", r"read[\s_-]*uncommitted"),
    ("READ_COMMITTED", r"read[\s_-]*committed"),
    ("REPEATABLE_READ", r"repeatable[\s_-]*read"),
    ("SERIALIZABLE", r"serializable"),
    ("SNAPSHOT", r"snapshot"),
)


def read_run(run_dir):
    """Return (rows by id, problem). A run whose files disagree with run.json is not believed."""
    run = _lib.read_json(os.path.join(run_dir, "run.json"))
    path = os.path.join(run_dir, "results.jsonl")
    if not isinstance(run, dict) or not os.path.isfile(path):
        return {}, "run.json or results.jsonl is missing: the run did not finish"
    with open(path, "rb") as fh:
        raw = fh.read()
    if hashlib.sha256(raw).hexdigest() != run.get("results_sha256"):
        return {}, "results.jsonl does not match the hash recorded in run.json"
    rows = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return {}, "results.jsonl holds a line that is not JSON"
        rows[row.get("id")] = row
    if len(rows) != run.get("total"):
        return {}, "results.jsonl holds %d cases but run.json says %s" % (len(rows), run.get("total"))
    return rows, None


def on_present_code(runs):
    """Of believable runs, oldest first, as (name, run.json, ...): those that ran the application as the latest one did.

    Every run carries a mark of the compiled application (run.json "code"). A run with another mark is
    evidence about other code: a behaviour seen before a change says nothing about the code after it.
    Runs made by a harness that did not write the mark yet count as one code of their own: after the
    harness is updated, the checks are run again once before anything is judged, which costs minutes and
    is always right; keeping unmarked runs would mix evidence from before and after a change.
    """
    if not runs:
        return runs
    now = (runs[-1][1] or {}).get("code")
    return [r for r in runs if (r[1] or {}).get("code") == now]


def latest_run(adir, kind):
    runs = os.path.join(adir, "runs")
    names = sorted(d for d in (os.listdir(runs) if os.path.isdir(runs) else []) if d.startswith(kind + "-"))
    return os.path.join(runs, names[-1]) if names else None


def parse_production(answer):
    """Read engine, major version and isolation level out of the person's own words."""
    if not answer:
        return None
    text = answer.lower()
    engine, version = None, None
    for name, pattern in ENGINE_WORDS:
        m = re.search(pattern, text)
        if m:
            engine = name
            v = re.search(r"\s*(?:v(?:ersion)?\.?\s*)?(\d+)(?:\.\d+)*", text[m.end():m.end() + 24])
            version = int(v.group(1)) if v else None
            break
    isolation = None
    for name, pattern in ISOLATION_WORDS:
        if re.search(pattern, text):
            isolation = name
            break
    return {"engine": engine, "major": version, "isolation": isolation}


def engine_name(product):
    p = (product or "").lower()
    for name, pattern in ENGINE_WORDS:
        if re.search(pattern, p):
            return name
    for s in SUBSTITUTE_ENGINES:
        if s in p:
            return s
    return p or None


def compare_with_production(production, engine):
    """confirmed | unconfirmed | mismatch, with the reason in plain words."""
    if production is None:
        return "unconfirmed", "nobody has said what production runs"
    if not production["engine"]:
        return "unconfirmed", "the answer about production names no database engine this script recognizes"
    actual = engine_name(engine.get("product"))
    if production["engine"] != actual:
        return "mismatch", "production runs %s, the audit runs on %s" % (production["engine"], actual)
    if production["major"] is None:
        return "unconfirmed", "the answer about production gives no version"
    if production["major"] != engine.get("major"):
        return "mismatch", "production runs version %s, the audit runs on version %s" % (production["major"], engine.get("major"))
    if production["isolation"] is None:
        return "unconfirmed", "the answer about production gives no isolation level"
    if production["isolation"] != engine.get("default_isolation"):
        return "mismatch", "production uses %s, the audit database defaults to %s" % (
            production["isolation"], engine.get("default_isolation"))
    return "confirmed", "same engine, same major version, same isolation level"


def capabilities(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    profile_path = os.path.join(adir, "profile.json")
    profile = _lib.read_json(profile_path)
    if not isinstance(profile, dict):
        _lib.die("audit/%s/profile.json not found: run audit-preflight first" % system)

    run_dir = latest_run(adir, "smoke")
    rows, problem = ({}, "no smoke run found: run audit-harness first") if run_dir is None else read_run(run_dir)

    def ok(case):
        return rows.get(case, {}).get("result") == "MATCH"

    def detail(case):
        return rows.get(case, {}).get("detail") or {}

    engine = detail("SMOKE-2").get("engine") or {}
    name = engine_name(engine.get("product"))
    real = bool(name) and name not in SUBSTITUTE_ENGINES
    answers = (_lib.read_json(os.path.join(adir, "answers.json"), {}) or {}).get("answers") or {}
    production = parse_production(answers.get("production_database"))
    match, why = compare_with_production(production, engine) if ok("SMOKE-2") else ("unconfirmed", "the database check did not pass")

    doubles = detail("SMOKE-5").get("doubles") or []
    context = _lib.read_json(os.path.join(adir, "facts", "context.json"), {}) or {}
    clock = bool(context.get("clock_bean"))

    executable = ok("SMOKE-1")
    sees_sql = ok("SMOKE-3")
    resets = ok("SMOKE-4")
    records = ok("SMOKE-5")
    runnable = executable and resets and ok("SMOKE-2")

    def grade(needs):
        """A when everything needed works on the production engine; B when it works with a caveat; C when it cannot run."""
        if not (runnable and all(needs)):
            return "C"
        return "A" if (real and match == "confirmed") else "B"

    if not runnable or not sees_sql:
        concurrency = "C"
    elif not real:
        concurrency = "not-run"         # a stand-in engine locks differently: its results would mislead
    else:
        concurrency = "A" if match == "confirmed" else "B"

    caps = {
        "computed": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "from_run": os.path.relpath(run_dir, adir).replace(os.sep, "/") if run_dir else None,
        "problem": problem,
        "executable": executable,
        "database": {
            "local": ok("SMOKE-2"),
            "engine": name,
            "version": engine.get("version"),
            "major": engine.get("major"),
            "default_isolation": engine.get("default_isolation"),
            "real_engine": real,
            "source": detail("SMOKE-2").get("source"),
        },
        "matches_production": match,
        "matches_production_why": why,
        "sql_interception": sees_sql,
        "repeatable_reset": resets,
        "doubles": {
            "recording": records,
            "edge": sorted(d["type"] for d in doubles if d.get("edge")),
            "inner": sorted(d["type"] for d in doubles if not d.get("edge")),
            "http": detail("SMOKE-5").get("http_restclient"),
            "http_rules": detail("SMOKE-5").get("http_rules", 0),
        },
        "facts": ok("SMOKE-6"),
        "time_controllable": clock,
        "ceilings": {
            "matrix": grade([]),
            "effects": grade([sees_sql, records]),
            "faults": grade([sees_sql, records]),
            "concurrency": concurrency,
            "time_dependent_rules": grade([]) if clock else ("C" if not runnable else "B"),
        },
    }
    profile["capabilities"] = caps
    _lib.write_json(profile_path, profile)
    return caps


def render_capabilities(c):
    lines = []
    if c["problem"]:
        lines.append("PROBLEM: %s" % c["problem"])
    db = c["database"]
    rows = [
        ("application starts in the harness", c["executable"]),
        ("database is local or a container", db["local"]),
        ("real database engine (%s %s)" % (db["engine"], db["major"]), db["real_engine"]),
        ("statements and commits are seen", c["sql_interception"]),
        ("reset is repeatable", c["repeatable_reset"]),
        ("outside systems are replaced by recording doubles", c["doubles"]["recording"]),
        ("facts extracted", c["facts"]),
        ("time can be controlled (a Clock bean exists)", c["time_controllable"]),
    ]
    lines += ["%-52s %s" % (label, "yes" if value else "NO") for label, value in rows]
    lines.append("%-52s %s (%s)" % ("matches production", c["matches_production"], c["matches_production_why"]))
    if c["doubles"]["inner"]:
        lines.append("doubles that are NOT at the edge: %s" % ", ".join(c["doubles"]["inner"]))
    lines.append("highest grade reachable: " + ", ".join("%s %s" % (k.replace("_", " "), v) for k, v in c["ceilings"].items()))
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# Findings and their evidence (design section 12.2)

REPRODUCED_RUNS = 3
CLASS_BY_BASIS = {"N3": "inconsistency", "N4": "anomaly", "N5": "anomaly"}
FINDING_KINDS = ("refused-but-changed", "read-wrote", "undecided", "error", "differs", "dropped", "unstable",
                 "rule-broken", "near-rule", "error-in-sequence", "fault", "race", "against-spec", "one-lets-through")
# A rule a person confirmed, a clause of the specification: going against either needs no further answer to be a defect.
CLASS_BY_KIND = {"rule-broken": "defect", "against-spec": "defect"}
RESERVATIONS = {
    "substitute-engine": "the database is a stand-in, not the real engine",
    "unconfirmed-version": "the database was not confirmed to match production",
    "synthetic-state": "the starting state held rows written directly, not through commands",
    "mock-boundary": "an outside system's stand-in answered with a default it was not configured for",
    "uncontrolled-time": "the case depends on the date and time was not under control",
    "recheck-weakened": "a reader given only the statement and the evidence found a weakness or another possible explanation",
    "recheck-refuted": "a reader given only the statement and the evidence concluded that the evidence does not show it",
}
RECHECK_VERDICTS = ("stands", "weakened", "refuted")
RECHECK_KEPT = ("verdict", "reason", "evidence_shows_it", "evidence_note", "state_by_commands_only", "state_note", "cited_code",
                "code_note", "signs_of_intent", "other_explanation")


def recheck_of(adir, finding_id, fingerprint):
    """What an independent reader said about this finding as it is now; None when nobody did, or about an older form of it."""
    reply = _lib.read_json(os.path.join(adir, "assure", "recheck", finding_id + ".json"))
    if not isinstance(reply, dict) or reply.get("fingerprint") != fingerprint or reply.get("verdict") not in RECHECK_VERDICTS:
        return None
    return {k: reply[k] for k in RECHECK_KEPT if k in reply}


def matrix_runs(adir, kind="matrix"):
    """Believable runs of one kind on the application as it is now, oldest first, as (name, run.json, rows by id)."""
    runs_dir = os.path.join(adir, "runs")
    out = []
    for name in sorted(d for d in (os.listdir(runs_dir) if os.path.isdir(runs_dir) else []) if d.startswith(kind + "-")):
        rows, problem = read_run(os.path.join(runs_dir, name))
        if not problem:
            out.append((name, _lib.read_json(os.path.join(runs_dir, name, "run.json"), {}) or {}, rows))
    return on_present_code(out)


def holds(question, decision, row):
    """Does this result show what the question is about?"""
    observed = row.get("observed") or {}
    if row.get("result") not in ("MATCH", "MISMATCH"):
        return None                                   # not executed: says nothing either way
    expected = row.get("expected") or {}
    if decision and decision.get("answer") == "not-intended" and expected.get("source") == "decision":
        return row.get("result") == "MISMATCH"        # compared against what the person said it must be
    if question["kind"] == "against-spec" and expected.get("source") == "spec":
        return row.get("result") == "MISMATCH"        # compared against what the specification says
    kind, flags = question["kind"], observed.get("flags") or []
    outcome = (observed.get("outcome") or "").lower()
    if kind == "refused-but-changed":
        return "changed-although-rejected" in flags or "changed-although-error" in flags
    if kind == "read-wrote":
        return "read-path-wrote" in flags
    if kind == "error":
        return outcome == "error"
    if kind == "dropped":
        return outcome == "ignored"
    if kind == "undecided":
        return outcome != "rejected" if (question.get("if_not_intended") or {}).get("outcome") == "rejected" else True
    if kind == "one-lets-through":
        return outcome == "allowed"
    if kind == "against-spec":
        want = question.get("if_not_intended") or {}
        if "outcome_not" in want:
            return outcome == want["outcome_not"]
        return outcome != want.get("outcome") or ("code" in want and observed.get("code") != want["code"])
    if kind == "differs":
        want = question.get("if_not_intended") or {}
        return outcome != want.get("outcome") or ("code" in want and observed.get("code") != want["code"])
    return None


def time_dependent(cell, command):
    text = _lib.canonical([cell.get("with"), cell.get("steps"), [v.get("sql") for v in command.get("vars", [])]])
    return "today" in text


def reservations(adir, cell, command, run, caps):
    evidence = _lib.read_json(os.path.join(adir, "runs", run, "evidence", cell["id"] + ".json"), {}) or {}
    out = []
    engine = engine_name(evidence.get("database_engine"))
    if not engine or engine in SUBSTITUTE_ENGINES:
        out.append("substitute-engine")
    if (caps or {}).get("matches_production") != "confirmed":
        out.append("unconfirmed-version")
    if evidence.get("state_built_by_commands_only") is False:
        out.append("synthetic-state")
    if any(e.get("unconfigured") or (e.get("defaulted") and e.get("kind") == "EXT") for e in evidence.get("effects", [])):
        out.append("mock-boundary")
    if not evidence.get("time_controlled") and time_dependent(cell, command):
        out.append("uncontrolled-time")
    return out


def cell_evidence(adir, q, decision, cells, commands, runs, caps):
    """Status of a finding about matrix cells, from every believable run of those cells."""
    per_cell, best, marks, last_run = {}, None, set(), None
    for cell_id in q["cells"]:
        cell = cells.get(cell_id)
        if cell is None:
            continue
        verdicts = []
        for name, _, rows in runs:
            row = rows.get(cell_id)
            if row and (row.get("detail") or {}).get("sha") == cell.get("sha"):
                h = holds(q, decision, row)
                if h is not None:
                    verdicts.append((name, h))
        recent = verdicts[-REPRODUCED_RUNS:]
        per_cell[cell_id] = {"runs": len(verdicts), "shown_in_last": sum(1 for _, h in recent if h),
                             "of_last": len(recent)}
        if len(recent) == REPRODUCED_RUNS and all(h for _, h in recent):
            best = "reproduced"
            marks.update(reservations(adir, cell, commands.get(cell["command"], {}), recent[-1][0], caps))
            last_run = max(last_run or "", recent[-1][0])
    executed = [v for v in per_cell.values() if v["runs"]]
    if q["kind"] == "unstable":
        status = "reproduced" if executed else "unconfirmed"       # two runs that disagree are the evidence
    elif best:
        status = best
    elif executed and all(v["shown_in_last"] == 0 for v in executed):
        status = "fixed"
    else:
        status = "unconfirmed"
    return status, per_cell, marks


def run_evidence(adir, q, caps):
    """Status of a finding from the sequence or fault runs. A sequence finding is reproduced when the
    runner replayed its shortest sequence three times and it showed each time; a fault finding when the
    last three fault runs all show it."""
    kind = q["source"]
    good = [(name, rows) for name, _run, rows in matrix_runs(adir, kind)]
    per_case, reproduced, seen, shown_latest = {}, False, False, False
    for case in q.get("cases", []):
        history = [(name, rows[case]) for name, rows in good if case in rows]
        if not history:
            continue
        seen = True
        if kind == "sequences":
            row = history[-1][1]
            shows = row.get("result") == "MISMATCH"
            replays = (row.get("observed") or {}).get("replays_violating") or 0
            per_case[case] = {"run": history[-1][0], "shows": shows, "replays_showing": replays}
            reproduced = reproduced or (shows and replays >= REPRODUCED_RUNS)
            shown_latest = shown_latest or shows
        else:
            recent = [r.get("result") == "MISMATCH" for _, r in history[-REPRODUCED_RUNS:]]
            per_case[case] = {"runs": len(history), "shown_in_last": sum(recent), "of_last": len(recent)}
            reproduced = reproduced or (len(recent) == REPRODUCED_RUNS and all(recent))
            shown_latest = shown_latest or recent[-1]
    marks = set()
    database = (caps or {}).get("database") or {}
    if database.get("real_engine") is False:
        marks.add("substitute-engine")
    if (caps or {}).get("matches_production") != "confirmed":
        marks.add("unconfirmed-version")
    status = "reproduced" if reproduced else ("fixed" if seen and not shown_latest else "unconfirmed")
    return status, per_case, marks


def findings(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    model = os.path.join(adir, "model")
    questions = (_lib.read_json(os.path.join(adir, "questions.json"), {}) or {}).get("questions")
    matrix = _lib.read_json(os.path.join(model, "matrix.json"))
    if questions is None or not isinstance(matrix, dict):
        _lib.die("audit/%s/questions.json or model/matrix.json not found: run audit-characterize first" % system)
    cells = {c["id"]: c for c in matrix["cells"]}
    commands = {c["id"]: c for c in (_lib.read_json(os.path.join(model, "commands.json"), {}) or {}).get("commands", [])}
    decisions = {}
    for d in (_lib.read_json(os.path.join(adir, "DECISIONS.json"), {}) or {}).get("decisions", []):
        decisions[d["fingerprint"]] = d
    caps = (_lib.read_json(os.path.join(adir, "profile.json"), {}) or {}).get("capabilities")
    runs = matrix_runs(adir)

    old = _lib.read_json(os.path.join(adir, "findings.json"), {}) or {}
    ids, nxt = dict(old.get("ids", {})), old.get("next", 1)
    out, accepted, unconfirmed = [], [], []
    parts = []
    for q in questions:
        if q["kind"] not in FINDING_KINDS:
            continue
        decision = decisions.get(q["fingerprint"])
        decided = set(decision.get("cells") or q["cells"]) & set(q["cells"]) if decision else set()
        if decision and decision.get("answer") in ("intended", "not-intended") and decided and decided != set(q["cells"]):
            # The person spoke about some of the cases: those are judged by the answer, the rest still wait.
            parts.append((dict(q, cells=sorted(decided)), decision))
            parts.append((dict(q, cells=sorted(set(q["cells"]) - decided)), None))
        else:
            parts.append((q, decision))
    for q, decision in parts:
        answer = decision.get("answer") if decision else None
        if answer == "intended":
            accepted.append({"question": q["id"], "decision": decision["id"], "title": q["title"], "observed": q["observed"],
                             "note": decision.get("note", "")})
            continue
        if q.get("source") in ("sequences", "faults", "races"):
            status, per_cell, marks = run_evidence(adir, q, caps)
        else:
            status, per_cell, marks = cell_evidence(adir, q, decision, cells, commands, runs, caps)
        cls = "defect" if answer == "not-intended" else CLASS_BY_KIND.get(q["kind"]) or CLASS_BY_BASIS.get(q["basis"], "anomaly")
        basis = [q["basis"]] + (["N2"] if answer == "not-intended" else [])
        handler = commands.get(q["cells"][0] and cells[q["cells"][0]]["command"], {}).get("handler") if q["cells"] else None
        fp = hashlib.sha256(_lib.canonical([q["kind"], q["cites"], sorted(cells[c]["key"] for c in q["cells"] if c in cells),
                                            handler, q["fingerprint"] if not q["cells"] else None]).encode("utf-8")).hexdigest()[:16]
        if fp not in ids:
            ids[fp] = nxt
            nxt += 1
        recheck = recheck_of(adir, "F-%03d" % ids[fp], fp)
        if recheck and recheck["verdict"] != "stands":
            marks = set(marks) | {"recheck-" + recheck["verdict"]}
        entry = {
            "id": "F-%03d" % ids[fp], "fingerprint": fp, "class": cls, "basis": basis, "kind": q["kind"],
            "title": q["title"], "statement": q["observed"], "question": q["id"],
            "decision": decision["id"] if decision else None, "answer": answer,
            "status": status,
            "grade": None if status != "reproduced" else ("B" if marks else "A"),
            "reservations": sorted(marks),
            "cells": q["cells"], "cases": q.get("cases", []), "cites": q["cites"], "anchor": handler, "evidence": per_cell,
            "severity": None,
        }
        if recheck:
            entry["recheck"] = recheck
        (unconfirmed if status == "unconfirmed" else out).append(entry)
    out.sort(key=lambda f: f["id"])
    result = {"schema": 1, "next": nxt, "ids": ids,
              "computed": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "rule": "reproduced = the last %d runs of one of the cells or failure points all show it, or the shortest "
                      "sequence that shows it was replayed %d times and showed it each time; grade A = reproduced with no "
                      "reservation; B = reproduced with a reservation; unconfirmed candidates are listed apart" % (
                          REPRODUCED_RUNS, REPRODUCED_RUNS),
              "findings": out, "unconfirmed": unconfirmed, "accepted": accepted}
    _lib.write_json(os.path.join(adir, "findings.json"), result)
    with open(os.path.join(adir, "FINDINGS.md"), "w", encoding="utf-8") as fh:
        fh.write(render_findings(system, result))
    return result


def render_findings(system, r):
    lines = ["# Findings: %s" % system, "", "Computed by `judge.py` from the result files; nothing here was typed by a model.",
             "Rule: " + r["rule"] + ".", ""]
    if not r["findings"]:
        lines.append("No finding has execution evidence.")
    for f in r["findings"]:
        lines += ["## %s — %s" % (f["id"], f["title"]), "",
                  "- **Class:** %s (basis %s)%s" % (f["class"], ", ".join(f["basis"]),
                                                    "; a person answered: not intended (%s)" % f["decision"] if f["answer"] == "not-intended" else
                                                    "; waiting for a person's answer to %s" % f["question"]),
                  "- **Status:** %s%s" % (f["status"], ", grade %s" % f["grade"] if f["grade"] else ""),
                  "- **What:** " + f["statement"]]
        if f["reservations"]:
            lines.append("- **Reservations:** " + "; ".join(RESERVATIONS[m] for m in f["reservations"]))
        if f.get("recheck"):
            lines.append("- **Independent recheck:** %s%s" % (f["recheck"]["verdict"],
                                                             " — " + " ".join(str(f["recheck"]["reason"]).split()) if f["recheck"].get("reason") else ""))
            for s in f["recheck"].get("signs_of_intent") or []:
                lines.append("  - a sign that it may be intended: `%s` — %s" % (s.get("where"), " ".join(str(s.get("what")).split())))
        if f["cells"]:
            lines.append("- **Cells:** " + ", ".join(f["cells"]))
        if f.get("cases"):
            lines.append("- **Cases:** " + ", ".join(f["cases"][:12]) + (" ..." if len(f["cases"]) > 12 else ""))
        if f["cites"]:
            lines.append("- **Code:** " + ", ".join("`%s`" % c for c in f["cites"]))
        lines.append("")
    if r["unconfirmed"]:
        lines += ["## Unconfirmed candidates", "", "Seen, but not in %d runs in a row, or not executed. Not findings." % REPRODUCED_RUNS, ""]
        lines += ["- %s (%s): %s" % (f["question"], f["kind"], f["statement"]) for f in r["unconfirmed"]]
        lines.append("")
    if r["accepted"]:
        lines += ["## Accepted as intended", ""]
        lines += ["- %s, %s: %s%s" % (a["question"], a["decision"], a["observed"], " — " + a["note"] if a["note"] else "")
                  for a in r["accepted"]]
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# The verdict on each entity (design section 12.3)

ITEMS = (
    ("matrix", "Matrix executed"),
    ("reconciled", "Model reconciled"),
    ("invariants", "Invariants checked"),
    ("faults", "Faults injected"),
    ("concurrency", "Concurrency explored"),
    ("canary", "Guard canary"),
    ("source", "Source untouched"),
)
VERDICT_RULES = """An entity is **reviewed completely** when all seven items pass, **reviewed in part** when none fails
but at least one could not pass, and **reviewed insufficiently** when any item fails.

1. Matrix executed — passes when every cell of the commands that change the entity has a result for its
   present definition from a run made after the source was recorded, or is classed unreachable with a
   reason. Fails when a cell has no such result, or the harness could not answer an outside call.
   Could not pass when a state was not reached: no recipe was found for it, or the recipe did not get there.
2. Model reconciled — passes when no difference between prediction and observation, and no case decided
   by no check, is left without a person's answer. Fails otherwise.
3. Invariants checked — passes when every rule about the entity that a person confirmed was checked after
   every call of the random sequences. Fails when a confirmed rule was never exercised. Could not pass
   when no rule about the entity has been confirmed, or no sequence run was made.
4. Faults injected — passes when every command that changes the entity had each step of a successful call
   fail in turn. Fails when such a command had no failure injected. Could not pass when the harness
   cannot place a failure, when no fault run was made, or when some failure points could not be run.
5. Concurrency explored — passes when pairs of overlapping calls involving a command of the entity were
   run on the real engine, confirmed to match production. Could not pass when no such run was made, no
   pair involved the entity, the engine is a stand-in or unconfirmed. Fails when a pair could not be run.
   Only pairs within the budget are explored, with one stop each; the item says how many.
6. Guard canary — passes when every check of the entity's commands that refuses with a `throw` was removed
   in a copy of the code, one at a time, and a pinned case noticed each time; a check the model already
   says decides no reachable case may go unnoticed. Fails when the removal of a check goes unnoticed
   otherwise. Could not pass when no canary run was made, it covered only part, or a copy did not build.
7. Source untouched — passes when every source, test and build file matches the manifest. Fails otherwise.

Open questions and the missing sign-off belong to people; they are listed and do not change the verdict."""


def invariants_item(table, rules, rows, run):
    confirmed = [r for r in rules if r.get("status") == "confirmed" and table in (r.get("tables") or [])]
    if not confirmed:
        return ("could-not-pass", "no rule about this entity has been confirmed by a person")
    if not run:
        return ("could-not-pass", "%d confirmed rules, but no sequence run has been made" % len(confirmed))
    unchecked = [r["id"] for r in confirmed if rows.get(r["id"], {}).get("result") not in ("MATCH", "MISMATCH")]
    if unchecked:
        return ("fail", "confirmed rules that no sequence exercised: " + ", ".join(unchecked))
    states = max((rows[r["id"]].get("observed") or {}).get("applied", 0) for r in confirmed)
    return ("pass", "%d confirmed rules checked after every call, in up to %d states" % (len(confirmed), states))


def faults_item(own_cells, commands, rows, run, caps):
    def loses_something(cell):
        e = cell.get("expected") or {}
        return e.get("outcome") == "allowed" and any(e.get(k) for k in ("written", "created", "deleted", "published"))
    changing = sorted({c["command"] for c in own_cells if "with" in c and "unreachable" not in c and loses_something(c)})
    if not changing:
        return ("pass", "no command changes this entity")
    if caps.get("sql_interception") is False:
        return ("could-not-pass", "statements are not seen by the harness here, so a failure cannot be placed")
    if not run:
        return ("could-not-pass", "no fault run has been made")
    by_command = {}
    for rid, row in rows.items():
        by_command.setdefault((row.get("detail") or {}).get("command"), []).append(row)
    untouched = [c for c in changing if not any(r.get("result") in ("MATCH", "MISMATCH") for r in by_command.get(c, []))]
    if untouched:
        return ("fail", "commands that change data and had no failure injected: " + ", ".join(untouched))
    broken = sum(1 for c in changing for r in by_command.get(c, []) if r.get("result") == "ERROR")
    points = sum(1 for c in changing for r in by_command.get(c, []) if r.get("result") in ("MATCH", "MISMATCH"))
    if broken:
        return ("could-not-pass", "%d failure points injected, %d could not be run" % (points, broken))
    return ("pass", "%d failure points injected in %d commands" % (points, len(changing)))


def canary_item(own_cells, canary):
    if not isinstance(canary, dict):
        return ("could-not-pass", "no canary run has been made")
    commands = {c["command"] for c in own_cells}
    mine = [m for m in canary.get("mutants", []) if any(g.get("command") in commands for g in m.get("guards", []))]
    count = {}
    for m in mine:
        count[m["result"]] = count.get(m["result"], 0) + 1
    survived = ["%s:%s" % (m["file"].split("/")[-1], m["lines"]) for m in mine if m["result"] == "survived"]
    if survived:
        return ("fail", "checks whose removal no pinned case notices: " + ", ".join(survived))
    if not canary.get("complete"):
        return ("could-not-pass", "the canary run covered only some of the checks")
    if count.get("invalid"):
        return ("could-not-pass", "%d checks could not be removed without breaking the build" % count["invalid"])
    if not count.get("killed") and not count.get("expected"):
        return ("pass", "no check of these commands refuses with a statement that can be removed")
    extra = "; %d more decide no reachable case, as the model says" % count["expected"] if count.get("expected") else ""
    return ("pass", "%d checks removed one at a time, each noticed by a pinned case%s" % (count.get("killed", 0), extra))


def races_item(own_cells, rows, run, caps):
    commands = {c["command"] for c in own_cells}
    if not run:
        return ("could-not-pass", "no run with overlapping calls has been made")
    mine = [r for r in rows.values() if {(r.get("detail") or {}).get("a_command"), (r.get("detail") or {}).get("b_command")} & commands]
    schedules = [r for r in mine if r.get("result") in ("MATCH", "MISMATCH")]
    if any(r.get("result") == "ERROR" for r in mine):
        return ("fail", "%d pairs involving this entity could not be run" % sum(1 for r in mine if r.get("result") == "ERROR"))
    if not schedules:
        return ("could-not-pass", "no pair of overlapping calls involved a command that changes this entity")
    database = caps.get("database") or {}
    if database.get("real_engine") is False:
        return ("could-not-pass", "the database is a stand-in: it does not lock as the real engine does")
    if caps.get("matches_production") != "confirmed":
        return ("could-not-pass", "%d schedules run, but the database was not confirmed to match production" % len(schedules))
    pairs = {(r["detail"].get("a"), r["detail"].get("b")) for r in schedules}
    return ("pass", "%d schedules of %d pairs of overlapping calls, one stop each" % (len(schedules), len(pairs)))


def verdict(workspace, system):
    import manifest as manifest_mod
    adir = _lib.audit_dir(workspace, system)
    model = os.path.join(adir, "model")
    matrix = _lib.read_json(os.path.join(model, "matrix.json"))
    rec = _lib.read_json(os.path.join(model, "reconciliation.json"))
    if not isinstance(matrix, dict) or not isinstance(rec, dict):
        _lib.die("audit/%s/model/matrix.json or reconciliation.json not found: run audit-characterize first" % system)
    commands = {c["id"]: c for c in (_lib.read_json(os.path.join(model, "commands.json"), {}) or {}).get("commands", [])}
    questions = (_lib.read_json(os.path.join(adir, "questions.json"), {}) or {}).get("questions", [])
    manifest_path = os.path.join(adir, "manifest.json")
    recorded = (_lib.read_json(manifest_path, {}) or {}).get("recorded") or ""
    source = manifest_mod.check(workspace, manifest_path)
    stamp = recorded.replace("-", "").replace(":", "").replace("T", "-").rstrip("Z")        # as in a run's folder name
    open_by_cell = {}
    for q in questions:
        if q["status"] == "open" and q["kind"] in ("differs", "undecided"):
            for c in q["cells"]:
                open_by_cell[c] = q["id"]

    rules = (_lib.read_json(os.path.join(model, "invariants.json"), {}) or {}).get("invariants", [])
    caps = (_lib.read_json(os.path.join(adir, "profile.json"), {}) or {}).get("capabilities") or {}
    seq_run, fault_run = latest_run(adir, "sequences"), latest_run(adir, "faults")
    canary = _lib.read_json(os.path.join(adir, "assure", "canary.json"))
    race_run = latest_run(adir, "races")
    race_rows = read_run(race_run)[0] if race_run else {}
    seq_rows = read_run(seq_run)[0] if seq_run else {}
    fault_rows = read_run(fault_run)[0] if fault_run else {}

    entities = {}
    for cell in matrix["cells"]:
        target = commands.get(cell["command"], {}).get("target") or "(no entity named)"
        entities.setdefault(target, []).append(cell)

    out = []
    for target in sorted(entities):
        own = entities[target]
        states = {c["id"]: rec["cells"].get(c["id"], {"state": "not-run"}) for c in own}
        stale = [c for c, s in states.items() if s.get("run") and s["run"].split("matrix-")[-1] < stamp]
        missing = [c for c, s in states.items() if s["state"] in ("not-run", "harness-gap", "runner-failed")]
        unreached = [c for c, s in states.items() if s["state"] in ("state-not-reached", "steps-failed", "no-recipe")]
        items = {}
        if missing or stale:
            items["matrix"] = ("fail", "%d cells without a result%s" % (
                len(missing), ", %d with a result older than the recorded source" % len(stale) if stale else ""))
        elif unreached:
            items["matrix"] = ("could-not-pass", "%d cells whose state no recipe reached: %s" % (
                len(unreached), ", ".join(sorted(unreached)[:8]) + (" ..." if len(unreached) > 8 else "")))
        else:
            items["matrix"] = ("pass", "%d cells executed, %d classed unreachable" % (
                sum(1 for s in states.values() if s["state"] in ("agree", "differ")),
                sum(1 for s in states.values() if s["state"] == "unreachable")))
        unresolved = sorted({open_by_cell[c] for c in states if c in open_by_cell})
        items["reconciled"] = ("fail", "open: " + ", ".join(unresolved)) if unresolved else ("pass", "no open difference")
        items["invariants"] = invariants_item(target, rules, seq_rows, seq_run)
        items["faults"] = faults_item(own, commands, fault_rows, fault_run, caps)
        items["concurrency"] = races_item(own, race_rows, race_run, caps)
        items["canary"] = canary_item(own, canary)
        items["source"] = ("pass", "%d files match the manifest" % source["tracked"]) if source["status"] == "untouched" else (
            "fail", "%d added, %d removed, %d modified since the manifest" % (
                len(source["added"]), len(source["removed"]), len(source["modified"])))
        words = [v[0] for v in items.values()]
        result = "insufficient" if "fail" in words else ("partial" if "could-not-pass" in words else "complete")
        out.append({"entity": target, "verdict": result, "cells": len(own),
                    "commands": sorted({c["command"] for c in own}),
                    "items": [{"item": key, "label": label, "result": items[key][0], "detail": items[key][1]} for key, label in ITEMS]})
    waiting = [q["id"] for q in questions if q["status"] == "open" and q["flagged"]]
    result = {"schema": 1, "computed": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "entities": out, "waiting_for_people": {"open_flagged_questions": waiting, "signed_off": False}}
    _lib.write_json(os.path.join(adir, "VERDICT.json"), result)
    with open(os.path.join(adir, "VERDICT.md"), "w", encoding="utf-8") as fh:
        fh.write(render_verdict(system, result))
    return result


def render_verdict(system, r):
    word = {"complete": "reviewed completely", "partial": "reviewed in part", "insufficient": "reviewed insufficiently"}
    mark = {"pass": "pass", "fail": "FAIL", "could-not-pass": "could not pass"}
    lines = ["# Verdict: %s" % system, "", "Computed by `judge.py` on %s from the result files." % r["computed"], "",
             "| Entity | Verdict | Cells |", "|---|---|---|"]
    lines += ["| `%s` | %s | %d |" % (e["entity"], word[e["verdict"]], e["cells"]) for e in r["entities"]]
    for e in r["entities"]:
        lines += ["", "## `%s` — %s" % (e["entity"], word[e["verdict"]]), "", "| Item | Result | Detail |", "|---|---|---|"]
        lines += ["| %s | %s | %s |" % (i["label"], mark[i["result"]], i["detail"]) for i in e["items"]]
    w = r["waiting_for_people"]
    lines += ["", "## Waiting for people", "",
              "- Open flagged questions: " + (", ".join(w["open_flagged_questions"]) or "none"),
              "- Sign-off: not signed", "", "## The rules", "", VERDICT_RULES, "",
              "## Sign-off", "", "| Name | Role | Date | Scope signed |", "|---|---|---|---|", "| | | | |", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("capabilities", "findings", "verdict"):
        c = sub.add_parser(name)
        c.add_argument("system")
        c.add_argument("--workspace", default=".")
        if name == "capabilities":
            c.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    workspace = os.path.abspath(args.workspace)
    if args.cmd == "findings":
        r = findings(workspace, args.system)
        by = {}
        for f in r["findings"]:
            key = "%s%s" % (f["class"], " grade " + f["grade"] if f["grade"] else " (" + f["status"] + ")")
            by[key] = by.get(key, 0) + 1
        print("%d findings%s; %d unconfirmed candidates; %d accepted as intended" % (
            len(r["findings"]), ": " + ", ".join("%d %s" % (n, k) for k, n in sorted(by.items())) if by else "",
            len(r["unconfirmed"]), len(r["accepted"])))
        return 0
    if args.cmd == "verdict":
        r = verdict(workspace, args.system)
        for e in r["entities"]:
            print("%-28s %s" % (e["entity"], e["verdict"]))
        return 0
    caps = capabilities(workspace, args.system)
    if args.json:
        json.dump(caps, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(render_capabilities(caps))
    return 1 if caps["problem"] or not caps["executable"] else 0


if __name__ == "__main__":
    sys.exit(main())
