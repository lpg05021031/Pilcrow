#!/usr/bin/env python3
"""Propose rules the data might always keep, and sort them by what the sequence runs showed.

    python3 invariants.py candidates <system> [--workspace DIR]
    python3 invariants.py assess <system> [--workspace DIR]

An invariant here is a query that returns the rows breaking a rule ("an order line whose shipped
quantity is larger than its quantity"); the rule holds in a state when the query returns nothing.

`candidates` writes model/invariants.json from fixed templates over the schema the harness read and
the lifecycle values the matrix runs observed. No model is involved, and no claim is made yet:

  bound    in one table, one number is never larger than another. Only for two columns that some
           check of the model reads together: a pair no code ever compares is left alone, because
           with few rows almost any two numbers happen to be ordered
  sum      a number on a parent row equals the sum, over its child rows, of a number or of a product
           of two, with or without a condition on a child's lifecycle value
  pair     a child row with one lifecycle value never sits under a parent with another

Most candidates are false; that is expected. The sequence runner evaluates every one in every state
it reaches and stops spending on those that break often. `assess` then reads the latest sequences
run and gives each candidate a status:

  candidate   held in every state it applied to (at least 20). Worth asking a person: is this a rule?
  near        held almost always and broke a few times (at most 1 in 20), and no stricter candidate
              about the same column holds. The sequence that breaks it was cut down and replayed.
              Either a rule the code fails to keep, or a coincidence.
  untested    applied in too few states to say anything
  discarded   broke often: not a rule
  redundant   holds, but a simpler candidate about the same column holds too

`confirmed` and `rejected` are set only by a person's decision and are never changed here.
"""
import argparse
import hashlib
import itertools
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402
import judge  # noqa: E402

SCHEMA = 1
NUMERIC = ("int", "serial", "numeric", "decimal", "number", "float", "double", "real", "money")
MIN_APPLIED = 20
NEAR_RATIO = 20                 # broken in at most 1 state of 20
KEEP_PER_COLUMN = 2
BY_PEOPLE = ("confirmed", "rejected")


def is_numeric(column):
    t = (column.get("type") or "").lower()
    return any(word in t for word in NUMERIC) and "interval" not in t


def ident(sql):
    return "INV-" + hashlib.sha1(" ".join(sql.split()).encode("utf-8")).hexdigest()[:8]


def lifecycle_values(adir):
    """table -> column -> values seen for it in the matrix runs."""
    rec = _lib.read_json(os.path.join(adir, "model", "reconciliation.json"), {}) or {}
    out = {}
    life = rec.get("lifecycle") or {}
    for t in life.get("transitions", []):
        table, column = t["column"].split(".", 1)
        for v in (t.get("from"), t.get("to")):
            if v is not None:
                out.setdefault(table, {}).setdefault(column, set()).add(v)
    for s in life.get("created_with", []):
        table, column = s["column"].split(".", 1)
        if s.get("value") is not None:
            out.setdefault(table, {}).setdefault(column, set()).add(s["value"])
    return out


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def compared_together(commands):
    """(table, {a, b}) for columns that one variable's query of the model names together."""
    out = set()
    for c in commands:
        for v in c.get("vars", []):
            words = set(re.findall(r"[a-z_][a-z0-9_]*", (v.get("sql") or "").lower()))
            out.add(frozenset(words))
    return out


