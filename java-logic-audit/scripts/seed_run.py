#!/usr/bin/env python3
"""Put known defects into a copy of the code, one at a time, and see which of the audit's checks notice.

    python3 seed_run.py <system> <defects.json> [--workspace DIR] [--only ID] [--checks matrix,faults,sequences,races]

The defects file is written by a person (or taken from fixes in the project's history, reversed):

    { "defects": [ { "id": "guard-1", "category": "guard", "what": "one sentence",
                     "file": "src/main/java/.../OrderService.java",
                     "find": "text that occurs exactly once in the file", "replace": "what stands there instead",
                     "also": [ { "file": "optional, another file", "find": "...", "replace": "..." } ] } ] }

For every defect the workspace is copied to a temporary directory outside the repository, the one edit
is made there, and the mechanical checks are run on the changed code:

  pins     the matrix in verify mode: a case behaves differently than was pinned
  rules    random sequences: a rule of the data that a person confirmed is broken
  errors   random sequences: a call ends in an error that the unchanged code never showed
  faults   failures placed in every step: something is left behind that the unchanged code did not leave
  races    two calls at the same time: data or answers that neither order gives, where the unchanged code gave one

Only what the unchanged code did not already show counts. The workspace itself is never changed. Writes
audit/<system>/assure/seeds.json and prints one line for each defect.

This measures what the checks built from the unchanged code catch when the code then changes. It does
not measure whether an audit started from scratch on the changed code would have raised the defect.
Each defect builds and runs the project once for every check; the database named by the AUDIT_DB_*
variables (or the project's own test database) is emptied over and over. Extra arguments for the build
tool are read from the environment variable AUDIT_BUILD_ARGS.
"""
import argparse
import os
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
ROUTES = ("pins", "rules", "errors", "faults", "races")
CHECKS = (("matrix", "verify"), ("faults", None), ("sequences", None), ("races", None))
LAID_OVER = ("decision", "spec")        # a case that already differs from what a person or the specification says


def rows_of(adir, kind):
    run_dir = judge.latest_run(adir, kind)
    if run_dir is None:
        return {}
    rows, problem = judge.read_run(run_dir)
    return {} if problem else rows


def signals(adir, kinds):
    """What each check shows in the latest runs under one audit directory."""
    out = {k: [] for k in ROUTES}
    out["ran"] = {}
    if "matrix" in kinds:
        matrix = rows_of(adir, "matrix")
        out["ran"]["matrix"] = len(matrix)
        for rid, row in sorted(matrix.items()):
            if row.get("result") == "MISMATCH" and (row.get("expected") or {}).get("source") not in LAID_OVER:
                out["pins"].append((row.get("detail") or {}).get("key") or rid)
    if "sequences" in kinds:
        sequences = rows_of(adir, "sequences")
        out["ran"]["sequences"] = len(sequences)
        for rid, row in sorted(sequences.items()):
            detail = row.get("detail") or {}
            if row.get("result") != "MISMATCH":
                continue
            if rid.startswith("SEQERR-"):
                out["errors"].append("%s %s" % (detail.get("command"), detail.get("code")))
            elif detail.get("status") == "confirmed":
                out["rules"].append(detail.get("statement"))
    if "faults" in kinds:
        faults = rows_of(adir, "faults")
        out["ran"]["faults"] = len(faults)
        for rid, row in sorted(faults.items()):
            if row.get("result") == "MISMATCH":
                detail = row.get("detail") or {}
                out["faults"].append("%s %s %s: %s" % (detail.get("command"), detail.get("step"), detail.get("fails"),
                                                       ",".join(detail.get("differs") or [])))
    if "races" in kinds:
        races = rows_of(adir, "races")
        out["ran"]["races"] = len(races)
        for rid, row in sorted(races.items()):
            if row.get("result") == "MISMATCH":
                detail = row.get("detail") or {}
                out["races"].append("%s + %s: %s" % (detail.get("a_command"), detail.get("b_command"), ",".join(detail.get("differs") or [])))
        out["races"] = sorted(set(out["races"]))
    return out


def apply(defect, workspace):
    """Make the defect's edits in a workspace. Every text to replace must occur exactly once."""
    for edit in [defect] + list(defect.get("also") or []):
        path = os.path.normpath(os.path.join(workspace, edit.get("file") or defect["file"]))
        text = _lib.read_text(path) if _lib.inside(workspace, path) else None
        if text is None:
            _lib.die("%s: cannot read %s" % (defect["id"], edit.get("file") or defect["file"]))
        if text.count(edit["find"]) != 1:
            _lib.die("%s: the text to replace occurs %d times in %s" % (defect["id"], text.count(edit["find"]),
                                                                       edit.get("file") or defect["file"]))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(edit["find"], edit["replace"]))


