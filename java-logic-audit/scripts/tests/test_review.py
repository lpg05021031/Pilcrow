"""Tests for the chain after the matrix runs: questions, decisions, findings, verdict."""
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
import judge  # noqa: E402
import manifest  # noqa: E402
import reconcile  # noqa: E402


def seen(outcome, code=None, **more):
    o = {"outcome": outcome, "code": code, "after": {}, "written": [], "created": [], "deleted": [], "published": [],
         "external": [], "flags": []}
    o.update(more)
    return o


class Chain(unittest.TestCase):
    """One small system: an HTTP command with a refusal that writes and a case nobody decides, and a message handler."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.adir = os.path.join(self.tmp, "audit", "s")
        os.makedirs(os.path.join(self.adir, "model"))
        os.makedirs(os.path.join(self.tmp, "src", "main", "java"))
        with open(os.path.join(self.tmp, "src", "main", "java", "A.java"), "w") as f:
            f.write("class A {}\n")
        manifest.build(self.tmp, os.path.join(self.adir, "manifest.json"))
        self.commands = [
            {"id": "CMD-pay", "handler": "x.PayController#pay", "target": "payment", "binding": {"kind": "http"},
             "guards": [{"id": "G1", "outcome": "rejected", "code": "STALE", "cite": "A.java:1"},
                        {"id": "G2", "outcome": "rejected", "code": "NEVER", "cite": "A.java:2"}]},
            {"id": "CMD-onEvent", "handler": "x.Listener#on", "target": "inbox", "binding": {"kind": "bean"},
             "guards": [{"id": "G1", "outcome": "ignored", "swallowed": "NOT_READY", "cite": "A.java:3"}]},
        ]
        def cell(n, key, static, **more):
            c = {"id": "CELL-%04d" % n, "key": key, "command": key.split("|")[0],
                 "deviation": None if key.endswith("|base") else key.split("|")[1], "given": {}, "static": static,
                 "sha": "sha-%d" % n, "with": {}}
            c.update(more)
            return c
        self.cells = [
            cell(1, "CMD-pay|base", {"outcome": "allowed", "guard": None}),
            cell(2, "CMD-pay|stale=true", {"outcome": "rejected", "guard": "G1", "code": "STALE"}),
            cell(3, "CMD-pay|frozen=true", {"outcome": "allowed", "guard": None, "unhandled": True}),
            cell(4, "CMD-onEvent|ready=false", {"outcome": "ignored", "guard": "G1"}),
            cell(5, "CMD-pay|amount=zero", {"outcome": "rejected", "guard": "G9", "code": "BAD"}),
        ]
        self.write("model/commands.json", {"commands": self.commands})
        self.write("map.json", {"entries": [{"handler": "x.PayController#pay", "kind": "http"},
                                            {"handler": "x.Listener#on", "kind": "message"}]})
        self.write("profile.json", {"capabilities": {"matches_production": "confirmed"}})
        self.save_matrix()
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

    def text(self, rel):
        with open(os.path.join(self.adir, rel)) as f:
            return f.read()

    def save_matrix(self):
        pinned = (self.read("model/expected.json") if os.path.isfile(os.path.join(self.adir, "model/expected.json")) else {}).get("cells", {})
        for c in self.cells:
            if c["key"] in pinned:
                c["expected"] = pinned[c["key"]]
        self.write("model/matrix.json", {"cells": self.cells, "commands": {"CMD-pay": {"guards_without_cell": ["G2"]}}})

    def run_matrix(self, mode="record", fixed=()):
        """What the application does: fixed names the cells whose behaviour has been corrected."""
        self.n += 1
        name = "matrix-29990101-00000%d" % self.n
        behaviour = {
            "CELL-0001": seen("ALLOWED", written=["payment.status"]),
            "CELL-0002": seen("REJECTED", "STALE", written=["payment.status"], flags=["changed-although-rejected"]),
            "CELL-0003": seen("ALLOWED", written=["payment.status"]),
            "CELL-0004": seen("IGNORED"),
            "CELL-0005": seen("ALLOWED", written=["payment.amount"]),
        }
        if "CELL-0002" in fixed:
            behaviour["CELL-0002"] = seen("REJECTED", "STALE")
        rows = []
        for c in self.cells:
            o = behaviour[c["id"]]
            expected = c.get("expected") if mode == "verify" and c.get("expected") else c["static"]
            differs = []
            if "outcome" in expected and expected["outcome"].upper() != o["outcome"]:
                differs.append("outcome")
            if expected.get("outcome_not", "").upper() == o["outcome"]:
                differs.append("outcome")
            for part in ("written", "created", "deleted"):
                if part in expected and expected[part] != o[part]:
                    differs.append(part)
            rows.append({"id": c["id"], "result": "MISMATCH" if differs else "MATCH", "expected": expected, "observed": o,
                         "detail": {"command": c["command"], "deviation": c["deviation"], "key": c["key"], "sha": c["sha"],
                                    "differs": differs}})
        raw = "".join(json.dumps(r) + "\n" for r in rows).encode("utf-8")
        d = os.path.join(self.adir, "runs", name)
        os.makedirs(os.path.join(d, "evidence"))
        with open(os.path.join(d, "results.jsonl"), "wb") as f:
            f.write(raw)
        self.write("runs/%s/run.json" % name, {"kind": "matrix", "mode": mode, "total": len(rows),
                                               "results_sha256": hashlib.sha256(raw).hexdigest()})
        for c in self.cells:
            self.write("runs/%s/evidence/%s.json" % (name, c["id"]),
                       {"database_engine": "PostgreSQL 16.4", "time_controlled": True, "state_built_by_commands_only": True,
                        "effects": [], "changes": []})

    def characterize(self):
        self.run_matrix("record")
        reconcile.reconcile(self.tmp, "s", pin=True)
        self.save_matrix()
        self.run_matrix("verify")
        self.run_matrix("verify")
        reconcile.reconcile(self.tmp, "s", pin=True)
        return build_questions.build(self.tmp, "s")["questions"]

    def test_each_kind_of_question_comes_from_its_rule(self):
        qs = {q["kind"]: q for q in self.characterize()}
        self.assertEqual(sorted(qs), ["differs", "dropped", "never-decides", "refused-but-changed", "undecided"])
        self.assertEqual(qs["refused-but-changed"]["cells"], ["CELL-0002"])
        self.assertEqual(qs["refused-but-changed"]["if_not_intended"], {"written": [], "created": [], "deleted": []})
        self.assertEqual(qs["refused-but-changed"]["cites"], ["A.java:1"])
        self.assertEqual(qs["undecided"]["if_not_intended"], {"outcome": "rejected"})
        self.assertEqual(qs["differs"]["if_not_intended"], {"outcome": "rejected", "code": "BAD"})
        self.assertIn("NOT_READY", qs["dropped"]["observed"])
        self.assertFalse(qs["never-decides"]["flagged"])
        self.assertTrue(all(q["status"] == "open" for q in qs.values()))
        self.assertIn("## Q-001", self.text("QUESTIONS.md"))

    def test_a_question_keeps_its_number_and_remembers_its_answer(self):
        before = {q["fingerprint"]: q["id"] for q in self.characterize()}
        q = [x for x in self.read("questions.json")["questions"] if x["kind"] == "dropped"][0]
        apply_decisions.record(self.tmp, "s", q["id"], "intended", "late events are expected")
        after = build_questions.build(self.tmp, "s")["questions"]
        self.assertEqual({x["fingerprint"]: x["id"] for x in after}, before)
        answered = [x for x in after if x["id"] == q["id"]][0]
        self.assertEqual((answered["status"], answered["answer"], answered["decision"]), ("answered", "intended", "D-001"))

    def test_unanswered_flagged_questions_are_findings_with_a_grade(self):
        self.characterize()
        r = judge.findings(self.tmp, "s")
        by = {f["kind"]: f for f in r["findings"]}
        self.assertEqual(sorted(by), ["differs", "dropped", "refused-but-changed", "undecided"])
        self.assertEqual(by["refused-but-changed"]["class"], "anomaly")
        self.assertEqual(by["differs"]["class"], "inconsistency")
        self.assertTrue(all(f["status"] == "reproduced" and f["grade"] == "A" for f in r["findings"]))
        self.assertEqual(by["dropped"]["anchor"], "x.Listener#on")

    def test_two_runs_are_not_enough_to_call_it_reproduced(self):
        self.run_matrix("record")
        reconcile.reconcile(self.tmp, "s", pin=True)
        build_questions.build(self.tmp, "s")
        r = judge.findings(self.tmp, "s")
        self.assertEqual(r["findings"], [])
        self.assertEqual(len(r["unconfirmed"]), 4)

    def test_a_reservation_lowers_the_grade(self):
        self.write("profile.json", {"capabilities": {"matches_production": "unconfirmed"}})
        self.characterize()
        f = judge.findings(self.tmp, "s")["findings"][0]
        self.assertEqual((f["grade"], f["reservations"]), ("B", ["unconfirmed-version"]))

    def test_not_intended_becomes_a_defect_and_then_fixed(self):
        qs = {q["kind"]: q for q in self.characterize()}
        d = apply_decisions.record(self.tmp, "s", qs["refused-but-changed"]["id"], "not-intended", "a refusal must change nothing")
        report = apply_decisions.apply(self.tmp, "s")
        self.assertEqual(report["cells_changed"], 1)
        pinned = self.read("model/expected.json")["cells"]["CMD-pay|stale=true"]
        self.assertEqual((pinned["source"], pinned["decision"], pinned["written"]), ("decision", d["id"], []))
        self.assertEqual(pinned["outcome"], "rejected")                        # the rest of the observation still holds
        self.assertEqual(pinned["observed"]["written"], ["payment.status"])
        self.save_matrix()
        self.run_matrix("verify")
        build_questions.build(self.tmp, "s")
        f = [x for x in judge.findings(self.tmp, "s")["findings"] if x["kind"] == "refused-but-changed"][0]
        self.assertEqual((f["class"], f["basis"], f["status"], f["grade"]), ("defect", ["N4", "N2"], "reproduced", "A"))
        # the code is corrected: three runs later the finding reads as fixed
        for _ in range(3):
            self.run_matrix("verify", fixed=("CELL-0002",))
        f = [x for x in judge.findings(self.tmp, "s")["findings"] if x["kind"] == "refused-but-changed"][0]
        self.assertEqual(f["status"], "fixed")

    def test_intended_is_listed_as_accepted_and_is_no_finding(self):
        qs = {q["kind"]: q for q in self.characterize()}
        apply_decisions.record(self.tmp, "s", qs["undecided"]["id"], "intended", "frozen accounts may still pay")
        apply_decisions.apply(self.tmp, "s")
        self.assertEqual(self.read("model/expected.json")["cells"]["CMD-pay|frozen=true"]["accepted_by"], "D-001")
        build_questions.build(self.tmp, "s")
        r = judge.findings(self.tmp, "s")
        self.assertNotIn("undecided", [f["kind"] for f in r["findings"]])
        self.assertEqual(r["accepted"][0]["note"], "frozen accounts may still pay")

    def test_a_later_answer_supersedes_and_leaves_nothing_behind(self):
        qs = {q["kind"]: q for q in self.characterize()}
        qid = qs["refused-but-changed"]["id"]
        apply_decisions.record(self.tmp, "s", qid, "not-intended")
        apply_decisions.apply(self.tmp, "s")
        second = apply_decisions.record(self.tmp, "s", qid, "unsure", "asking the product owner")
        self.assertEqual(second["supersedes"], "D-001")
        report = apply_decisions.apply(self.tmp, "s")
        self.assertEqual((report["not-intended"], report["unsure"], report["cells_changed"]), (0, 1, 0))
        pinned = self.read("model/expected.json")["cells"]["CMD-pay|stale=true"]
        self.assertEqual(pinned["written"], ["payment.status"])
        self.assertNotIn("decision", pinned)
        self.assertEqual(len(self.read("DECISIONS.json")["decisions"]), 2)
        self.assertIn("(superseded)", self.text("DECISIONS.md"))

    def test_an_answer_needs_a_known_question(self):
        self.characterize()
        with self.assertRaises(SystemExit):
            apply_decisions.record(self.tmp, "s", "Q-999", "intended")

    def test_verdict_per_entity(self):
        self.characterize()
        r = judge.verdict(self.tmp, "s")
        by = {e["entity"]: e for e in r["entities"]}
        self.assertEqual(sorted(by), ["inbox", "payment"])
        items = {i["item"]: i["result"] for i in by["payment"]["items"]}
        self.assertEqual(items["matrix"], "pass")
        self.assertEqual(items["reconciled"], "fail")                 # a difference and an undecided case are open
        self.assertEqual(items["source"], "pass")
        self.assertEqual(items["faults"], "could-not-pass")
        self.assertEqual(by["payment"]["verdict"], "insufficient")
        self.assertEqual(by["inbox"]["verdict"], "partial")
        self.assertEqual(len(r["waiting_for_people"]["open_flagged_questions"]), 4)
        self.assertIn("## The rules", self.text("VERDICT.md"))

    def test_verdict_fails_when_the_source_changed_or_a_cell_has_no_result(self):
        self.characterize()
        with open(os.path.join(self.tmp, "src", "main", "java", "A.java"), "a") as f:
            f.write("// edited\n")
        self.cells[3]["sha"] = "another"
        self.save_matrix()
        reconcile.reconcile(self.tmp, "s")
        items = {i["item"]: i for i in {e["entity"]: e for e in judge.verdict(self.tmp, "s")["entities"]}["inbox"]["items"]}
        self.assertEqual(items["source"]["result"], "fail")
        self.assertEqual(items["matrix"]["result"], "fail")


if __name__ == "__main__":
    unittest.main()
