#!/usr/bin/env python3
"""Compute what the audit may claim, from result files only.

    python3 judge.py capabilities <system> [--workspace DIR]

`capabilities` reads the newest smoke run under audit/<system>/runs/ (written by the harness itself
while it ran), the facts it extracted and the person's answer about the production database, and
writes the `capabilities` block of profile.json: what works on this application, whether the test
database matches production, and the highest evidence grade each kind of check can reach
(design sections 5.4 and 12.1). Every rule is in this file; nothing here is decided by a model.

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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capabilities")
    c.add_argument("system")
    c.add_argument("--workspace", default=".")
    c.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    caps = capabilities(os.path.abspath(args.workspace), args.system)
    if args.json:
        json.dump(caps, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(render_capabilities(caps))
    return 1 if caps["problem"] or not caps["executable"] else 0


if __name__ == "__main__":
    sys.exit(main())
