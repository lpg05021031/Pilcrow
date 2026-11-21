"""Tests for the M1 scripts: harness_install and judge.

    python3 -m unittest discover -s java-logic-audit/scripts/tests -v
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(SCRIPTS))
FIXTURES = os.path.join(REPO, "fixtures")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)

import harness_install  # noqa: E402
import judge  # noqa: E402
import manifest  # noqa: E402
import preflight_scan  # noqa: E402

INTENT = ("---\nsystem: orders\nmodule: .\ngoal: audit\nlanguage: auto\nbudget: quick\n"
          "base_commit: none\ndate: 2026-11-21\n---\n")


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class Workspace(unittest.TestCase):
    """A copy of sample 1 with the files preflight would have written."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jla-test-")
        self.ws = os.path.join(self.tmp, "orders")
        shutil.copytree(os.path.join(FIXTURES, "orders-jpa"), self.ws,
                        ignore=shutil.ignore_patterns("target", "build", ".gradle"))
        self.adir = os.path.join(self.ws, "audit", "orders")
        write(self.adir, "INTENT.md", INTENT)
        profile = preflight_scan.scan(self.ws, env=False)
        write(self.adir, "profile.json", json.dumps(profile))
        manifest.build(self.ws, os.path.join(self.adir, "manifest.json"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class Install(Workspace):
    def test_plan_follows_from_the_profile(self):
        p = harness_install.plan(self.ws, "orders")
        self.assertEqual(["core", "spring", "spring-restclient"], p["modules"])
        self.assertEqual(["net.ttddyy:datasource-proxy", "com.tngtech.archunit:archunit"],
                         [d["ga"] for d in p["dependencies"]])      # JUnit 5 and Mockito are already there
        self.assertEqual("./pom-audit.xml".lstrip("./"), p["build_entry"].lstrip("./"))

    def test_nothing_that_exists_is_edited(self):
        harness_install.copy(self.ws, "orders")
        harness_install.build_entry(self.ws, "orders")
        harness_install.settings(self.ws, "orders")
        harness_install.command(self.ws, "orders", "smoke")
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "src/audit/java/auditkit/AuditConfig.java")))
        self.assertTrue(os.path.isfile(os.path.join(self.ws, "src/audit/java/auditkit/spring/SmokeRunner.java")))
        self.assertEqual("untouched", manifest.check(self.ws, os.path.join(self.adir, "manifest.json"))["status"])

    def test_the_generated_pom_is_the_original_plus_one_profile(self):
        harness_install.build_entry(self.ws, "orders")
        with open(os.path.join(self.ws, "pom.xml"), encoding="utf-8") as fh:
            original = fh.read()
        with open(os.path.join(self.ws, "pom-audit.xml"), encoding="utf-8") as fh:
            generated = fh.read()
        self.assertTrue(generated.startswith("<?xml"))
        root = ET.fromstring(generated)                         # well-formed
        ns = {"m": "http://maven.apache.org/POM/4.0.0"}
        self.assertEqual(["audit"], [e.text for e in root.findall("m:profiles/m:profile/m:id", ns)])
        self.assertEqual(["src/audit/java"], [e.text for e in root.iter("{http://maven.apache.org/POM/4.0.0}source")])
        # Taking the added parts out again gives back the original text exactly.
        stripped = generated.replace(harness_install.HEADER, "")
        a, b = stripped.index("  <profiles>\n"), stripped.index("  </profiles>\n") + len("  </profiles>\n")
        self.assertEqual(original, stripped[:a] + stripped[b:])

    def test_an_existing_profiles_section_is_extended_not_duplicated(self):
        pom = os.path.join(self.ws, "pom.xml")
        with open(pom, encoding="utf-8") as fh:
            text = fh.read()
        with open(pom, "w", encoding="utf-8") as fh:
            fh.write(text.replace("</project>", "  <profiles>\n    <profile><id>ci</id></profile>\n  </profiles>\n</project>"))
        harness_install.build_entry(self.ws, "orders")
        with open(os.path.join(self.ws, "pom-audit.xml"), encoding="utf-8") as fh:
            root = ET.fromstring(fh.read())
        ns = {"m": "http://maven.apache.org/POM/4.0.0"}
        self.assertEqual(1, len(root.findall("m:profiles", ns)))
        self.assertEqual(["ci", "audit"], [e.text for e in root.findall("m:profiles/m:profile/m:id", ns)])

    def test_settings_draft_never_overwrites(self):
        first = harness_install.settings(self.ws, "orders")
        self.assertTrue(first["written"])
        with open(os.path.join(self.adir, "harness.json"), encoding="utf-8") as fh:
            draft = json.load(fh)
        self.assertEqual("com.example.orders.OrdersApplication", draft["application"])
        self.assertEqual([{"type": "io.nats.client.Connection", "kind": "messaging"}], draft["doubles"])
        self.assertTrue(any(t.startswith("http_doubles") for t in draft["todo"]))
        write(self.adir, "harness.json", json.dumps({"application": "kept", "todo": []}))
        self.assertFalse(harness_install.settings(self.ws, "orders")["written"])
        with open(os.path.join(self.adir, "harness.json"), encoding="utf-8") as fh:
            self.assertEqual("kept", json.load(fh)["application"])

    def test_command_uses_the_generated_pom_and_carries_no_secret(self):
        cmd = harness_install.command(self.ws, "orders", "smoke")
        self.assertIn("-f pom-audit.xml -Paudit test", cmd)
        self.assertIn("-Daudit.run=smoke", cmd)
        self.assertIn("-Daudit.dir=" + os.path.abspath(self.adir), cmd)
        self.assertNotIn("password", cmd.lower())
        with self.assertRaises(SystemExit):
            harness_install.command(self.ws, "orders", "anything-else")


