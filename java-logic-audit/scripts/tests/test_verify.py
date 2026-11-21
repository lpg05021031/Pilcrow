"""Tests for the verify step's scripts: candidate rules, their assessment, and what sequences and faults add
to the questions, the findings and the verdict."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(HERE))
import apply_decisions  # noqa: E402
import build_questions  # noqa: E402
import invariants  # noqa: E402
import judge  # noqa: E402
import manifest  # noqa: E402

SCHEMA = {"tables": {
    "orders": {"columns": [{"name": "id", "type": "int8"}, {"name": "total", "type": "numeric"}, {"name": "status", "type": "varchar"},
                           {"name": "version", "type": "int8"}],
               "primary_key": ["id"], "foreign_keys": []},
    "lines": {"columns": [{"name": "id", "type": "int8"}, {"name": "order_id", "type": "int8"}, {"name": "qty", "type": "int4"},
                          {"name": "shipped", "type": "int4"}, {"name": "price", "type": "numeric"}, {"name": "status", "type": "varchar"}],
              "primary_key": ["id"], "foreign_keys": [{"column": "order_id", "references_table": "orders", "references_column": "id"}]},
}}
MAP = {"entities": [{"id": "ENT-orders", "table": "orders", "version_property": "version",
                     "columns": [{"name": "version", "property": "version"}]}],
       "entries": [{"handler": "x.C#ship", "kind": "http"}]}
RECONCILIATION = {"cells": {}, "lifecycle": {
    "transitions": [{"column": "lines.status", "from": "OPEN", "to": "DONE"}],
    "created_with": [{"column": "orders.status", "value": "NEW"}]}}
COMMANDS = [{"id": "CMD-ship", "handler": "x.C#ship", "target": "lines", "binding": {"kind": "http"}, "guards": [],
             "vars": [{"name": "q", "sql": "select case when :n > l.shipped then 'a' when :n = l.qty then 'b' end from lines l"}]}]


class Verify(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.adir = os.path.join(self.tmp, "audit", "s")
        os.makedirs(os.path.join(self.adir, "model"))
        self.write("facts/schema.json", SCHEMA)
        self.write("map.json", MAP)
        self.write("model/reconciliation.json", RECONCILIATION)
        self.write("model/commands.json", {"commands": COMMANDS})
        self.write("profile.json", {"capabilities": {"matches_production": "confirmed", "sql_interception": True,
                                                     "database": {"real_engine": True}}})
        cell = {"id": "CELL-0001", "key": "CMD-ship|base", "command": "CMD-ship", "deviation": None, "given": {},
                "static": {"outcome": "allowed", "guard": None}, "sha": "s1", "with": {},
                "expected": {"outcome": "allowed", "written": ["lines.shipped"], "created": [], "deleted": [], "published": []}}
        self.write("model/matrix.json", {"cells": [cell], "commands": {}})
        manifest.build(self.tmp, os.path.join(self.adir, "manifest.json"))
        self.n = 0

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, rel, body):
        path = os.path.join(self.adir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(body, f)

    def read(self, rel):
        with open(os.path.join(self.adir, rel)) as f:
            return json.load(f)

    def run_of(self, kind, rows):
        self.n += 1
        name = "%s-29990101-00000%d" % (kind, self.n)
        raw = "".join(json.dumps(r) + "\n" for r in rows).encode("utf-8")
        d = os.path.join(self.adir, "runs", name)
        os.makedirs(d)
        with open(os.path.join(d, "results.jsonl"), "wb") as f:
            f.write(raw)
        self.write("runs/%s/run.json" % name, {"kind": kind, "total": len(rows), "results_sha256": hashlib.sha256(raw).hexdigest()})
        return name

    def rules(self):
        return {i["statement"]: i for i in invariants.candidates(self.tmp, "s")}

    def seq_row(self, inv, applied, violated, replays=None):
        observed = {"applied": applied, "violated": violated}
        if replays is not None:
            observed.update(replays_violating=replays, steps_to_reproduce=4)
        return {"id": inv["id"], "result": "MISMATCH" if violated else ("MATCH" if applied else "SKIPPED"), "observed": observed,
                "detail": {"class": inv["class"], "statement": inv["statement"], "status": inv.get("status")}}

    # ---- candidates

    def test_templates(self):
        by = self.rules()
        # a bound only between two columns that one check of the model reads together
        self.assertIn("lines.shipped is never larger than lines.qty", by)
        self.assertIn("lines.qty is never larger than lines.shipped", by)
        self.assertNotIn("lines.qty is never larger than lines.price", by)
        # sums over child rows, plain, as a product, and with a condition on a lifecycle value seen in the runs
        self.assertIn("orders.total equals the sum of lines.qty over its lines rows", by)
        self.assertIn("orders.total equals the sum of lines.qty x price over its lines rows whose status is not DONE", by)
        self.assertNotIn("orders.version equals the sum of lines.qty over its lines rows", by)      # version columns are left out
        # pairs of lifecycle values across parent and child
        pair = by["no lines row with status OPEN belongs to a orders row with status NEW"]
        self.assertEqual((pair["class"], pair["tables"]), ("pair", ["lines", "orders"]))
        self.assertTrue(all(i["sql"].lower().startswith("select") and ";" not in i["sql"] for i in by.values()))
        self.assertTrue(all(i["status"] == "proposed" and i["id"].startswith("INV-") for i in by.values()))

    def test_what_a_person_settled_survives_regeneration(self):
        by = self.rules()
        data = self.read("model/invariants.json")
        for inv in data["invariants"]:
            if inv["statement"] == "lines.shipped is never larger than lines.qty":
                inv["status"] = "confirmed"
        data["invariants"].append({"id": "INV-custom", "class": "custom", "statement": "from a reader", "sql": "select 1 from lines where 1=0",
                                   "source": "reader", "status": "candidate", "tables": ["lines"]})
        self.write("model/invariants.json", data)
        again = self.rules()
        self.assertEqual(again["lines.shipped is never larger than lines.qty"]["status"], "confirmed")
        self.assertIn("from a reader", again)
        self.assertEqual(len(again), len(by) + 1)

    # ---- assessment

    def test_candidates_are_sorted_by_what_the_run_showed(self):
        by = self.rules()
        holds = by["lines.shipped is never larger than lines.qty"]
        wrong = by["lines.qty is never larger than lines.shipped"]
        exact = by["orders.total equals the sum of lines.qty x price over its lines rows whose status is not DONE"]
        loose = by["orders.total equals the sum of lines.qty x price over its lines rows"]
        almost = by["orders.total equals the sum of lines.shipped over its lines rows"]
        few = by["no lines row with status OPEN belongs to a orders row with status NEW"]
        rare = by["no lines row with status DONE belongs to a orders row with status NEW"]
        self.run_of("sequences", [self.seq_row(holds, 500, 0), self.seq_row(wrong, 40, 30), self.seq_row(exact, 500, 0),
                                  self.seq_row(loose, 500, 9, 3), self.seq_row(few, 5, 0), self.seq_row(rare, 400, 3, 3)])
        r = invariants.assess(self.tmp, "s")
        now = {i["id"]: i for i in self.read("model/invariants.json")["invariants"]}
        self.assertEqual(now[holds["id"]]["status"], "candidate")
        self.assertEqual(now[wrong["id"]]["status"], "discarded")
        self.assertEqual(now[exact["id"]]["status"], "candidate")
        self.assertEqual(now[loose["id"]]["status"], "discarded")                 # a stricter one about the same column holds
        self.assertEqual(now[loose["id"]]["support"]["refined_by"], exact["id"])
        self.assertEqual(now[few["id"]]["status"], "untested")
        self.assertEqual(now[rare["id"]]["status"], "near")
        self.assertEqual(now[almost["id"]]["status"], "proposed")                # no run has looked at it: not a claim yet
        self.assertEqual(r["counts"]["near"], 1)

    def test_a_looser_sum_is_near_only_when_no_stricter_one_holds(self):
        by = self.rules()
        loose = by["orders.total equals the sum of lines.qty x price over its lines rows"]
        self.run_of("sequences", [self.seq_row(loose, 500, 9, 3)])
        invariants.assess(self.tmp, "s")
        now = {i["id"]: i for i in self.read("model/invariants.json")["invariants"]}
        self.assertEqual(now[loose["id"]]["status"], "near")

    # ---- questions, decisions, findings

    def after_a_run(self):
        by = self.rules()
        self.holds = by["lines.shipped is never larger than lines.qty"]
        self.loose = by["orders.total equals the sum of lines.qty x price over its lines rows"]
        rows = [self.seq_row(self.holds, 500, 0), self.seq_row(self.loose, 500, 9, 3),
                {"id": "SEQERR-001", "result": "MISMATCH", "observed": {"replays_violating": 3, "steps_to_reproduce": 2},
                 "detail": {"class": "error", "command": "CMD-ship", "code": "IllegalStateException"}}]
        self.run_of("sequences", rows)
        invariants.assess(self.tmp, "s")
        return {q["kind"]: q for q in build_questions.build(self.tmp, "s")["questions"]}

    def test_questions_from_sequences(self):
        qs = self.after_a_run()
        self.assertEqual(sorted(qs), ["error-in-sequence", "near-rule", "rule?"])
        self.assertTrue(qs["near-rule"]["flagged"] and qs["error-in-sequence"]["flagged"])
        self.assertFalse(qs["rule?"]["flagged"])
        self.assertIn("held in 491 of 500 states and was broken in 9", qs["near-rule"]["observed"])
        self.assertIn("lines.shipped is never larger than lines.qty (500 states)", qs["rule?"]["observed"])
        self.assertEqual(qs["rule?"]["invariants"], [self.holds["id"]])
        self.assertEqual(qs["error-in-sequence"]["cases"], ["SEQERR-001"])

    def test_yes_to_is_this_a_rule_confirms_it_and_a_later_answer_can_take_it_back(self):
        qs = self.after_a_run()
        apply_decisions.record(self.tmp, "s", qs["rule?"]["id"], "intended", "yes")
        report = apply_decisions.apply(self.tmp, "s")
        self.assertEqual(report["rules_confirmed"], 1)
        now = {i["id"]: i for i in self.read("model/invariants.json")["invariants"]}
        self.assertEqual((now[self.holds["id"]]["status"], now[self.holds["id"]]["decision"]), ("confirmed", "D-001"))
        apply_decisions.record(self.tmp, "s", qs["rule?"]["id"], "unsure")
        apply_decisions.apply(self.tmp, "s")
        now = {i["id"]: i for i in self.read("model/invariants.json")["invariants"]}
        self.assertEqual(now[self.holds["id"]]["status"], "candidate")
        self.assertNotIn("decision", now[self.holds["id"]])

    def test_calling_the_exceptions_not_intended_makes_it_a_rule_and_its_breaking_a_defect(self):
        qs = self.after_a_run()
        apply_decisions.record(self.tmp, "s", qs["near-rule"]["id"], "not-intended", "the total must match")
        apply_decisions.apply(self.tmp, "s")
        now = {i["id"]: i for i in self.read("model/invariants.json")["invariants"]}
        self.assertEqual(now[self.loose["id"]]["status"], "confirmed")
        qs = {q["kind"]: q for q in build_questions.build(self.tmp, "s")["questions"]}
        self.assertIn("rule-broken", qs)
        found = {f["kind"]: f for f in judge.findings(self.tmp, "s")["findings"]}
        self.assertEqual((found["rule-broken"]["class"], found["rule-broken"]["status"], found["rule-broken"]["grade"]),
                         ("defect", "reproduced", "A"))
        self.assertEqual(found["error-in-sequence"]["class"], "anomaly")

    def test_a_sequence_finding_needs_its_three_replays(self):
        by = self.rules()
        loose = by["orders.total equals the sum of lines.qty x price over its lines rows"]
        self.run_of("sequences", [self.seq_row(loose, 500, 9, 2)])
        invariants.assess(self.tmp, "s")
        build_questions.build(self.tmp, "s")
        r = judge.findings(self.tmp, "s")
        self.assertEqual(r["findings"], [])
        self.assertEqual([f["kind"] for f in r["unconfirmed"]], ["near-rule"])

    def fault_row(self, rid, differs, step="WRITE", fails="UPDATE lines"):
        return {"id": rid, "result": "MISMATCH" if differs else "MATCH",
                "observed": {"state_after_failure": "in-between" if "atomicity" in differs else "as-before"},
                "detail": {"cell": "CELL-0001", "command": "CMD-ship", "step": step, "ordinal": 1, "fails": fails, "differs": differs}}

    def test_fault_questions_group_what_was_left_behind_and_need_three_runs(self):
        rows = [self.fault_row("FAULT-0001-WRITE-1", ["atomicity"]), self.fault_row("FAULT-0001-COMMIT-1", ["atomicity"], "COMMIT", "commit"),
                self.fault_row("FAULT-0001-WRITE-2", [])]
        self.run_of("faults", rows)
        qs = [q for q in build_questions.build(self.tmp, "s")["questions"] if q["kind"] == "fault"]
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0]["cases"], ["FAULT-0001-COMMIT-1", "FAULT-0001-WRITE-1"])
        self.assertIn("the data is left in between", qs[0]["observed"])
        self.assertIn("the step: commit; UPDATE lines", qs[0]["observed"])
        self.assertEqual(len(judge.findings(self.tmp, "s")["unconfirmed"]), 1)
        self.run_of("faults", rows)
        self.run_of("faults", rows)
        f = judge.findings(self.tmp, "s")["findings"][0]
        self.assertEqual((f["kind"], f["status"], f["grade"], f["class"]), ("fault", "reproduced", "A", "anomaly"))
        # the code is corrected: the next run shows nothing
        self.run_of("faults", [self.fault_row("FAULT-0001-WRITE-1", []), self.fault_row("FAULT-0001-COMMIT-1", [], "COMMIT", "commit")])
        self.assertEqual(judge.findings(self.tmp, "s")["findings"][0]["status"], "fixed")

    def race_row(self, rid, differs, same_as="neither"):
        return {"id": rid, "result": "MISMATCH" if differs else "MATCH",
                "expected": {"first_then_second": "ALLOWED / REJECTED DUP", "second_then_first": "REJECTED DUP / ALLOWED"},
                "observed": {"first": "REJECTED CONFLICT", "second": "ALLOWED", "same_as": same_as, "rules_broken": []},
                "detail": {"a": "CMD-ship|base", "b": "CMD-ship|base", "a_command": "CMD-ship", "b_command": "CMD-ship",
                           "first": "a", "stopped_after_steps": 1, "differs": differs}}

    def test_overlapping_calls_become_a_question_a_finding_and_an_item_of_the_verdict(self):
        self.assertIn("no run with overlapping calls", self.items()["concurrency"]["detail"])
        rows = [self.race_row("RACE-01-A00", []), self.race_row("RACE-01-A01", ["answers-of-no-order"]),
                self.race_row("RACE-01-B01", ["answers-of-no-order"])]
        for _ in range(3):
            self.run_of("races", rows)
        qs = [q for q in build_questions.build(self.tmp, "s")["questions"] if q["kind"] == "race"]
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0]["cases"], ["RACE-01-A01", "RACE-01-B01"])
        self.assertIn("CMD-ship is sent twice at the same time", qs[0]["observed"])
        self.assertIn("REJECTED CONFLICT / ALLOWED", qs[0]["observed"])
        self.assertIn("Seen in 2 schedules", qs[0]["observed"])
        f = [x for x in judge.findings(self.tmp, "s")["findings"] if x["kind"] == "race"][0]
        self.assertEqual((f["status"], f["grade"], f["class"]), ("reproduced", "A", "anomaly"))
        item = self.items()["concurrency"]
        self.assertEqual(item["result"], "pass")
        self.assertIn("3 schedules of 1 pairs", item["detail"])
        self.write("profile.json", {"capabilities": {"matches_production": "unconfirmed", "database": {"real_engine": True}}})
        self.assertEqual(self.items()["concurrency"]["result"], "could-not-pass")
        self.write("profile.json", {"capabilities": {"matches_production": "confirmed", "database": {"real_engine": False}}})
        self.assertIn("stand-in", self.items()["concurrency"]["detail"])

    def test_the_canary_item(self):
        self.assertIn("no canary run", self.items()["canary"]["detail"])
        def canary(*results, **more):
            self.write("assure/canary.json", dict({"complete": True, "mutants": [
                {"id": "CAN-%03d" % i, "file": "src/A.java", "lines": "%d-%d" % (i, i), "result": r,
                 "guards": [{"command": "CMD-ship", "id": "G%d" % i}]} for i, r in enumerate(results, 1)]}, **more))
            return self.items()["canary"]
        item = canary("killed", "killed", "expected", "no-mutant")
        self.assertEqual(item["result"], "pass")
        self.assertIn("2 checks removed one at a time", item["detail"])
        self.assertIn("1 more decide no reachable case", item["detail"])
        item = canary("killed", "survived")
        self.assertEqual((item["result"], "A.java:2-2" in item["detail"]), ("fail", True))
        self.assertEqual(canary("killed", complete=False)["result"], "could-not-pass")
        self.assertEqual(canary("killed", "invalid")["result"], "could-not-pass")
        self.assertEqual(canary("no-mutant")["result"], "pass")
        # a check of another entity's command does not count here
        self.write("assure/canary.json", {"complete": True, "mutants": [
            {"id": "CAN-001", "file": "src/B.java", "lines": "1-1", "result": "survived", "guards": [{"command": "CMD-other", "id": "G1"}]}]})
        self.assertEqual(self.items()["canary"]["result"], "pass")

    def test_removing_a_throw(self):
        import canary as canary_mod
        text = "class A {\n  void a(int q) {\n    if (q < 1) {\n      throw new IllegalArgumentException(\n          \"bad\");\n    }\n    go();\n  }\n}\n"
        out = canary_mod.without_throw(text, 3, 5)
        self.assertNotIn("throw new", out)
        self.assertIn("if (q < 1) {", out)
        self.assertIn("go();", out)
        self.assertIsNone(canary_mod.without_throw(text, 7, 7))             # the cited lines hold no throw
        places = canary_mod.places(
            [{"id": "CMD-a", "guards": [{"id": "G1", "outcome": "rejected", "code": "X", "cite": "src/A.java:3-5"},
                                         {"id": "G2", "outcome": "rejected", "cite": "src/A.java:9"}]},
             {"id": "CMD-b", "guards": [{"id": "G1", "outcome": "rejected", "code": "X", "cite": "src/A.java:3-5"}]}],
            {"commands": {"CMD-a": {"guards_without_cell": ["G1", "G2"]}, "CMD-b": {"guards_without_cell": []}}})
        by = {p["first"]: p for p in places}
        self.assertEqual(len(by[3]["guards"]), 2)
        self.assertFalse(by[3]["all_never_decide"])       # CMD-b's cases can reach it
        self.assertTrue(by[9]["all_never_decide"])

    # ---- the verdict's items 3 and 4

    def items(self):
        self.write("model/reconciliation.json", dict(RECONCILIATION, cells={"CELL-0001": {"state": "agree", "run": "matrix-29990101-000001"}}))
        self.write("questions.json", {"questions": []})
        return {i["item"]: i for i in judge.verdict(self.tmp, "s")["entities"][0]["items"]}

    def test_without_runs_the_two_items_could_not_pass(self):
        items = self.items()
        self.assertEqual((items["invariants"]["result"], items["faults"]["result"]), ("could-not-pass", "could-not-pass"))
        self.assertIn("no rule about this entity has been confirmed", items["invariants"]["detail"])
        self.assertIn("no fault run", items["faults"]["detail"])

    def test_confirmed_rules_must_have_been_exercised(self):
        by = self.rules()
        holds = by["lines.shipped is never larger than lines.qty"]
        data = self.read("model/invariants.json")
        for inv in data["invariants"]:
            if inv["id"] == holds["id"]:
                inv["status"] = "confirmed"
        self.write("model/invariants.json", data)
        self.assertIn("no sequence run", self.items()["invariants"]["detail"])
        self.run_of("sequences", [self.seq_row(holds, 0, 0)])
        self.assertEqual(self.items()["invariants"]["result"], "fail")
        self.run_of("sequences", [self.seq_row(holds, 700, 0)])
        item = self.items()["invariants"]
        self.assertEqual(item["result"], "pass")
        self.assertIn("700 states", item["detail"])

    def test_every_command_that_changes_the_entity_needs_a_failure_injected(self):
        self.run_of("faults", [dict(self.fault_row("FAULT-0009-WRITE-1", []), detail={"command": "CMD-other"})])
        self.assertEqual(self.items()["faults"]["result"], "fail")
        self.run_of("faults", [self.fault_row("FAULT-0001-WRITE-1", []), self.fault_row("FAULT-0001-COMMIT-1", ["atomicity"])])
        item = self.items()["faults"]
        self.assertEqual(item["result"], "pass")              # the item is about coverage; what was found is in the findings
        self.assertIn("2 failure points injected in 1 commands", item["detail"])


if __name__ == "__main__":
    unittest.main()
