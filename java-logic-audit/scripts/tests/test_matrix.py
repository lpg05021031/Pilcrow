"""Tests for build_matrix.py: cells, static outcomes, permanent ids, and the reviewed model of sample 1."""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(HERE))
import build_matrix  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
MODEL = os.path.join(REPO, "fixtures", "expected", "orders-jpa.model")
SAMPLE = os.path.join(REPO, "fixtures", "orders-jpa")


def command(**over):
    c = {
        "id": "CMD-x",
        "binding": {"kind": "http", "method": "POST", "path": "/x/{id}"},
        "vars": [
            {"name": "status", "kind": "state", "sql": "select status from x where id = :id", "domain": ["OPEN", "DONE", "null"]},
            {"name": "qty", "kind": "param", "domain": ["ok", "zero"]},
            {"name": "flag", "kind": "state", "sql": "select flag from x where id = :id", "domain": ["false", "true"]},
        ],
        "base": {"status": "OPEN", "qty": "ok", "flag": False},
        "guards": [
            {"id": "G1", "when": {"var": "status", "is": "null"}, "outcome": "rejected", "code": "NOT_FOUND"},
            {"id": "G2", "when": {"var": "status", "not_in": ["OPEN"]}, "outcome": "rejected", "code": "NOT_OPEN"},
            {"id": "G3", "when": {"var": "qty", "is": "zero"}, "outcome": "rejected", "code": "BAD_QTY"},
        ],
    }
    c.update(over)
    return c


RECIPES = {"CMD-x": {
    "base": {"fixture": "one", "with": {"id": "${x.id}", "qty": 1}},
    "deviations": {
        "status=DONE": {"fixture": "done"},
        "status=null": {"with": {"id": 999}},
        "qty=zero": {"with": {"qty": 0}},
    }}}
FIXTURES = {"one": {"steps": [{"do": "CMD-x", "with": {}}]}, "done": {"from": "one", "steps": []}}


class Cells(unittest.TestCase):

    def test_one_cell_for_the_base_and_one_for_each_other_value(self):
        cells = build_matrix.cells_of(command(), RECIPES)
        self.assertEqual([c["deviation"] for c in cells], [None, "status=DONE", "status=null", "qty=zero", "flag=true"])
        self.assertEqual(cells[0]["given"], {"status": "OPEN", "qty": "ok", "flag": "false"})
        self.assertEqual(cells[1]["given"], {"status": "DONE", "qty": "ok", "flag": "false"})

    def test_the_first_guard_that_holds_decides(self):
        by = {c["deviation"]: c["static"] for c in build_matrix.cells_of(command(), RECIPES)}
        self.assertEqual(by[None], {"outcome": "allowed", "guard": None})
        self.assertEqual((by["status=null"]["guard"], by["status=null"]["code"]), ("G1", "NOT_FOUND"))
        self.assertEqual((by["status=DONE"]["guard"], by["status=DONE"]["code"]), ("G2", "NOT_OPEN"))
        self.assertEqual(by["qty=zero"]["code"], "BAD_QTY")

    def test_a_variable_no_guard_looks_at_is_unhandled(self):
        by = {c["deviation"]: c for c in build_matrix.cells_of(command(), RECIPES)}
        self.assertTrue(by["flag=true"]["static"].get("unhandled"))
        self.assertNotIn("unhandled", by["qty=zero"]["static"])
        self.assertNotIn("with", by["flag=true"])                    # and it has no recipe

    def test_a_recipe_adds_to_the_base_recipe(self):
        by = {c["deviation"]: c for c in build_matrix.cells_of(command(), RECIPES)}
        self.assertEqual(by["qty=zero"]["with"], {"id": "${x.id}", "qty": 0})
        self.assertEqual(by["qty=zero"]["fixture"], "one")
        self.assertEqual(by["status=DONE"]["fixture"], "done")

    def test_also_changes_what_the_cell_is_about_and_what_is_predicted(self):
        recipes = {"CMD-x": {"base": RECIPES["CMD-x"]["base"],
                             "deviations": {"qty=zero": {"with": {"qty": 0}, "also": {"status": "DONE"}}}}}
        cell = [c for c in build_matrix.cells_of(command(), recipes) if c["deviation"] == "qty=zero"][0]
        self.assertEqual(cell["given"]["status"], "DONE")
        self.assertEqual(cell["static"]["guard"], "G2")

    def test_unreachable_is_kept_and_not_executable(self):
        recipes = {"CMD-x": {"base": RECIPES["CMD-x"]["base"],
                             "deviations": {"status=DONE": {"unreachable": "nothing sets it"}}}}
        cell = [c for c in build_matrix.cells_of(command(), recipes) if c["deviation"] == "status=DONE"][0]
        self.assertEqual(cell["unreachable"], "nothing sets it")
        self.assertFalse(build_matrix.executable(cell))

    def test_same_as_borrows_another_commands_recipes(self):
        other = command(id="CMD-y")
        cells = build_matrix.cells_of(other, dict(RECIPES, **{"CMD-y": {"same_as": "CMD-x"}}))
        self.assertEqual(cells[0]["fixture"], "one")

    def test_conditions(self):
        v = {"a": "1", "b": "x"}
        self.assertTrue(build_matrix.holds({"all": [{"var": "a", "is": 1}, {"var": "b", "in": ["x", "y"]}]}, v))
        self.assertTrue(build_matrix.holds({"any": [{"var": "a", "is": 2}, {"not": {"var": "b", "is": "y"}}]}, v))
        self.assertFalse(build_matrix.holds({"var": "b", "not_in": ["x"]}, v))
        self.assertEqual(build_matrix.mentioned({"all": [{"var": "a", "is": 1}, {"not": {"var": "b", "is": 2}}]}), {"a", "b"})


