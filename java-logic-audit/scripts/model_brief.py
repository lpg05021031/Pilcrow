#!/usr/bin/env python3
"""Prepare exactly what a reader is given, and nothing else.

    python3 model_brief.py shards  <system> [--pattern TEXT] [--workspace DIR]
    python3 model_brief.py mine    <system> <shard> [--workspace DIR]
    python3 model_brief.py referee <system> <group> [--part N] [--workspace DIR]
    python3 model_brief.py recipes <system> <group> [--part N] [--workspace DIR]
    python3 model_brief.py triage  <system> [--command CMD-x] [--workspace DIR]

Each prints one JSON object: the brief of one agent. The briefs are built from the map and the facts,
so that what a reader is told about entry points, tables and columns is what the tools found, not
what another reader said.

  shards    the groups of entry points to model, with a count of the agents the model step will start
  mine      one group: its entry points as the map has them, the files to read, the tables
  referee   the commands proposed for one group, at most eight at a time: only their guards and
            branches with the lines they cite. `parts` says how many briefs the group needs
  recipes   one group's commands, an index of every command that a step may call, the tables. A
            group with many cases is given in parts (`parts` says how many); the variants of one
            entry point stay together
  triage    the cells of the last run that were not executed or did not agree, each with what was
            expected, what happened and where the evidence is. Without --command: which commands
            have such cells. With it: the whole brief for that command
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402

FORMAT = os.path.normpath(os.path.join(HERE, "..", "references", "model-format.md"))
ENTRY_KEYS = ("id", "kind", "method", "path", "handler", "parameters", "body_type", "returns", "bean", "public_methods",
              "handler_methods", "schedule", "source", "destinations", "writes_tables")
AGENTS_PER_SHARD = 2          # one reader and one referee; a group with more than eight commands needs more referees
MAX_TABLES = 40


def load(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    shards = (_lib.read_json(os.path.join(adir, "shards.json"), {}) or {}).get("shards")
    themap = _lib.read_json(os.path.join(adir, "map.json"))
    if shards is None or not isinstance(themap, dict):
        _lib.die("audit/%s/shards.json or map.json not found: run audit-map first" % system)
    return adir, [s for s in shards if s.get("direction") == "top-down"], themap


def shard_named(shards, name):
    for s in shards:
        if s["name"] == name:
            return s
    _lib.die("no group %r; the groups are: %s" % (name, ", ".join(s["name"] for s in shards)))


def tables(adir, themap, wanted=None):
    """Columns, keys and lifecycle columns of the tables, from the schema the harness read."""
    schema = (_lib.read_json(os.path.join(adir, "facts", "schema.json"), {}) or {}).get("tables", {})
    lifecycle = {}
    for e in themap.get("entities", []):
        props = {c.get("property"): c.get("name") for c in e.get("columns", [])}
        lifecycle[e.get("table")] = [props.get(p, p) for p in e.get("lifecycle_candidates", [])]
    names = sorted(schema)
    if wanted is not None and len(names) > MAX_TABLES:
        names = [n for n in names if n in wanted]
    out = {}
    for name in names:
        t = schema[name]
        out[name] = {
            "columns": [{"name": c.get("name"), "type": c.get("type"), "nullable": c.get("nullable")} for c in t.get("columns", [])],
            "primary_key": t.get("primary_key", []),
            "foreign_keys": t.get("foreign_keys", []),
            "lifecycle_columns": lifecycle.get(name, []),
        }
    return out


def stand_ins(harness):
    """What the outside systems answer during the audit, so that a reader knows what a call will see."""
    return {"http": [{k: r.get(k) for k in ("method", "url", "status", "body")} for r in harness.get("http_doubles", [])],
            "replaced_types": [d.get("type") for d in harness.get("doubles", [])],
            "note": "a call to a replaced type is recorded and returns nothing; an HTTP call no rule covers fails"}


def cmd_shards(workspace, system, pattern=None):
    adir, shards, themap = load(workspace, system)
    chosen = [s for s in shards if not pattern or pattern.lower() in s["name"].lower()]
    done = {c.get("shard") for c in (_lib.read_json(os.path.join(adir, "model_result.json"), {}) or {}).get("commands", [])}
    return {
        "system": system,
        "groups": [{"name": s["name"], "entry_points": len(s.get("entries", [])), "files": len(s.get("files", [])),
                    "files_left_out": s.get("files_left_out", 0), "already_modelled": s["name"] in done} for s in chosen],
        "not_selected": len(shards) - len(chosen),
        "estimate": {"readers": len(chosen), "agents": len(chosen) * AGENTS_PER_SHARD,
                     "referees": "one for each group, and one more for every eight commands beyond the first eight",
                     "entry_points": sum(len(s.get("entries", [])) for s in chosen)},
    }


def cmd_mine(workspace, system, name):
    adir, shards, themap = load(workspace, system)
    shard = shard_named(shards, name)
    entries = [e for e in themap.get("entries", []) if e.get("id") in set(shard.get("entries", []))]
    wanted = {t for e in entries for t in e.get("writes_tables", [])}
    harness = _lib.read_json(os.path.join(adir, "harness.json"), {}) or {}
    return {
        "task": "mine", "system": system, "group": name, "format": FORMAT,
        "entry_points": [{k: e[k] for k in ENTRY_KEYS if k in e} for e in entries],
        "files": shard.get("files", []), "files_left_out": shard.get("files_left_out", 0),
        "tables": tables(adir, themap, wanted),
        "refusals": {"exception_types": harness.get("rejections", []),
                     "error_code_field": harness.get("error_code_path", "code")},
        "database": (_lib.read_json(os.path.join(adir, "facts", "schema.json"), {}) or {}).get("engine"),
    }


REFEREE_BATCH = 8


def claims_of(c):
    claims = []
    for g in c.get("guards", []):
        claims.append({"id": g.get("id"), "claim": "when %s the call is %s%s" % (
            json.dumps(g.get("when"), ensure_ascii=False), g.get("outcome"),
            " with code %s" % g["code"] if g.get("code") else ""), "cite": g.get("cite"), "because": g.get("because")})
    for b in c.get("branches", []):
        claims.append({"id": b.get("id"), "claim": "when %s an allowed call also writes %s" % (
            json.dumps(b.get("when"), ensure_ascii=False), ", ".join(b.get("writes", []))), "cite": b.get("cite")})
    return claims


def cmd_referee(workspace, system, group, part=1):
    adir = _lib.audit_dir(workspace, system)
    result = _lib.read_json(os.path.join(adir, "model_result.json"), {}) or {}
    proposed = {c.get("id"): c for c in result.get("commands", [])}
    for fix in result.get("corrections", []):
        if isinstance(fix.get("command"), dict):
            old = proposed.get(fix["command"].get("id"), {})
            proposed[fix["command"].get("id")] = dict(fix["command"], shard=fix["command"].get("shard") or old.get("shard"))
    own = sorted((c for c in proposed.values() if c.get("shard") == group or c.get("id") == group), key=lambda c: c["id"])
    if not own:
        _lib.die("model_result.json has no command of group %s" % group)
    parts = (len(own) + REFEREE_BATCH - 1) // REFEREE_BATCH
    if not 1 <= part <= parts:
        _lib.die("group %s has %d part(s)" % (group, parts))
    batch = own[(part - 1) * REFEREE_BATCH: part * REFEREE_BATCH]
    return {"task": "referee", "system": system, "group": group, "part": part, "parts": parts,
            "commands": [{"command": c["id"], "handler": c.get("handler"),
                          "variables": [{"name": v.get("name"), "meaning": v.get("meaning"), "sql": v.get("sql")}
                                        for v in c.get("vars", [])],
                          "claims": claims_of(c)} for c in batch]}


RECIPE_CASES = 60          # about what one reader works out with care in one go


def recipe_parts(own):
    """Split a group's commands into parts of at most RECIPE_CASES cases, keeping variants of one entry together."""
    roots = {}
    for c in own:
        roots.setdefault((c.get("variant") or {}).get("of") or c["id"], []).append(c)
    parts, current, size = [], [], 0
    for root in sorted(roots):
        cases = sum(1 + sum(len(v.get("domain", [])) - 1 for v in c.get("vars", [])) for c in roots[root])
        if current and size + cases > RECIPE_CASES:
            parts.append(current)
            current, size = [], 0
        current += roots[root]
        size += cases
    if current:
        parts.append(current)
    return parts


