#!/usr/bin/env python3
"""Expand the command model into the matrix of cells to execute.

    python3 build_matrix.py <system> [--workspace DIR]

Reads audit/<system>/model/commands.json (each command's variables, its guards in the order the code
applies them, and its base case) model/fixtures.json (named starting situations, each a list of commands) and model/recipes.json (how
to reach each case of each command), and writes model/matrix.json. A cell keeps its id for good: the ids are kept in model/ids.json, and
a cell that disappears does not give its number to another. What model/expected.json pins for a cell
(an observation, a person's decision) is copied into it.

The cells of a command are its base case, in which every guard passes, and one cell for every other
value of every variable, the rest staying at the base (design section 11.2). So every state value
and every parameter class that a guard distinguishes is executed at least once, without multiplying
them with each other.

For each cell the script works out what reading the code predicts ("static"): the first guard, in
order, whose condition holds decides; if none holds the command is allowed. A command may also list
`branches`: conditions under which an allowed call writes something more (a line that closes when
its last delivery ships). For an allowed cell the prediction names the branches that hold and the
columns they write. A cell whose deviating variable no guard and no branch mentions is marked
`unhandled`: the code makes no decision about it, so what happens there happens by default.

Nothing here is judged. A recipe that is missing leaves the cell without one, and the runner
reports it as not executed. For each command the script also lists the guards that decide none of
its executable cells: code that, as far as the model can reach, never fires from that entry.
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402

MATRIX_SCHEMA = 1
OUTCOMES = ("allowed", "rejected", "ignored", "error")


def text(value):
    """Values are compared as the text the database and JSON give: true/false, null, and plain strings."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def holds(cond, valuation):
    if "all" in cond:
        return all(holds(c, valuation) for c in cond["all"])
    if "any" in cond:
        return any(holds(c, valuation) for c in cond["any"])
    if "not" in cond:
        return not holds(cond["not"], valuation)
    value = valuation.get(cond["var"])
    if "is" in cond:
        return value == text(cond["is"])
    if "in" in cond:
        return value in [text(v) for v in cond["in"]]
    if "not_in" in cond:
        return value not in [text(v) for v in cond["not_in"]]
    raise ValueError("a condition needs one of: is, in, not_in, all, any, not")


def mentioned(cond):
    if "var" in cond:
        return {cond["var"]}
    out = set()
    for key in ("all", "any"):
        for c in cond.get(key, []):
            out |= mentioned(c)
    if "not" in cond:
        out |= mentioned(cond["not"])
    return out


def static_outcome(command, valuation):
    for guard in command.get("guards", []):
        if holds(guard["when"], valuation):
            out = {"outcome": guard["outcome"], "guard": guard["id"]}
            if guard.get("code"):
                out["code"] = guard["code"]
            if guard.get("because"):
                out["because"] = guard["because"]
            if guard.get("writes"):
                out["writes_include"] = sorted(guard["writes"])      # a refusal that itself changes data
            return out
    out = {"outcome": command.get("default_outcome", "allowed"), "guard": None}
    taken = [b for b in command.get("branches", []) if holds(b["when"], valuation)]
    if taken:
        out["branches"] = [b["id"] for b in taken]
    # What every allowed call writes and sends, and what the branches taken add.
    writes = sorted(set(command.get("writes", [])) | {w for b in taken for w in b.get("writes", [])})
    sends = sorted(set(command.get("sends", [])) | {x for b in taken for x in b.get("sends", [])})
    if writes:
        out["writes_include"] = writes
    if sends:
        out["sends_include"] = sends
    return out


def check_command(command):
    """Return a list of problems in one command's definition."""
    problems = []
    cid = command.get("id", "?")
    names = [v["name"] for v in command.get("vars", [])]
    if len(names) != len(set(names)):
        problems.append("%s: a variable name is used twice" % cid)
    base = command.get("base", {})
    for v in command.get("vars", []):
        domain = [text(x) for x in v.get("domain", [])]
        if v["name"] not in base:
            problems.append("%s: no base value for %s" % (cid, v["name"]))
        elif text(base[v["name"]]) not in domain:
            problems.append("%s: base value of %s is not in its domain" % (cid, v["name"]))
        if v.get("kind") == "state" and not v.get("sql"):
            problems.append("%s: state variable %s has no sql" % (cid, v["name"]))
    seen = set()
    for g in command.get("guards", []):
        for w in g.get("writes", []):
            if "." not in w:
                problems.append("%s: guard %s writes %r; name the column as table.column" % (cid, g.get("id"), w))
        if g.get("outcome") not in OUTCOMES:
            problems.append("%s: guard %s has outcome %r" % (cid, g.get("id"), g.get("outcome")))
        if g.get("id") in seen:
            problems.append("%s: guard id %s is used twice" % (cid, g.get("id")))
        seen.add(g.get("id"))
        for name in sorted(mentioned(g["when"])):
            if name not in names:
                problems.append("%s: guard %s mentions %s, which is not a variable of the command" % (cid, g.get("id"), name))
    for w in command.get("writes", []):
        if "." not in w:
            problems.append("%s: writes %r; name the column as table.column" % (cid, w))
    for b in command.get("branches", []):
        if b.get("id") in seen:
            problems.append("%s: id %s is used twice" % (cid, b.get("id")))
        seen.add(b.get("id"))
        for name in sorted(mentioned(b["when"])):
            if name not in names:
                problems.append("%s: branch %s mentions %s, which is not a variable of the command" % (cid, b.get("id"), name))
        for w in b.get("writes", []):
            if "." not in w:
                problems.append("%s: branch %s writes %r; name the column as table.column" % (cid, b.get("id"), w))
    base_valuation = {k: text(v) for k, v in base.items()}
    if not problems and static_outcome(command, base_valuation)["outcome"] not in ("allowed", command.get("default_outcome", "allowed")):
        problems.append("%s: in the base case a guard already refuses; the base must be a case every guard lets through" % cid)
    return problems