def run_checks(workspace, system, kinds):
    adir = _lib.audit_dir(workspace, system)
    os.makedirs(os.path.join(adir, "logs"), exist_ok=True)
    extra = shlex.split(os.environ.get("AUDIT_BUILD_ARGS", ""))
    for kind, mode in CHECKS:
        if kind not in kinds:
            continue
        line = shlex.split(harness_install.command(workspace, system, kind, mode=mode)) + extra
        with open(os.path.join(adir, "logs", "seed-%s.log" % kind), "w") as log:
            subprocess.call(line, cwd=workspace, stdout=log, stderr=subprocess.STDOUT)
    return adir


def summarize(results):
    by_category = {}
    for r in results:
        c = by_category.setdefault(r["category"], {"defects": 0, "noticed": 0, "without_pins": 0})
        c["defects"] += 1
        c["noticed"] += 1 if r["detected"] else 0
        c["without_pins"] += 1 if [x for x in r["routes"] if x != "pins"] else 0
    return {"defects": len(results), "noticed": sum(1 for r in results if r["detected"]),
            "noticed_without_pins": sum(1 for r in results if [x for x in r["routes"] if x != "pins"]),
            "by_category": by_category}


def run(workspace, system, defects_file, only=None, kinds=None, keep_in=None, say=print):
    kinds = tuple(kinds or [k for k, _ in CHECKS])
    defects = (_lib.read_json(defects_file) or {}).get("defects")
    if not isinstance(defects, list):
        _lib.die("%s holds no list of defects" % defects_file)
    adir = _lib.audit_dir(workspace, system)
    base = signals(adir, kinds)
    missing = [k for k in kinds if not base["ran"].get(k)]
    if missing:
        _lib.die("the unchanged code has no believable run of: %s. Run audit-characterize and audit-verify first" % ", ".join(missing))
    results = []
    for defect in defects:
        if only and defect["id"] != only:
            continue
        tmp = keep_in or tempfile.mkdtemp(prefix="audit-seed-")
        copy = os.path.join(tmp, defect["id"])
        try:
            if os.path.exists(copy):
                shutil.rmtree(copy)
            _lib.copy_workspace(workspace, copy)
            apply(defect, copy)
            seen = signals(run_checks(copy, system, kinds), kinds)
        finally:
            if not keep_in:
                shutil.rmtree(tmp, ignore_errors=True)
        new = {k: [x for x in seen[k] if x not in base[k]] for k in ROUTES}
        routes = [k for k in ROUTES if new[k]]
        did_not_run = [k for k in kinds if not seen["ran"].get(k)]
        results.append({"id": defect["id"], "category": defect.get("category") or "", "what": defect.get("what") or "",
                        "detected": bool(routes), "routes": routes, "did_not_run": did_not_run,
                        "counts": {k: len(new[k]) for k in new}, "examples": {k: new[k][:3] for k in new if new[k]}})
        say("%-16s %-13s %s%s" % (defect["id"], defect.get("category") or "",
                                  ", ".join("%s %d" % (k, len(new[k])) for k in routes) or "NOT NOTICED",
                                  "   (a check did not run: %s)" % ", ".join(did_not_run) if did_not_run else ""))
    out = {"schema": SCHEMA, "checks": list(kinds), "baseline_already_showed": {k: len(base[k]) for k in ROUTES},
           "complete": only is None, "summary": summarize(results), "results": results}
    if only is None:
        _lib.write_json(os.path.join(adir, "assure", "seeds.json"), out)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("system")
    ap.add_argument("defects")
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--only", help="one defect id; the result is printed and not saved")
    ap.add_argument("--checks", help="comma-separated: matrix,faults,sequences,races (default: all four)")
    args = ap.parse_args(argv)
    kinds = [k.strip() for k in args.checks.split(",")] if args.checks else None
    unknown = [k for k in kinds or [] if k not in [c for c, _ in CHECKS]]
    if unknown:
        _lib.die("unknown check: %s" % ", ".join(unknown))
    out = run(os.path.abspath(args.workspace), args.system, os.path.abspath(args.defects), args.only, kinds,
              say=lambda line: print(line, flush=True))
    s = out["summary"]
    print("\nnoticed %d of %d; %d of them by something other than a changed pin" % (s["noticed"], s["defects"], s["noticed_without_pins"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