class Capabilities(Workspace):
    ENGINE = {"product": "PostgreSQL", "version": "16.15", "major": 16, "minor": 15,
              "default_isolation": "READ_COMMITTED"}

    def smoke_run(self, results, tamper=False):
        run = os.path.join(self.adir, "runs", "smoke-20261121-000000")
        lines = "".join(json.dumps(r) + "\n" for r in results)
        write(run, "results.jsonl", lines)
        digest = hashlib.sha256(lines.encode("utf-8")).hexdigest()
        write(run, "run.json", json.dumps({"kind": "smoke", "total": len(results), "results_sha256": digest}))
        if tamper:
            write(run, "results.jsonl", lines.replace("MISMATCH", "MATCH"))

    def rows(self, engine=None, failing=()):
        engine = engine or self.ENGINE
        details = {
            "SMOKE-1": {}, "SMOKE-2": {"host": "127.0.0.1", "engine": engine, "source": "offered"}, "SMOKE-3": {},
            "SMOKE-4": {}, "SMOKE-5": {"doubles": [{"type": "io.nats.client.Connection", "edge": True},
                                                   {"type": "com.example.orders.pricing.PricingClient", "edge": False}],
                                        "http_restclient": True, "http_rules": 1}, "SMOKE-6": {}}
        return [{"id": k, "result": "MISMATCH" if k in failing else "MATCH", "detail": v} for k, v in details.items()]

    def answer(self, text):
        write(self.adir, "answers.json", json.dumps({"asked": "2026-11-21", "answers": {
            "scope": None, "production_database": text, "build_and_test": None, "off_limits": None, "focus": None}}))

    def test_everything_works_and_production_matches(self):
        self.smoke_run(self.rows())
        self.answer("Production runs PostgreSQL 16 at READ COMMITTED.")
        write(self.adir, "facts/context.json", json.dumps({"clock_bean": True}))
        c = judge.capabilities(self.ws, "orders")
        self.assertIsNone(c["problem"])
        self.assertEqual("confirmed", c["matches_production"])
        self.assertEqual({"matrix": "A", "effects": "A", "faults": "A", "concurrency": "A",
                          "time_dependent_rules": "A"}, c["ceilings"])
        self.assertEqual(["com.example.orders.pricing.PricingClient"], c["doubles"]["inner"])
        with open(os.path.join(self.adir, "profile.json"), encoding="utf-8") as fh:
            self.assertEqual("confirmed", json.load(fh)["capabilities"]["matches_production"])

    def test_an_open_or_different_answer_lowers_the_ceiling(self):
        self.smoke_run(self.rows())
        c = judge.capabilities(self.ws, "orders")                     # nobody said what production runs
        self.assertEqual(("unconfirmed", "B", "B"), (c["matches_production"], c["ceilings"]["matrix"], c["ceilings"]["concurrency"]))
        self.assertEqual("B", c["ceilings"]["time_dependent_rules"])  # and no Clock bean was reported
        for text, verdict in (("MySQL 8.0, repeatable read", "mismatch"), ("Postgres 14 read committed", "mismatch"),
                              ("PostgreSQL 16, REPEATABLE READ", "mismatch"), ("postgres 16", "unconfirmed"),
                              ("an Oracle thing, I think", "mismatch"), ("no idea", "unconfirmed")):
            self.answer(text)
            self.assertEqual(verdict, judge.capabilities(self.ws, "orders")["matches_production"], text)

    def test_a_stand_in_engine_never_runs_concurrency(self):
        self.smoke_run(self.rows(engine={"product": "H2", "major": 2, "default_isolation": "READ_COMMITTED"}))
        c = judge.capabilities(self.ws, "orders")
        self.assertFalse(c["database"]["real_engine"])
        self.assertEqual(("B", "not-run"), (c["ceilings"]["matrix"], c["ceilings"]["concurrency"]))

    def test_a_failed_check_caps_what_depends_on_it(self):
        self.smoke_run(self.rows(failing=("SMOKE-3",)))
        self.answer("PostgreSQL 16, read committed")
        c = judge.capabilities(self.ws, "orders")
        self.assertEqual(("A", "C", "C", "C"), tuple(c["ceilings"][k] for k in ("matrix", "effects", "faults", "concurrency")))
        self.smoke_run(self.rows(failing=("SMOKE-1",)))
        self.assertEqual("C", judge.capabilities(self.ws, "orders")["ceilings"]["matrix"])

    def test_result_files_that_were_changed_afterwards_are_not_believed(self):
        self.smoke_run(self.rows(failing=("SMOKE-1",)), tamper=True)
        c = judge.capabilities(self.ws, "orders")
        self.assertIn("hash", c["problem"])
        self.assertFalse(c["executable"])
        self.assertEqual("C", c["ceilings"]["matrix"])

    def test_no_run_at_all(self):
        c = judge.capabilities(self.ws, "orders")
        self.assertIn("no smoke run", c["problem"])
        self.assertEqual("C", c["ceilings"]["matrix"])


if __name__ == "__main__":
    unittest.main()
