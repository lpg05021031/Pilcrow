#!/usr/bin/env python3
"""Remove each check in turn and see whether the pinned cases notice.

    python3 canary.py <system> [--workspace DIR] [--only FILE:LINES] [--limit N]

A pinned case is only worth something if it fails when the behaviour it pins is taken away. For every
place where the model cites a check that refuses with a `throw`, this script makes a copy of the
workspace outside the repository, deletes that one `throw` statement in the copy, runs the matrix in
verify mode there, and looks at the result:

  killed      a case that was pinned now behaves differently: the cases guard this check
  survived    nothing changed: no case would notice if this check disappeared
  expected    survived, and the model already says this check decides no case that can be reached
              from the commands that use it (an earlier check always fires first)
  invalid     the copy did not compile or did not start without the statement
  no-mutant   the cited lines hold no `throw` statement to delete (a lookup, an early return)

The repository itself is never changed: every edit is made in a temporary copy that is deleted
afterwards. Writes audit/<system>/assure/canary.json. Each copy builds and runs the project once, so
this takes about a minute for each check; the database named by the AUDIT_DB_* variables (or the
project's own test database) is emptied over and over, as in every other run.

Extra arguments for the build tool (for example `-o` when there is no network) are read from the
environment variable AUDIT_BUILD_ARGS.
"""
import argparse
import datetime
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402
import harness_install  # noqa: E402
import judge  # noqa: E402

SCHEMA = 1
SKIP = {"target", "build", ".gradle", "runs", "logs", "model_raw", ".git", "assure"}
THROW = re.compile(r"throw\s+new\b[^;]*;", re.S)
CITE = re.compile(r"^(.+):(\d+)(?:-(\d+))?$")


def places(commands, matrix):
    """Every cited place that refuses, with the guards of the commands that rest on it."""
    never = {cid: set(info.get("guards_without_cell", [])) for cid, info in (matrix.get("commands") or {}).items()}
    out = {}
    for c in commands:
        for g in c.get("guards", []):
            m = CITE.match(g.get("cite") or "")
            if not m or g.get("outcome") not in ("rejected", "ignored", "error"):
                continue
            key = (m.group(1), int(m.group(2)), int(m.group(3) or m.group(2)))
            place = out.setdefault(key, {"file": key[0], "first": key[1], "last": key[2], "guards": [], "all_never_decide": True})
            place["guards"].append({"command": c["id"], "id": g["id"], "code": g.get("code") or g.get("swallowed")})
            if g["id"] not in never.get(c["id"], set()):
                place["all_never_decide"] = False
    return [out[k] for k in sorted(out)]


def without_throw(text, first, last):
    """The file's text with the throw statements that start in the cited lines deleted, or None when there is none."""
    lines = text.split("\n")
    start = sum(len(line) + 1 for line in lines[:first - 1])
    end = sum(len(line) + 1 for line in lines[:last])
    found = [m for m in THROW.finditer(text) if start <= m.start() < end]
    if not found:
        return None
    out = text
    for m in reversed(found):
        out = out[:m.start()] + "/* check removed by the audit's canary */" + out[m.end():]
    return out


def mismatches(adir):
    """Keys of the cases that differ from their pin in the latest matrix run; None when the run did not happen."""
    run_dir = judge.latest_run(adir, "matrix")
    if run_dir is None:
        return None
    rows, problem = judge.read_run(run_dir)
    if problem or not rows:
        return None
    return sorted((row.get("detail") or {}).get("key") or rid for rid, row in rows.items()
                  if row.get("result") == "MISMATCH" and (row.get("expected") or {}).get("source") != "decision")


def run(workspace, system, only=None, limit=None):
    adir = _lib.audit_dir(workspace, system)
    model = os.path.join(adir, "model")
    commands = (_lib.read_json(os.path.join(model, "commands.json"), {}) or {}).get("commands")
    matrix = _lib.read_json(os.path.join(model, "matrix.json"))
    if commands is None or not isinstance(matrix, dict):
        _lib.die("audit/%s/model is not complete: run audit-characterize first" % system)
    if not any("expected" in c for c in matrix["cells"]):
        _lib.die("no case is pinned yet: run audit-characterize first")
    todo = places(commands, matrix)
    if only:
        todo = [p for p in todo if "%s:%d" % (p["file"], p["first"]) == only or "%s:%d-%d" % (p["file"], p["first"], p["last"]) == only]
    extra = shlex.split(os.environ.get("AUDIT_BUILD_ARGS", ""))
    mutants = []
    for n, place in enumerate(todo, 1):
        entry = {"id": "CAN-%03d" % n, "file": place["file"], "lines": "%d-%d" % (place["first"], place["last"]),
                 "guards": place["guards"]}
        source = os.path.join(workspace, place["file"])
        text = _lib.read_text(source) if _lib.inside(workspace, source) else None
        changed = without_throw(text, place["first"], place["last"]) if text else None
        if changed is None:
            entry["result"] = "no-mutant"
            mutants.append(entry)
            continue
        if limit is not None and sum(1 for m in mutants if m["result"] != "no-mutant") >= limit:
            break
        tmp = tempfile.mkdtemp(prefix="audit-canary-")
        try:
            copy = os.path.join(tmp, "workspace")
            shutil.copytree(workspace, copy, ignore=lambda _d, names: [x for x in names if x in SKIP])
            with open(os.path.join(copy, place["file"]), "w", encoding="utf-8") as fh:
                fh.write(changed)
            line = shlex.split(harness_install.command(copy, system, "matrix", mode="verify")) + extra
            copy_adir = _lib.audit_dir(copy, system)
            os.makedirs(os.path.join(copy_adir, "logs"), exist_ok=True)
            with open(os.path.join(copy_adir, "logs", "canary.log"), "w") as log:
                subprocess.call(line, cwd=copy, stdout=log, stderr=subprocess.STDOUT)
            differing = mismatches(copy_adir)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if differing is None:
            entry["result"] = "invalid"
        elif differing:
            entry["result"] = "killed"
            entry["killed_by"] = differing[:5]
            entry["cases_that_noticed"] = len(differing)
        else:
            entry["result"] = "expected" if place["all_never_decide"] else "survived"
        mutants.append(entry)
        print("%s %-9s %s:%s  %s" % (entry["id"], entry["result"], place["file"].split("/")[-1], entry["lines"],
                                    ", ".join(sorted({g["code"] or "?" for g in place["guards"]}))), flush=True)
    counts = {}
    for m in mutants:
        counts[m["result"]] = counts.get(m["result"], 0) + 1
    out = {"schema": SCHEMA, "made": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "complete": only is None and limit is None, "counts": counts, "mutants": mutants}
    _lib.write_json(os.path.join(adir, "assure", "canary.json"), out)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("system")
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--only", help="one cited place, as path/File.java:82-84")
    ap.add_argument("--limit", type=int, help="stop after this many checks were removed (the result is then marked incomplete)")
    args = ap.parse_args(argv)
    out = run(os.path.abspath(args.workspace), args.system, args.only, args.limit)
    c = out["counts"]
    order = ("killed", "survived", "expected", "invalid", "no-mutant")
    print("canary: " + ", ".join("%d %s" % (c[k], k) for k in order if c.get(k)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
