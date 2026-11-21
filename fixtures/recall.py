#!/usr/bin/env python3
"""Seed one defect at a time into an audited copy of a sample and see which checks notice.

    python3 fixtures/recall.py <baseline workspace> <defects.json> <work dir> [--only ID] [--system NAME]

The baseline workspace is a copy of the sample in which the audit has been carried through the verify
step: it holds the harness, the model, the pinned behaviour, the rules a person confirmed, and the runs
of the unchanged code. For every defect the script copies that workspace, applies the one edit, and
runs the three mechanical checks on the changed code:

  pins     the matrix in verify mode: a case behaves differently than was pinned
  rules    random sequences: a confirmed rule of the data is broken
  errors   random sequences: a call ends in an error that the unchanged code never showed
  faults   failures injected into every step: something is left behind that the unchanged code did not leave
  races    two calls at the same time: data or answers that neither order gives, where the unchanged code gave one

This measures what the checks built from the *unchanged* code catch when the code then changes: the
recall of the audit's own tests. It does not measure whether an audit started from scratch on the
changed code would have raised the defect; that needs the readers to run again on every copy.

A development tool for the plugin itself; it is not part of the plugin. The database named by the
AUDIT_DB_* environment variables is emptied over and over.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

SKIP = {"target", "runs", "logs", "model_raw", ".git"}
ROUTES = ("pins", "rules", "errors", "faults", "races")


def read_run(run_dir):
    """Rows by id of a run whose files agree with its run.json, else {}."""
    try:
        with open(os.path.join(run_dir, "run.json"), encoding="utf-8") as fh:
            run = json.load(fh)
        with open(os.path.join(run_dir, "results.jsonl"), "rb") as fh:
            raw = fh.read()
    except (OSError, ValueError):
        return {}
    if hashlib.sha256(raw).hexdigest() != run.get("results_sha256"):
        return {}
    return {json.loads(line)["id"]: json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()}


def latest(adir, kind):
    runs = os.path.join(adir, "runs")
    names = sorted(d for d in (os.listdir(runs) if os.path.isdir(runs) else []) if d.startswith(kind + "-"))
    return read_run(os.path.join(runs, names[-1])) if names else {}


def signals(adir):
    """What each check shows in the runs under one audit directory."""
    out = {"pins": [], "rules": [], "errors": [], "faults": [], "races": [], "ran": {}}
    matrix = latest(adir, "matrix")
    out["ran"]["matrix"] = len(matrix)
    for rid, row in sorted(matrix.items()):
        if row.get("result") == "MISMATCH" and (row.get("expected") or {}).get("source") not in ("decision", "spec"):
            out["pins"].append((row.get("detail") or {}).get("key") or rid)
    sequences = latest(adir, "sequences")
    out["ran"]["sequences"] = len(sequences)
    for rid, row in sorted(sequences.items()):
        detail = row.get("detail") or {}
        if row.get("result") != "MISMATCH":
            continue
        if rid.startswith("SEQERR-"):
            out["errors"].append("%s %s" % (detail.get("command"), detail.get("code")))
        elif detail.get("status") == "confirmed":
            out["rules"].append(detail.get("statement"))
    faults = latest(adir, "faults")
    out["ran"]["faults"] = len(faults)
    for rid, row in sorted(faults.items()):
        if row.get("result") == "MISMATCH":
            detail = row.get("detail") or {}
            out["faults"].append("%s %s %s: %s" % (detail.get("command"), detail.get("step"), detail.get("fails"),
                                                   ",".join(detail.get("differs") or [])))
    races = latest(adir, "races")
    out["ran"]["races"] = len(races)
    for rid, row in sorted(races.items()):
        if row.get("result") == "MISMATCH":
            detail = row.get("detail") or {}
            out["races"].append("%s + %s: %s" % (detail.get("a_command"), detail.get("b_command"), ",".join(detail.get("differs") or [])))
    out["races"] = sorted(set(out["races"]))
    return out


def copy_workspace(src, dst):
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=lambda _d, names: [n for n in names if n in SKIP])


def apply(defect, workspace):
    path = os.path.join(workspace, defect["file"])
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for edit in [defect] + defect.get("also", []):
        if text.count(edit["find"]) != 1:
            raise SystemExit("%s: the text to replace occurs %d times in %s" % (defect["id"], text.count(edit["find"]), defect["file"]))
        text = text.replace(edit["find"], edit["replace"])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def run_checks(workspace, system):
    adir = os.path.join(workspace, "audit", system)
    os.makedirs(os.path.join(adir, "logs"), exist_ok=True)
    for run, extra in (("matrix", ["-Daudit.mode=verify"]), ("faults", []), ("sequences", []), ("races", [])):
        with open(os.path.join(adir, "logs", "recall-%s.log" % run), "w") as log:
            subprocess.call(["mvn", "-B", "-o", "-q", "-f", "pom-audit.xml", "-Paudit", "test",
                             "-Daudit.dir=" + adir, "-Daudit.run=" + run] + extra,
                            cwd=workspace, stdout=log, stderr=subprocess.STDOUT)
    return adir


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("baseline")
    ap.add_argument("defects")
    ap.add_argument("work")
    ap.add_argument("--only")
    ap.add_argument("--system", default="orders")
    args = ap.parse_args(argv)
    for name in ("AUDIT_DB_URL", "AUDIT_DB_USER", "AUDIT_DB_PASSWORD"):
        if not os.environ.get(name):
            raise SystemExit("set %s: the checks need the audit's throwaway database" % name)
    with open(args.defects, encoding="utf-8") as fh:
        defects = json.load(fh)["defects"]
    base = signals(os.path.join(args.baseline, "audit", args.system))
    results = []
    for defect in defects:
        if args.only and defect["id"] != args.only:
            continue
        workspace = os.path.join(os.path.abspath(args.work), defect["id"])
        copy_workspace(args.baseline, workspace)
        apply(defect, workspace)
        seen = signals(run_checks(workspace, args.system))
        new = {k: [x for x in seen[k] if x not in base[k]] for k in ROUTES}
        routes = [k for k in ROUTES if new[k]]
        broke = not all(seen["ran"].values())
        results.append({"id": defect["id"], "category": defect["category"], "what": defect["what"],
                        "detected": bool(routes), "routes": routes, "did_not_run": [k for k, n in seen["ran"].items() if not n],
                        "counts": {k: len(new[k]) for k in new}, "examples": {k: new[k][:3] for k in new if new[k]}})
        print("%-14s %-13s %s%s" % (defect["id"], defect["category"],
                                    ", ".join("%s %d" % (k, len(new[k])) for k in routes) or "NOT NOTICED",
                                    "   (a check did not run: %s)" % ", ".join(results[-1]["did_not_run"]) if broke else ""), flush=True)
    by_category = {}
    for r in results:
        c = by_category.setdefault(r["category"], {"defects": 0, "noticed": 0, "without_pins": 0})
        c["defects"] += 1
        c["noticed"] += 1 if r["detected"] else 0
        c["without_pins"] += 1 if [x for x in r["routes"] if x != "pins"] else 0
    summary = {"defects": len(results), "noticed": sum(1 for r in results if r["detected"]),
               "noticed_without_pins": sum(1 for r in results if [x for x in r["routes"] if x != "pins"]),
               "by_category": by_category}
    out = os.path.join(os.path.dirname(os.path.abspath(args.defects)), "results.json")
    if not args.only:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "baseline_already_showed": {k: len(base[k]) for k in ROUTES},
                       "summary": summary, "results": results}, fh, indent=2)
            fh.write("\n")
    print("\nnoticed %d of %d; %d of them by something other than a changed pin" % (
        summary["noticed"], summary["defects"], summary["noticed_without_pins"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
