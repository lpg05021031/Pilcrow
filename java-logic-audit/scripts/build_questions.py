#!/usr/bin/env python3
"""Turn what the reconciliation found into the questions only a person can answer.

    python3 build_questions.py <system> [--workspace DIR]

Reads audit/<system>/model/reconciliation.json, model/matrix.json, model/commands.json and
DECISIONS.json, and writes questions.json and QUESTIONS.md.

A question is about behaviour that was observed, never about a guess: every one names the cells that
show it. The kinds, each from one fixed rule:

  refused-but-changed   a call that was refused (or failed) changed data all the same
  read-wrote            a call the model marks as a read changed data
  undecided             nothing in the code decides this case; what happened, happened by default
  error                 a call ended in an error that is not a refusal
  differs               running the code gave something else than reading it predicts
  dropped               a message was taken, had no effect at all, and its sender was not told
  unstable              the same call under the same conditions did not give the same result twice
  rule-broken           a rule a person confirmed was broken by a sequence of ordinary calls
  near-rule             a relation between numbers held in nearly every state and broke in a few
  error-in-sequence     a call ended in an error after a sequence of ordinary calls
  fault                 a failure in the middle of a call left the data in between, or something sent
                        that was not kept, or a retry that does not converge
  never-decides         a check that, as far as the audit can reach, never fires from this entry
  rare-combination      two lifecycle values that almost never occur together, with exceptions
  rule?                 something that held in every state the audit reached: is it a rule?

The last three are asked only on request; the others are flagged: they bear on whether the data is
right. A question keeps its id for as long as its fingerprint (kind, command, what was seen) is
produced; a decision already recorded for that fingerprint marks the question as answered.

`if_not_intended` is what the cells must show instead if a person answers "not intended"; it is
applied by apply_decisions.py and from then on the cells are compared against it.
"""
import argparse
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402
import judge  # noqa: E402

SCHEMA = 1
FLAGGED = ("refused-but-changed", "read-wrote", "undecided", "error", "differs", "dropped", "unstable",
           "rule-broken", "near-rule", "error-in-sequence", "fault")
UNFLAGGED = ("never-decides", "rare-combination", "rule?")
BASIS = {"refused-but-changed": "N4", "read-wrote": "N4", "undecided": "N3", "error": "N4", "differs": "N3",
         "dropped": "N4", "unstable": "N4", "never-decides": "N3", "rule-broken": "N2", "near-rule": "N4",
         "error-in-sequence": "N4", "fault": "N4", "rare-combination": "N4", "rule?": "N4"}


def sentence(text):
    return text[:1].upper() + text[1:]


def FAULT_TEXT(differs, observed):
    """What a failure left behind, in words."""
    parts = []
    if "atomicity" in differs:
        parts.append("the data is left in between: neither as before nor as after a successful call")
    if "sent-not-kept" in differs:
        parts.append("something was already sent out although the change was rolled back")
    if "kept-not-sent" in differs:
        parts.append("the change was kept although what a successful call sends did not go out")
    if "reported-success" in differs:
        parts.append("the caller is told the call succeeded")
    if "retry-diverges" in differs:
        parts.append("calling again does not end where an undisturbed call ends")
    if "sent-again-on-retry" in differs:
        parts.append("calling again sends it a second time")
    return "; ".join(parts) or "something differs"
NOTHING_CHANGES = {"written": [], "created": [], "deleted": []}
ANSWERS = ("not-intended", "intended", "unsure")


def fingerprint(kind, command, what):
    return hashlib.sha256(_lib.canonical([kind, command, what]).encode("utf-8")).hexdigest()[:16]


def root_of(command):
    """Variants of one entry point are asked about together."""
    return (command.get("variant") or {}).get("of") or command["id"]


def case_text(cell):
    return "the base case" if not cell.get("deviation") else cell["deviation"].replace("=", " = ")


def changed_text(item):
    parts = []
    if item.get("written"):
        parts.append("changes " + ", ".join(item["written"]))
    if item.get("created"):
        parts.append("adds rows to " + ", ".join(item["created"]))
    if item.get("deleted"):
        parts.append("removes rows from " + ", ".join(item["deleted"]))
    return " and ".join(parts) or "changes data"


