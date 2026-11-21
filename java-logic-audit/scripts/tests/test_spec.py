"""Tests for the specification trace: sections, briefs, the join, and what follows from a difference."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import apply_decisions  # noqa: E402
import build_questions  # noqa: E402
import judge  # noqa: E402
import reconcile  # noqa: E402
import spec  # noqa: E402
from test_review import System  # noqa: E402

PRD = """# Payments

Some background that requires nothing.

## Paying

An ordinary payment goes through.
A stale payment is refused with the code STALE.
A payment with an amount of zero
must be refused.
A frozen account cannot pay.

## Events

An event that arrives early is skipped.
"""


class Spec(System):
    """The small system of the review tests, with a specification that agrees in two places and differs in two."""

    def setUp(self):
        System.setUp(self)
        usual = {"stale": "false", "frozen": "false", "amount": "positive"}
        self.commands[0]["vars"] = [{"name": "stale", "kind": "state", "domain": ["false", "true"], "sql": "select stale from payment"},
                                    {"name": "frozen", "kind": "state", "domain": ["false", "true"], "sql": "select frozen from account"},
                                    {"name": "amount", "kind": "param", "domain": ["positive", "zero"],
                                     "meaning": {"zero": "the amount is 0"}}]
        self.commands[0]["base"] = dict(usual)
        self.commands[0]["title"] = "Pay"
        self.commands[1]["vars"] = [{"name": "ready", "kind": "state", "domain": ["true", "false"], "sql": "select ready from inbox"}]
        self.commands[1]["base"] = {"ready": "true"}
        self.write("model/commands.json", {"commands": self.commands})
        for c in self.cells:
            c["given"] = dict(usual) if c["command"] == "CMD-pay" else {"ready": "true"}
            if c["deviation"]:
                name, value = c["deviation"].split("=")
                c["given"][name] = value
        with open(os.path.join(self.tmp, "prd.md"), "w") as f:
            f.write(PRD)
        self.characterize()
        self.save_matrix()
        self.sections = spec.split(self.tmp, "s", "prd.md")

    def reply(self, section, clauses):
        self.write("spec/raw/clauses-%s.json" % section, {"clauses": clauses})

    def paying(self, *more):
        self.reply("SEC-02", [
            {"quote": "An ordinary payment goes through.", "lines": [7, 7], "status": "testable",
             "expectations": [{"command": "CMD-pay", "when": {}, "outcome": "allowed"}]},
            {"quote": "A stale payment is refused with the code STALE.", "lines": [8, 8], "status": "testable",
             "expectations": [{"command": "CMD-pay", "when": {"stale": "true"}, "outcome": "rejected", "code": "STALE"}]},
            {"quote": "A payment with an amount of zero must be refused.", "lines": [9, 10], "status": "testable",
             "expectations": [{"command": "CMD-pay", "when": {"amount": "zero"}, "outcome": "rejected"}]},
            {"quote": "A frozen account cannot pay.", "lines": [11, 11], "status": "testable",
             "expectations": [{"command": "CMD-pay", "when": {"frozen": "true"}, "outcome": "not-allowed"}]},
        ] + list(more))

    def by_quote(self, start):
        clause = [c for c in self.read("spec/clauses.json")["clauses"] if c["quote"].startswith(start)][0]
        return clause["id"], [x for x in self.read("spec/expectations.json")["expectations"] if x["clause"] == clause["id"]]

    def test_sections_keep_their_lines_and_the_vocabulary_says_nothing_about_behaviour(self):
        self.assertEqual([(s["id"], s["title"], s["first_line"], s["last_line"]) for s in self.sections],
                         [("SEC-01", "Payments", 1, 4), ("SEC-02", "Paying", 5, 12), ("SEC-03", "Events", 13, 16)])
        self.assertIn("9: A payment with an amount of zero", self.sections[1]["text"])
        vocabulary = json.dumps(self.read("spec/vocabulary.json"))
        for word in ("CMD-pay", "frozen", "zero", "the amount is 0", "select stale from payment"):
            self.assertIn(word, vocabulary)
        for word in ("guards", "STALE", "NOT_READY", "rejected", "A.java"):
            self.assertNotIn(word, vocabulary)

    def test_a_brief_holds_the_section_and_the_names_and_the_check_numbers_what_was_written(self):
        brief = spec.brief(self.tmp, "s", "SEC-02")
        self.assertEqual((brief["task"], brief["save_reply_as"]), ("clauses", "audit/s/spec/raw/clauses-SEC-02.json"))
        self.assertIn("11: A frozen account cannot pay.", brief["section"]["text"])
        self.paying()
        check = spec.brief(self.tmp, "s", "SEC-02", check=True)
        self.assertEqual([i["ref"] for i in check["expectations"]], ["1.1", "2.1", "3.1", "4.1"])
        self.assertEqual(check["expectations"][2]["expectation"]["when"], {"amount": "zero"})
        with self.assertRaises(SystemExit):
            spec.brief(self.tmp, "s", "SEC-03", check=True)             # nobody has read that section yet

    def test_each_expectation_is_tied_to_the_case_with_exactly_those_values(self):
        self.paying()
        out = spec.join(self.tmp, "s")
        self.assertEqual(out["expectations"], {"agrees": 2, "differs": 2})
        self.assertEqual([x["cell"] for x in self.by_quote("An ordinary")[1]], ["CELL-0001"])
        self.assertEqual([(x["cell"], x["result"]) for x in self.by_quote("A stale")[1]], [("CELL-0002", "agrees")])
        self.assertEqual([(x["cell"], x["result"], x["observed"]["outcome"]) for x in self.by_quote("A payment with")[1]],
                         [("CELL-0005", "differs", "allowed")])
        self.assertEqual([(x["cell"], x["result"]) for x in self.by_quote("A frozen")[1]], [("CELL-0003", "differs")])
        self.assertEqual(out["sections_not_read"], ["SEC-01", "SEC-03"])
        self.assertEqual(out["cases_no_clause_speaks_about"], {"CMD-onEvent": 1})
        trace = self.text("SPEC_TRACE.md")
        self.assertIn("**differs** (observed: allowed)", trace)
        self.assertIn("Sections no analyst has read yet:** SEC-01, SEC-03", trace)

    def test_a_quote_that_is_not_in_the_document_drops_the_clause(self):
        self.reply("SEC-02", [
            {"quote": "A payment above the limit must be refused.", "lines": [9, 10], "status": "testable",
             "expectations": [{"command": "CMD-pay", "when": {"amount": "zero"}, "outcome": "rejected"}]},
            {"quote": "A frozen account cannot pay.", "lines": [7, 8], "status": "testable",          # the words exist, elsewhere
             "expectations": [{"command": "CMD-pay", "when": {"frozen": "true"}, "outcome": "not-allowed"}]}])
        out = spec.join(self.tmp, "s")
        self.assertEqual((out["clauses"], len(out["dropped"]), out["expectations"]), (0, 2, {}))
        self.assertEqual(self.read("model/expected.json")["cells"]["CMD-pay|amount=zero"]["source"], "observed")

    def test_what_cannot_be_compared_is_said_and_never_guessed(self):
        self.paying(
            {"quote": "A stale payment is refused with the code STALE.", "lines": [8, 8], "status": "testable",
             "expectations": [{"command": "CMD-pay", "when": {"stale": "true", "frozen": "true"}, "outcome": "rejected"},
                              {"command": "CMD-refund", "when": {}, "outcome": "rejected"},
                              {"command": "CMD-pay", "when": {"colour": "red"}, "outcome": "rejected"}]},
            {"quote": "A frozen account cannot pay.", "lines": [11, 11], "status": "ambiguous", "note": "frozen by whom?",
             "expectations": [{"command": "CMD-pay", "when": {"frozen": "true"}, "outcome": "allowed"}]})
        spec.join(self.tmp, "s")
        results = [x["result"] for x in self.read("spec/expectations.json")["expectations"]]
        self.assertEqual(results, ["agrees", "agrees", "differs", "differs", "no-case", "not-usable", "no-case"])
        self.assertIn("ambiguous: frozen by whom?", self.text("SPEC_TRACE.md"))

    def test_a_second_reader_sets_an_expectation_aside(self):
        self.paying()
        self.write("spec/raw/check-SEC-02.json", {"verdicts": [
            {"ref": "1.1", "verdict": "faithful"}, {"ref": "2.1", "verdict": "faithful"}, {"ref": "3.1", "verdict": "faithful"},
            {"ref": "4.1", "verdict": "not-faithful", "reason": "the words do not say the account's payments are refused"}]})
        out = spec.join(self.tmp, "s")
        self.assertEqual(out["expectations"], {"agrees": 2, "differs": 1, "set-aside": 1})
        self.assertEqual(out["sections_not_checked_by_a_second_reader"], [])
        self.assertEqual(self.read("model/expected.json")["cells"]["CMD-pay|frozen=true"]["source"], "observed")

    def test_two_clauses_that_want_different_things_of_one_case_are_listed(self):
        self.paying({"quote": "An event that arrives early is skipped.", "lines": [15, 15], "status": "testable",
                     "expectations": [{"command": "CMD-pay", "when": {"amount": "zero"}, "outcome": "allowed"}]})
        out = spec.join(self.tmp, "s")
        self.assertEqual([c["cell"] for c in out["contradictions"]], ["CELL-0005"])

    def test_a_difference_is_expected_from_then_on_and_is_a_defect_resting_on_the_specification(self):
        self.paying()
        spec.join(self.tmp, "s")
        cid = self.by_quote("A payment with")[0]
        pinned = self.read("model/expected.json")["cells"]
        zero, stale, frozen = pinned["CMD-pay|amount=zero"], pinned["CMD-pay|stale=true"], pinned["CMD-pay|frozen=true"]
        self.assertEqual({k: zero[k] for k in ("outcome", "source", "clause")}, {"outcome": "rejected", "source": "spec", "clause": cid})
        self.assertEqual(zero["observed"]["written"], ["payment.amount"])            # what was seen is kept beside it
        self.assertNotIn("written", zero)                                            # what the right call writes is not known
        self.assertEqual((frozen["outcome_not"], "outcome" in frozen), ("allowed", False))
        self.assertEqual((stale["source"], stale["spec"]), ("observed", [self.by_quote("A stale")[0]]))
        self.save_matrix()
        self.run_matrix("verify")
        rec = reconcile.reconcile(self.tmp, "s", pin=True)
        self.assertEqual(sorted(x["cell"] for x in rec["against_expectation"]["against_specification"]), ["CELL-0003", "CELL-0005"])
        self.assertEqual(rec["against_expectation"]["changed_since_pinned"], [])
        self.assertEqual(self.read("model/expected.json")["cells"]["CMD-pay|amount=zero"]["source"], "spec")     # a pin never replaces it
        qs = [q for q in build_questions.build(self.tmp, "s")["questions"] if q["kind"] == "against-spec"]
        self.assertEqual(len(qs), 2)
        q = [x for x in qs if x["cells"] == ["CELL-0005"]][0]
        self.assertIn('The specification says "A payment with an amount of zero must be refused." (%s, lines 9-10)' % cid, q["observed"])
        self.assertIn("CMD-pay with amount = zero is allowed", q["observed"])
        self.assertTrue(q["flagged"])
        f = [x for x in judge.findings(self.tmp, "s")["findings"] if x["question"] == q["id"]][0]
        self.assertEqual((f["class"], f["basis"], f["status"], f["grade"]), ("defect", ["N1"], "reproduced", "A"))

    def test_a_person_can_say_the_specification_is_the_one_that_is_wrong(self):
        self.paying()
        spec.join(self.tmp, "s")
        qs = [q for q in build_questions.build(self.tmp, "s")["questions"] if q["kind"] == "against-spec"]
        q = [x for x in qs if x["cells"] == ["CELL-0005"]][0]
        other = [x for x in qs if x["cells"] == ["CELL-0003"]][0]
        d = apply_decisions.record(self.tmp, "s", q["id"], "intended", "zero-amount payments are authorisations")
        report = apply_decisions.apply(self.tmp, "s")
        self.assertEqual(report["from_specification"], 1)                           # the frozen account still follows the clause
        zero = self.read("model/expected.json")["cells"]["CMD-pay|amount=zero"]
        self.assertEqual((zero["outcome"], zero["accepted_by"], zero["written"]), ("allowed", d["id"], ["payment.amount"]))
        self.assertNotEqual(zero.get("source"), "spec")
        build_questions.build(self.tmp, "s")
        result = judge.findings(self.tmp, "s")
        self.assertEqual([a["question"] for a in result["accepted"]], [q["id"]])
        self.assertEqual([f["question"] for f in result["findings"] if f["kind"] == "against-spec"], [other["id"]])
        # and "not intended" makes it a person's decision, which then comes first
        apply_decisions.record(self.tmp, "s", other["id"], "not-intended", "a frozen account must be refused")
        apply_decisions.apply(self.tmp, "s")
        frozen = self.read("model/expected.json")["cells"]["CMD-pay|frozen=true"]
        self.assertEqual((frozen["source"], frozen["outcome_not"], frozen["observed"]["outcome"]), ("decision", "allowed", "allowed"))
        spec.join(self.tmp, "s")                                                    # joining again changes neither
        pinned = self.read("model/expected.json")["cells"]
        self.assertEqual(pinned["CMD-pay|frozen=true"]["source"], "decision")
        self.assertEqual(pinned["CMD-pay|amount=zero"]["accepted_by"], d["id"])

    def test_a_clause_that_is_withdrawn_leaves_nothing_behind(self):
        self.paying()
        spec.join(self.tmp, "s")
        before = self.read("model/expected.json")["cells"]["CMD-pay|amount=zero"]["observed"]
        self.reply("SEC-02", [])
        out = spec.join(self.tmp, "s")
        self.assertEqual(out["expectations"], {})
        pinned = self.read("model/expected.json")["cells"]
        self.assertEqual({k: v for k, v in pinned["CMD-pay|amount=zero"].items() if k != "source"}, before)
        self.assertNotIn("spec", pinned["CMD-pay|stale=true"])
        self.assertEqual([q for q in build_questions.build(self.tmp, "s")["questions"] if q["kind"] == "against-spec"], [])

    def test_when_the_code_is_changed_to_follow_the_clause_the_finding_reads_as_fixed(self):
        self.paying()
        spec.join(self.tmp, "s")
        self.save_matrix()
        self.run_matrix("verify")
        q = [x for x in build_questions.build(self.tmp, "s")["questions"] if x["kind"] == "against-spec" and x["cells"] == ["CELL-0003"]][0]
        zero = [x for x in build_questions.build(self.tmp, "s")["questions"] if x["kind"] == "against-spec" and x["cells"] == ["CELL-0005"]][0]
        for _ in range(3):
            self.run_matrix("verify", fixed=("CELL-0005",))
        by = {f["question"]: f for f in judge.findings(self.tmp, "s")["findings"]}
        self.assertEqual((by[zero["id"]]["status"], by[q["id"]]["status"]), ("fixed", "reproduced"))


if __name__ == "__main__":
    unittest.main()
