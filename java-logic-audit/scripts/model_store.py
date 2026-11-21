#!/usr/bin/env python3
"""Assemble the model's inputs from the readers' replies, which are kept as they came.

    python3 model_store.py name    <kind> <name>
    python3 model_store.py collect <system> [--workspace DIR]
    python3 model_store.py recipes <system> [--workspace DIR]

Every reply of a reader is saved, unchanged, as one file under audit/<system>/model_raw/:

    mine-<group>.json      { "group", "commands": [...], "could_not_model": [...], "instruction_shaped": [...] }
    referee-<command>.json { "command", "verdicts": [ { "id", "verdict", "cite", "reason" } ] }
    recipes-<group>.json   { "group", "fixtures": {...}, "recipes": {...} }
    fix-<nn>.json          { "commands": [...], "remove_commands": [...], "fixtures": {...}, "recipes": {...},
                             "remove_fixtures": [...], "harness": [...], "left_as_found": [...] }

`name` prints the file name for a reply (`model_store.py name mine entries:OrderController`).
`collect` builds model_result.json from the mine, referee and fix files; run render_model.py after it.
`recipes` builds model/fixtures.json and model/recipes.json from the recipes and fix files; run
build_matrix.py after it. In both, files are read in name order and a later fix replaces what came
before, whole command by whole command, fixture by fixture.

Nothing is typed into the model by hand: to change it, save another reply and run the script again.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402

KINDS = ("mine", "referee", "recipes", "fix")


def file_name(kind, name):
    if kind not in KINDS:
        _lib.die("unknown kind %r: one of %s" % (kind, ", ".join(KINDS)))
    return "%s-%s.json" % (kind, re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-"))


def replies(adir, kind):
    """(file name, content) of every readable reply of one kind, in name order. Unreadable files are reported."""
    raw = os.path.join(adir, "model_raw")
    good, bad = [], []
    for name in sorted(os.listdir(raw) if os.path.isdir(raw) else []):
        if not (name.startswith(kind + "-") and name.endswith(".json")):
            continue
        body = _lib.read_json(os.path.join(raw, name))
        if isinstance(body, dict):
            good.append((name, body))
        else:
            bad.append(name)
    return good, bad


def collect(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    commands, gaps, flagged, referee, corrections = [], [], [], [], []
    mined, bad = replies(adir, "mine")
    gaps += ["%s is not a JSON object and was not used" % b for b in bad]
    for name, body in mined:
        group = body.get("group") or name[len("mine-"):-len(".json")]
        got = [c for c in body.get("commands", []) if isinstance(c, dict) and c.get("id")]
        if not got:
            gaps.append("%s: the reader returned no command" % group)
        for c in got:
            c["shard"] = group
            commands.append(c)
        for x in body.get("could_not_model", []):
            gaps.append("%s: %s" % (group, x if isinstance(x, str) else "%s: %s" % (x.get("entry"), x.get("why"))))
        flagged += [x for x in body.get("instruction_shaped", []) if isinstance(x, dict)]
    verdicts, bad = replies(adir, "referee")
    gaps += ["%s is not a JSON object and was not used" % b for b in bad]
    for name, body in verdicts:
        for v in body.get("verdicts", []):
            if isinstance(v, dict):
                referee.append(dict(v, command=body.get("command")))
    fixes, bad = replies(adir, "fix")
    gaps += ["%s is not a JSON object and was not used" % b for b in bad]
    for name, body in fixes:
        for cid in body.get("remove_commands", []):
            corrections.append({"remove": cid, "from": name})
        for c in body.get("commands", []):
            if isinstance(c, dict) and c.get("id"):
                corrections.append({"command": c, "from": name})
    ids = [c["id"] for c in commands]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        gaps.append("the command id %s was returned by more than one group; the last one is used" % dup)
    out = {"schema": 1, "commands": commands, "referee": referee, "corrections": corrections, "gaps": gaps,
           "instruction_shaped": flagged}
    _lib.write_json(os.path.join(adir, "model_result.json"), out)
    return out


def recipes(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    fixtures, by_command, notes = {}, {}, []
    got, bad = replies(adir, "recipes")
    fixes, bad2 = replies(adir, "fix")
    notes += ["%s is not a JSON object and was not used" % b for b in bad + bad2]
    for name, body in got + fixes:
        for fname in body.get("remove_fixtures", []):
            fixtures.pop(fname, None)
        for fname, fixture in (body.get("fixtures") or {}).items():
            if isinstance(fixture, dict):
                if fname in fixtures and not name.startswith("fix-") and fixtures[fname] != fixture:
                    notes.append("fixture %s is defined by two groups; the one from %s is used" % (fname, name))
                fixtures[fname] = fixture
        for cid, entry in (body.get("recipes") or {}).items():
            if isinstance(entry, dict):
                by_command[cid] = entry
    _lib.write_json(os.path.join(adir, "model", "fixtures.json"), {"schema": 1, "fixtures": fixtures})
    _lib.write_json(os.path.join(adir, "model", "recipes.json"), {"schema": 1, "recipes": by_command})
    return {"fixtures": len(fixtures), "commands_with_recipes": len(by_command), "notes": notes}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("name")
    n.add_argument("kind")
    n.add_argument("name")
    for name in ("collect", "recipes"):
        p = sub.add_parser(name)
        p.add_argument("system")
        p.add_argument("--workspace", default=".")
    args = ap.parse_args(argv)
    if args.cmd == "name":
        print(file_name(args.kind, args.name))
        return 0
    workspace = os.path.abspath(args.workspace)
    if args.cmd == "collect":
        out = collect(workspace, args.system)
        print("collected %d commands, %d referee verdicts, %d corrections; %d gaps" % (
            len(out["commands"]), len(out["referee"]), len(out["corrections"]), len(out["gaps"])))
        for g in out["gaps"]:
            print("  gap: " + g)
    else:
        out = recipes(workspace, args.system)
        print("wrote %d fixtures and the recipes of %d commands" % (out["fixtures"], out["commands_with_recipes"]))
        for note in out["notes"]:
            print("  note: " + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
