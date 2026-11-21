"""Tests for build_map, on the facts the harness extracted from sample 1.

The facts under data/orders-facts/ were written by a real smoke run of the harness on
fixtures/orders-jpa (PostgreSQL 16). The expectations below are what a person who knows that
sample can check by reading its source.

    python3 -m unittest discover -s java-logic-audit/scripts/tests -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(SCRIPTS))
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)

import build_map  # noqa: E402
import preflight_scan  # noqa: E402

INTENT = ("---\nsystem: orders\nmodule: .\ngoal: audit\nlanguage: auto\nbudget: quick\n"
          "base_commit: none\ndate: 2026-11-21\n---\n")


class OrdersMap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="jla-test-")
        cls.ws = os.path.join(cls.tmp, "orders")
        shutil.copytree(os.path.join(REPO, "fixtures", "orders-jpa"), cls.ws,
                        ignore=shutil.ignore_patterns("target", "build", ".gradle"))
        adir = os.path.join(cls.ws, "audit", "orders")
        shutil.copytree(os.path.join(HERE, "data", "orders-facts"), os.path.join(adir, "facts"))
        with open(os.path.join(adir, "INTENT.md"), "w", encoding="utf-8") as fh:
            fh.write(INTENT)
        with open(os.path.join(adir, "profile.json"), "w", encoding="utf-8") as fh:
            json.dump(preflight_scan.scan(cls.ws, env=False), fh)
        cls.map, cls.shards = build_map.build(cls.ws, "orders")
        cls.adir = adir

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def entry(self, what):
        for e in self.map["entries"]:
            if e.get("path") == what or e["handler"].endswith(what):
                return e
        self.fail("no entry " + what)

    def sites(self, table, column=None, kind=None):
        return [s for s in self.map["write_sites"] if s["table"] == table
                and (column is None or column in s["columns"]) and (kind is None or s["kind"] == kind)]

    def test_entry_points(self):
        self.assertEqual({"http": 10, "message": 1, "scheduled": 2}, self.map["stats"]["entries_by_kind"])
        self.assertEqual(["EP-%03d" % i for i in range(1, 14)], [e["id"] for e in self.map["entries"]])
        shipment = self.entry("ShipmentListener#onMessage")
        self.assertEqual(["orders.shipments"], shipment["destinations"])       # seen on the double at start-up
        # The framework's own error endpoint and the start-up listener are not business entry points.
        self.assertFalse([e for e in self.map["entries"] if "BasicErrorController" in e["handler"]])
        self.assertFalse([e for e in self.map["entries"] if "NatsSubscriptions" in e["handler"]])

    def test_what_each_entry_can_write(self):
        self.assertEqual([], self.entry("/orders/{orderId}/lines/{lineId}/changes/preview")["writes_tables"])
        self.assertEqual([], self.entry("/orders/{id}")["writes_tables"])
        self.assertEqual(["change_request"], self.entry("/orders/{orderId}/lines/{lineId}/changes")["writes_tables"])
        self.assertEqual(["order_header", "order_line", "schedule_line"], self.entry("/orders")["writes_tables"])
        self.assertEqual(["change_request", "order_header", "order_line", "outbox_event", "schedule_line"],
                         self.entry("/changes/{id}/commit")["writes_tables"])
        self.assertEqual(["order_header"], self.entry("OrderClosingJob#closeCompleted")["writes_tables"])
        self.assertEqual(["outbox_event"], self.entry("OutboxRelay#relay")["writes_tables"])

    def test_entities_and_parents(self):
        by_id = {e["id"]: e for e in self.map["entities"]}
        self.assertEqual(6, len(by_id))
        self.assertIsNone(by_id["ENT-order_header"]["parent"])
        self.assertEqual("ENT-order_header", by_id["ENT-order_line"]["parent"]["entity"])
        self.assertEqual("ENT-order_line", by_id["ENT-schedule_line"]["parent"]["entity"])
        self.assertEqual(["status"], by_id["ENT-order_line"]["lifecycle_candidates"])
        self.assertEqual("version", by_id["ENT-order_line"]["version_property"])
        self.assertIsNone(by_id["ENT-schedule_line"]["version_property"])
        self.assertEqual([["header_id", "line_no"]], by_id["ENT-order_line"]["unique"])

    def test_write_sites_of_the_columns_that_matter(self):
        def where(table, column):
            return sorted("%s:%s" % (s["site"].split(".")[-1].split("(")[0], s["via"])
                          for s in self.sites(table, column, "update"))
        # The quantity of a line is changed in exactly one place; so is the shipped quantity.
        self.assertEqual(["ChangeService#apply:OrderLine.setQty"], where("order_line", "qty"))
        self.assertEqual(["ShipmentService#apply:OrderLine.setShippedQty"], where("order_line", "shipped_qty"))
        self.assertEqual(["ChangeService#apply:OrderLine.setStatus", "OrderService#cancel:OrderLine.setStatus",
                          "ShipmentService#apply:OrderLine.setStatus"], where("order_line", "status"))
        # A domain method that assigns two mapped fields is one write site with both columns.
        self.assertEqual([["sent_at", "status"]], [s["columns"] for s in self.sites("outbox_event", kind="update")])
        # Every change to a line or to its schedule lines is followed by touch(): revision has the same three sites.
        self.assertEqual(3, len(self.sites("order_line", "revision", "update")))

    def test_rows_are_created_where_the_source_says(self):
        created = sorted((s["table"], s["site"].split(".")[-1].split("(")[0]) for s in self.map["write_sites"]
                         if s["kind"] == "create")
        self.assertEqual([("change_request", "ChangeService#propose"), ("order_header", "OrderService#create"),
                          ("order_line", "OrderService#create"), ("outbox_event", "OutboxWriter#add"),
                          ("processed_message", "ShipmentService#apply"), ("schedule_line", "OrderService#create")], created)

    def test_an_identifier_the_application_assigns_is_a_written_column(self):
        created = [s for s in self.map["write_sites"] if s["kind"] == "create" and s["table"] == "processed_message"][0]
        self.assertEqual(["message_id", "processed_at"], created["columns"])    # the id is the deduplication key
        header = [s for s in self.map["write_sites"] if s["kind"] == "create" and s["table"] == "order_header"][0]
        self.assertNotIn("id", header["columns"])                               # generated by the database

    def test_lifecycle_columns_and_who_writes_them(self):
        by = {(w["entity"], w["column"]): w for w in self.map["lifecycle_writers"]}
        self.assertEqual(5, len(by))
        line = by[("ENT-order_line", "status")]
        self.assertEqual(["com.example.orders.service.OrderService"], line["set_at_creation_by"])
        self.assertEqual(["ChangeService", "OrderService", "ShipmentService"],
                         [c["class"].rsplit(".", 1)[-1] for c in line["changed_by"]])
        request = by[("ENT-change_request", "status")]
        self.assertEqual(["ChangeService"], [c["class"].rsplit(".", 1)[-1] for c in request["changed_by"]])
        self.assertEqual(4, sum(len(c["write_sites"]) for c in request["changed_by"]))

    def test_reachability_and_transactions(self):
        st = self.map["stats"]
        self.assertEqual(0, st["orphan_write_sites"])
        self.assertEqual(0, st["write_sites_outside_transaction"])
        self.assertEqual([], self.map["self_invocations"])
        self.assertEqual([], self.map["unresolved"])
        total = [s for s in self.sites("order_header", "total_amount") if "OrderTotals" in s["site"]][0]
        self.assertEqual(4, len(total["entries"]))               # commit, create, shipment, nightly job
        self.assertTrue(all("REQUIRED" in t for t in total["transaction"]))

    def test_slices_cover_every_entry_and_entity_with_real_files(self):
        top = [s for s in self.shards if s["direction"] == "top-down"]
        bottom = [s for s in self.shards if s["direction"] == "bottom-up"]
        self.assertEqual(sorted(e["id"] for e in self.map["entries"]), sorted(i for s in top for i in s["entries"]))
        self.assertEqual(sorted(e["id"] for e in self.map["entities"]), sorted(s["entity"] for s in bottom))
        for s in self.shards:
            self.assertTrue(s["files"], s["name"])
            for f in s["files"]:
                self.assertTrue(os.path.isfile(os.path.join(self.ws, f)), f)

    def test_the_same_facts_give_the_same_map(self):
        again, shards = build_map.build(self.ws, "orders")
        self.assertEqual(self.map, again)
        self.assertEqual(self.shards, shards)
        self.assertTrue(os.path.isfile(os.path.join(self.adir, "MAP.md")))


class Transactions(unittest.TestCase):
    """Synthetic facts for the two things sample 1 does right: a write outside any transaction and
    a transactional method called from its own class."""

    def test_writes_outside_a_transaction_and_self_invocation_are_reported(self):
        def method(calls=(), sets=()):
            return {"annotations": [], "calls": [{"owner": o, "name": n, "line": l} for o, n, l in calls],
                    "fields": [{"owner": o, "name": n, "mode": "set", "line": 1} for o, n in sets]}
        types = {
            "a.Thing": {"kind": "class", "source": "Thing.java", "annotations": [], "supertypes": [],
                        "methods": {"setState(String)": method(sets=[("a.Thing", "state")])}},
            "a.Svc": {"kind": "class", "source": "Svc.java", "annotations": [], "supertypes": [],
                      "methods": {"outer()": method(calls=[("a.Svc", "inner", 10)]),
                                  "inner()": method(calls=[("a.Thing", "setState", 20)]),
                                  "job()": method(calls=[("a.Thing", "setState", 30)])}},
            "a.Web": {"kind": "class", "source": "Web.java", "annotations": [], "supertypes": [],
                      "methods": {"post()": method(calls=[("a.Svc", "outer", 5)])}},
        }
        graph = build_map.Graph(types)
        entities = {"a.Thing": {"id": "ENT-thing", "table": "thing", "properties": {"state": ["state"]},
                                "version_property": None}}
        entries = [{"id": "EP-001", "handler_methods": ["a.Web#post()"]},
                   {"id": "EP-002", "handler_methods": ["a.Svc#job()"]}]
        tx = [{"method": "a.Svc#inner", "propagation": "REQUIRES_NEW", "read_only": False}]
        sites, _ = build_map.build_write_sites(graph, entities, build_map.entity_mutators(graph, entities), [])
        build_map.attach_reachability(graph, sites, entries, tx)
        by_line = {s["line"]: s for s in sites}
        # inner() is transactional, but it is only called from outer() in its own class, so its settings
        # never apply: the write at line 20 runs with no transaction when reached from EP-001.
        self.assertEqual((["EP-001"], ["EP-001"], []),
                         (by_line[20]["entries"], by_line[20]["outside_transaction_from"], by_line[20]["transaction"]))
        self.assertEqual((["EP-002"], ["EP-002"]), (by_line[30]["entries"], by_line[30]["outside_transaction_from"]))
        # Entered from another class, the same method's transaction does apply.
        types["a.Other"] = {"kind": "class", "source": "Other.java", "annotations": [], "supertypes": [],
                            "methods": {"run()": method(calls=[("a.Svc", "inner", 7)])}}
        graph2 = build_map.Graph(types)
        entries2 = [{"id": "EP-003", "handler_methods": ["a.Other#run()"]}]
        sites2, _ = build_map.build_write_sites(graph2, entities, build_map.entity_mutators(graph2, entities), [])
        build_map.attach_reachability(graph2, sites2, entries2, tx)
        covered = {s["line"]: s for s in sites2}[20]
        self.assertEqual((["Svc#inner REQUIRES_NEW"], []), (covered["transaction"], covered["outside_transaction_from"]))
        self.assertEqual([{"method": "a.Svc#inner()", "called_from": "a.Svc#outer()", "line": 10,
                           "propagation": "REQUIRES_NEW", "read_only": False}], build_map.self_invocations(graph, tx))


class OpaqueWrites(unittest.TestCase):
    def test_sql_the_script_cannot_read_is_listed_then_merged_once_a_reader_settles_it(self):
        types = {"a.Job": {"kind": "class", "source": "Job.java", "annotations": [], "supertypes": [], "methods": {
            "run()": {"annotations": [], "fields": [], "calls": [
                {"owner": "org.springframework.jdbc.core.JdbcTemplate", "name": "update", "line": 12}]}}}}
        graph = build_map.Graph(types)
        entities = {"a.Thing": {"id": "ENT-thing", "table": "thing", "properties": {}, "version_property": None}}
        sites, unresolved = build_map.build_write_sites(graph, entities, {}, [])
        self.assertEqual(([], 1), (sites, len(unresolved)))
        self.assertEqual(("a.Job#run()", 12), (unresolved[0]["site"], unresolved[0]["line"]))
        resolved = [{"site": "a.Job#run()", "line": 12, "table": "thing", "columns": ["state"], "kind": "update",
                     "cite": "Job.java:12-14"}]
        sites, unresolved = build_map.build_write_sites(graph, entities, {}, [], resolved)
        self.assertEqual([], unresolved)
        self.assertEqual(("ENT-thing", ["state"], "Job.java:12-14"),
                         (sites[0]["entity"], sites[0]["columns"], sites[0]["resolved_by"]))


if __name__ == "__main__":
    unittest.main()


class StockMap(unittest.TestCase):
    """Sample 2: MyBatis instead of JPA. The facts under data/stock-facts/ were written by a real smoke
    run of the harness on fixtures/stock-mybatis (Spring Boot 2.7, MySQL 8, Gradle)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="jla-test-")
        cls.ws = os.path.join(cls.tmp, "stock")
        shutil.copytree(os.path.join(REPO, "fixtures", "stock-mybatis"), cls.ws,
                        ignore=shutil.ignore_patterns("target", "build", ".gradle"))
        adir = os.path.join(cls.ws, "audit", "stock")
        shutil.copytree(os.path.join(HERE, "data", "stock-facts"), os.path.join(adir, "facts"))
        with open(os.path.join(adir, "INTENT.md"), "w", encoding="utf-8") as fh:
            fh.write(INTENT.replace("orders", "stock"))
        with open(os.path.join(adir, "profile.json"), "w", encoding="utf-8") as fh:
            json.dump(preflight_scan.scan(cls.ws, env=False), fh)
        cls.map, cls.shards = build_map.build(cls.ws, "stock")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def sites(self, table, column=None):
        return [s for s in self.map["write_sites"] if s["table"] == table and (column is None or column in s["columns"])]

    def test_a_table_no_class_is_mapped_to_is_still_an_entity(self):
        by = {e["id"]: e for e in self.map["entities"]}
        self.assertEqual(sorted(by), ["ENT-reservation", "ENT-stock_item", "ENT-stock_movement"])
        self.assertIsNone(by["ENT-reservation"]["class"])
        self.assertEqual(by["ENT-reservation"]["lifecycle_candidates"], ["status"])
        self.assertEqual(by["ENT-reservation"]["parent"], {"entity": "ENT-stock_item", "columns": ["sku"]})
        self.assertEqual(by["ENT-stock_item"]["lifecycle_candidates"], [])
        self.assertEqual(self.map["tables_without_entity"], [])

    def test_every_call_of_a_mapper_statement_is_a_write_site_with_the_columns_its_sql_assigns(self):
        self.assertEqual((self.map["stats"]["write_sites"], self.map["stats"]["unresolved"]), (15, 0))
        reserved = self.sites("stock_item", "reserved")
        self.assertEqual(sorted((s["via"], s["site"].split("#")[1].split("(")[0]) for s in reserved if s["kind"] == "update"),
                         [("StockMapper.addReserved", "expireDue"), ("StockMapper.addReserved", "hold"),
                          ("StockMapper.addReserved", "release"), ("StockMapper.ship", "confirm")])
        dynamic = [s for s in self.map["write_sites"] if s.get("dynamic")]
        self.assertEqual([(s["via"], s["columns"]) for s in dynamic], [("StockMapper.correct", ["note", "on_hand"])])
        self.assertTrue(dynamic[0]["sql_at"].startswith("src/main/resources/mapper/StockMapper.xml:"))
        created = [s for s in self.sites("reservation") if s["kind"] == "create"]
        self.assertEqual(created[0]["sql_at"].split(":")[0], "src/main/java/com/example/stock/mapper/ReservationMapper.java")

    def test_each_entry_knows_the_tables_it_can_write_and_runs_in_a_transaction(self):
        by = {e["handler"].split("#")[1]: e for e in self.map["entries"] if e["kind"] == "http"}
        self.assertEqual(by["confirm"]["writes_tables"], ["reservation", "stock_item", "stock_movement"])
        self.assertEqual(by["get"]["writes_tables"], [])
        job = [e for e in self.map["entries"] if e["kind"] == "scheduled"][0]
        self.assertEqual(job["writes_tables"], ["reservation", "stock_item"])
        self.assertEqual(self.map["stats"]["write_sites_outside_transaction"], 0)
        status = [w for w in self.map["lifecycle_writers"] if w["column"] == "status"][0]
        self.assertEqual(len(status["changed_by"][0]["write_sites"]), 4)

    def test_a_statement_kept_in_an_xml_file_is_in_the_slice_of_what_reaches_it(self):
        by = {s["name"]: s for s in self.shards}
        xml = "src/main/resources/mapper/StockMapper.xml"
        self.assertIn(xml, by["entries:ReservationController"]["files"])
        self.assertIn(xml, by["entity:stock_item"]["files"])
        self.assertNotIn(xml, by["entity:stock_movement"]["files"])


