#!/usr/bin/env python3
"""Record a person's answers and apply what they mean for the cells.

    python3 apply_decisions.py record <system> --question Q-003 --answer not-intended|intended|unsure
                               [--cells CELL-0005,CELL-0006] [--note "their words"] [--by NAME] [--workspace DIR]
    python3 apply_decisions.py apply <system> [--workspace DIR]

`record` appends to DECISIONS.json and rewrites DECISIONS.md. A decision is kept for good and is tied
to the question's fingerprint, not its number, so it still applies after the questions are rebuilt.
A later answer to the same question is a new decision that supersedes the earlier one; nothing is
erased. The note is the person's own words and is stored as given. A question may group several cases
(a message dropped for a draft, a closed and a cancelled order); when the person's answer is about some
of them only, --cells names those, and the question stays open for the rest.

`apply` turns the latest decision of each question into expectations in model/expected.json:

  not-intended   the cells of the question are expected to show what the question's
                 `if_not_intended` says instead (a refusal that changes nothing, a rejection where
                 the call went through). From then on the cells are compared against that, so the
                 behaviour shows up as a mismatch until the code changes. Source: "decision".
  intended       the observed behaviour stays the expectation and is marked as accepted.
  unsure         nothing changes; the question stays open and is not used as a basis for anything.

Only this script writes decisions into the model; the files are not edited by hand.
"""
import argparse
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402

SCHEMA = 1
ANSWERS = ("not-intended", "intended", "unsure")
OWN_KEYS = ("source", "run", "left_out", "decision", "accepted_by", "observed")


def load(adir):
    data = _lib.read_json(os.path.join(adir, "DECISIONS.json"), {}) or {}
    data.setdefault("schema", SCHEMA)
    data.setdefault("next", 1)
    data.setdefault("decisions", [])
    return data


def latest(decisions):
    """The decision in force for each question fingerprint: the last one recorded."""
    out = {}
    for d in decisions:
        out[d["fingerprint"]] = d
    return out


def record(workspace, system, question_id, answer, note=None, by=None, now=None, cells=None):
    if answer not in ANSWERS:
        _lib.die("unknown answer %r: one of %s" % (answer, ", ".join(ANSWERS)))
    adir = _lib.audit_dir(workspace, system)
    questions = (_lib.read_json(os.path.join(adir, "questions.json"), {}) or {}).get("questions", [])
    q = next((x for x in questions if x["id"] == question_id), None)
    if q is None:
        _lib.die("no question %s in audit/%s/questions.json" % (question_id, system))
    covered = list(q["cells"])
    if cells:
        unknown = [c for c in cells if c not in q["cells"]]
        if unknown:
            _lib.die("%s is not about %s; its cases are %s" % (question_id, ", ".join(unknown), ", ".join(q["cells"])))
        covered = [c for c in q["cells"] if c in cells]
    data = load(adir)
    earlier = latest(data["decisions"]).get(q["fingerprint"])
    decision = {
        "id": "D-%03d" % data["next"],
        "question": q["id"], "fingerprint": q["fingerprint"], "kind": q["kind"], "command": q["command"],
        "asked": q["observed"], "answer": answer, "note": note or "",
        "cells": covered, "covers": "all" if len(covered) == len(q["cells"]) else "part",
        "invariants": q.get("invariants", []),
        "if_not_intended": q.get("if_not_intended"),
        "decided_at": now or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "by": by or "",
    }
    if earlier:
        decision["supersedes"] = earlier["id"]
    data["next"] += 1
    data["decisions"].append(decision)
    _lib.write_json(os.path.join(adir, "DECISIONS.json"), data)
    with open(os.path.join(adir, "DECISIONS.md"), "w", encoding="utf-8") as fh:
        fh.write(render(system, data["decisions"]))
    return decision


