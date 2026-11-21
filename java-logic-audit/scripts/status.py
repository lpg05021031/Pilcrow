#!/usr/bin/env python3
"""Where the audit of one system stands, what is stale, and the exact next command.

    python3 status.py <system> [--workspace DIR] [--json] [--prefix /java-logic-audit:]

Reads only files under audit/<system>/ (and, for the source check, the tracked source files).
It inspects and never modifies. The answer is computed here by fixed rules, so two people who
run it on the same files get the same next step.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402
import manifest as manifest_mod  # noqa: E402

GOALS = ("understand", "audit", "spec", "rerun")

# stage -> (artifacts that must all exist, artifacts it is derived from)
STAGES = [
    ("intent",       ["INTENT.md"],                                         []),
    ("preflight",    ["PREFLIGHT.md", "profile.json", "manifest.json"],     ["INTENT.md"]),
    ("harness",      ["harness.json", "facts/"],                            ["profile.json"]),
    ("map",          ["map.json", "MAP.md"],                                ["facts/"]),
    ("model",        ["model_result.json", "model/", "MODEL.md"],           ["map.json"]),
    ("characterize", ["recipes.json", "questions.json"],                    ["model/"]),
    ("review",       ["DECISIONS.json"],                                    []),
    ("verify",       ["runs/"],                                             ["model/", "DECISIONS.json"]),
    ("spec",         ["spec/clauses.json", "SPEC_TRACE.md"],                ["model/"]),
    ("assure",       ["ASSURANCE.md"],                                      ["runs/"]),
    ("report",       ["VERDICT.md", "REPORT.html"],                         ["runs/", "DECISIONS.json", "spec/"]),
]
VERIFY_KINDS = ("sequences", "relations", "faults", "concurrency")
ROADS = {
    "understand": ["intent", "preflight", "harness", "map", "model", "characterize", "review"],
    "audit":      ["intent", "preflight", "harness", "map", "model", "characterize", "review", "verify",
                   "spec", "assure", "report"],
    "spec":       ["intent", "preflight", "harness", "map", "model", "characterize", "review", "spec", "report"],
}
COMMANDS = {
    "intent": "audit", "preflight": "audit-preflight", "harness": "audit-harness", "map": "audit-map",
    "model": "audit-model", "characterize": "audit-characterize", "review": "audit-review",
    "verify": "audit-verify", "spec": "audit-spec", "assure": "audit-assure", "report": "audit-report",
}
REASONS = {
    "intent": "nothing is recorded yet about what this audit is for",
    "preflight": "the environment and the applicability of the method have not been checked",
    "harness": "the test harness is not installed or not proven to run",
    "map": "entry points, entities and write sites are not mapped",
    "model": "guards, states and the command-by-state matrix are not derived",
    "characterize": "the matrix has not been executed",
    "review": "flagged questions are waiting for a person",
    "verify": "sequences, faults and concurrency have not been checked",
    "spec": "the PRD has not been traced",
    "assure": "the findings have not been re-checked and the tests have not been shown to fail",
    "report": "the verdict has not been computed from the current result files",
}


def parse_intent(path):
    """INTENT.md starts with a `---` block of `key: value` lines."""
    text = _lib.read_text(path)
    out = {}
    if not text:
        return out
    m = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
    if not m:
        return out
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$", line)
        if km:
            out[km.group(1)] = re.sub(r"\s+#.*$", "", km.group(2)).strip().strip("'\"")
    return out


def _mtime(path):
    """Newest modification time of a file, or of any file under a directory. None when absent or empty."""
    if os.path.isfile(path):
        return os.path.getmtime(path)
    if os.path.isdir(path):
        newest = None
        for dirpath, _dirs, files in os.walk(path):
            for name in files:
                t = os.path.getmtime(os.path.join(dirpath, name))
                newest = t if newest is None or t > newest else newest
        return newest
    return None


def _oldest(paths):
    times = [t for t in (_mtime(p) for p in paths) if t is not None]
    return min(times) if times else None


def inventory(adir, has_prd):
    rows = []
    for stage, needs, derived_from in STAGES:
        present = [n for n in needs if _mtime(os.path.join(adir, n)) is not None]
        if stage == "verify":
            runs = os.path.join(adir, "runs")
            kinds = sorted({d.split("-")[0] for d in (os.listdir(runs) if os.path.isdir(runs) else [])
                            if d.split("-")[0] in VERIFY_KINDS})
            state = "done" if len(kinds) == len(VERIFY_KINDS) else ("partial" if kinds else "missing")
            detail = "kinds run: " + (", ".join(kinds) if kinds else "none")
        else:
            state = "done" if len(present) == len(needs) else ("partial" if present else "missing")
            detail = "missing: " + ", ".join(n for n in needs if n not in present) if state == "partial" else ""
        stale_because = []
        if state != "missing":
            own = _oldest([os.path.join(adir, n) for n in present]) if present else None
            for dep in derived_from:
                t = _mtime(os.path.join(adir, dep))
                if own is not None and t is not None and t > own + 1:
                    stale_because.append(dep)
        if stage == "spec" and not has_prd and state == "missing":
            state, detail = "not-applicable", "no PRD named in INTENT.md"
        rows.append({"stage": stage, "state": state, "detail": detail, "stale_because": stale_because})
    return rows


def review_counts(adir):
    questions = _lib.read_json(os.path.join(adir, "questions.json"), {})
    decisions = _lib.read_json(os.path.join(adir, "DECISIONS.json"), {})
    qs = questions.get("questions", []) if isinstance(questions, dict) else []
    ds = decisions.get("decisions", {}) if isinstance(decisions, dict) else {}
    decided = set()
    if isinstance(ds, dict):
        decided = {d.get("question") for d in ds.values() if isinstance(d, dict)}
    elif isinstance(ds, list):
        decided = {d.get("question") for d in ds if isinstance(d, dict)}
    flagged = [q for q in qs if isinstance(q, dict) and q.get("flagged")]
    return {
        "questions": len(qs),
        "flagged": len(flagged),
        "flagged_open": sum(1 for q in flagged if q.get("id") not in decided),
        "decided": len(decided),
    }


def status(workspace, system, prefix="/java-logic-audit:"):
    adir = _lib.audit_dir(workspace, system)
    intent = parse_intent(os.path.join(adir, "INTENT.md"))
    goal = intent.get("goal") if intent.get("goal") in GOALS else None
    has_prd = bool(intent.get("prd"))
    rows = inventory(adir, has_prd)
    by = {r["stage"]: r for r in rows}
    review = review_counts(adir)

    # Review is a person's stage: it is done when no flagged question is open.
    if by["characterize"]["state"] == "done":
        if review["flagged_open"]:
            by["review"]["state"] = "waiting"
            by["review"]["detail"] = "%d of %d flagged questions open" % (review["flagged_open"], review["flagged"])
        else:
            by["review"]["state"] = "done"
            by["review"]["detail"] = "%d decided, none flagged open" % review["decided"]

    profile = _lib.read_json(os.path.join(adir, "profile.json"), {}) or {}
    verdict = (profile.get("applicability") or {}).get("verdict")

    source = None
    manifest_path = os.path.join(adir, "manifest.json")
    if os.path.isfile(manifest_path):
        # The manifest is always recorded and checked at the workspace root (the whole repository).
        try:
            source = manifest_mod.check(workspace, manifest_path)
        except SystemExit:
            source = None

    road = ROADS.get(goal or "audit")
    if not has_prd:
        road = [s for s in road if s != "spec"]

    done = [s for s in road if by[s]["state"] == "done" and not by[s]["stale_because"]]
    furthest = None
    for s in road:
        if by[s]["state"] == "done":
            furthest = s
        else:
            break

    stale = ["%s (older than %s)" % (r["stage"], ", ".join(r["stale_because"])) for r in rows if r["stale_because"]]
    if source and source["status"] == "changed":
        stale.append("source changed since preflight: %d added, %d removed, %d modified" % (
            len(source["added"]), len(source["removed"]), len(source["modified"])))

    nxt, why = None, ""
    if not intent:
        nxt, why = "audit %s" % system, REASONS["intent"]
    elif verdict == "not-applicable":
        nxt, why = None, "preflight judged the method not applicable to this system; nothing further to run"
    elif goal == "rerun" or (source and source["status"] == "changed" and by["model"]["state"] != "missing"):
        nxt, why = "audit-rerun %s" % system, "the source changed after it was modelled"
    else:
        for s in road:
            r = by[s]
            if r["state"] in ("missing", "partial", "waiting") or r["stale_because"]:
                nxt = "%s %s" % (COMMANDS[s], system)
                why = ("its inputs are newer: " + ", ".join(r["stale_because"])) if r["stale_because"] and r["state"] == "done" \
                    else REASONS[s]
                break
        if nxt is None:
            nxt, why = None, "every step of this road is done; a named person signs VERDICT.md"

    return {
        "system": system,
        "goal": goal,
        "applicability": verdict,
        "stages": rows,
        "review": review,
        "source": source and {k: source[k] for k in ("status", "tracked")} or None,
        "where": "%s done (%d of %d steps on the %s road)" % (furthest, len(done), len(road), goal or "audit")
                 if furthest else "nothing done yet",
        "stale": stale,
        "next": (prefix + nxt) if nxt else None,
        "why": why,
    }


def render(st):
    lines = ["| Stage | State | Detail |", "|---|---|---|"]
    for r in st["stages"]:
        detail = r["detail"]
        if r["stale_because"]:
            detail = (detail + "; " if detail else "") + "stale: older than " + ", ".join(r["stale_because"])
        lines.append("| %s | %s | %s |" % (r["stage"], r["state"], detail))
    lines.append("")
    lines.append("Where you are: %s" % st["where"])
    lines.append("What is stale: %s" % ("; ".join(st["stale"]) if st["stale"] else "nothing"))
    lines.append("Next command: %s" % (("%s  (%s)" % (st["next"], st["why"])) if st["next"] else "none: " + st["why"]))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("system")
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--prefix", default="/java-logic-audit:")
    args = ap.parse_args(argv)
    st = status(os.path.abspath(args.workspace), args.system, args.prefix)
    if args.json:
        json.dump(st, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(render(st))
    return 0


if __name__ == "__main__":
    sys.exit(main())
