#!/usr/bin/env python3
"""Measure a derived model against the reviewed one, by what each made the application do.

    python3 fixtures/compare_model.py <reference audit dir> <candidate audit dir>

Both directories are audit/<system> folders in which the matrix has been run and pinned. Two models of
the same code rarely name their variables alike, so cells cannot be matched one to one. What can be
compared is behaviour: for each entry point, the set of distinct things the application was seen to do
(outcome, code, columns changed, tables that gained or lost rows).

  coverage   the share of the reference's behaviours that the candidate also exhibited. This is the
             acceptance gate of the model step (design section 17.2): at least 95 %.
  extra      behaviours only the candidate exhibited: cases the reviewed model does not have. Each is
             either something the review missed or a recipe that does not do what it says.
  guards     the share of the reference's checks (by file and overlapping lines) the candidate also has.

A development tool for the plugin itself; it is not part of the plugin.
"""
import json
import os
import re
import sys

VERSION_LIKE = re.compile(r"\.(version|revision|updated_at|modified_at)$")


def load(adir, name):
    with open(os.path.join(adir, "model", name), encoding="utf-8") as fh:
        return json.load(fh)


def behaviours(adir):
    """handler -> {behaviour: [cell keys]}"""
    commands = {c["id"]: c for c in load(adir, "commands.json")["commands"]}
    pinned = load(adir, "expected.json")["cells"]
    out = {}
    for cell in load(adir, "matrix.json")["cells"]:
        entry = pinned.get(cell["key"])
        if entry is None:
            continue
        seen = entry.get("observed") if entry.get("source") == "decision" else entry
        handler = commands[cell["command"]]["handler"]
        behaviour = (seen.get("outcome"), seen.get("code"),
                     tuple(sorted(w for w in seen.get("written") or [] if not VERSION_LIKE.search(w))),
                     tuple(sorted(seen.get("created") or [])), tuple(sorted(seen.get("deleted") or [])))
        out.setdefault(handler, {}).setdefault(behaviour, []).append(cell["key"])
    return out


def guards(adir):
    """handler -> list of (file, first line, last line, code)"""
    out = {}
    for c in load(adir, "commands.json")["commands"]:
        for g in c.get("guards", []) + c.get("branches", []):
            m = re.match(r"^(.+):(\d+)(?:-(\d+))?$", g.get("cite") or "")
            if m:
                out.setdefault(c["handler"], []).append(
                    (os.path.basename(m.group(1)), int(m.group(2)), int(m.group(3) or m.group(2)), g.get("code")))
    return out


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    ref, cand = behaviours(argv[0]), behaviours(argv[1])
    total = found = 0
    print("%-62s %9s %6s" % ("entry point", "reference", "found"))
    missing, extra = [], []
    for handler in sorted(set(ref) | set(cand)):
        r, c = ref.get(handler, {}), cand.get(handler, {})
        hit = [b for b in r if b in c]
        total += len(r)
        found += len(hit)
        print("%-62s %9d %6d" % (handler.split(".")[-1], len(r), len(hit)))
        missing += [(handler, b, r[b]) for b in r if b not in c]
        extra += [(handler, b, c[b]) for b in c if b not in r]
    print("\ncoverage: %d of %d reference behaviours = %.1f %%" % (found, total, 100.0 * found / max(total, 1)))
    for title, rows in (("missing from the candidate", missing), ("only in the candidate", extra)):
        print("\n%s: %d" % (title, len(rows)))
        for handler, b, keys in rows:
            print("  %s: %s %s writes=%s creates=%s   e.g. %s" % (
                handler.split("#")[-1], b[0], b[1] or "", list(b[2]), list(b[3]), keys[0]))

    rg, cg = guards(argv[0]), guards(argv[1])
    g_total = g_found = 0
    lost = []
    for handler, items in sorted(rg.items()):
        seen = set()
        for item in items:
            if item[:3] in seen:
                continue
            seen.add(item[:3])
            g_total += 1
            if any(o[0] == item[0] and o[1] <= item[2] and item[1] <= o[2] for o in cg.get(handler, [])):
                g_found += 1
            else:
                lost.append((handler, item))
    print("\nguards: %d of %d reference checks have a counterpart = %.1f %%" % (g_found, g_total, 100.0 * g_found / max(g_total, 1)))
    for handler, item in lost:
        print("  no counterpart: %s %s:%d-%d %s" % (handler.split("#")[-1], item[0], item[1], item[2], item[3] or ""))
    return 0 if total and found * 100 >= total * 95 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