def apply(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    model = os.path.join(adir, "model")
    matrix = _lib.read_json(os.path.join(model, "matrix.json"))
    if not isinstance(matrix, dict):
        _lib.die("audit/%s/model/matrix.json not found" % system)
    keys = {c["id"]: c["key"] for c in matrix["cells"]}
    path = os.path.join(model, "expected.json")
    pinned = _lib.read_json(path, {}) or {}
    cells = pinned.setdefault("cells", {})
    report = {"not-intended": 0, "intended": 0, "unsure": 0, "cells_changed": 0, "without_effect": []}

    # Start from the observation everywhere, so that a decision that was superseded leaves nothing behind.
    for entry in cells.values():
        if entry.get("source") == "decision" and isinstance(entry.get("observed"), dict):
            observed = entry["observed"]
            entry.clear()
            entry.update(observed)
        entry.pop("accepted_by", None)

    for d in latest(load(adir)["decisions"]).values():
        report[d["answer"]] += 1
        if d["answer"] == "unsure":
            continue
        for cell_id in d.get("cells", []):
            key = keys.get(cell_id)
            entry = cells.get(key)
            if entry is None:
                continue
            if d["answer"] == "intended":
                entry["accepted_by"] = d["id"]
                continue
            patch = d.get("if_not_intended")
            if not patch:
                if d["id"] not in report["without_effect"]:
                    report["without_effect"].append(d["id"])
                continue
            observed = {k: v for k, v in entry.items() if k not in OWN_KEYS or k in ("run", "left_out")}
            wanted = {k: v for k, v in entry.items() if k not in OWN_KEYS}
            if "outcome" in patch or "outcome_not" in patch:
                # The person said what the result must (not) be; what the call then writes is not known yet.
                wanted = {}
            wanted.pop("response_sha", None)      # what the right answer to the caller looks like is not known either
            wanted.update(patch)
            entry.clear()
            entry.update(wanted)
            entry.update({"source": "decision", "decision": d["id"], "observed": observed})
            report["cells_changed"] += 1
    pinned["schema"] = SCHEMA
    _lib.write_json(path, pinned)

    # A decision about a rule of the data: "is this a rule?" answered yes confirms it; a relation that
    # almost always held and whose exceptions a person calls not intended is a rule too.
    inv_path = os.path.join(model, "invariants.json")
    inv_data = _lib.read_json(inv_path)
    if isinstance(inv_data, dict):
        in_force = {}
        for d in latest(load(adir)["decisions"]).values():
            if d["answer"] == "unsure" or not d.get("invariants"):
                continue
            is_rule = (d["answer"] == "intended") if d.get("kind") == "rule?" else (d["answer"] == "not-intended")
            for inv_id in d["invariants"]:
                in_force[inv_id] = ("confirmed" if is_rule else "rejected", d["id"])
        report["rules_confirmed"] = report["rules_rejected"] = 0
        for inv in inv_data.get("invariants", []):
            if inv["id"] in in_force:
                if not inv.get("decision"):
                    inv["before_decision"] = inv.get("status")
                inv["status"], inv["decision"] = in_force[inv["id"]]
                report["rules_" + inv["status"]] += 1
            elif inv.get("decision"):
                inv.pop("decision")
                inv["status"] = inv.pop("before_decision", None) or "proposed"      # the decision was superseded
        _lib.write_json(inv_path, inv_data)
    return report


def render(system, decisions):
    in_force = {d["id"] for d in latest(decisions).values()}
    lines = ["# Decisions: %s" % system, "",
             "Recorded by `apply_decisions.py`. A decision is never removed; a later answer to the same question",
             "supersedes the earlier one.", ""]
    for d in decisions:
        lines += ["## %s — %s: %s%s" % (d["id"], d["question"], d["answer"], "" if d["id"] in in_force else " (superseded)"), "",
                  "- **Asked:** " + d["asked"],
                  "- **Answer:** " + d["answer"] + (" — " + d["note"] if d["note"] else ""),
                  "- **Covers:** " + ("every case of the question" if d.get("covers", "all") == "all"
                                      else "only " + ", ".join(d["cells"])),
                  "- **When:** %s%s" % (d["decided_at"], ", by " + d["by"] if d["by"] else ""), ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("system")
    r.add_argument("--question", required=True)
    r.add_argument("--answer", required=True, choices=ANSWERS)
    r.add_argument("--cells", help="comma-separated cell ids, when the answer is about some of the question's cases only")
    r.add_argument("--note")
    r.add_argument("--by")
    r.add_argument("--workspace", default=".")
    a = sub.add_parser("apply")
    a.add_argument("system")
    a.add_argument("--workspace", default=".")
    args = ap.parse_args(argv)
    workspace = os.path.abspath(args.workspace)
    if args.cmd == "record":
        cells = [c.strip() for c in args.cells.split(",") if c.strip()] if args.cells else None
        d = record(workspace, args.system, args.question, args.answer, args.note, args.by, cells=cells)
        print("recorded %s: %s is %s%s" % (d["id"], d["question"], d["answer"],
                                           "" if d["covers"] == "all" else " for " + ", ".join(d["cells"])))
    else:
        rep = apply(workspace, args.system)
        print("decisions in force: %d not-intended, %d intended, %d unsure; %d cells now expect something else than observed" % (
            rep["not-intended"], rep["intended"], rep["unsure"], rep["cells_changed"]))
        if rep.get("rules_confirmed") or rep.get("rules_rejected"):
            print("rules of the data: %d confirmed, %d rejected" % (rep.get("rules_confirmed", 0), rep.get("rules_rejected", 0)))
        if rep["without_effect"]:
            print("recorded but with no expectation to set (the question says what was seen, not what should be): "
                  + ", ".join(rep["without_effect"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