class Statements(unittest.TestCase):
    def read(self, sql):
        import sql_statements
        return sql_statements.read_write(sql)

    def test_the_three_shapes_of_a_write(self):
        self.assertEqual(self.read("insert into T_Order (id, `status`, total) values (#{id}, 'NEW', #{total})"),
                         {"kind": "create", "table": "t_order", "columns": ["id", "status", "total"]})
        self.assertEqual(self.read("UPDATE orders o SET o.status = #{s}, total = coalesce(#{t}, total) WHERE o.id = #{id}"),
                         {"kind": "update", "table": "orders", "columns": ["status", "total"]})
        self.assertEqual(self.read("delete from sales.order_line where order_id = #{id}"),
                         {"kind": "delete", "table": "order_line", "columns": []})
        self.assertEqual(self.read("insert into t (a, b) values (?, ?) on duplicate key update b = values(b), c = c + 1")["columns"],
                         ["a", "b", "c"])

    def test_what_cannot_be_read_with_certainty_is_not_guessed(self):
        for sql in ("update ${table} set a = 1", "insert into t values (#{a}, #{b})", "insert into t select * from u",
                    "update a join b on a.id = b.id set a.x = b.x", "update t set a = 1; delete from u", "call do_it(#{id})"):
            self.assertIsNone(self.read(sql), sql)

    def test_text_put_in_at_run_time_matters_only_where_the_columns_are_named(self):
        self.assertEqual(self.read("update t set deleted = 1 where ( ${criterion.condition} )"),
                         {"kind": "update", "table": "t", "columns": ["deleted"]})
        self.assertEqual(self.read("delete from t where ${c}"), {"kind": "delete", "table": "t", "columns": []})
        self.assertEqual(self.read("insert into t (a, b) values (${x}, #{y})")["columns"], ["a", "b"])
        for sql in ("update t set ${col} = 1 where id = #{id}", "update t set a = 1 ${more} where id = 1",
                    "insert into t (a, ${col}) values (1, 2)", "delete from ${t} where id = 1",
                    "insert into t (a) values (1) on duplicate key update ${col} = 2"):
            self.assertIsNone(self.read(sql), sql)

    def test_statements_a_generator_writes(self):
        import sql_statements
        xml = """<?xml version="1.0"?>
<mapper namespace="a.AdMapper">
  <sql id="Update_By_Example_Where_Clause">
    <where>
      <foreach collection="example.oredCriteria" item="criteria" separator="or">
        <trim prefix="(" prefixOverrides="and" suffix=")">
          <foreach collection="criteria.criteria" item="criterion">
            <choose><when test="criterion.noValue">and ${criterion.condition}</when></choose>
          </foreach>
        </trim>
      </foreach>
    </where>
  </sql>
  <insert id="insertSelective">
    <selectKey keyProperty="id" order="AFTER" resultType="java.lang.Integer">SELECT LAST_INSERT_ID()</selectKey>
    insert into shop_ad
    <trim prefix="(" suffix=")" suffixOverrides=",">
      <if test="name != null">`name`,</if>
      <if test="link != null">link,</if>
    </trim>
    <trim prefix="values (" suffix=")" suffixOverrides=",">
      <if test="name != null">#{name,jdbcType=VARCHAR},</if>
      <if test="link != null">#{link,jdbcType=VARCHAR},</if>
    </trim>
  </insert>
  <update id="updateByExampleSelective" parameterType="map">
    update shop_ad
    <set>
      <if test="record.name != null">`name` = #{record.name,jdbcType=VARCHAR},</if>
      <if test="record.enabled != null">enabled = #{record.enabled,jdbcType=BIT},</if>
    </set>
    <if test="_parameter != null"><include refid="Update_By_Example_Where_Clause" /></if>
  </update>
  <update id="logicalDeleteByExample" parameterType="map">
    update shop_ad set deleted = 1
    <if test="_parameter != null"><include refid="Update_By_Example_Where_Clause" /></if>
  </update>
</mapper>"""
        got = {k[1]: v for k, v in sql_statements.from_xml(xml, "m/AdMapper.xml").items()}
        self.assertEqual(("create", "shop_ad", ["link", "name"], True),
                         tuple(got["insertSelective"][k] for k in ("kind", "table", "columns", "dynamic")))
        self.assertEqual(("update", "shop_ad", ["enabled", "name"]),
                         tuple(got["updateByExampleSelective"][k] for k in ("kind", "table", "columns")))
        self.assertEqual(("update", "shop_ad", ["deleted"]),
                         tuple(got["logicalDeleteByExample"][k] for k in ("kind", "table", "columns")))

    def test_mapper_xml_with_dynamic_sql_fragments_and_comments(self):
        import sql_statements
        xml = """<?xml version="1.0"?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN" "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="a.OrderMapper">
  <sql id="cols">status, total</sql>
  <!-- <update id="old">update gone set x = 1</update> -->
  <insert id="add" useGeneratedKeys="true">
    <selectKey keyProperty="id" order="BEFORE">select nextval('s')</selectKey>
    insert into orders (id, <include refid="cols"/>) values (#{id}, #{status}, #{total})
  </insert>
  <update id="patch">
    update orders
    <set>
      <if test="status != null">status = #{status},</if>
      <if test="total != null and total &gt; 0">total = #{total},</if>
    </set>
    where id = #{id}
  </update>
  <update id="broken">update orders set <include refid="nowhere"/> where id = #{id}</update>
  <select id="find" resultType="map">select * from orders</select>
</mapper>"""
        got = sql_statements.from_xml(xml, "m/OrderMapper.xml")
        self.assertEqual(sorted(k[1] for k in got), ["add", "broken", "patch"])
        self.assertEqual((got[("a.OrderMapper", "add")]["columns"], got[("a.OrderMapper", "add")]["dynamic"]),
                         (["id", "status", "total"], False))
        patch = got[("a.OrderMapper", "patch")]
        self.assertEqual((patch["kind"], patch["columns"], patch["dynamic"], patch["cite"]),
                         ("update", ["status", "total"], True, "m/OrderMapper.xml:10-17"))
        self.assertIsNone(got[("a.OrderMapper", "broken")]["table"])          # listed as unresolved, for a reader

    def test_annotations_with_concatenated_strings_scripts_and_text_blocks(self):
        import sql_statements
        java = '''package a;
public interface PayMapper {
    @Update("update payment set status = #{status} " +
            "where id = #{id}")
    int mark(@Param("id") long id, @Param("status") String status);

    @Insert({"insert into payment (id, amount)", "values (#{id}, #{amount})"})
    @Options(useGeneratedKeys = true)
    int add(Payment p);

    @Update("<script>update payment <set><if test='note != null'>note = #{note},</if></set> where id = #{id}</script>")
    int note(Payment p);

    @Delete("""
        delete from payment
        where id = #{id}
        """)
    int remove(long id);

    @Select("select * from payment where id = #{id}")
    Payment find(long id);
}'''
        got = sql_statements.from_java(java, "a/PayMapper.java")
        self.assertEqual({k[1]: (v["kind"], v["table"], v["columns"]) for k, v in got.items()},
                         {"mark": ("update", "payment", ["status"]), "add": ("create", "payment", ["amount", "id"]),
                          "note": ("update", "payment", ["note"]), "remove": ("delete", "payment", [])})
        self.assertTrue(got[("a.PayMapper", "note")]["dynamic"])
        self.assertEqual(got[("a.PayMapper", "mark")]["cite"], "a/PayMapper.java:3-5")