def generate(schema, themap, values, commands=()):
    tables = schema.get("tables", {})
    version = {}
    for e in themap.get("entities", []):
        props = {c.get("property"): c.get("name") for c in e.get("columns", [])}
        if e.get("version_property"):
            version[e.get("table")] = props.get(e["version_property"], e["version_property"])
    numbers, pk = {}, {}
    for name, t in tables.items():
        keys = set(t.get("primary_key", [])) | {f.get("column") for f in t.get("foreign_keys", [])}
        keys.add(version.get(name))
        numbers[name] = [c["name"] for c in t.get("columns", []) if is_numeric(c) and c["name"] not in keys]
        pk[name] = (t.get("primary_key") or ["id"])[0]
    out = []

    def add(cls, statement, sql, applies, involved, anchor):
        out.append({"id": ident(sql), "class": cls, "statement": statement, "sql": " ".join(sql.split()),
                    "applies": " ".join(applies.split()), "tables": sorted(involved), "anchor": anchor,
                    "source": "template", "status": "candidate"})

    together = compared_together(commands)
    for name in sorted(tables):
        for a, b in itertools.permutations(numbers[name], 2):
            if not any(name in words and a in words and b in words for words in together):
                continue
            add("bound", "%s.%s is never larger than %s.%s" % (name, a, name, b),
                "select %s from %s where %s > %s" % (pk[name], name, a, b),
                "select 1 from %s" % name, [name], "%s.%s<=%s" % (name, a, b))

    for child in sorted(tables):
        for fk in tables[child].get("foreign_keys", []):
            parent = fk.get("references_table")
            if parent not in tables or parent == child:
                continue
            join = "c.%s = p.%s" % (fk["column"], fk.get("references_column") or pk[parent])
            has_child = "exists (select 1 from %s c where %s)" % (child, join)
            filters = [("", "")]
            for column, seen in sorted(values.get(child, {}).items()):
                for v in sorted(seen):
                    filters.append((" and c.%s <> %s" % (column, quote(v)), " whose %s is not %s" % (column, v)))
                    filters.append((" and c.%s = %s" % (column, quote(v)), " whose %s is %s" % (column, v)))
            terms = [("c.%s" % y, "%s.%s" % (child, y)) for y in numbers[child]]
            terms += [("c.%s * c.%s" % (y, z), "%s.%s x %s" % (child, y, z))
                      for y, z in itertools.combinations(numbers[child], 2)]
            for x in numbers[parent]:
                for term, term_text in terms:
                    for cond, cond_text in filters:
                        add("sum", "%s.%s equals the sum of %s over its %s rows%s" % (parent, x, term_text, child, cond_text),
                            "select p.%s from %s p where %s and p.%s <> coalesce((select sum(%s) from %s c where %s%s), 0)" % (
                                pk[parent], parent, has_child, x, term, child, join, cond),
                            "select 1 from %s p where %s" % (parent, has_child), [parent, child], "%s.%s" % (parent, x))
            for ccol, cvals in sorted(values.get(child, {}).items()):
                for pcol, pvals in sorted(values.get(parent, {}).items()):
                    for cv in sorted(cvals):
                        for pv in sorted(pvals):
                            add("pair", "no %s row with %s %s belongs to a %s row with %s %s" % (child, ccol, cv, parent, pcol, pv),
                                "select c.%s from %s c join %s p on %s where c.%s = %s and p.%s = %s" % (
                                    pk[child], child, parent, join, ccol, quote(cv), pcol, quote(pv)),
                                "select 1 from %s c where c.%s = %s" % (child, ccol, quote(cv)), [parent, child],
                                "%s.%s/%s.%s" % (child, ccol, parent, pcol))
    return out