def build(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    model = os.path.join(adir, "model")
    rec = _lib.read_json(os.path.join(model, "reconciliation.json"))
    matrix = _lib.read_json(os.path.join(model, "matrix.json"))
    if not isinstance(rec, dict) or not isinstance(matrix, dict):
        _lib.die("audit/%s/model/reconciliation.json or matrix.json not found: run the matrix and reconcile first" % system)
    commands = {c["id"]: c for c in (_lib.read_json(os.path.join(model, "commands.json"), {}) or {}).get("commands", [])}
    cells = {c["id"]: c for c in matrix["cells"]}
    # What kind of entry point a command is comes from the map, not from the model.
    entry_kind = {e.get("handler"): e.get("kind") for e in (_lib.read_json(os.path.join(adir, "map.json"), {}) or {}).get("entries", [])}

    def root(cid):
        return root_of(commands.get(cid, {"id": cid}))

    def cites(cid, guard_ids):
        c = commands.get(cid, {})
        by = {g["id"]: g for g in c.get("guards", []) + c.get("branches", [])}
        return sorted({by[g]["cite"] for g in guard_ids if g in by and by[g].get("cite")})

    groups = {}

    def add(kind, command, what, cell_id, **fields):
        key = (kind, command, _lib.canonical(what))
        q = groups.setdefault(key, dict(kind=kind, command=command, what=what, cells=[], cites=set(), **fields))
        if cell_id and cell_id not in q["cells"]:
            q["cells"].append(cell_id)
        static = cells.get(cell_id, {}).get("static", {})
        if static.get("guard"):
            q["cites"].update(cites(cells[cell_id]["command"], [static["guard"]]))
        return q

    for f in rec.get("flags", []):
        if f["flag"] in ("changed-although-rejected", "changed-although-error"):
            what = {"code": f.get("code"), "written": f.get("written"), "created": f.get("created"), "deleted": f.get("deleted")}
            add("refused-but-changed", root(f["command"]), what, f["cell"],
                title="A refused call still changes data",
                observed="%s answers %s%s and still %s." % (
                    root(f["command"]), (f.get("outcome") or "").lower(), " (%s)" % f["code"] if f.get("code") else "",
                    changed_text(f)),
                why="Whoever is told the call was refused will assume nothing changed.",
                if_not_intended=dict(NOTHING_CHANGES))
        elif f["flag"] == "read-path-wrote":
            what = {"written": f.get("written"), "created": f.get("created"), "deleted": f.get("deleted")}
            add("read-wrote", root(f["command"]), what, f["cell"],
                title="A read changes data",
                observed="%s is a read, and it %s." % (root(f["command"]), changed_text(f)),
                why="A read that writes can change data when it is retried, cached or called by a monitor.",
                if_not_intended=dict(NOTHING_CHANGES))

    for u in rec.get("unhandled", []):
        outcome = (u.get("observed_outcome") or "").lower()
        add("undecided", u["command"], {"case": u["deviation"], "outcome": outcome}, u["cell"],
            title="Nothing in the code decides this case",
            observed="%s with %s is %s%s. No check of this command looks at that value." % (
                u["command"], case_text(u), outcome, " (%s)" % u["observed_code"] if u.get("observed_code") else ""),
            why="When no check mentions a state, what happens in it was probably never decided by anyone.",
            if_not_intended={"outcome": "rejected"} if outcome == "allowed" else {"outcome_not": outcome})

    for d in rec.get("differences", []):
        static, observed = d["static"], d["observed"]
        want = {k: static[k] for k in ("outcome", "code", "writes_include") if k in static}
        add("differs", d["command"], {"case": d["deviation"], "static": want,
                                      "observed": [(observed.get("outcome") or "").lower(), observed.get("code")]},
            d["cell"],
            title="Running the code gives something else than reading it predicts",
            observed="%s with %s: reading the code predicts %s%s; running it gives %s%s (differs in: %s)." % (
                d["command"], case_text(d), static.get("outcome"), " (%s)" % static["code"] if static.get("code") else "",
                (observed.get("outcome") or "").lower(), " (%s)" % observed["code"] if observed.get("code") else "",
                ", ".join(d.get("differs") or ["?"])),
            why="Either the model of this command is wrong, or the code does not do what it reads as doing.",
            if_not_intended=want)

    for cell_id, state in sorted(rec.get("cells", {}).items()):
        cell = cells.get(cell_id)
        if not cell or state["state"] not in ("agree", "differ"):
            continue
        seen = state                                   # what the latest record run observed
        outcome = seen.get("outcome")
        if outcome == "error":
            add("error", root(cell["command"]), {"code": seen.get("code")}, cell_id,
                title="A call ends in an error that is not a refusal",
                observed="%s ends in an error%s." % (root(cell["command"]), " (%s)" % seen["code"] if seen.get("code") else ""),
                why="An error that is not one of the application's own refusals usually means a case nobody handled.",
                if_not_intended={"outcome_not": "error"})
        elif outcome == "ignored" and entry_kind.get(commands.get(cell["command"], {}).get("handler")) == "message":
            # A scheduled task that finds nothing to do is not asked about; a message that vanishes is.
            guard = cell["static"].get("guard")
            swallowed = {g["id"]: g.get("swallowed") for g in commands[cell["command"]].get("guards", [])}.get(guard)
            q = add("dropped", root(cell["command"]), {"guard": guard, "refusal": swallowed}, cell_id,
                    title="A message is dropped: no effect, no trace in the data, the sender is not told",
                    observed="%s takes the message, changes nothing and sends nothing%s. Cases: %%s." % (
                        root(cell["command"]), " (the refusal %s is caught and only logged)" % swallowed if swallowed else ""),
                    why="A message that is dropped is not delivered again; if it was valid but early, it is lost.",
                    if_not_intended={"outcome_not": "ignored"})
            q.setdefault("case_texts", []).append(case_text(cell))
            if guard:
                q["cites"].update(cites(cell["command"], [guard]))

    for u in rec.get("unstable", []):
        add("unstable", u["command"], {"case": u["deviation"], "parts": u["parts"]}, u["cell"],
            title="The same call does not give the same result twice",
            observed="%s with %s gave different %s in runs under the same conditions." % (
                u["command"], case_text(u), ", ".join(u["parts"])),
            why="Something outside what the audit controls decides the result: time, ordering or random values.",
            if_not_intended=None)

    for g in rec.get("guards_without_cell", []):
        q = add("never-decides", root(g["command"]), {"cite": g.get("cite"), "code": g.get("code"), "writes": g.get("writes")}, None,
                title="A check that never fires from this entry",
                observed="In %s the check at %s%s decides no case the audit could reach." % (
                    root(g["command"]), g.get("cite"), " (%s)" % g["code"] if g.get("code") else ""),
                why="Either an earlier check always fires first, or the state it tests cannot arise; "
                    "it may be protection for a path that no longer exists.",
                if_not_intended=None)
        if g.get("cite"):
            q["cites"].add(g["cite"])
        q.setdefault("variants", [])
        if g["command"] not in q["variants"]:
            q["variants"].append(g["command"])

    # ---- what the sequence runs showed about rules of the data
    invariants = (_lib.read_json(os.path.join(model, "invariants.json"), {}) or {}).get("invariants", [])
    for inv in invariants:
        support = inv.get("support") or {}
        applied, violated = support.get("applied", 0), support.get("violated", 0)
        where = " The sequence that breaks it was cut down to %s steps and replayed (evidence of %s in %s)." % (
            support.get("steps_to_reproduce", "?"), inv["id"], support.get("run")) if violated else ""
        if inv.get("status") == "confirmed" and violated:
            add("rule-broken", "(data)", {"rule": inv["id"]}, None, source="sequences", cases=[inv["id"]], invariants=[inv["id"]],
                title="A confirmed rule is broken",
                observed="A person confirmed this rule: %s. It was broken in %d of %d states.%s" % (inv["statement"], violated, applied, where),
                why="A rule a person confirmed does not hold after some sequence of ordinary calls.",
                if_not_intended=None)
        elif inv.get("status") == "near" and inv.get("class") in ("sum", "bound"):
            add("near-rule", "(data)", {"rule": inv["id"]}, None, source="sequences", cases=[inv["id"]], invariants=[inv["id"]],
                title="Something that almost always holds was broken",
                observed="%s: this held in %d of %d states and was broken in %d.%s" % (
                    sentence(inv["statement"]), applied - violated, applied, violated, where),
                why="A relation between numbers that holds nearly everywhere is usually a rule; the few states that break it "
                    "are either a path that forgets it, or a sign that it is no rule.",
                if_not_intended=None)
        elif inv.get("status") == "near":
            add("rare-combination", "(data)", {"rule": inv["id"]}, None, source="sequences", cases=[inv["id"]], invariants=[inv["id"]],
                title="A combination of states that is rare",
                observed="%s, with %d exceptions in %d states.%s" % (sentence(inv["statement"]), violated, applied, where),
                why="A combination that almost never occurs may be one that was never meant to.",
                if_not_intended=None)
        elif inv.get("status") == "candidate":
            group = inv.get("anchor") if inv.get("class") == "pair" else inv["id"]
            q = add("rule?", "(data)", {"about": group}, None, source="sequences", cases=[], invariants=[],
                    title="Is this a rule?",
                    observed="In every state the audit reached: %s.",
                    why="If it is a rule, later runs treat any state that breaks it as a defect. If it only happened to hold, say so.",
                    if_not_intended=None)
            q["cases"].append(inv["id"])
            q["invariants"].append(inv["id"])
            q.setdefault("statements", []).append("%s (%d states)" % (inv["statement"], applied))

    # ---- errors met in sequences, and what failures left behind
    for kind, prefix in (("sequences", "SEQERR-"), ("faults", "FAULT-")):
        run_dir = judge.latest_run(adir, kind)
        rows, problem = ({}, "none") if run_dir is None else judge.read_run(run_dir)
        for rid, row in sorted(rows.items()):
            if not rid.startswith(prefix) or row.get("result") != "MISMATCH":
                continue
            d, o = row.get("detail") or {}, row.get("observed") or {}
            if kind == "sequences":
                add("error-in-sequence", root(d.get("command")), {"code": d.get("code")}, None, source="sequences", cases=[rid],
                    title="A call ends in an error after an ordinary sequence of calls",
                    observed="%s ends in an error (%s) after a sequence of %s calls (evidence of %s in %s)." % (
                        root(d.get("command")), d.get("code"), o.get("steps_to_reproduce", "?"), rid, os.path.basename(run_dir)),
                    why="An error that is not one of the application's own refusals usually means a case nobody handled.",
                    if_not_intended=None)
                continue
            differs = sorted(d.get("differs") or [])
            target = d.get("fails", "")
            target = target if d.get("step") in ("WRITE", "COMMIT") else d.get("step")
            q = add("fault", root(d.get("command")), {"differs": differs}, None, source="faults", cases=[],
                    title="A failure in the middle of a call leaves something behind",
                    observed="When a step of %s fails (the step: %%s), %s." % (root(d.get("command")), FAULT_TEXT(differs, o)),
                    why="After a failure the data should be as before or as after success, what was sent should match what "
                        "was kept, and trying again should end where an undisturbed call ends.",
                    if_not_intended=None)
            q["cases"].append(rid)
            q.setdefault("statements", [])
            if target not in q["statements"]:
                q["statements"].append(target)

    # Ids are kept by fingerprint; decisions are found the same way.
    old = _lib.read_json(os.path.join(adir, "questions.json"), {}) or {}
    ids = dict(old.get("ids", {}))
    nxt = old.get("next", 1)
    decided = {}
    for d in (_lib.read_json(os.path.join(adir, "DECISIONS.json"), {}) or {}).get("decisions", []):
        decided[d.get("fingerprint")] = d
    questions = []
    order = {k: i for i, k in enumerate(FLAGGED + UNFLAGGED)}
    for key in sorted(groups, key=lambda k: (order[k[0]], k[1], k[2])):
        q = groups[key]
        fp = fingerprint(q["kind"], q["command"], q["what"])
        if fp not in ids:
            ids[fp] = nxt
            nxt += 1
        decision = decided.get(fp)
        if q.get("statements"):
            observed = q["observed"] % "; ".join(q["statements"])
        elif q.get("case_texts"):
            observed = q["observed"] % "; ".join(q["case_texts"])
        else:
            observed = q["observed"]
        entry = {
            "id": "Q-%03d" % ids[fp], "fingerprint": fp, "kind": q["kind"], "flagged": q["kind"] in FLAGGED,
            "basis": BASIS[q["kind"]], "command": q["command"], "title": q["title"], "observed": observed,
            "why": q["why"], "cells": sorted(q["cells"]), "cites": sorted(q["cites"]),
            "if_not_intended": q.get("if_not_intended"),
            "status": "answered" if decision and decision.get("answer") in ("not-intended", "intended")
                      and set(decision.get("cells") or q["cells"]) >= set(q["cells"]) else "open",
        }
        if decision and entry["status"] == "open" and decision.get("answer") in ("not-intended", "intended"):
            entry["answered_for"] = sorted(set(decision.get("cells", [])) & set(q["cells"]))
        if q.get("variants"):
            entry["variants"] = sorted(q["variants"])
        for key in ("source", "cases", "invariants"):
            if q.get(key):
                entry[key] = sorted(q[key]) if isinstance(q[key], list) else q[key]
        if decision:
            entry["decision"] = decision.get("id")
            entry["answer"] = decision.get("answer")
        questions.append(entry)
    questions.sort(key=lambda q: q["id"])
    out = {"schema": SCHEMA, "next": nxt, "ids": ids, "questions": questions}
    _lib.write_json(os.path.join(adir, "questions.json"), out)
    with open(os.path.join(adir, "QUESTIONS.md"), "w", encoding="utf-8") as fh:
        fh.write(render(system, questions))
    return out


def render(system, questions):
    lines = ["# Questions for a person: %s" % system, "",
             "Written by `build_questions.py` from the matrix runs. Each question is about behaviour that was",
             "observed; the cells it names can be run again. Answer with `/java-logic-audit:audit-review %s`." % system, ""]
    open_flagged = [q for q in questions if q["status"] == "open" and q["flagged"]]
    lines.append("%d questions: %d open and flagged, %d open and not flagged, %d answered." % (
        len(questions), len(open_flagged), sum(1 for q in questions if q["status"] == "open" and not q["flagged"]),
        sum(1 for q in questions if q["status"] == "answered")))
    for q in questions:
        lines += ["", "## %s — %s" % (q["id"], q["title"]), "",
                  "- **Status:** %s%s%s" % (q["status"], " (%s: %s)" % (q["decision"], q["answer"]) if q.get("decision") else "",
                                            ", answered only for " + ", ".join(q["answered_for"]) if q.get("answered_for") else ""),
                  "- **Observed:** " + q["observed"],
                  "- **Why it is asked:** " + q["why"],
                  "- **Kind:** %s%s" % (q["kind"], "" if q["flagged"] else " (not flagged: asked only with `all`)")]
        if q["cells"]:
            lines.append("- **Cells:** " + ", ".join(q["cells"]))
        if q["cites"]:
            lines.append("- **Code:** " + ", ".join("`%s`" % c for c in q["cites"]))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("system")
    ap.add_argument("--workspace", default=".")
    args = ap.parse_args(argv)
    out = build(os.path.abspath(args.workspace), args.system)
    qs = out["questions"]
    print("%d questions: %d open and flagged, %d open and not flagged, %d answered" % (
        len(qs), sum(1 for q in qs if q["status"] == "open" and q["flagged"]),
        sum(1 for q in qs if q["status"] == "open" and not q["flagged"]),
        sum(1 for q in qs if q["status"] == "answered")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
