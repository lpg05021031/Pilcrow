#!/usr/bin/env python3
"""Remove each check in turn and see whether the pinned cases notice.

    python3 canary.py <system> [--workspace DIR] [--only FILE:LINES] [--limit N]

A pinned case is only worth something if it fails when the behaviour it pins is taken away. For every
place where the model cites a check, this script makes a copy of the workspace outside the repository,
switches that one check off in the copy, runs in verify mode the cases of the commands that rest on it,
and compares every case's result with the same case in the latest verify run of the unchanged code.

A check is switched off in the first of these ways that fits the cited lines:

  condition   an `if (...)` that starts there never holds: `if (false && (...))`
  throw       a `throw new ...;` statement that starts there is deleted
  lookup      `.orElseThrow(...)` becomes `.orElse(null)`: a missing row is no longer refused there

The result for each check:

  killed      a case gives another result than before: it met its expectation and now does not, or (a
              case a person or the specification says is wrong) it did not and now does, or it differs
              in other parts. The cases guard this check
  survived    every case gives the result it gave before: none would notice if this check disappeared
  expected    survived, and the model already says this check decides no case that can be reached
              from the commands that use it (an earlier check always fires first)
  invalid     the copy did not compile or did not start with the check switched off
  no-mutant   the cited lines hold nothing this script can switch off (a constraint written as an
              annotation, a loop, a query)

The repository itself is never changed: every edit is made in a temporary copy that is deleted
afterwards. Writes audit/<system>/assure/canary.json. Each copy builds and starts the project once, so
this takes a minute or more for each check; the database named by the AUDIT_DB_* variables (or the
project's own test database) is emptied over and over, as in every other run. With --only, one check
is done again and its result replaces the earlier one in the file.

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
THROW = re.compile(r"throw\s+new\b[^;]*;", re.S)
IF = re.compile(r"\bif\s*\(")
OR_ELSE_THROW = re.compile(r"\.\s*orElseThrow\s*\(")
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


def span(text, first, last):
    lines = text.split("\n")
    return sum(len(line) + 1 for line in lines[:first - 1]), sum(len(line) + 1 for line in lines[:last])


def closing(text, opened):
    """The position of the bracket that closes the one at `opened`, or None. Brackets inside string and character literals do not count."""
    depth, i, quote = 0, opened, None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def without_throw(text, first, last):
    """The file's text with the throw statements that start in the cited lines deleted, or None when there is none."""
    start, end = span(text, first, last)
    found = [m for m in THROW.finditer(text) if start <= m.start() < end]
    if not found:
        return None
    out = text
    for m in reversed(found):
        out = out[:m.start()] + "/* check removed by the audit's canary */" + out[m.end():]
    return out


def without_condition(text, first, last):
    """The text with the first `if` that starts in the cited lines made never to hold, or None."""
    start, end = span(text, first, last)
    m = next((m for m in IF.finditer(text) if start <= m.start() < end), None)
    close = closing(text, m.end() - 1) if m else None
    if close is None:
        return None
    return text[:m.end()] + "false /* switched off by the audit's canary */ && (" + text[m.end():close] + ")" + text[close:]


def without_lookup(text, first, last):
    """The text with the first `.orElseThrow(...)` in the cited lines turned into `.orElse(null)`, or None."""
    start, end = span(text, first, last)
    m = next((m for m in OR_ELSE_THROW.finditer(text) if start <= m.start() < end), None)
    close = closing(text, m.end() - 1) if m else None
    if close is None:
        return None
    return text[:m.start()] + ".orElse(null /* refusal removed by the audit's canary */)" + text[close + 1:]


WAYS = (("condition", without_condition), ("throw", without_throw), ("lookup", without_lookup))


def switched_off(text, first, last):
    """(how, the changed text) for the first way that fits the cited lines, or (None, None)."""
    for how, change in WAYS:
        changed = change(text, first, last)
        if changed is not None:
            return how, changed
    return None, None


def results(adir, mode=None):
    """How each case ended in the latest matrix run, by key: (result, the parts that differ). None when there is no such run."""
    for name, run, rows in reversed(judge.matrix_runs(adir)):
        if rows and (mode is None or run.get("mode") == mode):
            return {(row.get("detail") or {}).get("key") or rid: (row.get("result"), tuple((row.get("detail") or {}).get("differs") or []))
                    for rid, row in rows.items()}
    return None


