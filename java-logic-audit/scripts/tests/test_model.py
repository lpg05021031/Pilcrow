"""Tests for the model step's scripts: briefs, the reply store, and what render_model keeps."""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(HERE))
import model_brief  # noqa: E402
import model_store  # noqa: E402
import render_model  # noqa: E402

MAP = {
    "entries": [
        {"id": "EP-001", "kind": "http", "method": "POST", "path": "/orders/{id}/cancel", "handler": "x.OrderController#cancel",
         "parameters": [{"name": "id", "in": "path"}], "returns": "x.OrderView", "writes_tables": ["orders"]},
        {"id": "EP-002", "kind": "message", "handler": "x.Listener#onMessage", "bean": "listener",
         "public_methods": ["handle[x.Shipment]", "onMessage[io.Message]"], "writes_tables": ["orders"]},
    ],
    "entities": [{"id": "ENT-orders", "table": "orders", "lifecycle_candidates": ["state"],
                  "columns": [{"name": "status", "property": "state"}]}],
}
SHARDS = {"shards": [
    {"name": "entries:OrderController", "direction": "top-down", "entries": ["EP-001"], "files": ["src/A.java"]},
    {"name": "entries:Listener", "direction": "top-down", "entries": ["EP-002"], "files": ["src/A.java"]},
    {"name": "entity:orders", "direction": "bottom-up", "entries": [], "files": []},
]}
SCHEMA = {"engine": {"product": "PostgreSQL"}, "tables": {"orders": {
    "columns": [{"name": "id", "type": "int8", "nullable": False}, {"name": "status", "type": "varchar", "nullable": False}],
    "primary_key": ["id"], "foreign_keys": []}}}


def cancel(**over):
    c = {
        "id": "CMD-cancelOrder", "title": "Cancel", "handler": "x.OrderController#cancel",
        "binding": {"kind": "http", "method": "GET", "path": "/orders/{orderId}/cancel"}, "target": "orders",
        "vars": [{"name": "order", "kind": "state", "domain": ["OPEN", "DONE", "null"],
                  "sql": "select status from orders where id = :orderId"}],
        "base": {"order": "OPEN"},
        "guards": [
            {"id": "G1", "when": {"var": "order", "is": "null"}, "outcome": "rejected", "code": "NOT_FOUND", "cite": "src/A.java:2"},
            {"id": "G2", "when": {"var": "order", "is": "DONE"}, "outcome": "rejected", "code": "DONE", "cite": "src/A.java:3-4"},
        ],
    }
    c.update(over)
    return c


