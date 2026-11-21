#!/usr/bin/env python3
"""Compare what the matrix runs observed with what reading the code predicted, and pin the behaviour.

    python3 reconcile.py <system> [--workspace DIR] [--pin] [--repin]

Reads audit/<system>/model/matrix.json and every matrix run under audit/<system>/runs/. A run is
believed only if its files agree with its run.json, and a result counts only for the definition of
the cell it was run for (the cell's fingerprint), so results from before a change to the model are
set aside rather than mixed in.

Writes model/reconciliation.json:

- for every cell, whether it was executed and whether the prediction held;
- the differences, the cells that were not executed and why, the flags the runner raised
  (a refused call that changed data, a read that wrote), the cells nothing in the code decides;
- cells whose runs did not agree with each other, and in what;
- the guards that decide no executable cell;
- every change of a lifecycle column that was seen, with the commands that made it.

With --pin, the observed behaviour of every executed cell becomes its expectation in
model/expected.json (source "observed"), unless a person's decision or a specification already
set one. An observation that differs from an earlier pin is reported, and replaces it only with
--repin. Parts that differed between runs of the same cell are left out of the pin.

Nothing is judged here: the output says what was seen and where it differs.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402
import judge  # noqa: E402

SCHEMA = 1
PINNED_PARTS = ("outcome", "code", "after", "written", "created", "deleted", "published", "external")
EXECUTED = ("MATCH", "MISMATCH")
KEPT_SOURCES = ("decision", "spec")          # a pin from one of these is never replaced by an observation
NOTES = ("source", "run", "left_out", "accepted_by", "decision", "observed")      # about the pin, not part of it


def load_runs(adir):
    """Every believable matrix run, oldest first: (name, run.json, rows by id). Also the names set aside, with why."""
    runs_dir = os.path.join(adir, "runs")
    names = sorted(d for d in (os.listdir(runs_dir) if os.path.isdir(runs_dir) else []) if d.startswith("matrix-"))
    good, bad = [], []
    for name in names:
        rows, problem = judge.read_run(os.path.join(runs_dir, name))
        if problem:
            bad.append({"run": name, "problem": problem})
            continue
        good.append((name, _lib.read_json(os.path.join(runs_dir, name, "run.json"), {}) or {}, rows))
    return good, bad


def results_for(cell, runs):
    """The results of one cell that were run for its present definition, oldest first."""
    out = []
    for name, run, rows in runs:
        row = rows.get(cell["id"])
        if row and (row.get("detail") or {}).get("sha") == cell.get("sha"):
            out.append({"run": name, "mode": run.get("mode") or "record", "row": row})
    return out


def observed_parts(row):
    o = row.get("observed") or {}
    out = {"outcome": (o.get("outcome") or "").lower(), "code": o.get("code")}
    for part in PINNED_PARTS[2:]:
        out[part] = o.get(part) if o.get(part) is not None else ([] if part != "after" else {})
    return out


def unstable_parts(results):
    """The parts in which executed runs of the same cell did not agree."""
    seen = [observed_parts(r["row"]) for r in results if r["row"].get("result") in EXECUTED]
    return [part for part in PINNED_PARTS if len({_lib.canonical(s[part]) for s in seen}) > 1]


def classify(cell, results):
    """One word for where a cell stands, from its latest result in record mode."""
    record = [r for r in results if r["mode"] == "record"]
    if not record:
        return "unreachable" if "unreachable" in cell else ("no-recipe" if "with" not in cell else "not-run"), None
    row = record[-1]["row"]
    result = row.get("result")
    if result == "MATCH":
        return "agree", record[-1]
    if result == "MISMATCH":
        return "differ", record[-1]
    why = (row.get("detail") or {}).get("why")
    if result == "HARNESS_GAP":
        return "harness-gap", record[-1]
    if result == "ERROR":
        return "runner-failed", record[-1]
    return {"unreachable": "unreachable", "no-recipe": "no-recipe", "state-not-reached": "state-not-reached",
            "steps-failed": "steps-failed"}.get(why, "not-run"), record[-1]


def lifecycle_columns(adir):
    """table.column for every lifecycle column the map names."""
    m = _lib.read_json(os.path.join(adir, "map.json"), {}) or {}
    tables = {e.get("id"): e.get("table") for e in m.get("entities", [])}
    props = {}
    for e in m.get("entities", []):
        for c in e.get("columns", []):
            props[(e.get("id"), c.get("property"))] = c.get("name")
    out = set()
    for w in m.get("lifecycle_writers", []):
        table = tables.get(w.get("entity"))
        column = props.get((w.get("entity"), w.get("column")), w.get("column"))
        if table and column:
            out.add("%s.%s" % (table, column))
    return out


def transitions(adir, cells, latest, columns):
    """Changes of lifecycle columns seen in the evidence of executed cells, with who made them."""
    moves, starts = {}, {}
    for cell in cells:
        hit = latest.get(cell["id"])
        if not hit or hit["row"].get("result") not in EXECUTED:
            continue
        evidence = _lib.read_json(os.path.join(adir, "runs", hit["run"], "evidence", cell["id"] + ".json"), {}) or {}
        for change in evidence.get("changes", []):
            table = change.get("table")
            if change.get("change") == "modified":
                column = "%s.%s" % (table, change.get("column"))
                if column in columns:
                    key = (column, change.get("before"), change.get("after"))
                    moves.setdefault(key, {"commands": set(), "cells": set()})
                    moves[key]["commands"].add(cell["command"])
                    moves[key]["cells"].add(cell["id"])
            elif change.get("change") == "added":
                for name, value in (change.get("values") or {}).items():
                    column = "%s.%s" % (table, name)
                    if column in columns:
                        starts.setdefault((column, value), set()).add(cell["command"])
    return ([{"column": k[0], "from": k[1], "to": k[2], "commands": sorted(v["commands"]), "cells": sorted(v["cells"])}
             for k, v in sorted(moves.items(), key=lambda kv: tuple(str(x) for x in kv[0]))],
            [{"column": k[0], "value": k[1], "commands": sorted(v)}
             for k, v in sorted(starts.items(), key=lambda kv: tuple(str(x) for x in kv[0]))])


def reconcile(workspace, system, pin=False, repin=False):
    adir = _lib.audit_dir(workspace, system)
    model = os.path.join(adir, "model")
    matrix = _lib.read_json(os.path.join(model, "matrix.json"))
    if not isinstance(matrix, dict) or "cells" not in matrix:
        _lib.die("audit/%s/model/matrix.json not found: build the matrix first" % system)
    commands = {c["id"]: c for c in (_lib.read_json(os.path.join(model, "commands.json"), {}) or {}).get("commands", [])}
    runs, set_aside = load_runs(adir)

    counts, per_cell, latest = {}, {}, {}
    differences, not_executed, flags, unhandled, unstable = [], [], [], [], []
    for cell in matrix["cells"]:
        results = results_for(cell, runs)
        state, hit = classify(cell, results)
        counts[state] = counts.get(state, 0) + 1
        entry = {"state": state, "runs": len(results)}
        per_cell[cell["id"]] = entry
        base = {"cell": cell["id"], "key": cell["key"], "command": cell["command"], "deviation": cell["deviation"]}
        if hit:
            latest[cell["id"]] = hit
            entry["run"] = hit["run"]
        row = hit["row"] if hit else {}
        observed = row.get("observed") or {}
        if row.get("result") in EXECUTED:
            entry["outcome"] = (observed.get("outcome") or "").lower()
            entry["code"] = observed.get("code")
        if state == "differ":
            differences.append(dict(base, static=cell["static"], given=cell["given"],
                                    observed={k: observed.get(k) for k in ("outcome", "code", "status", "error")
                                              if observed.get(k) is not None},
                                    differs=(row.get("detail") or {}).get("differs", []), run=hit["run"]))
        elif state not in ("agree",):
            reason = cell.get("unreachable") or (row.get("detail") or {}).get("reason") or {
                "no-recipe": "the model does not say how to reach this state",
                "not-run": "no run holds a result for the present definition of this cell"}.get(state, "")
            not_executed.append(dict(base, state=state, reason=reason, static=cell["static"]))
        if row.get("result") in EXECUTED:
            for flag in observed.get("flags", []):
                flags.append(dict(base, flag=flag, outcome=observed.get("outcome"), code=observed.get("code"),
                                  written=observed.get("written", []), created=observed.get("created", []),
                                  deleted=observed.get("deleted", [])))
            if cell["static"].get("unhandled"):
                unhandled.append(dict(base, given=cell["given"], observed_outcome=observed.get("outcome"),
                                      observed_code=observed.get("code")))
            parts = unstable_parts(results)
            if parts:
                entry["unstable"] = parts
                unstable.append(dict(base, parts=parts, runs=[r["run"] for r in results]))

    dead = []
    for cid, info in sorted((matrix.get("commands") or {}).items()):
        guards = {g["id"]: g for g in commands.get(cid, {}).get("guards", []) + commands.get(cid, {}).get("branches", [])}
        for gid in info.get("guards_without_cell", []):
            g = guards.get(gid, {})
            dead.append({"command": cid, "guard": gid, "outcome": g.get("outcome", "allowed"),
                         "code": g.get("code") or g.get("swallowed"), "writes": g.get("writes"), "cite": g.get("cite")})

    moves, starts = transitions(adir, matrix["cells"], latest, lifecycle_columns(adir))
    out = {
        "schema": SCHEMA,
        "runs": [name for name, _, _ in runs],
        "runs_set_aside": set_aside,
        "counts": dict(sorted(counts.items()), total=len(matrix["cells"])),
        "cells": per_cell,
        "differences": differences,
        "not_executed": not_executed,
        "flags": flags,
        "unhandled": unhandled,
        "unstable": unstable,
        "guards_without_cell": dead,
        "lifecycle": {"transitions": moves, "created_with": starts},
    }

    if pin or repin:
        path = os.path.join(model, "expected.json")
        pinned = _lib.read_json(path, {}) or {}
        known = pinned.setdefault("cells", {})
        report = {"new": 0, "same": 0, "kept": 0, "changed": []}
        for cell in matrix["cells"]:
            hit = latest.get(cell["id"])
            if not hit or hit["row"].get("result") not in EXECUTED:
                continue
            skip = per_cell[cell["id"]].get("unstable", [])
            now = {k: v for k, v in observed_parts(hit["row"]).items() if k not in skip}
            old = known.get(cell["key"])
            if old and old.get("source") in KEPT_SOURCES:
                report["kept"] += 1
                continue
            if old is None:
                report["new"] += 1
            elif {k: old.get(k) for k in now} == now and not (set(old) - set(now) - set(NOTES)):
                report["same"] += 1
                continue
            else:
                report["changed"].append({"cell": cell["id"], "key": cell["key"],
                                          "parts": sorted(k for k in set(now) | (set(old) - set(NOTES))
                                                          if old.get(k) != now.get(k))})
                if not repin:
                    continue
            entry = dict(now, source="observed", run=hit["run"])
            if skip:
                entry["left_out"] = skip
            if old and old.get("accepted_by"):
                entry["accepted_by"] = old["accepted_by"]
            known[cell["key"]] = entry
        pinned["schema"] = SCHEMA
        _lib.write_json(path, pinned)
        out["pin"] = report

    _lib.write_json(os.path.join(model, "reconciliation.json"), out)
    return out


def summary(out):
    c = out["counts"]
    order = ("agree", "differ", "unreachable", "no-recipe", "state-not-reached", "steps-failed", "harness-gap",
             "runner-failed", "not-run")
    line = "%d cells: " % c["total"] + ", ".join("%d %s" % (c[k], k) for k in order if c.get(k))
    extra = []
    for key, word in (("flags", "flagged"), ("unhandled", "decided by no guard"), ("unstable", "unstable between runs"),
                      ("guards_without_cell", "guards that decide no executable cell")):
        if out[key]:
            extra.append("%d %s" % (len(out[key]), word))
    if out["runs_set_aside"]:
        extra.append("%d runs set aside as unreliable" % len(out["runs_set_aside"]))
    if "pin" in out:
        p = out["pin"]
        extra.append("pinned %d new, %d unchanged, %d kept from decisions, %d observed differently than pinned%s" % (
            p["new"], p["same"], p["kept"], len(p["changed"]), "" if not p["changed"] else " (see reconciliation.json)"))
    return line + ("; " + "; ".join(extra) if extra else "")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("system")
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--pin", action="store_true", help="make what was observed the expectation of each executed cell")
    ap.add_argument("--repin", action="store_true", help="as --pin, and replace earlier pins that were observed differently")
    args = ap.parse_args(argv)
    out = reconcile(os.path.abspath(args.workspace), args.system, pin=args.pin, repin=args.repin)
    print(summary(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