def check_recipes(commands, recipes, fixtures):
    """Return a list of problems in the recipes and fixtures: names that lead nowhere."""
    problems = []
    by_id = {c["id"]: c for c in commands}

    def check_steps(steps, where):
        for n, step in enumerate(steps or [], 1):
            if "set_today" in step:
                continue
            if step.get("do") not in by_id:
                problems.append("%s, step %d: no command %r" % (where, n, step.get("do")))

    for name, fixture in fixtures.items():
        if fixture.get("from") is not None and fixture["from"] not in fixtures:
            problems.append("fixture %s starts from %s, which does not exist" % (name, fixture["from"]))
        check_steps(fixture.get("steps"), "fixture %s" % name)
    for cid, entry in recipes.items():
        if cid not in by_id:
            problems.append("recipes.json names %s, which commands.json does not have" % cid)
            continue
        if entry.get("same_as"):
            if entry["same_as"] not in recipes:
                problems.append("recipes of %s: same as %s, which has no recipes" % (cid, entry["same_as"]))
            continue
        cases = {"%s=%s" % (v["name"], text(x)) for v in by_id[cid].get("vars", []) for x in v.get("domain", [])}
        for key, recipe in [("base", entry.get("base") or {})] + sorted((entry.get("deviations") or {}).items()):
            where = "recipe of %s, %s" % (cid, key)
            if key != "base" and key not in cases:
                problems.append("%s: the command has no such case" % where)
            if recipe.get("fixture") is not None and recipe["fixture"] not in fixtures:
                problems.append("%s: no fixture %r" % (where, recipe["fixture"]))
            check_steps(recipe.get("steps"), where)
    return problems


def recipe_for(recipes, cid, key):
    """The recipe of a cell: the base recipe, with the deviation's own entries over it."""
    entry = recipes.get(cid)
    if entry and entry.get("same_as"):
        entry = recipes.get(entry["same_as"])
    if not entry or "base" not in entry:
        return None
    base = entry["base"]
    if key is None:
        return dict(base)
    dev = entry.get("deviations", {}).get(key)
    if dev is None:
        return None
    out = {"fixture": dev.get("fixture", base.get("fixture")),
           "steps": dev.get("steps", base.get("steps", [])),
           "with": dict(base.get("with") or {}, **(dev.get("with") or {}))}
    for extra in ("also", "unreachable", "note"):
        if extra in dev:
            out[extra] = dev[extra]
    return out


def cells_of(command, recipes):
    cid = command["id"]
    base = {v["name"]: text(command["base"][v["name"]]) for v in command.get("vars", [])}
    guard_vars = set()
    for g in command.get("guards", []) + command.get("branches", []):
        guard_vars |= mentioned(g["when"])

    def cell(deviation, valuation, recipe, var=None):
        given = dict(valuation)
        if recipe and recipe.get("also"):
            given.update({k: text(v) for k, v in recipe["also"].items()})
        static = static_outcome(command, given)
        if var is not None and var not in guard_vars and static["outcome"] == "allowed":
            static["unhandled"] = True          # no guard of this command looks at the variable that differs
        out = {"key": "%s|%s" % (cid, deviation or "base"), "command": cid, "deviation": deviation,
               "given": given, "static": static}
        if recipe:
            if recipe.get("unreachable"):
                out["unreachable"] = recipe["unreachable"]
            else:
                out["fixture"] = recipe.get("fixture")
                out["steps"] = recipe.get("steps", [])
                out["with"] = recipe.get("with") or {}
            if recipe.get("note"):
                out["note"] = recipe["note"]
        return out

    out = [cell(None, base, recipe_for(recipes, cid, None))]
    for v in command.get("vars", []):
        for value in v.get("domain", []):
            if text(value) == base[v["name"]]:
                continue
            key = "%s=%s" % (v["name"], text(value))
            out.append(cell(key, dict(base, **{v["name"]: text(value)}), recipe_for(recipes, cid, key), v["name"]))
    return out


def assign_ids(cells, registry):
    """Give every cell the id its key already has, or the next free number. Numbers are never reused."""
    known = registry.setdefault("cells", {})
    nxt = registry.get("next", 1)
    for cell in cells:
        if cell["key"] not in known:
            known[cell["key"]] = nxt
            nxt += 1
        cell["id"] = "CELL-%04d" % known[cell["key"]]
    registry["next"] = nxt
    registry["schema"] = 1
    return registry