def candidates(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    schema = _lib.read_json(os.path.join(adir, "facts", "schema.json"))
    themap = _lib.read_json(os.path.join(adir, "map.json"))
    if not isinstance(schema, dict) or not isinstance(themap, dict):
        _lib.die("audit/%s/facts/schema.json or map.json not found" % system)
    path = os.path.join(adir, "model", "invariants.json")
    old = {i["id"]: i for i in (_lib.read_json(path, {}) or {}).get("invariants", [])}
    commands = (_lib.read_json(os.path.join(adir, "model", "commands.json"), {}) or {}).get("commands", [])
    fresh = generate(schema, themap, lifecycle_values(adir), commands)
    merged, seen = [], set()
    for inv in fresh:
        if inv["id"] in seen:
            continue
        seen.add(inv["id"])
        before = old.get(inv["id"])
        if before:
            for key in ("status", "support", "decision"):
                if key in before:
                    inv[key] = before[key]
        merged.append(inv)
    # What a person or a reader added by hand of script, and what a decision settled, is never dropped.
    for inv_id, inv in old.items():
        if inv_id not in seen and (inv.get("source") != "template" or inv.get("status") in BY_PEOPLE):
            merged.append(inv)
    _lib.write_json(path, {"schema": SCHEMA, "invariants": merged})
    return merged


def assess(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    path = os.path.join(adir, "model", "invariants.json")
    data = _lib.read_json(path)
    if not isinstance(data, dict):
        _lib.die("audit/%s/model/invariants.json not found: run `invariants.py candidates` first" % system)
    run_dir = judge.latest_run(adir, "sequences")
    rows, problem = ({}, "no sequences run found") if run_dir is None else judge.read_run(run_dir)
    if problem:
        _lib.die("cannot use the sequences run: %s" % problem, code=1)
    run = os.path.basename(run_dir)
    counts = {}
    holding = {}
    for inv in data["invariants"]:
        row = rows.get(inv["id"])
        if row is None:
            continue
        observed = row.get("observed") or {}
        applied, violated = observed.get("applied", 0), observed.get("violated", 0)
        inv["support"] = {"run": run, "applied": applied, "violated": violated}
        for key in ("replays_violating", "steps_to_reproduce"):
            if key in observed:
                inv["support"][key] = observed[key]
        if inv.get("status") in BY_PEOPLE:
            continue
        if row.get("result") == "ERROR":
            inv["status"] = "discarded"
            inv["support"]["problem"] = (row.get("detail") or {}).get("reason")
        elif violated == 0:
            inv["status"] = "candidate" if applied >= MIN_APPLIED else "untested"
            if inv["status"] == "candidate":
                holding.setdefault((inv["class"], inv.get("anchor")), []).append(inv)
        elif violated * NEAR_RATIO <= applied:
            inv["status"] = "near"
        else:
            inv["status"] = "discarded"
    # Of several sums that all hold for one column, the simplest ones say it; the others add nothing.
    for (cls, _anchor), group in holding.items():
        if cls != "sum":
            continue
        group.sort(key=lambda i: (len(i["sql"]), i["sql"]))
        for inv in group[KEEP_PER_COLUMN:]:
            inv["status"] = "redundant"
    # A sum or a bound that almost holds, beside one about the same column that always holds, is only a
    # looser wording of that one (the total without the condition on cancelled lines).
    for inv in data["invariants"]:
        if inv.get("status") == "near" and inv["class"] in ("sum", "bound") and holding.get((inv["class"], inv.get("anchor"))):
            inv["status"] = "discarded"
            inv["support"]["refined_by"] = holding[(inv["class"], inv.get("anchor"))][0]["id"]
    for inv in data["invariants"]:
        counts[inv["status"]] = counts.get(inv["status"], 0) + 1
    _lib.write_json(path, data)
    return {"run": run, "counts": counts}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("candidates", "assess"):
        p = sub.add_parser(name)
        p.add_argument("system")
        p.add_argument("--workspace", default=".")
    args = ap.parse_args(argv)
    workspace = os.path.abspath(args.workspace)
    if args.cmd == "candidates":
        got = candidates(workspace, args.system)
        by = {}
        for inv in got:
            by[inv["class"]] = by.get(inv["class"], 0) + 1
        print("%d candidate rules written: %s" % (len(got), ", ".join("%d %s" % (n, k) for k, n in sorted(by.items()))))
    else:
        r = assess(workspace, args.system)
        order = ("confirmed", "near", "candidate", "redundant", "untested", "discarded", "rejected")
        print("from %s: %s" % (r["run"], ", ".join("%d %s" % (r["counts"][k], k) for k in order if r["counts"].get(k))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