class Checks(unittest.TestCase):

    def test_a_sound_command_has_no_problems(self):
        self.assertEqual(build_matrix.check_command(command()), [])
        self.assertEqual(build_matrix.check_recipes([command()], RECIPES, FIXTURES), [])

    def test_problems_in_a_command(self):
        self.assertIn("base value of status is not in its domain",
                      " ".join(build_matrix.check_command(command(base={"status": "GONE", "qty": "ok", "flag": False}))))
        self.assertIn("base must be a case every guard lets through",
                      " ".join(build_matrix.check_command(command(base={"status": "DONE", "qty": "ok", "flag": False}))))
        bad = command()
        bad["guards"].append({"id": "G9", "when": {"var": "nope", "is": 1}, "outcome": "rejected"})
        self.assertIn("mentions nope", " ".join(build_matrix.check_command(bad)))
        bad = command()
        bad["guards"][0]["outcome"] = "maybe"
        self.assertIn("has outcome 'maybe'", " ".join(build_matrix.check_command(bad)))

    def test_problems_in_recipes(self):
        recipes = {"CMD-x": {"base": {"fixture": "nowhere", "with": {}},
                             "deviations": {"status=GONE": {}, "qty=zero": {"steps": [{"do": "CMD-none"}]}}},
                   "CMD-z": {"base": {}}}
        problems = " | ".join(build_matrix.check_recipes([command()], recipes, FIXTURES))
        self.assertIn("no fixture 'nowhere'", problems)
        self.assertIn("status=GONE: the command has no such case", problems)
        self.assertIn("no command 'CMD-none'", problems)
        self.assertIn("names CMD-z", problems)