def cmd_recipes(workspace, system, name, part=1):
    adir, shards, themap = load(workspace, system)
    shard = shard_named(shards, name)
    handlers = {e.get("handler") for e in themap.get("entries", []) if e.get("id") in set(shard.get("entries", []))}
    commands = (_lib.read_json(os.path.join(adir, "model", "commands.json"), {}) or {}).get("commands")
    if commands is None:
        _lib.die("audit/%s/model/commands.json not found: run audit-model first" % system)
    parts = recipe_parts([c for c in commands if c.get("handler") in handlers]) or [[]]
    if not 1 <= part <= len(parts):
        _lib.die("group %s has %d part(s)" % (name, len(parts)))
    own = parts[part - 1]
    cases = {}
    for c in own:
        base = c.get("base", {})
        cases[c["id"]] = ["%s=%s" % (v["name"], x) for v in c.get("vars", []) for x in map(str, v.get("domain", []))
                          if str(x) != str(base.get(v["name"])).lower() and str(x) != str(base.get(v["name"]))]
    harness = _lib.read_json(os.path.join(adir, "harness.json"), {}) or {}
    existing = (_lib.read_json(os.path.join(adir, "model", "fixtures.json"), {}) or {}).get("fixtures", {})
    return {
        "task": "recipes", "system": system, "group": name, "part": part, "parts": len(parts), "format": FORMAT,
        "commands": own,
        "cases_to_reach": cases,
        "commands_a_step_may_call": [{"id": c["id"], "title": c.get("title"), "binding": c.get("binding"),
                                      "returns": next((e.get("returns") for e in themap.get("entries", [])
                                                       if e.get("handler") == c.get("handler")), None)} for c in commands],
        "fixtures_that_exist": existing,
        "commands_that_have_recipes": sorted((_lib.read_json(os.path.join(adir, "model", "recipes.json"), {}) or {})
                                             .get("recipes", {})),
        "fixture_prefix": name.split(":")[-1],
        "files": shard.get("files", []),
        "tables": tables(adir, themap),
        "time_can_be_moved": bool(harness.get("clock")),
        "today_is": (harness.get("clock") or "")[:10] or None,
        "stand_ins": stand_ins(harness),
    }


