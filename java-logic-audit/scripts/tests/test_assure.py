"""Tests for the checks of the audit's own work: the brief for a reader, what a reply does to a finding, seeded defects."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import assure  # noqa: E402
import judge  # noqa: E402
import seed_run  # noqa: E402
from test_review import System  # noqa: E402


class Recheck(System):

    def setUp(self):
        System.setUp(self)
        self.characterize()
        self.run_matrix("verify")
        self.findings = {f["kind"]: f for f in judge.findings(self.tmp, "s")["findings"]}
        self.f = self.findings["refused-but-changed"]

    def reply(self, verdict, **more):
        body = {"finding": self.f["id"], "fingerprint": self.f["fingerprint"], "verdict": verdict, "reason": "because",
                "evidence_shows_it": "yes", "state_by_commands_only": "yes", "cited_code": "says-it", "signs_of_intent": []}
        body.update(more)
        self.write("assure/recheck/%s.json" % self.f["id"], body)
        return [f for f in judge.findings(self.tmp, "s")["findings"] if f["id"] == self.f["id"]][0]

    def test_the_brief_says_where_the_evidence_is_and_not_what_to_conclude(self):
        brief = assure.brief(self.tmp, "s", self.f["id"])
        self.assertEqual(brief["finding"], {"id": self.f["id"], "fingerprint": self.f["fingerprint"], "statement": self.f["statement"]})
        case = brief["cases"][0]
        self.assertEqual((case["case"], case["command"], case["situation"]), ("CELL-0002", "CMD-pay", "stale=true"))
        self.assertTrue(case["result_line"].startswith("audit/s/runs/matrix-29990101-000004/results.jsonl"))
        self.assertEqual(case["evidence_file"], "audit/s/runs/matrix-29990101-000004/evidence/CELL-0002.json")
        self.assertEqual(brief["cited_code"], ["A.java:1"])
        self.assertEqual(brief["save_reply_as"], "audit/s/assure/recheck/%s.json" % self.f["id"])
        told = json.dumps({k: v for k, v in brief.items() if k != "reply"})
        for word in ("anomaly", "defect", "grade", "N4", "Whoever is told", "decision"):
            self.assertNotIn(word, told)
        with self.assertRaises(SystemExit):
            assure.brief(self.tmp, "s", "F-999")

    def test_a_reader_who_agrees_changes_nothing_but_is_shown(self):
        f = self.reply("stands", signs_of_intent=[{"where": "src/test/java/PayTest.java:40-52", "what": "a test expects the status to change"}])
        self.assertEqual((f["status"], f["grade"], f["reservations"]), ("reproduced", "A", []))
        self.assertEqual(f["recheck"]["verdict"], "stands")
        text = self.text("FINDINGS.md")
        self.assertIn("- **Independent recheck:** stands — because", text)
        self.assertIn("a sign that it may be intended: `src/test/java/PayTest.java:40-52`", text)

    def test_a_reader_who_disagrees_lowers_the_grade_and_removes_nothing(self):
        f = self.reply("refuted", reason="the row that changed belongs to another payment")
        self.assertEqual((f["status"], f["grade"], f["reservations"]), ("reproduced", "B", ["recheck-refuted"]))
        f = self.reply("weakened")
        self.assertEqual((f["grade"], f["reservations"]), ("B", ["recheck-weakened"]))

    def test_a_reply_about_an_older_form_of_the_finding_or_without_a_verdict_is_not_used(self):
        self.assertNotIn("recheck", self.reply("refuted", fingerprint="0000"))
        self.assertNotIn("recheck", self.reply("probably fine"))

    def test_the_summary_is_written_from_what_exists(self):
        self.reply("weakened", reason="the state was written by hand")
        self.write("assure/canary.json", {"complete": True, "counts": {"killed": 1, "survived": 1}, "mutants": [
            {"id": "CAN-001", "file": "src/A.java", "lines": "1-1", "result": "killed", "guards": [{"command": "CMD-pay", "id": "G1", "code": "STALE"}]},
            {"id": "CAN-002", "file": "src/A.java", "lines": "2-2", "result": "survived", "guards": [{"command": "CMD-pay", "id": "G2", "code": "NEVER"}]}]})
        out = assure.summary(self.tmp, "s")
        self.assertEqual((out["rechecked"], out["verdicts"], out["seeds"]), (1, {"weakened": 1}, None))
        text = self.text("ASSURANCE.md")
        self.assertIn("| %s | **weakened** | yes | yes | says-it | the state was written by hand |" % self.f["id"], text)
        self.assertIn("not rechecked", text)
        self.assertIn("- `src/A.java:2-2` (CMD-pay NEVER)", text)
        self.assertIn("Not run: no defects were given to seed.", text)


class Seeds(unittest.TestCase):

    def test_a_defect_is_one_exact_edit_and_anything_else_stops(self):
        import tempfile
        import shutil
        tmp = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmp, "src"))
            for name, text in (("A.java", "if (qty < 1) { refuse(); }\n"), ("B.java", "x = 1;\nx = 1;\n")):
                with open(os.path.join(tmp, "src", name), "w") as f:
                    f.write(text)
            seed_run.apply({"id": "d1", "file": "src/A.java", "find": "qty < 1", "replace": "qty < 0",
                            "also": [{"find": "refuse()", "replace": "warn()"}]}, tmp)
            with open(os.path.join(tmp, "src", "A.java")) as f:
                self.assertEqual(f.read(), "if (qty < 0) { warn(); }\n")
            with self.assertRaises(SystemExit):
                seed_run.apply({"id": "d2", "file": "src/B.java", "find": "x = 1;", "replace": "x = 2;"}, tmp)      # twice in the file
            with self.assertRaises(SystemExit):
                seed_run.apply({"id": "d3", "file": "../outside.java", "find": "a", "replace": "b"}, tmp)
        finally:
            shutil.rmtree(tmp)

    def test_the_summary_counts_what_was_noticed_by_something_other_than_a_pin(self):
        results = [{"category": "guard", "detected": True, "routes": ["pins"]},
                   {"category": "guard", "detected": True, "routes": ["pins", "rules"]},
                   {"category": "race", "detected": False, "routes": []}]
        s = seed_run.summarize(results)
        self.assertEqual((s["defects"], s["noticed"], s["noticed_without_pins"]), (3, 2, 1))
        self.assertEqual(s["by_category"]["guard"], {"defects": 2, "noticed": 2, "without_pins": 1})


if __name__ == "__main__":
    unittest.main()
