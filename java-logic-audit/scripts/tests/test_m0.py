"""Tests for the M0 scripts: preflight_scan, manifest, status, validate.

    python3 -m unittest discover -s java-logic-audit/scripts/tests -v

The fixture tests compare the scanner's output with the reviewed expected profiles under
fixtures/expected/ (every key except `size` and `environment`). This is the M0 acceptance check
"the profile matches the answer key exactly" (design section 17.2).
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(SCRIPTS))
FIXTURES = os.path.join(REPO, "fixtures")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)

import manifest  # noqa: E402
import preflight_scan  # noqa: E402
import status  # noqa: E402
import validate  # noqa: E402


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


class FixtureProfiles(unittest.TestCase):
    maxDiff = None

    def check(self, name):
        got = preflight_scan.scan(os.path.join(FIXTURES, name), env=False)
        got.pop("size")
        with open(os.path.join(FIXTURES, "expected", name + ".profile.json"), encoding="utf-8") as fh:
            want = json.load(fh)
        self.assertEqual(want, got)
        return got

    def test_sample1_orders_is_applicable_and_supported(self):
        p = self.check("orders-jpa")
        self.assertEqual(["postgres:16-alpine"], p["tests"]["container_images"])
        self.assertEqual("container", p["tests"]["database_source"])
        self.assertEqual("full", p["applicability"]["verdict"])
        self.assertEqual("supported", p["support"]["verdict"])

    def test_sample2_stock_is_supported_on_gradle_mybatis_mysql_and_junit4(self):
        p = self.check("stock-mybatis")
        self.assertEqual(("gradle", "2.7.18", "javax"), (p["build"]["tool"], p["framework"]["version"], p["framework"]["namespace"]))
        self.assertEqual((["mybatis"], ["mysql"]), (p["persistence"], p["database"]["engines"]))
        self.assertEqual(["junit4"], p["tests"]["frameworks"])        # JUnit 5 is excluded from the test starter
        self.assertEqual(("full", "supported"), (p["applicability"]["verdict"], p["support"]["verdict"]))

    def test_sample3_library_is_not_applicable(self):
        p = self.check("lib-pure")
        self.assertEqual("not-applicable", p["applicability"]["verdict"])

    def test_sample3_stream_pipeline_is_not_applicable(self):
        p = self.check("stream-pipeline")
        self.assertEqual("not-applicable", p["applicability"]["verdict"])
        self.assertIn("stream-processing", p["applicability"]["reasons"])

    def test_profiles_are_schema_valid(self):
        schema = os.path.join(SCRIPTS, "..", "schemas", "profile.schema.json")
        with open(schema, encoding="utf-8") as fh:
            schema = json.load(fh)
        for name in ("orders-jpa", "lib-pure", "stream-pipeline"):
            profile = preflight_scan.scan(os.path.join(FIXTURES, name), env=False)
            self.assertEqual([], validate.validate(profile, schema), name)


class SyntheticProjects(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jla-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gradle_mybatis_mysql_junit4(self):
        write(self.tmp, "settings.gradle", "rootProject.name = 'tickets'\ninclude 'app', 'common'\n")
        write(self.tmp, "build.gradle", """
            plugins { id 'org.springframework.boot' version '2.7.18' apply false }
            subprojects { sourceCompatibility = '1.8' }
        """)
        write(self.tmp, "app/build.gradle", """
            dependencies {
                implementation 'org.springframework.boot:spring-boot-starter-web'
                implementation "org.mybatis.spring.boot:mybatis-spring-boot-starter:2.3.2"
                runtimeOnly 'mysql:mysql-connector-java:8.0.33'
                implementation 'org.springframework.kafka:spring-kafka'
                testImplementation 'junit:junit:4.13.2'
                testRuntimeOnly 'com.h2database:h2'
            }
        """)
        write(self.tmp, "app/src/main/java/t/App.java",
              "package t;\nimport javax.servlet.Filter;\n@SpringBootApplication\npublic class App {}\n")
        write(self.tmp, "app/src/main/java/t/TicketController.java", """package t;
            @RestController
            @RequestMapping("/tickets")
            public class TicketController {
                @RequestMapping(method = RequestMethod.POST) public void open() {}
                @PutMapping("/{id}/close") public void close() {}
                // @GetMapping("/not/an/entry") in a comment must not count
            }""")
        write(self.tmp, "app/src/main/java/t/TicketMapper.java", "package t;\n@Mapper\npublic interface TicketMapper {}\n")
        write(self.tmp, "app/src/main/java/t/Events.java",
              "package t;\npublic class Events {\n  @KafkaListener(topics = \"t\") public void on(String s) {}\n}\n")
        write(self.tmp, "app/src/main/resources/mapper/TicketMapper.xml", """<?xml version="1.0"?>
            <mapper namespace="t.TicketMapper">
              <select id="find">select * from ticket</select>
              <update id="close">update ticket set status = 'CLOSED' where id = #{id}</update>
            </mapper>""")
        write(self.tmp, "app/src/main/resources/db/migration/V1__init.sql", """
            CREATE TABLE ticket (
              id BIGINT PRIMARY KEY,
              title VARCHAR(100) NOT NULL,
              status VARCHAR(20) NOT NULL,
              price DECIMAL(10, 2)
            );
            CREATE TABLE estate (id BIGINT PRIMARY KEY, backstage INT);
            CREATE TRIGGER trg BEFORE UPDATE ON ticket FOR EACH ROW SET NEW.title = NEW.title;
        """)
        write(self.tmp, "app/src/main/resources/application.yml", """
            spring:
              datasource:
                url: jdbc:mysql://db.prod.example.com:3306/tickets?useSSL=true
                username: app
                password: s3cret
              kafka:
                bootstrap-servers: ${KAFKA:localhost:9092}
        """)
        write(self.tmp, "app/src/test/java/t/TicketTest.java",
              "package t;\nimport org.junit.Test;\npublic class TicketTest { @Test public void t() {} }\n")

        p = preflight_scan.scan(self.tmp, module="app", env=False)
        self.assertEqual("gradle", p["build"]["tool"])
        self.assertEqual("8", p["build"]["jdk"])
        self.assertEqual(("spring-boot", "2.7.18", "mvc", "javax"),
                         (p["framework"]["name"], p["framework"]["version"], p["framework"]["web"],
                          p["framework"]["namespace"]))
        self.assertEqual(["mybatis"], p["persistence"])
        self.assertEqual(["mysql"], p["database"]["engines"])
        self.assertEqual(["h2"], p["tests"]["substitute_db"])
        self.assertEqual(["junit4"], p["tests"]["frameworks"])
        self.assertEqual(2, p["entry_points"]["http"])
        self.assertEqual(1, p["entry_points"]["message"])
        self.assertEqual(1, p["entities"]["mapper_xml"])
        self.assertEqual(2, p["entities"]["mapper_statements"])
        self.assertEqual([{"table": "ticket", "columns": ["status"]}], p["entities"]["ddl_tables_with_lifecycle"])
        self.assertEqual(1, p["database"]["db_side_logic"]["triggers"])
        self.assertEqual({"verdict": "partial", "reasons": ["logic-in-database"]}, p["applicability"])
        self.assertEqual("supported", p["support"]["verdict"])
        by_key = {c["key"]: c for c in p["connections"]}
        self.assertEqual(("db.prod.example.com", False), (by_key["spring.datasource.url"]["host"],
                                                          by_key["spring.datasource.url"]["local"]))
        self.assertTrue(by_key["spring.kafka.bootstrap-servers"]["local"])
        self.assertNotIn("s3cret", json.dumps(p))

        root_scan = preflight_scan.scan(self.tmp, env=False)
        self.assertEqual(["app", "common"], root_scan["build"]["modules"])

    def test_an_application_module_is_scanned_with_the_sibling_modules_it_is_built_from(self):
        pom = """<?xml version="1.0"?>
            <project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>
              <parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId>
                <version>2.1.5.RELEASE</version></parent>
              <groupId>s</groupId><artifactId>%s</artifactId><version>1</version>%s</project>"""
        dep = "<dependency><groupId>s</groupId><artifactId>%s</artifactId><version>1</version></dependency>"
        write(self.tmp, "pom.xml", pom % ("shop", "<packaging>pom</packaging><modules><module>shop-db</module>"
                                          "<module>shop-api</module><module>shop-all</module><module>shop-tools</module></modules>"))
        write(self.tmp, "shop-db/pom.xml", pom % ("shop-db", "<dependencies><dependency><groupId>mysql</groupId>"
              "<artifactId>mysql-connector-java</artifactId></dependency><dependency><groupId>org.mybatis.spring.boot</groupId>"
              "<artifactId>mybatis-spring-boot-starter</artifactId><version>2.0.1</version></dependency></dependencies>"))
        write(self.tmp, "shop-api/pom.xml", pom % ("shop-api", "<dependencies>" + dep % "shop-db" +
              "<dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId>"
              "</dependency></dependencies>"))
        write(self.tmp, "shop-all/pom.xml", pom % ("shop-all", "<dependencies>" + dep % "shop-api" + "</dependencies>"))
        write(self.tmp, "shop-tools/pom.xml", pom % ("shop-tools", ""))
        write(self.tmp, "shop-all/src/main/java/s/Application.java",
              "package s;\n@SpringBootApplication\npublic class Application {}\n")
        write(self.tmp, "shop-api/src/main/java/s/ApiApplication.java",
              "package s;\n@SpringBootApplication\npublic class ApiApplication {}\n")
        write(self.tmp, "shop-api/src/main/java/s/OrderController.java", """package s;
            @RestController
            public class OrderController {
                @PostMapping("/order/submit") public Object submit() { return null; }
                @PostMapping("/order/cancel") public Object cancel() { return null; }
            }""")
        write(self.tmp, "shop-db/src/main/java/s/OrderMapper.java", "package s;\npublic interface OrderMapper {}\n")
        write(self.tmp, "shop-db/src/main/resources/s/OrderMapper.xml", """<?xml version="1.0"?>
            <mapper namespace="s.OrderMapper">
              <update id="cancel">update shop_order set order_status = 102 where id = #{id}</update>
            </mapper>""")
        write(self.tmp, "shop-db/sql/shop_table.sql", """
            CREATE TABLE `shop_order` (
              `id` int(11) NOT NULL AUTO_INCREMENT,
              `order_status` smallint(6) NOT NULL COMMENT 'state',
              PRIMARY KEY (`id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='orders';
        """)
        write(self.tmp, "shop-tools/src/main/java/s/ToolController.java",
              "package s;\n@RestController\npublic class ToolController { @GetMapping(\"/tool\") public void t() {} }\n")

        p = preflight_scan.scan(self.tmp, module="shop-all", env=False)
        self.assertEqual(["shop-api", "shop-db"], sorted(p["build"]["sibling_modules"]))     # not shop-tools
        self.assertEqual(["s.Application"], p["framework"]["application_classes"])           # the module's own
        self.assertEqual(2, p["entry_points"]["http"])
        self.assertEqual(["mybatis"], p["persistence"])
        self.assertEqual(["mysql"], p["database"]["engines"])
        self.assertEqual(1, p["entities"]["mapper_statements"])
        self.assertEqual([{"table": "shop_order", "columns": ["order_status"]}], p["entities"]["ddl_tables_with_lifecycle"])
        self.assertEqual("full", p["applicability"]["verdict"])
        self.assertEqual("supported", p["support"]["verdict"])
        # A module with no siblings says nothing about them.
        self.assertNotIn("sibling_modules", preflight_scan.scan(self.tmp, module="shop-tools", env=False)["build"])

    def test_out_of_scope_technology_is_static_only(self):
        write(self.tmp, "pom.xml", """<project><modelVersion>4.0.0</modelVersion>
            <parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId>
            <version>3.3.0</version></parent>
            <properties><java.version>21</java.version></properties>
            <dependencies>
              <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-webflux</artifactId></dependency>
              <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-data-jpa</artifactId></dependency>
              <dependency><groupId>com.oracle.database.jdbc</groupId><artifactId>ojdbc11</artifactId></dependency>
            </dependencies></project>""")
        write(self.tmp, "src/main/java/a/Thing.java", """package a;
            import jakarta.persistence.Entity;
            @Entity public class Thing {
                @Id private Long id;
                private boolean cancelled;
                void m() { String state = "x"; }
            }""")
        p = preflight_scan.scan(self.tmp, env=False)
        self.assertEqual(["cancelled"], p["entities"]["jpa"][0]["lifecycle_fields"])
        self.assertEqual("static-only", p["support"]["verdict"])
        self.assertEqual(["web:webflux", "database:oracle"], p["support"]["out_of_scope"])
        self.assertEqual("full", p["applicability"]["verdict"])

    def test_untrusted_xml_is_refused_not_expanded(self):
        write(self.tmp, "pom.xml", '<?xml version="1.0"?><!DOCTYPE p [<!ENTITY a "aaaa">]><project><modules>'
                                   "<module>&a;</module></modules></project>")
        p = preflight_scan.scan(self.tmp, env=False)
        self.assertEqual([], p["build"]["modules"])

    def test_module_must_stay_inside_the_repository(self):
        with self.assertRaises(SystemExit):
            preflight_scan.scan(self.tmp, module="../elsewhere", env=False)


class PlainText(unittest.TestCase):
    """What the installer packs can be reviewed by eye: ASCII only, no hidden or look-alike characters."""

    def test_every_packed_file_is_plain_ascii(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("make_installer", os.path.join(REPO, "tools", "make_installer.py"))
        make_installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(make_installer)
        odd = []
        for name, path in make_installer.members():
            with open(path, "rb") as fh:
                why = make_installer.not_plain(fh.read())
            if why:
                odd.append("%s, %s" % (name, why))
        self.assertEqual([], odd)
        self.assertIsNone(make_installer.not_plain(b"plain\ttext\n"))
        self.assertIn("not ASCII", make_installer.not_plain("a \u2014 b\n".encode("utf-8")))
        self.assertIn("line 2", make_installer.not_plain(b"one\ntwo\x1b[0m\n"))
        self.assertIn("not ASCII", make_installer.not_plain("warn\ufe0f\n".encode("utf-8")))     # a character that cannot be seen


class Endpoints(unittest.TestCase):
    def test_hosts_and_locality(self):
        cases = {
            "jdbc:postgresql://localhost:5432/app": ("localhost", True),
            "jdbc:postgresql://user:pw@10.1.2.3:5432/app": ("10.1.2.3", False),
            "jdbc:mysql://a.example.com:3306,b.example.com:3306/app": ("a.example.com", False),
            "jdbc:sqlserver://sql.corp.example;databaseName=app": ("sql.corp.example", False),
            "jdbc:oracle:thin:@//ora.example.com:1521/SVC": ("ora.example.com", False),
            "jdbc:tc:postgresql:16:///app": ("(testcontainers)", True),
            "jdbc:h2:mem:testdb": ("(embedded)", True),
            "nats://${NATS_HOST:localhost}:4222": ("localhost", True),
            "http://[::1]:8080/api": ("[::1]", True),
            "${KAFKA:localhost:9092}": ("localhost", True),
            "${DB_URL}": ("(placeholder)", None),
            "broker-1.internal:9092,broker-2.internal:9092": ("broker-1.internal", False),
        }
        for value, (host, local) in cases.items():
            got = preflight_scan._endpoint(value)
            self.assertIsNotNone(got, value)
            self.assertEqual((host, local), (got[1], got[2]), value)
        self.assertIsNone(preflight_scan._endpoint("not a url at all"))


class EnvironmentOverrides(unittest.TestCase):
    def test_an_environment_variable_can_move_a_local_endpoint_elsewhere(self):
        tmp = tempfile.mkdtemp(prefix="jla-test-")
        try:
            write(tmp, "src/main/resources/application.properties",
                  "spring.datasource.url=jdbc:postgresql://${DB_HOST:localhost}:5432/app\n"
                  "orders.nats.url=nats://localhost:4222\n")
            files = ["src/main/resources/application.properties"]
            plain = {c["key"]: c for c in preflight_scan.scan_connections(tmp, files)}
            self.assertEqual((True, False), (plain["spring.datasource.url"]["local"],
                                             plain["spring.datasource.url"]["env_override"]))
            moved = {c["key"]: c for c in preflight_scan.scan_connections(
                tmp, files, {"DB_HOST": "db.prod.example.com", "ORDERS_NATS_URL": "nats://nats.prod.example.com:4222"})}
            self.assertEqual(("db.prod.example.com", False, True),
                             tuple(moved["spring.datasource.url"][k] for k in ("host", "local", "env_override")))
            self.assertEqual(("nats.prod.example.com", False, True),
                             tuple(moved["orders.nats.url"][k] for k in ("host", "local", "env_override")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Listeners(unittest.TestCase):
    def test_framework_lifecycle_listeners_are_not_business_events(self):
        tmp = tempfile.mkdtemp(prefix="jla-test-")
        try:
            write(tmp, "pom.xml", "<project><modelVersion>4.0.0</modelVersion></project>")
            write(tmp, "src/main/java/a/L.java", """package a;
                class L {
                    @EventListener(ApplicationReadyEvent.class) public void ready() {}
                    @EventListener public void refreshed(ContextRefreshedEvent e) {}
                    @TransactionalEventListener public void paid(OrderPaid e) {}
                }""")
            p = preflight_scan.scan(tmp, env=False)
            self.assertEqual([{"kind": "spring-events", "listeners": 1, "publishers": 0}], p["messaging"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Baseline(unittest.TestCase):
    def test_a_blocked_run_is_not_a_failing_code_base_and_secrets_are_not_recorded(self):
        tmp = tempfile.mkdtemp(prefix="jla-test-")
        try:
            profile = write(tmp, "profile.json", json.dumps({"schema": 1}))
            write(tmp, "reports/TEST-a.xml", '<testsuite name="a" time="6.5"><testcase classname="a" name="x" time="0.01">'
                                             '<error/></testcase></testsuite>')
            r = preflight_scan.baseline(
                profile, "mvn test -Dspring.datasource.password=hunter2 -Dspring.datasource.url=jdbc:x://h/db",
                [os.path.join(tmp, "reports")], blocked="Connection to 127.0.0.1:54329 refused")
            self.assertEqual("blocked", r["status"])
            self.assertEqual(6.5, r["seconds"])
            self.assertNotIn("hunter2", json.dumps(r))
            self.assertIn("password=[redacted]", r["command"])
            self.assertIn("jdbc:x://h/db", r["command"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_counts_come_from_parsed_result_files(self):
        tmp = tempfile.mkdtemp(prefix="jla-test-")
        try:
            profile = write(tmp, "profile.json", json.dumps({"schema": 1}))
            write(tmp, "reports/TEST-a.xml", '<testsuite name="a"><testcase classname="a" name="ok" time="0.5"/>'
                                             '<testcase classname="a" name="bad"><failure/></testcase>'
                                             '<testcase classname="a" name="skip"><skipped/></testcase></testsuite>')
            write(tmp, "reports/evil.xml", '<!DOCTYPE x [<!ENTITY a "b">]><testsuite><testcase name="x"/></testsuite>')
            r = preflight_scan.baseline(profile, "mvn test", [os.path.join(tmp, "reports")])
            self.assertEqual((3, 1, 0, 1, "failing"), (r["tests"], r["failures"], r["errors"], r["skipped"], r["status"]))
            r = preflight_scan.baseline(profile, "mvn test", [os.path.join(tmp, "nothing-here")])
            self.assertEqual("none-executed", r["status"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Manifest(unittest.TestCase):
    POM = "<project>\n  <modelVersion>4.0.0</modelVersion>\n</project>\n"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jla-test-")
        write(self.tmp, "pom.xml", self.POM)
        write(self.tmp, "src/main/java/a/A.java", "class A {}\n")
        write(self.tmp, "src/test/java/a/ATest.java", "class ATest {}\n")
        self.path = os.path.join(self.tmp, "audit", "sys", "manifest.json")
        manifest.build(self.tmp, self.path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_untouched(self):
        self.assertEqual("untouched", manifest.check(self.tmp, self.path)["status"])

    def test_the_plugins_own_files_are_not_tracked(self):
        write(self.tmp, "pom-audit.xml", self.POM.replace("</project>", "<profiles/></project>"))
        write(self.tmp, "src/audit/java/auditkit/Runner.java", "class Runner {}\n")
        write(self.tmp, "audit/sys/model/entities.json", "{}")
        self.assertEqual("untouched", manifest.check(self.tmp, self.path)["status"])

    def test_any_change_to_an_existing_file_is_reported(self):
        write(self.tmp, "pom.xml", self.POM.replace("</project>", "  <!-- a comment -->\n</project>"))
        write(self.tmp, "src/main/java/a/A.java", "class A { int x; }\n")
        write(self.tmp, "src/main/java/a/New.java", "class New {}\n")
        os.remove(os.path.join(self.tmp, "src/test/java/a/ATest.java"))
        r = manifest.check(self.tmp, self.path)
        self.assertEqual("changed", r["status"])
        self.assertEqual(["src/main/java/a/New.java"], r["added"])
        self.assertEqual(["src/test/java/a/ATest.java"], r["removed"])
        self.assertEqual(["pom.xml", "src/main/java/a/A.java"], r["modified"])

    def test_manifest_is_schema_valid(self):
        schema = os.path.join(SCRIPTS, "..", "schemas", "manifest.schema.json")
        self.assertEqual([], validate.validate_file(self.path, schema))


class Status(unittest.TestCase):
    INTENT = "---\nsystem: sys\nmodule: .\ngoal: %s\nlanguage: zh-TW\n%s---\n# Intent\n"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jla-test-")
        self.adir = os.path.join(self.tmp, "audit", "sys")
        write(self.tmp, "src/main/java/a/A.java", "class A {}\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, rel, text="{}", age=0):
        path = write(self.adir, rel, text)
        if age:
            t = time.time() - age
            os.utime(path, (t, t))
        return path

    def test_nothing_yet_points_at_the_front_door(self):
        st = status.status(self.tmp, "sys")
        self.assertEqual("/java-logic-audit:audit sys", st["next"])

    def test_after_intent_comes_preflight(self):
        self.put("INTENT.md", self.INTENT % ("audit", ""))
        st = status.status(self.tmp, "sys")
        self.assertEqual("/java-logic-audit:audit-preflight sys", st["next"])
        self.assertEqual("audit", st["goal"])

    def test_not_applicable_ends_the_road(self):
        self.put("INTENT.md", self.INTENT % ("audit", ""), age=30)
        self.put("PREFLIGHT.md", "x")
        self.put("profile.json", json.dumps({"applicability": {"verdict": "not-applicable", "reasons": []}}))
        manifest.build(self.tmp, os.path.join(self.adir, "manifest.json"))
        st = status.status(self.tmp, "sys")
        self.assertIsNone(st["next"])
        self.assertIn("not applicable", st["why"])

    def _through_characterize(self, goal="audit", extra=""):
        self.put("INTENT.md", self.INTENT % (goal, extra), age=100)
        self.put("PREFLIGHT.md", "x", age=90)
        self.put("profile.json", json.dumps({"applicability": {"verdict": "full", "reasons": []}}), age=90)
        manifest.build(self.tmp, os.path.join(self.adir, "manifest.json"))
        t = time.time() - 90
        os.utime(os.path.join(self.adir, "manifest.json"), (t, t))
        self.put("harness.json", age=80)
        self.put("facts/entries.json", age=80)
        self.put("map.json", age=70)
        self.put("MAP.md", "x", age=70)
        self.put("model_result.json", age=60)
        self.put("model/commands.json", age=60)
        self.put("MODEL.md", "x", age=60)
        self.put("model/recipes.json", age=50)
        self.put("model/reconciliation.json", age=50)

    def test_open_flagged_questions_wait_for_a_person(self):
        self._through_characterize()
        self.put("questions.json", json.dumps({"questions": [
            {"id": "Q-001", "flagged": True}, {"id": "Q-002", "flagged": True}, {"id": "Q-003", "flagged": False}]}),
            age=50)
        st = status.status(self.tmp, "sys")
        self.assertEqual("/java-logic-audit:audit-review sys", st["next"])
        self.assertEqual(2, st["review"]["flagged_open"])

        self.put("DECISIONS.json", json.dumps({"decisions": {
            "D-001": {"question": "Q-001"}, "D-002": {"question": "Q-002"}}}), age=40)
        st = status.status(self.tmp, "sys")
        self.assertEqual("/java-logic-audit:audit-verify sys", st["next"])

    def test_understand_road_stops_after_review(self):
        self._through_characterize(goal="understand")
        self.put("questions.json", json.dumps({"questions": []}), age=50)
        st = status.status(self.tmp, "sys")
        self.assertIsNone(st["next"])

    def test_spec_is_on_the_road_only_when_a_prd_is_named(self):
        self._through_characterize(goal="spec", extra="prd: docs/prd.md\n")
        self.put("questions.json", json.dumps({"questions": []}), age=50)
        st = status.status(self.tmp, "sys")
        self.assertEqual("/java-logic-audit:audit-spec sys", st["next"])

    def test_stale_artifact_is_named_and_rerun(self):
        self._through_characterize()
        self.put("questions.json", json.dumps({"questions": []}), age=50)
        self.put("facts/entries.json")                      # facts are now newer than the map
        st = status.status(self.tmp, "sys")
        self.assertEqual("/java-logic-audit:audit-map sys", st["next"])
        self.assertTrue(any(s.startswith("map ") for s in st["stale"]))

    def test_changed_source_asks_for_a_rerun(self):
        self._through_characterize()
        self.put("questions.json", json.dumps({"questions": []}), age=50)
        write(self.tmp, "src/main/java/a/A.java", "class A { int changed; }\n")
        st = status.status(self.tmp, "sys")
        self.assertEqual("/java-logic-audit:audit-rerun sys", st["next"])

    def test_system_name_is_checked(self):
        with self.assertRaises(SystemExit):
            status.status(self.tmp, "../etc")


class IntentFile(unittest.TestCase):
    GOOD = ("---\nsystem: sys\nmodule: .\ngoal: understand\nlanguage: auto\nbudget: quick\n"
            "base_commit: abc1234\ndate: 2026-11-21\n---\n# Intent\n")

    def check(self, text):
        tmp = tempfile.mkdtemp(prefix="jla-test-")
        try:
            return validate.validate_intent(write(tmp, "INTENT.md", text), "sys")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_well_formed_intent_is_valid(self):
        self.assertEqual([], self.check(self.GOOD))

    def test_common_mistakes_are_named(self):
        self.assertTrue(self.check("```\n" + self.GOOD + "```\n"))                 # wrapped in a code fence
        self.assertTrue(any("goal" in e for e in self.check(self.GOOD.replace("understand", "everything"))))
        self.assertTrue(any("prd" in e for e in self.check(self.GOOD.replace("understand", "spec"))))
        self.assertTrue(any("system" in e for e in self.check(self.GOOD.replace("system: sys", "system: other"))))
        self.assertTrue(any("budget" in e for e in self.check(self.GOOD.replace("budget: quick\n", ""))))


class Validator(unittest.TestCase):
    def test_reports_each_violation_with_its_path(self):
        schema = {"type": "object", "required": ["a"], "additionalProperties": False,
                  "properties": {"a": {"type": "array", "minItems": 1, "items": {"type": "integer", "minimum": 0}},
                                 "b": {"type": ["string", "null"], "enum": ["x", None]}}}
        self.assertEqual([], validate.validate({"a": [1], "b": None}, schema))
        errors = validate.validate({"a": [-1, True], "b": "y", "c": 1}, schema)
        self.assertEqual(4, len(errors), errors)
        self.assertTrue(any("$.a[1]" in e for e in errors))
        self.assertEqual(["$: missing required key 'a'"], validate.validate({}, schema))


if __name__ == "__main__":
    unittest.main()