def noticed(before, after):
    """Keys of the cases whose result is not what it was."""
    return sorted(key for key in after if key in before and after[key] != before[key])


def run(workspace, system, only=None, limit=None):
    adir = _lib.audit_dir(workspace, system)
    model = os.path.join(adir, "model")
    commands = (_lib.read_json(os.path.join(model, "commands.json"), {}) or {}).get("commands")
    matrix = _lib.read_json(os.path.join(model, "matrix.json"))
    if commands is None or not isinstance(matrix, dict):
        _lib.die("audit/%s/model is not complete: run audit-characterize first" % system)
    if not any("expected" in c for c in matrix["cells"]):
        _lib.die("no case is pinned yet: run audit-characterize first")
    before = results(adir, "verify")
    if before is None:
        _lib.die("no verify run of the unchanged code to compare with: run the matrix in verify mode first")
    todo = list(enumerate(places(commands, matrix), 1))
    if only:
        todo = [(n, p) for n, p in todo if only in ("%s:%d" % (p["file"], p["first"]), "%s:%d-%d" % (p["file"], p["first"], p["last"]),
                                                    "CAN-%03d" % n)]
        if not todo:
            _lib.die("no cited check at %s" % only)
    extra = shlex.split(os.environ.get("AUDIT_BUILD_ARGS", ""))
    mutants = []
    for n, place in todo:
        entry = {"id": "CAN-%03d" % n, "file": place["file"], "lines": "%d-%d" % (place["first"], place["last"]),
                 "guards": place["guards"]}
        source = os.path.join(workspace, place["file"])
        text = _lib.read_text(source) if _lib.inside(workspace, source) else None
        how, changed = switched_off(text, place["first"], place["last"]) if text else (None, None)
        if changed is None:
            entry["result"] = "no-mutant"
            mutants.append(entry)
            continue
        entry["how"] = how
        rest_on_it = sorted({g["command"] for g in place["guards"]})
        if limit is not None and sum(1 for m in mutants if m["result"] != "no-mutant") >= limit:
            break
        tmp = tempfile.mkdtemp(prefix="audit-canary-")
        try:
            copy = os.path.join(tmp, "workspace")
            _lib.copy_workspace(workspace, copy)
            with open(os.path.join(copy, place["file"]), "w", encoding="utf-8") as fh:
                fh.write(changed)
            line = shlex.split(harness_install.command(copy, system, "matrix", mode="verify", only_command=",".join(rest_on_it))) + extra
            copy_adir = _lib.audit_dir(copy, system)
            os.makedirs(os.path.join(copy_adir, "logs"), exist_ok=True)
            with open(os.path.join(copy_adir, "logs", "canary.log"), "w") as log:
                subprocess.call(line, cwd=copy, stdout=log, stderr=subprocess.STDOUT)
            after = results(copy_adir)
            differing = None if after is None else noticed(before, after)
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
        print("%s %-9s %-9s %s:%s  %s" % (entry["id"], entry["result"], how, place["file"].split("/")[-1], entry["lines"],
                                         ", ".join(sorted({g["code"] or "?" for g in place["guards"]}))), flush=True)
    path = os.path.join(adir, "assure", "canary.json")
    complete = only is None and limit is None
    earlier = _lib.read_json(path)
    if only and isinstance(earlier, dict):
        # One check done again: its result takes the place of the earlier one, the rest of the file stands.
        again = {m["id"]: m for m in mutants}
        mutants = [again.pop(m["id"], m) for m in earlier.get("mutants", [])] + list(again.values())
        complete = bool(earlier.get("complete"))
    counts = {}
    for m in mutants:
        counts[m["result"]] = counts.get(m["result"], 0) + 1
    out = {"schema": SCHEMA, "made": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "complete": complete, "counts": counts, "mutants": mutants}
    _lib.write_json(path, out)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("system")
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--only", help="one cited check, as path/File.java:82-84 or by its id (CAN-007)")
    ap.add_argument("--limit", type=int, help="stop after this many checks were removed (the result is then marked incomplete)")
    args = ap.parse_args(argv)
    out = run(os.path.abspath(args.workspace), args.system, args.only, args.limit)
    c = out["counts"]
    order = ("killed", "survived", "expected", "invalid", "no-mutant")
    print("canary: " + ", ".join("%d %s" % (c[k], k) for k in order if c.get(k)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
