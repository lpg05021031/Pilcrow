#!/usr/bin/env python3
"""Seed one defect at a time into an audited copy of a sample and see which checks notice.

    python3 fixtures/recall.py <baseline workspace> <defects.json> <work dir> [--only ID] [--system NAME]

The baseline workspace is a copy of the sample in which the audit has been carried through the verify
step: it holds the harness, the model, the pinned behaviour, the rules a person confirmed, and the runs
of the unchanged code. The work is done by the plugin's own `scripts/seed_run.py` (what `audit-assure`
runs for a person's defects); this wrapper keeps each changed copy under the work directory for a look
afterwards and writes the result next to the defects file as `results.json`.

It measures what the checks built from the *unchanged* code catch when the code then changes: the
recall of the audit's own tests. It does not measure whether an audit started from scratch on the
changed code would have raised the defect; that needs the readers to run again on every copy.

A development tool for the plugin itself; it is not part of the plugin. The database named by the
AUDIT_DB_* environment variables is emptied over and over.
"""
import argparse
import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "java-logic-audit", "scripts"))
import seed_run  # noqa: E402


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
    os.environ.setdefault("AUDIT_BUILD_ARGS", "-o -q")          # the samples' libraries are already in the local cache
    work = os.path.abspath(args.work)
    os.makedirs(work, exist_ok=True)
    out = seed_run.run(os.path.abspath(args.baseline), args.system, os.path.abspath(args.defects), args.only,
                       keep_in=work, say=lambda line: print(line, flush=True))
    if not args.only:
        path = os.path.join(os.path.dirname(os.path.abspath(args.defects)), "results.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"schema": 1, "baseline_already_showed": out["baseline_already_showed"],
                       "summary": out["summary"], "results": out["results"]}, fh, indent=2)
            fh.write("\n")
    s = out["summary"]
    print("\nnoticed %d of %d; %d of them by something other than a changed pin" % (s["noticed"], s["defects"], s["noticed_without_pins"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