def uses(cell, fixtures, bindings):
    """The fixtures a cell runs (its own and the ones those continue) and the bindings of every command
    its steps call. A change anywhere else does not change what running this cell means."""
    chain, name, seen = [], cell.get("fixture"), set()
    while name and name in fixtures and name not in seen:
        seen.add(name)
        chain.append({"name": name, "steps": fixtures[name].get("steps"), "from": fixtures[name].get("from")})
        name = fixtures[name].get("from")
    called = set()
    for steps in [f["steps"] for f in chain] + [cell.get("steps")]:
        for step in steps or []:
            if step.get("do"):
                called.add(step["do"])
    return {"fixtures": chain, "bindings": {c: bindings.get(c) for c in sorted(called)}}


def fingerprint(cell, command, shared):
    """A hash of everything that decides what running the cell means. A result is for one fingerprint."""
    parts = {k: cell.get(k) for k in ("command", "deviation", "given", "fixture", "steps", "with", "unreachable")}
    parts["definition"] = {k: command.get(k) for k in ("binding", "vars", "reads_only", "writes", "sends")}
    parts["predicted_writes"] = [cell.get("static", {}).get("writes_include"), cell.get("static", {}).get("sends_include")]
    parts["shared"] = shared
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def executable(cell):
    return "with" in cell and "unreachable" not in cell


def guards_without_cell(command, cells):
    """Guards that decide no executable cell of their command, and branches no executable cell takes."""
    own = [c for c in cells if c["command"] == command["id"] and executable(c)]
    deciding = {c["static"].get("guard") for c in own}
    for c in own:
        deciding |= set(c["static"].get("branches", []))
    return [g["id"] for g in command.get("guards", []) + command.get("branches", []) if g["id"] not in deciding]


def build(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    model = os.path.join(adir, "model")
    commands = _lib.read_json(os.path.join(model, "commands.json"))
    if not isinstance(commands, dict) or "commands" not in commands:
        _lib.die("audit/%s/model/commands.json not found or unreadable" % system)
    recipes = (_lib.read_json(os.path.join(model, "recipes.json"), {}) or {}).get("recipes", {})
    problems = []
    for c in commands["commands"]:
        problems += check_command(c)
    known = {c["id"] for c in commands["commands"]}
    if len(known) != len(commands["commands"]):
        problems.append("commands.json uses a command id twice")
    fixtures = (_lib.read_json(os.path.join(model, "fixtures.json"), {}) or {}).get("fixtures", {})
    problems += check_recipes(commands["commands"], recipes, fixtures)
    if problems:
        _lib.die("the command model has problems:\n  " + "\n  ".join(problems), code=1)

    cells = []
    for c in commands["commands"]:
        cells += cells_of(c, recipes)
    ids_path = os.path.join(model, "ids.json")
    _lib.write_json(ids_path, assign_ids(cells, _lib.read_json(ids_path, {}) or {}))
    by_id = {c["id"]: c for c in commands["commands"]}
    bindings = {cid: c.get("binding") for cid, c in by_id.items()}
    pinned = (_lib.read_json(os.path.join(model, "expected.json"), {}) or {}).get("cells", {})
    for cell in cells:
        cell["sha"] = fingerprint(cell, by_id[cell["command"]], uses(cell, fixtures, bindings))
        if cell["key"] in pinned:
            cell["expected"] = pinned[cell["key"]]
    cells = [dict([("id", c["id"])] + [(k, v) for k, v in c.items() if k != "id"]) for c in cells]

    per_command = {}
    for c in commands["commands"]:
        own = [x for x in cells if x["command"] == c["id"]]
        per_command[c["id"]] = {
            "cells": len(own),
            "executable": sum(1 for x in own if executable(x)),
            "guards_without_cell": guards_without_cell(c, own),
        }
    stats = {
        "commands": len(commands["commands"]),
        "cells": len(cells),
        "with_recipe": sum(1 for c in cells if executable(c)),
        "unreachable": sum(1 for c in cells if "unreachable" in c),
        "without_recipe": sum(1 for c in cells if "with" not in c and "unreachable" not in c),
        "unhandled": sum(1 for c in cells if c["static"].get("unhandled")),
        "pinned": sum(1 for c in cells if "expected" in c),
        "guards_without_cell": sum(len(v["guards_without_cell"]) for v in per_command.values()),
    }
    _lib.write_json(os.path.join(model, "matrix.json"),
                    {"schema": MATRIX_SCHEMA, "stats": stats, "commands": per_command, "cells": cells})
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("system")
    ap.add_argument("--workspace", default=".")
    args = ap.parse_args(argv)
    st = build(os.path.abspath(args.workspace), args.system)
    print("matrix written: %(cells)d cells for %(commands)d commands; %(with_recipe)d with a recipe, "
          "%(unreachable)d marked unreachable, %(without_recipe)d without a recipe, %(unhandled)d unhandled, "
          "%(pinned)d pinned; %(guards_without_cell)d guards decide no executable cell" % st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