class Model(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.adir = os.path.join(self.tmp, "audit", "s")
        os.makedirs(os.path.join(self.adir, "model_raw"))
        os.makedirs(os.path.join(self.tmp, "src"))
        with open(os.path.join(self.tmp, "src", "A.java"), "w") as f:
            f.write("class A {\n  // one\n  void a() {\n  }\n}\n")
        self.write("map.json", MAP)
        self.write("shards.json", SHARDS)
        self.write("facts/schema.json", SCHEMA)
        self.write("harness.json", {"rejections": ["x.BusinessException"], "clock": "2030-03-15T10:00:00Z"})

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

    def mined(self, *commands, **more):
        self.write("model_raw/" + model_store.file_name("mine", "entries:OrderController"),
                   dict({"group": "entries:OrderController", "commands": list(commands)}, **more))

    def render(self):
        model_store.collect(self.tmp, "s")
        return render_model.render(self.tmp, "s")

    # ---- briefs

    def test_the_groups_are_the_top_down_ones(self):
        out = model_brief.cmd_shards(self.tmp, "s")
        self.assertEqual([g["name"] for g in out["groups"]], ["entries:OrderController", "entries:Listener"])
        self.assertEqual((out["estimate"]["readers"], out["estimate"]["agents"]), (2, 4))
        self.assertEqual(len(model_brief.cmd_shards(self.tmp, "s", "listener")["groups"]), 1)

    def test_a_reader_is_given_what_the_tools_found(self):
        brief = model_brief.cmd_mine(self.tmp, "s", "entries:Listener")
        self.assertEqual(brief["entry_points"][0]["bean"], "listener")
        self.assertEqual(brief["tables"]["orders"]["lifecycle_columns"], ["status"])
        self.assertEqual(brief["refusals"]["exception_types"], ["x.BusinessException"])
        self.assertTrue(os.path.isfile(brief["format"]))

    def test_a_referee_sees_claims_and_citations_only(self):
        self.mined(cancel())
        model_store.collect(self.tmp, "s")
        brief = model_brief.cmd_referee(self.tmp, "s", "entries:OrderController")
        self.assertEqual((brief["part"], brief["parts"]), (1, 1))
        one = brief["commands"][0]
        self.assertEqual([c["id"] for c in one["claims"]], ["G1", "G2"])
        self.assertEqual(one["claims"][1]["cite"], "src/A.java:3-4")
        self.assertNotIn("files", brief)
        self.assertNotIn("base", one)

    def test_a_large_group_is_refereed_in_parts(self):
        self.mined(*[cancel(id="CMD-c%02d" % n) for n in range(11)])
        model_store.collect(self.tmp, "s")
        first = model_brief.cmd_referee(self.tmp, "s", "entries:OrderController")
        second = model_brief.cmd_referee(self.tmp, "s", "entries:OrderController", 2)
        self.assertEqual((first["parts"], len(first["commands"]), len(second["commands"])), (2, 8, 3))
        with self.assertRaises(SystemExit):
            model_brief.cmd_referee(self.tmp, "s", "entries:OrderController", 3)

    def test_the_recipe_brief_lists_the_cases_and_what_a_step_may_call(self):
        self.mined(cancel())
        self.render()
        brief = model_brief.cmd_recipes(self.tmp, "s", "entries:OrderController")
        self.assertEqual(brief["cases_to_reach"], {"CMD-cancelOrder": ["order=DONE", "order=null"]})
        self.assertEqual(brief["commands_a_step_may_call"][0]["returns"], "x.OrderView")
        self.assertEqual(brief["fixture_prefix"], "OrderController")
        self.assertTrue(brief["time_can_be_moved"])

    def test_a_group_with_many_cases_gets_its_recipes_in_parts_and_variants_stay_together(self):
        def variant(root, name, values):
            return cancel(id="CMD-%s.%s" % (root, name), variant={"of": "CMD-" + root},
                          vars=[{"name": "order", "kind": "state", "domain": ["OPEN"] + ["V%d" % i for i in range(values)],
                                 "sql": "select status from orders where id = :orderId"}], guards=[])
        own = [variant("a", "X", 20), variant("a", "Y", 20), variant("b", "X", 30), cancel(id="CMD-c")]
        parts = model_brief.recipe_parts(own)
        self.assertEqual([[c["id"] for c in p] for p in parts],
                         [["CMD-a.X", "CMD-a.Y"], ["CMD-b.X", "CMD-c"]])
        self.assertEqual(len(model_brief.recipe_parts([cancel()])), 1)

    # ---- the store

    def test_replies_are_collected_in_name_order_and_fixes_replace(self):
        self.mined(cancel(), could_not_model=[{"entry": "EP-009", "why": "reflection"}],
                   instruction_shaped=[{"cite": "src/A.java:2", "text": "tells the reader to skip"}])
        self.write("model_raw/referee-entries-OrderController.json",
                   {"verdicts": [{"command": "CMD-cancelOrder", "id": "G1", "verdict": "confirmed"}]})
        fixed = cancel(title="Cancel an order")
        self.write("model_raw/fix-01.json", {"commands": [fixed]})
        out = model_store.collect(self.tmp, "s")
        self.assertEqual(out["commands"][0]["shard"], "entries:OrderController")
        self.assertEqual(out["referee"][0]["command"], "CMD-cancelOrder")
        self.assertEqual(out["corrections"][0]["command"]["title"], "Cancel an order")
        self.assertIn("entries:OrderController: EP-009: reflection", out["gaps"])
        self.assertEqual(out["instruction_shaped"][0]["cite"], "src/A.java:2")

    def test_recipes_are_assembled_and_a_fix_wins(self):
        self.write("model_raw/recipes-entries-OrderController.json", {
            "fixtures": {"open": {"steps": []}, "gone": {"steps": []}},
            "recipes": {"CMD-cancelOrder": {"base": {"fixture": "open", "with": {"orderId": 1}}}}})
        self.write("model_raw/fix-02.json", {"remove_fixtures": ["gone"], "fixtures": {"open": {"steps": [{"do": "CMD-x"}]}},
                                             "recipes": {"CMD-cancelOrder": {"base": {"fixture": "open", "with": {"orderId": 2}}}}})
        out = model_store.recipes(self.tmp, "s")
        self.assertEqual(out["fixtures"], 1)
        self.assertEqual(self.read("model/fixtures.json")["fixtures"]["open"]["steps"], [{"do": "CMD-x"}])
        self.assertEqual(self.read("model/recipes.json")["recipes"]["CMD-cancelOrder"]["base"]["with"], {"orderId": 2})

    def test_file_names_are_safe(self):
        self.assertEqual(model_store.file_name("mine", "entries:Order Controller/../x"), "mine-entries-Order-Controller-..-x.json")
        with self.assertRaises(SystemExit):
            model_store.file_name("other", "x")

    # ---- what the model keeps

    def test_a_sound_command_is_kept_and_bound_as_the_map_says(self):
        self.mined(cancel())
        report = self.render()
        self.assertEqual(report["stats"]["commands"], 1)
        kept = self.read("model/commands.json")["commands"][0]
        self.assertEqual(kept["binding"]["method"], "POST")             # the map's method, not the reader's
        self.assertEqual(kept["binding"]["path"], "/orders/{orderId}/cancel")
        self.assertNotIn("shard", kept)
        self.assertEqual(report["entries_without_command"], ["EP-002 x.Listener#onMessage"])
        with open(os.path.join(self.adir, "MODEL.md")) as f:
            self.assertIn("## CMD-cancelOrder", f.read())

    def test_the_referee_decides_which_checks_stay(self):
        self.mined(cancel())
        self.write("model_raw/referee-CMD-cancelOrder.json", {"command": "CMD-cancelOrder", "verdicts": [
            {"id": "G1", "verdict": "wrong-citation", "cite": "src/A.java:3"},
            {"id": "G2", "verdict": "refuted", "reason": "only a comment says so"}]})
        report = self.render()
        kept = self.read("model/commands.json")["commands"][0]
        self.assertEqual([g["id"] for g in kept["guards"]], ["G1"])
        self.assertEqual((kept["guards"][0]["cite"], kept["guards"][0]["verified"]), ("src/A.java:3", True))
        self.assertEqual(report["dropped"][0]["id"], "G2")
        self.assertEqual(report["stats"]["guards"], {"confirmed": 0, "relocated": 1, "refuted": 1, "unverified": 0, "bad_citation": 0})

    def test_a_referee_who_names_several_places_does_not_cost_the_claim(self):
        self.mined(cancel())
        self.write("model_raw/referee-CMD-cancelOrder.json", {"command": "CMD-cancelOrder", "verdicts": [
            {"id": "G1", "verdict": "wrong-citation", "cite": "the check is at src/A.java:3, the write at src/A.java:4-5"},
            {"id": "G2", "verdict": "wrong-citation", "cite": "somewhere in the service layer"}]})
        report = self.render()
        kept = {g["id"]: g for g in self.read("model/commands.json")["commands"][0]["guards"]}
        self.assertEqual((kept["G1"]["cite"], kept["G1"]["also_at"], kept["G1"]["verified"]), ("src/A.java:3", ["src/A.java:4-5"], True))
        self.assertFalse(kept["G2"]["verified"])                 # kept at the place the reader gave, and marked as not verified
        self.assertEqual(report["dropped"], [])

    def test_a_citation_must_lead_to_lines_that_exist(self):
        bad = cancel()
        bad["guards"][0]["cite"] = "src/A.java:400"
        bad["guards"][1]["cite"] = "../../etc/passwd:1"
        self.mined(bad)
        report = self.render()
        self.assertEqual(sorted(d["id"] for d in report["dropped"]), ["G1", "G2"])
        self.assertIn("outside the file", report["dropped"][0]["why"])
        self.assertIn("does not exist in the workspace", report["dropped"][1]["why"])

    def test_commands_that_do_not_stand_up_are_left_out_with_the_reason(self):
        ghost = cancel(id="CMD-ghost", handler="x.Nope#x")
        wrong_path = cancel(id="CMD-elsewhere")
        wrong_path["binding"] = {"kind": "http", "method": "POST", "path": "/orders/{id}"}
        writes = cancel(id="CMD-writes")
        writes["vars"] = [dict(writes["vars"][0], sql="update orders set status = 'X'")]
        two = cancel(id="CMD-two")
        two["vars"] = [dict(two["vars"][0], sql="select 1; select 2")]
        incoherent = cancel(id="CMD-incoherent", base={"order": "DONE"})
        handler = {"id": "CMD-onMessage", "handler": "x.Listener#onMessage", "target": "orders", "vars": [], "base": {}, "guards": [],
                   "binding": {"kind": "bean", "bean": "listener", "method": "secret", "args": []}}
        self.mined(ghost, wrong_path, writes, two, incoherent, handler)
        report = self.render()
        why = {x["command"]: " ".join(x["problems"]) for x in report["left_out"]}
        self.assertEqual(report["stats"]["commands"], 0)
        self.assertIn("which the map does not have", why["CMD-ghost"])
        self.assertIn("its entry point is /orders/{id}/cancel", why["CMD-elsewhere"])
        self.assertIn("is not a SELECT", why["CMD-writes"])
        self.assertIn("more than one statement", why["CMD-two"])
        self.assertIn("base must be a case every guard lets through", why["CMD-incoherent"])
        self.assertIn("does not list for this entry point", why["CMD-onMessage"])

    def test_a_bean_method_the_map_lists_is_accepted(self):
        handler = {"id": "CMD-shipment", "handler": "x.Listener#onMessage", "target": "orders", "vars": [], "base": {}, "guards": [],
                   "binding": {"kind": "bean", "bean": "listener", "method": "handle", "args": [{"id": "${id}"}]}}
        self.mined(handler)
        self.assertEqual(self.render()["stats"]["commands"], 1)

    def test_a_corrected_command_must_be_refereed_again(self):
        self.mined(cancel())
        self.write("model_raw/referee-entries-OrderController.json",
                   {"verdicts": [{"command": "CMD-cancelOrder", "id": "G1", "verdict": "confirmed"},
                                 {"command": "CMD-cancelOrder", "id": "G2", "verdict": "confirmed"}]})
        self.write("model_raw/fix-01.json", {"cases": [{"cell": "CELL-0001", "kind": "model", "why": "a check was missing"}],
                                             "commands": [cancel(title="Corrected")]})
        self.render()
        kept = self.read("model/commands.json")["commands"][0]
        self.assertEqual([g["verified"] for g in kept["guards"]], [False, False])
        self.assertEqual(self.read("model_result.json")["triage"][0]["kind"], "model")
        self.write("model_raw/referee-fix-01-CMD-cancelOrder.json",
                   {"verdicts": [{"command": "CMD-cancelOrder", "id": "G1", "verdict": "confirmed"},
                                 {"command": "CMD-cancelOrder", "id": "G2", "verdict": "refuted", "reason": "not there"}]})
        self.render()
        kept = self.read("model/commands.json")["commands"][0]
        self.assertEqual([(g["id"], g["verified"]) for g in kept["guards"]], [("G1", True)])

    def test_a_correction_replaces_or_removes_a_command(self):
        self.mined(cancel(), cancel(id="CMD-other"))
        self.write("model_raw/fix-01.json", {"remove_commands": ["CMD-other"], "commands": [cancel(title="Fixed")]})
        self.render()
        kept = self.read("model/commands.json")["commands"]
        self.assertEqual([(c["id"], c["title"]) for c in kept], [("CMD-cancelOrder", "Fixed")])

    def test_query_checks(self):
        self.assertIsNone(render_model.check_sql("select count(*) from t where a = :a"))
        self.assertIsNotNone(render_model.check_sql("select * into x from t"))
        self.assertIsNotNone(render_model.check_sql("with x as (delete from t returning 1) select 1"))
        self.assertIsNotNone(render_model.check_sql("select pg_sleep(1); drop table t"))


if __name__ == "__main__":
    unittest.main()


class DatabaseClock(unittest.TestCase):
    def test_a_query_that_asks_the_database_for_the_time_is_recognised_and_the_parameter_now_is_not(self):
        import render_model
        for sql in ("select expires_at < now() from r where id = :id", "select d < CURRENT_DATE from r",
                    "select case when t < current_timestamp then 'due' else 'not' end from r", "select sysdate() > t from r"):
            self.assertIsNotNone(render_model.DB_CLOCK.search(sql), sql)
        for sql in ("select expires_at < :now from r where id = :id", "select d < :today from r",
                    "select known_at, snow from r", "select now_playing from r"):
            self.assertIsNone(render_model.DB_CLOCK.search(sql), sql)


class Replies(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.raw = os.path.join(self.tmp, "audit", "s", "model_raw")
        os.makedirs(os.path.join(self.raw, "briefs"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def put(self, name, text):
        path = os.path.join(self.raw, name)
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_a_reply_file_is_one_json_object_written_by_the_agent(self):
        import model_store
        self.assertIsNone(model_store.check_reply(self.put("mine-a.json", '{"commands": [{"sql": "select a < b from t"}]}')))
        self.assertIn("does not exist", model_store.check_reply(os.path.join(self.raw, "mine-b.json")))
        self.assertIn("not one JSON object", model_store.check_reply(self.put("mine-c.json", '```json\n{"commands": []}\n```')))
        self.assertIn("not one JSON object", model_store.check_reply(self.put("mine-d.json", '[1, 2]')))
        self.assertIn("empty", model_store.check_reply(self.put("mine-e.json", "  \n")))
        # what a reply looks like after being copied from a display: the comparison became an entity, and the query two statements
        self.assertIn("HTML entities", model_store.check_reply(self.put("mine-f.json", '{"sql": "select a &lt; b from t"}')))

    def test_a_correction_gets_the_next_free_number_and_keeps_it_until_it_is_used(self):
        import model_brief
        self.put("fix-01.json", '{"commands": [{"id": "CMD-a"}]}')
        self.put("fix-02.json", '{"commands": [{"id": "CMD-b"}, {"id": "CMD-a"}]}')
        brief_a = os.path.join(self.raw, "briefs", "triage-CMD-a.json")
        brief_b = os.path.join(self.raw, "briefs", "triage-CMD-b.json")
        self.assertEqual(model_brief.next_fix(self.raw, brief_a), 3)
        self.put("briefs/triage-CMD-a.json", json.dumps({"save_reply_as": "audit/s/model_raw/fix-03.json"}))
        self.assertEqual(model_brief.next_fix(self.raw, brief_b), 4)          # another brief made meanwhile: not the same number
        self.assertEqual(model_brief.next_fix(self.raw, brief_a), 3)          # asked again before it was answered: the same number
        self.put("fix-03.json", '{"commands": []}')
        self.assertEqual(model_brief.next_fix(self.raw, brief_a), 4)          # answered: the next round gets a new one
        self.assertEqual((model_brief.latest_fix_of(self.raw, "CMD-a"), model_brief.latest_fix_of(self.raw, "CMD-b"),
                          model_brief.latest_fix_of(self.raw, "CMD-c")), (2, 2, None))