def cmd_triage(workspace, system, command_id=None):
    adir = _lib.audit_dir(workspace, system)
    model = os.path.join(adir, "model")
    rec = _lib.read_json(os.path.join(model, "reconciliation.json"))
    matrix = _lib.read_json(os.path.join(model, "matrix.json"))
    if not isinstance(rec, dict) or not isinstance(matrix, dict):
        _lib.die("audit/%s/model/reconciliation.json not found: run the matrix and reconcile.py first" % system)
    cells = {c["id"]: c for c in matrix["cells"]}
    items = []
    for d in rec.get("differences", []):
        cell = cells[d["cell"]]
        items.append({"cell": d["cell"], "command": d["command"], "case": d["deviation"] or "base", "what": "differs",
                      "predicted": d["static"], "observed": d["observed"], "differs_in": d["differs"],
                      "recipe": {k: cell.get(k) for k in ("fixture", "steps", "with")},
                      "evidence": "audit/%s/runs/%s/evidence/%s.json" % (system, d["run"], d["cell"])})
    for n in rec.get("not_executed", []):
        if n["state"] in ("unreachable", "no-recipe"):
            continue            # a claim, or a case nobody wrote a recipe for: nothing here to explain
        cell = cells[n["cell"]]
        items.append({"cell": n["cell"], "command": n["command"], "case": n["deviation"] or "base", "what": n["state"],
                      "reason": n["reason"], "predicted": n["static"], "given": cell.get("given"),
                      "recipe": {k: cell.get(k) for k in ("fixture", "steps", "with")} if "with" in cell else None})
    by_command = {}
    for item in items:
        by_command.setdefault(item["command"], []).append(item)
    if command_id is None:
        return {"system": system, "counts": rec.get("counts"), "to_look_at": len(items),
                "commands": {cid: len(v) for cid, v in sorted(by_command.items())}}
    commands = {c["id"]: c for c in (_lib.read_json(os.path.join(model, "commands.json"), {}) or {}).get("commands", [])}
    recipes = (_lib.read_json(os.path.join(model, "recipes.json"), {}) or {}).get("recipes", {})
    fixtures = (_lib.read_json(os.path.join(model, "fixtures.json"), {}) or {}).get("fixtures", {})
    entry = recipes.get(command_id, {})
    if entry.get("same_as"):
        entry = {"same_as": entry["same_as"], "recipes_of_that_command": recipes.get(entry["same_as"])}
    themap = _lib.read_json(os.path.join(adir, "map.json"), {}) or {}
    return {"task": "triage", "system": system, "format": FORMAT, "command": commands.get(command_id),
            "recipes": entry, "fixtures": fixtures,
            "commands_a_step_may_call": [{"id": c["id"], "binding": c.get("binding")} for c in commands.values()],
            "tables": tables(adir, themap),
            "cases": by_command.get(command_id, [])}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, extra in (("shards", None), ("mine", "shard"), ("referee", "shard"), ("recipes", "shard"), ("triage", None)):
        p = sub.add_parser(name)
        p.add_argument("system")
        if extra:
            p.add_argument(extra)
        if name == "shards":
            p.add_argument("--pattern")
        if name == "triage":
            p.add_argument("--command")
        if name in ("referee", "recipes"):
            p.add_argument("--part", type=int, default=1)
        if name != "shards":
            p.add_argument("--file", action="store_true",
                           help="write the brief to a file under model_raw/briefs/ and print its path, for an agent to read")
        p.add_argument("--workspace", default=".")
    args = ap.parse_args(argv)
    workspace = os.path.abspath(args.workspace)
    if args.cmd == "shards":
        out = cmd_shards(workspace, args.system, args.pattern)
    elif args.cmd == "mine":
        out = cmd_mine(workspace, args.system, args.shard)
    elif args.cmd == "referee":
        out = cmd_referee(workspace, args.system, args.shard, args.part)
    elif args.cmd == "recipes":
        out = cmd_recipes(workspace, args.system, args.shard, args.part)
    else:
        out = cmd_triage(workspace, args.system, args.command)
    if getattr(args, "file", False):
        # The agent reads the brief itself: nothing is retyped on the way, so nothing can change on the way.
        import re
        who = getattr(args, "shard", None) or getattr(args, "command", None) or "all"
        part = "-%d" % args.part if getattr(args, "part", 1) > 1 else ""
        path = os.path.join(_lib.audit_dir(workspace, args.system), "model_raw", "briefs",
                            "%s-%s%s.json" % (args.cmd, re.sub(r"[^A-Za-z0-9_.-]+", "_", who), part))
        _lib.write_json(path, out)
        print("brief: %s" % os.path.relpath(path, workspace).replace(os.sep, "/"))
        if isinstance(out, dict) and out.get("parts", 1) > 1:
            print("parts: %d" % out["parts"])
        return 0
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
