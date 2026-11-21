"""Tests for reconcile.py: which results count, how cells are classified, what is pinned."""
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
import reconcile  # noqa: E402


def cell(n, key, static, **more):
    c = {"id": "CELL-%04d" % n, "key": key, "command": key.split("|")[0], "deviation": None if key.endswith("|base") else key.split("|")[1],
         "given": {}, "static": static, "sha": "sha-%d" % n}
    c.update(more)
    return c


def row(c, result, observed=None, why=None, differs=None, sha=None):
    detail = {"command": c["command"], "deviation": c["deviation"], "key": c["key"], "sha": sha or c["sha"]}
    if why:
        detail["why"] = why
        detail["reason"] = why
    if differs:
        detail["differs"] = differs
    out = {"id": c["id"], "result": result, "expected": c["static"], "detail": detail}
    if observed is not None:
        out["observed"] = observed
    return out


def seen(outcome, code=None, **more):
    o = {"outcome": outcome, "code": code, "after": {}, "written": [], "created": [], "deleted": [], "published": [],
         "external": [], "flags": []}
    o.update(more)
    return o


class Reconcile(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.adir = os.path.join(self.tmp, "audit", "s")
        os.makedirs(os.path.join(self.adir, "model"))
        self.cells = [
            cell(1, "CMD-a|base", {"outcome": "allowed", "guard": None}, **{"with": {}}),
            cell(2, "CMD-a|status=DONE", {"outcome": "rejected", "guard": "G1", "code": "NOT_OPEN"}, **{"with": {}}),
            cell(3, "CMD-a|status=null", {"outcome": "rejected", "guard": "G2"}, unreachable="nothing deletes it"),
            cell(4, "CMD-a|flag=true", {"outcome": "allowed", "guard": None, "unhandled": True}),
        ]
        self.write("model/matrix.json", {"cells": self.cells,
                                         "commands": {"CMD-a": {"guards_without_cell": ["G2"]}}})
        self.write("model/commands.json", {"commands": [{"id": "CMD-a", "guards": [
            {"id": "G1", "outcome": "rejected", "code": "NOT_OPEN", "cite": "A.java:1"},
            {"id": "G2", "outcome": "rejected", "code": "NOT_FOUND", "cite": "A.java:2"}]}]})

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

    def run_dir(self, name, rows, mode="record", evidence=None, tamper=False):
        d = os.path.join(self.adir, "runs", name)
        os.makedirs(os.path.join(d, "evidence"))
        raw = "".join(json.dumps(r) + "\n" for r in rows).encode("utf-8")
        with open(os.path.join(d, "results.jsonl"), "wb") as f:
            f.write(raw + (b"\n" if tamper else b""))
        self.write("runs/%s/run.json" % name, {"kind": "matrix", "mode": mode, "total": len(rows),
                                               "results_sha256": hashlib.sha256(raw).hexdigest()})
        for cell_id, body in (evidence or {}).items():
            self.write("runs/%s/evidence/%s.json" % (name, cell_id), body)

    def first_run(self, **more):
        c = self.cells
        self.run_dir("matrix-1", [
            row(c[0], "MATCH", seen("ALLOWED", written=["t.status"])),
            row(c[1], "MISMATCH", seen("ALLOWED"), differs=["outcome"]),
            row(c[2], "SKIPPED", why="unreachable"),
            row(c[3], "SKIPPED", why="no-recipe"),
        ], **more)

    def test_cells_are_classified_from_the_latest_record_run(self):
        self.first_run()
        out = reconcile.reconcile(self.tmp, "s")
        self.assertEqual(out["counts"], {"agree": 1, "differ": 1, "no-recipe": 1, "unreachable": 1, "total": 4})
        self.assertEqual(out["differences"][0]["cell"], "CELL-0002")
        self.assertEqual(out["differences"][0]["differs"], ["outcome"])
        self.assertEqual([n["state"] for n in out["not_executed"]], ["unreachable", "no-recipe"])
        self.assertEqual(out["not_executed"][0]["reason"], "nothing deletes it")
        self.assertEqual(out["guards_without_cell"], [{"command": "CMD-a", "guard": "G2", "outcome": "rejected",
                                                       "code": "NOT_FOUND", "writes": None, "cite": "A.java:2"}])
        self.assertEqual(self.read("model/reconciliation.json")["counts"]["total"], 4)

    def test_a_result_for_an_older_definition_of_the_cell_does_not_count(self):
        c = self.cells
        self.run_dir("matrix-1", [row(c[0], "MATCH", seen("ALLOWED"), sha="older")])
        out = reconcile.reconcile(self.tmp, "s")
        self.assertEqual(out["cells"]["CELL-0001"]["state"], "not-run")

    def test_a_run_whose_files_disagree_is_set_aside(self):
        self.first_run(tamper=True)
        out = reconcile.reconcile(self.tmp, "s")
        self.assertEqual(out["runs"], [])
        self.assertEqual(out["runs_set_aside"][0]["run"], "matrix-1")
        self.assertEqual(out["counts"]["not-run"], 2)

    def test_flags_and_cells_no_guard_decides_are_listed(self):
        c = self.cells
        c[3]["with"] = {}
        self.write("model/matrix.json", {"cells": c, "commands": {}})
        self.run_dir("matrix-1", [
            row(c[1], "MATCH", seen("REJECTED", "NOT_OPEN", written=["t.status"], flags=["changed-although-rejected"])),
            row(c[3], "MATCH", seen("ALLOWED")),
        ])
        out = reconcile.reconcile(self.tmp, "s")
        self.assertEqual(out["flags"][0]["flag"], "changed-although-rejected")
        self.assertEqual(out["flags"][0]["written"], ["t.status"])
        self.assertEqual(out["unhandled"][0]["cell"], "CELL-0004")

    def test_pinning_takes_what_was_observed(self):
        self.first_run()
        out = reconcile.reconcile(self.tmp, "s", pin=True)
        self.assertEqual(out["pin"]["new"], 2)
        pinned = self.read("model/expected.json")["cells"]
        self.assertEqual(sorted(pinned), ["CMD-a|base", "CMD-a|status=DONE"])
        self.assertEqual(pinned["CMD-a|base"]["outcome"], "allowed")
        self.assertEqual(pinned["CMD-a|base"]["written"], ["t.status"])
        self.assertEqual(pinned["CMD-a|base"]["source"], "observed")
        again = reconcile.reconcile(self.tmp, "s", pin=True)
        self.assertEqual((again["pin"]["new"], again["pin"]["same"]), (0, 2))

    def test_a_part_that_differs_between_runs_is_unstable_and_left_out_of_the_pin(self):
        c = self.cells
        self.run_dir("matrix-1", [row(c[0], "MATCH", seen("ALLOWED", published=["send a"]))])
        self.run_dir("matrix-2", [row(c[0], "MISMATCH", seen("ALLOWED", published=["send b"]), differs=["published"])], mode="verify")
        out = reconcile.reconcile(self.tmp, "s", pin=True)
        self.assertEqual(out["unstable"][0]["parts"], ["published"])
        self.assertEqual(out["cells"]["CELL-0001"]["state"], "agree")        # the record run is what is compared with the code
        pinned = self.read("model/expected.json")["cells"]["CMD-a|base"]
        self.assertNotIn("published", pinned)
        self.assertEqual(pinned["left_out"], ["published"])

    def test_a_different_observation_replaces_a_pin_only_when_asked(self):
        c = self.cells
        self.run_dir("matrix-1", [row(c[0], "MATCH", seen("ALLOWED", written=["t.a"]))])
        reconcile.reconcile(self.tmp, "s", pin=True)
        c[0]["sha"] = "sha-new"
        self.write("model/matrix.json", {"cells": c, "commands": {}})
        self.run_dir("matrix-2", [row(c[0], "MATCH", seen("ALLOWED", written=["t.a", "t.b"]))])
        out = reconcile.reconcile(self.tmp, "s", pin=True)
        self.assertEqual(out["pin"]["changed"], [{"cell": "CELL-0001", "key": "CMD-a|base", "parts": ["written"]}])
        self.assertEqual(self.read("model/expected.json")["cells"]["CMD-a|base"]["written"], ["t.a"])
        reconcile.reconcile(self.tmp, "s", repin=True)
        self.assertEqual(self.read("model/expected.json")["cells"]["CMD-a|base"]["written"], ["t.a", "t.b"])

    def test_what_the_caller_was_answered_is_pinned_too(self):
        c = self.cells
        self.run_dir("matrix-1", [row(c[0], "MATCH", seen("ALLOWED", response_sha="aaaa")),
                                  row(c[1], "MATCH", seen("REJECTED", response_sha=None))])
        reconcile.reconcile(self.tmp, "s", pin=True)
        pinned = self.read("model/expected.json")["cells"]
        self.assertEqual(pinned["CMD-a|base"]["response_sha"], "aaaa")
        self.assertIsNone(pinned["CMD-a|status=DONE"]["response_sha"])       # it answered nothing, and that is pinned

    def test_a_run_from_before_answers_were_recorded_is_not_read_as_a_different_answer(self):
        c = self.cells
        older = seen("ALLOWED")
        self.assertNotIn("response_sha", older)
        self.run_dir("matrix-1", [row(c[0], "MATCH", older)])
        self.run_dir("matrix-2", [row(c[0], "MATCH", seen("ALLOWED", response_sha="aaaa"))])
        self.run_dir("matrix-3", [row(c[0], "MATCH", seen("ALLOWED", response_sha="aaaa"))], mode="verify")
        out = reconcile.reconcile(self.tmp, "s", repin=True)
        self.assertEqual(out["unstable"], [])
        self.assertEqual(self.read("model/expected.json")["cells"]["CMD-a|base"]["response_sha"], "aaaa")
        self.run_dir("matrix-4", [row(c[0], "MISMATCH", seen("ALLOWED", response_sha="bbbb"), differs=["response_sha"])], mode="verify")
        self.assertEqual(reconcile.reconcile(self.tmp, "s")["unstable"][0]["parts"], ["response_sha"])

    def test_a_pin_for_a_case_that_is_no_longer_executed_is_dropped(self):
        self.first_run()
        reconcile.reconcile(self.tmp, "s", pin=True)
        self.cells[0]["unreachable"] = "found to be impossible"
        self.write("model/matrix.json", {"cells": self.cells, "commands": {}})
        out = reconcile.reconcile(self.tmp, "s", pin=True)
        self.assertEqual(out["pin"]["dropped"], 1)
        self.assertEqual(sorted(self.read("model/expected.json")["cells"]), ["CMD-a|status=DONE"])

    def test_a_decision_is_never_replaced_by_an_observation(self):
        self.write("model/expected.json", {"cells": {"CMD-a|base": {"outcome": "rejected", "source": "decision", "decision": "D-001"}}})
        self.first_run()
        out = reconcile.reconcile(self.tmp, "s", repin=True)
        self.assertEqual(out["pin"]["kept"], 1)
        self.assertEqual(self.read("model/expected.json")["cells"]["CMD-a|base"]["outcome"], "rejected")

    def test_changes_of_lifecycle_columns_are_collected_from_the_evidence(self):
        self.write("map.json", {
            "entities": [{"id": "ENT-t", "table": "t", "columns": [{"name": "status", "property": "state"}]}],
            "lifecycle_writers": [{"entity": "ENT-t", "column": "state"}]})
        self.first_run(evidence={"CELL-0001": {"changes": [
            {"table": "t", "row": 0, "change": "modified", "column": "status", "before": "OPEN", "after": "DONE"},
            {"table": "t", "row": 0, "change": "modified", "column": "qty", "before": "1", "after": "2"},
            {"table": "t", "row": 1, "change": "added", "values": {"id": "2", "status": "OPEN"}}]}})
        life = reconcile.reconcile(self.tmp, "s")["lifecycle"]
        self.assertEqual(life["transitions"], [{"column": "t.status", "from": "OPEN", "to": "DONE",
                                                "commands": ["CMD-a"], "cells": ["CELL-0001"]}])
        self.assertEqual(life["created_with"], [{"column": "t.status", "value": "OPEN", "commands": ["CMD-a"]}])


if __name__ == "__main__":
    unittest.main()