class Build(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.model = os.path.join(self.tmp, "audit", "s", "model")
        os.makedirs(self.model)
        self.write("commands.json", {"commands": [command()]})
        self.write("recipes.json", {"recipes": RECIPES})
        self.write("fixtures.json", {"fixtures": FIXTURES})

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, name, body):
        with open(os.path.join(self.model, name), "w") as f:
            json.dump(body, f)

    def read(self, name):
        with open(os.path.join(self.model, name)) as f:
            return json.load(f)

    def test_a_cell_keeps_its_id_when_the_model_grows(self):
        build_matrix.build(self.tmp, "s")
        before = {c["key"]: c["id"] for c in self.read("matrix.json")["cells"]}
        grown = command()
        grown["vars"][0]["domain"].insert(1, "HELD")            # a new value in the middle
        self.write("commands.json", {"commands": [command(id="CMD-a", guards=[]), grown]})
        build_matrix.build(self.tmp, "s")
        after = {c["key"]: c["id"] for c in self.read("matrix.json")["cells"]}
        for key, cell_id in before.items():
            self.assertEqual(after[key], cell_id)
        self.assertEqual(len(set(after.values())), len(after))
        self.assertNotIn(after["CMD-x|status=HELD"], before.values())

    def test_a_number_is_not_given_out_twice(self):
        build_matrix.build(self.tmp, "s")
        shrunk = command()
        shrunk["vars"] = shrunk["vars"][:2]
        shrunk["base"] = {"status": "OPEN", "qty": "ok"}
        self.write("commands.json", {"commands": [shrunk]})
        build_matrix.build(self.tmp, "s")
        grown = command()
        grown["vars"][1]["domain"].append("huge")
        self.write("commands.json", {"commands": [grown]})
        build_matrix.build(self.tmp, "s")
        ids = self.read("ids.json")["cells"]
        self.assertEqual(len(set(ids.values())), len(ids))
        self.assertEqual(ids["CMD-x|qty=huge"], 6)

    def test_what_is_pinned_is_copied_into_the_cell(self):
        self.write("expected.json", {"cells": {"CMD-x|qty=zero": {"outcome": "rejected", "code": "BAD_QTY", "source": "observed"}}})
        stats = build_matrix.build(self.tmp, "s")
        self.assertEqual(stats["pinned"], 1)
        cell = [c for c in self.read("matrix.json")["cells"] if c["key"] == "CMD-x|qty=zero"][0]
        self.assertEqual(cell["expected"]["source"], "observed")

    def test_guards_that_decide_no_executable_cell_are_listed(self):
        recipes = {"CMD-x": {"base": RECIPES["CMD-x"]["base"],
                             "deviations": {"status=DONE": {"fixture": "done"}, "status=null": {"unreachable": "never"}}}}
        self.write("recipes.json", {"recipes": recipes})
        build_matrix.build(self.tmp, "s")
        self.assertEqual(self.read("matrix.json")["commands"]["CMD-x"]["guards_without_cell"], ["G1", "G3"])

    def test_a_broken_model_is_refused(self):
        self.write("recipes.json", {"recipes": {"CMD-x": {"base": {"fixture": "nowhere"}}}})
        with self.assertRaises(SystemExit):
            build_matrix.build(self.tmp, "s")


class ReviewedModelOfSampleOne(unittest.TestCase):
    """The model under fixtures/expected is what the plugin's own derivation is measured against."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        shutil.copytree(MODEL, os.path.join(cls.tmp, "audit", "orders", "model"))
        cls.stats = build_matrix.build(cls.tmp, "orders")
        with open(os.path.join(cls.tmp, "audit", "orders", "model", "matrix.json")) as f:
            cls.matrix = json.load(f)
        with open(os.path.join(MODEL, "commands.json")) as f:
            cls.commands = json.load(f)["commands"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_every_cell_has_a_recipe_or_a_reason(self):
        self.assertEqual(self.stats["commands"], 19)
        self.assertEqual(self.stats["cells"], 174)
        self.assertEqual(self.stats["without_recipe"], 0)
        self.assertEqual(self.stats["unreachable"], 12)
        self.assertEqual(self.stats["unhandled"], 0)

    def test_guards_that_never_decide(self):
        by = {k: v["guards_without_cell"] for k, v in self.matrix["commands"].items() if v["guards_without_cell"]}
        self.assertEqual(sorted(by), ["CMD-commitChange.CANCEL", "CMD-commitChange.DATE", "CMD-commitChange.QTY", "CMD-confirmOrder"])
        self.assertEqual(by["CMD-confirmOrder"], ["G3"])
        self.assertNotIn("G9", by["CMD-commitChange.DATE"])          # time moving past the date is reachable

    def test_every_guard_cites_lines_that_exist_and_hold_its_code(self):
        from_mapping = {"NOT_FOUND", "INVALID_REQUEST", "STALE_PREVIEW"}       # raised or named elsewhere than the check
        for c in self.commands:
            for guard in c["guards"]:
                where = "%s %s" % (c["id"], guard["id"])
                m = re.match(r"^(.+):(\d+)(?:-(\d+))?$", guard.get("cite", ""))
                self.assertTrue(m, where + " has no citation")
                path = os.path.join(SAMPLE, m.group(1))
                self.assertTrue(os.path.isfile(path), where + " cites a file that does not exist")
                with open(path) as f:
                    lines = f.read().split("\n")
                first, last = int(m.group(2)), int(m.group(3) or m.group(2))
                self.assertTrue(1 <= first <= last <= len(lines), where + " cites lines outside the file")
                code = guard.get("code") or guard.get("swallowed")
                if code and code not in from_mapping:
                    self.assertIn('"%s"' % code, "\n".join(lines[first - 1:last]), where)


if __name__ == "__main__":
    unittest.main()
