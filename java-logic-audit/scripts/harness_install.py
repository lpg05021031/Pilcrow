#!/usr/bin/env python3
"""Install the audit harness into the repository, deterministically.

    python3 harness_install.py plan <system>            what would be installed, as JSON
    python3 harness_install.py copy <system>            copy the auditkit sources into <module>/src/audit/java
    python3 harness_install.py build-entry <system>     write the build entry
    python3 harness_install.py settings <system>        write a first draft of audit/<system>/harness.json
    python3 harness_install.py command <system> --run smoke    print the exact command that runs a runner

All subcommands take --workspace DIR (default: the current directory).

Nothing that already exists in the repository is edited. Three places are added (design section 4.1):
audit/<system>/, <module>/src/audit/, and the build entry. For Maven the build entry is a generated
file, <module>/pom-audit.xml: the module's own pom.xml plus one profile, used with `-f pom-audit.xml`.
The project's pom.xml is never changed, so the artifact the normal build produces is byte for byte
the same (Maven packs pom.xml into the jar, so even a comment added to it would end up in the
artifact). For Gradle the build entry is an init script under audit/<system>/build/.

`command` regenerates pom-audit.xml first, so it always reflects the current pom.xml.

Which parts of auditkit are copied, and which test dependencies the profile adds, follow from
profile.json by fixed rules.
"""
import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402
import status as status_mod  # noqa: E402

PLUGIN = os.path.dirname(HERE)
KIT = os.path.join(PLUGIN, "assets", "auditkit")
VERSIONS = os.path.join(PLUGIN, "assets", "build", "versions.json")
AUDIT_POM = "pom-audit.xml"
DEFAULT_CLOCK = "2030-03-15T10:00:00Z"    # a fixed "now" for every case: a Friday, far from a month or year end
RUNS = ("smoke", "matrix", "faults", "sequences", "races")
MODES = ("record", "verify")


def load(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    profile = _lib.read_json(os.path.join(adir, "profile.json"))
    if not isinstance(profile, dict):
        _lib.die("audit/%s/profile.json not found: run audit-preflight first" % system)
    intent = status_mod.parse_intent(os.path.join(adir, "INTENT.md"))
    module = (intent.get("module") or profile.get("module") or ".").strip("/") or "."
    module_dir = os.path.normpath(os.path.join(workspace, module))
    if not _lib.inside(workspace, module_dir) or not os.path.isdir(module_dir):
        _lib.die("module directory not found inside the workspace: %s" % module)
    return adir, profile, module, module_dir


def plan(workspace, system):
    adir, profile, module, module_dir = load(workspace, system)
    versions = _lib.read_json(VERSIONS) or {}
    table = dict(versions.get("default", {}))
    if profile["build"].get("jdk") == "8":
        table.update(versions.get("jdk8", {}))

    fw = profile["framework"]["name"]
    modules = ["core"]
    if fw == "spring-boot":
        modules.append("spring")
        if any(o["kind"] == "restclient" for o in profile.get("outbound", [])):
            modules.append("spring-restclient")
    managed = fw == "spring-boot"        # Spring Boot's dependency management supplies JUnit and Mockito versions
    deps = [
        {"ga": "net.ttddyy:datasource-proxy", "version": table["net.ttddyy:datasource-proxy"], "why": "see every SQL statement and commit"},
        {"ga": "com.tngtech.archunit:archunit", "version": table["com.tngtech.archunit:archunit"], "why": "read calls and field writes from the compiled classes"},
    ]
    if fw == "spring-boot":
        # The harness calls HTTP entry points in-process through Spring's own test support. Most projects have it
        # through spring-boot-starter-test; one that has no tests at all does not, and would not compile the harness.
        deps.append({"ga": "org.springframework:spring-test", "version": None,
                     "why": "call the application's HTTP entry points in-process"})
    tests = profile.get("tests", {})
    if "junit5" not in tests.get("frameworks", []):
        # Spring Boot before 2.2 manages JUnit 5.3, which has the api and the engine but not yet the one artifact
        # that stands for both.
        old_boot = bool(re.match(r"^(1\.|2\.[01]\.)", profile["framework"].get("version") or ""))
        for ga in (("org.junit.jupiter:junit-jupiter-api", "org.junit.jupiter:junit-jupiter-engine") if managed and old_boot
                   else ("org.junit.jupiter:junit-jupiter",)):
            deps.append({"ga": ga, "version": None if managed else table["org.junit.jupiter:junit-jupiter"],
                         "why": "the runners are JUnit 5 tests"})
    if not tests.get("mockito"):
        deps.append({"ga": "org.mockito:mockito-core",
                     "version": None if managed else table["org.mockito:mockito-core"], "why": "recording doubles for outside systems"})
    # A module that is built from sibling modules of the same Maven build is run through the reactor, so that a
    # change in a sibling's source is compiled into the run instead of being taken from the local repository.
    reactor = None
    siblings = profile["build"].get("sibling_modules") or []
    if profile["build"]["tool"] == "maven" and module != "." and siblings:
        root_pom = _lib.read_text(os.path.join(workspace, "pom.xml")) or ""
        own_pom = _lib.read_text(os.path.join(module_dir, "pom.xml")) or ""
        listed = re.search(r"<module>\s*%s\s*</module>" % re.escape(module), root_pom)
        artifact = re.search(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", re.sub(r"<parent>.*?</parent>", "", own_pom, flags=re.S))
        if listed and artifact:
            reactor = {"file": AUDIT_POM, "artifact": artifact.group(1)}
    return {
        "system": system,
        "module": module,
        "reactor": reactor,
        "build_tool": profile["build"]["tool"],
        "supported": fw == "spring-boot",
        "modules": modules,
        "sources_to": os.path.join(module, "src", "audit", "java").replace(os.sep, "/"),
        "dependencies": deps,
        "plugins": {"org.codehaus.mojo:build-helper-maven-plugin": table["org.codehaus.mojo:build-helper-maven-plugin"]},
        "build_entry": (os.path.join(module, AUDIT_POM).replace(os.sep, "/") if profile["build"]["tool"] == "maven"
                        else "audit/%s/build/audit.init.gradle" % system),
    }


def copy(workspace, system):
    p = plan(workspace, system)
    if not p["supported"]:
        _lib.die("the harness supports Spring Boot applications only; this profile says %r" % (
            _lib.read_json(os.path.join(_lib.audit_dir(workspace, system), "profile.json"))["framework"]["name"],))
    dest_root = os.path.join(workspace, p["sources_to"])
    kit_dest = os.path.join(dest_root, "auditkit")
    if os.path.isdir(kit_dest):
        shutil.rmtree(kit_dest)                  # the kit is replaced as a whole; nothing else lives in this package
    copied = []
    for module in p["modules"]:
        src_root = os.path.join(KIT, module, "java")
        for dirpath, _dirs, files in os.walk(src_root):
            for name in sorted(files):
                if not name.endswith(".java"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name), src_root)
                target = os.path.join(dest_root, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(os.path.join(dirpath, name), target)
                copied.append(rel.replace(os.sep, "/"))
    return {"copied": sorted(copied), "to": p["sources_to"], "modules": p["modules"]}


def _dep_xml(dep, indent):
    g, a = dep["ga"].split(":")
    lines = ["<dependency>", "  <groupId>%s</groupId>" % g, "  <artifactId>%s</artifactId>" % a]
    if dep.get("version"):
        lines.append("  <version>%s</version>" % dep["version"])
    lines += ["  <scope>test</scope>", "</dependency>"]
    return "\n".join(indent + line for line in lines)


def maven_profile(p, indent="    "):
    i1, i2, i3, i4, i5, i6 = (indent + "  " * n for n in range(1, 7))
    deps = "\n".join(_dep_xml(d, i2) for d in p["dependencies"])
    helper = p["plugins"]["org.codehaus.mojo:build-helper-maven-plugin"]
    return "\n".join([
        indent + "<profile>",
        i1 + "<id>audit</id>",
        # A project that switches its tests off as a rule (maven.test.skip) must still compile and run the audit's.
        i1 + "<properties><maven.test.skip>false</maven.test.skip></properties>",
        i1 + "<dependencies>",
        deps,
        i1 + "</dependencies>",
        i1 + "<build>",
        i2 + "<plugins>",
        i3 + "<plugin>",
        i4 + "<groupId>org.codehaus.mojo</groupId>",
        i4 + "<artifactId>build-helper-maven-plugin</artifactId>",
        i4 + "<version>%s</version>" % helper,
        i4 + "<executions>",
        i5 + "<execution>",
        i6 + "<id>audit-sources</id>",
        i6 + "<phase>generate-test-sources</phase>",
        i6 + "<goals><goal>add-test-source</goal></goals>",
        i6 + "<configuration><sources><source>src/audit/java</source></sources></configuration>",
        i5 + "</execution>",
        i4 + "</executions>",
        i3 + "</plugin>",
        i3 + "<plugin>",
        i4 + "<groupId>org.apache.maven.plugins</groupId>",
        i4 + "<artifactId>maven-surefire-plugin</artifactId>",
        i4 + "<configuration>",
        i5 + "<includes><include>auditkit/**/*Runner.java</include></includes>",
        i5 + "<trimStackTrace>false</trimStackTrace>",
        # Written out, so that -DskipTests (given to keep sibling modules from running their own tests) does not reach here.
        i5 + "<skipTests>false</skipTests>",
        i5 + "<skip>false</skip>",
        i4 + "</configuration>",
        i3 + "</plugin>",
        i2 + "</plugins>",
        i1 + "</build>",
        indent + "</profile>",
    ])


HEADER = ("<!-- Generated by the java-logic-audit plugin from pom.xml: the same build plus the 'audit' profile below.\n"
          "     Do not edit; it is rewritten from pom.xml before every audit run. Used only with: mvn -f pom-audit.xml -Paudit -->\n")


def audit_pom(text, p):
    """The module's pom.xml with the audit profile added. The original text is otherwise kept as it is."""
    profile = maven_profile(p)
    last_profiles = text.rfind("</profiles>")
    project_end = text.rfind("</project>")
    if project_end < 0:
        _lib.die("pom.xml has no </project>")
    # Only the project's own <profiles> counts: one that comes after the last </build> or </dependencies>.
    own_profiles = last_profiles > max(text.rfind("</build>"), text.rfind("</dependencies>"), 0)
    if own_profiles:
        line_start = text.rfind("\n", 0, last_profiles) + 1
        out = text[:line_start] + profile + "\n" + text[line_start:]
    else:
        line_start = text.rfind("\n", 0, project_end) + 1
        out = text[:line_start] + "  <profiles>\n" + profile + "\n  </profiles>\n" + text[line_start:]
    # The header goes after the XML declaration, which must stay first.
    m = re.match(r"\s*<\?xml[^>]*\?>\s*\n?", out)
    at = m.end() if m else 0
    return out[:at] + HEADER + out[at:]


GRADLE_INIT = """// Written by the java-logic-audit plugin. Used only when passed with --init-script; no build file is changed.
// The harness runs in a task of its own, auditTest, so that what the project sets for its own tests
// (JUnit 4, filters, forks) neither applies to the audit nor is changed by it.
allprojects {
    plugins.withId('java') {
        if (project.projectDir == new File('%(module_dir)s')) {
            sourceSets.test.java.srcDir 'src/audit/java'
            dependencies {
%(deps)s
            }
            def auditTest = tasks.register('auditTest', Test) {
                description = 'Runs the java-logic-audit harness.'
                group = 'verification'
                testClassesDirs = sourceSets.test.output.classesDirs
                classpath = sourceSets.test.runtimeClasspath
                outputs.upToDateWhen { false }
            }
            // After the project's own build file has had its say: a block there that configures every Test task must not win.
            afterEvaluate {
                auditTest.configure {
                    useJUnitPlatform()
                    filter {
                        includeTestsMatching 'auditkit.*'
                        failOnNoMatchingTests = true
                    }
                    ignoreFailures = false
                    ['audit.dir', 'audit.run', 'audit.mode', 'audit.command', 'audit.case', 'audit.seed', 'audit.sequences', 'audit.steps', 'audit.pairs'].each { key ->
                        if (System.getProperty(key) != null) { systemProperty key, System.getProperty(key) }
                    }
                    ['AUDIT_DB_URL', 'AUDIT_DB_USER', 'AUDIT_DB_PASSWORD'].each { key ->
                        if (System.getenv(key) != null) { environment key, System.getenv(key) }
                    }
                }
            }
        }
    }
}
"""


def build_entry(workspace, system):
    p = plan(workspace, system)
    adir = _lib.audit_dir(workspace, system)
    module_dir = os.path.normpath(os.path.join(workspace, p["module"]))
    if p["build_tool"] == "maven":
        pom = os.path.join(module_dir, "pom.xml")
        text = _lib.read_text(pom)
        if text is None:
            _lib.die("cannot read %s" % pom)
        new = audit_pom(text, p)
        path = os.path.join(module_dir, AUDIT_POM)
        changed = _lib.read_text(path) != new
        if changed:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
        if p.get("reactor"):
            # The root build, with the audited module named by its generated POM: the same reactor otherwise.
            root_text = _lib.read_text(os.path.join(workspace, "pom.xml"))
            root_new = re.sub(r"(<module>\s*)%s(\s*</module>)" % re.escape(p["module"]),
                              lambda m: m.group(1) + p["module"] + "/" + AUDIT_POM + m.group(2), root_text, count=1)
            m = re.match(r"\s*<\?xml[^>]*\?>\s*\n?", root_new)
            at = m.end() if m else 0
            root_new = root_new[:at] + HEADER.replace("the same build plus the 'audit' profile below",
                                                    "the same modules, the audited one named by its own generated POM") + root_new[at:]
            root_path = os.path.join(workspace, AUDIT_POM)
            if _lib.read_text(root_path) != root_new:
                changed = True
                with open(root_path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(root_new)
    elif p["build_tool"] == "gradle":
        deps = "\n".join("                testImplementation '%s%s'" % (d["ga"], ":" + d["version"] if d.get("version") else "")
                         for d in p["dependencies"])
        # Gradle starts the tests through the JUnit Platform launcher; it must be the one that fits the engine on the
        # class path, not the one bundled with Gradle. Spring Boot's dependency management names the version.
        deps += "\n                testRuntimeOnly 'org.junit.platform:junit-platform-launcher'"
        new = GRADLE_INIT % {"module_dir": module_dir.replace("\\", "/"), "deps": deps}
        path = os.path.join(adir, "build", "audit.init.gradle")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        changed = _lib.read_text(path) != new
        if changed:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
    else:
        _lib.die("unsupported build tool: %s" % p["build_tool"])
    return {"file": os.path.relpath(path, workspace).replace(os.sep, "/"), "written": changed,
            "adds": [d["ga"] + (":" + d["version"] if d.get("version") else "") for d in p["dependencies"]]}


# The outermost client of each messaging system: what the application's own code ends up calling.
MESSAGING_EDGE = {
    "nats": "io.nats.client.Connection",
    "kafka": "org.springframework.kafka.core.KafkaTemplate",
    "rabbitmq": "org.springframework.amqp.rabbit.core.RabbitTemplate",
    "jms": "org.springframework.jms.core.JmsTemplate",
}
HTTP_CLIENTS = ("restclient", "resttemplate", "webclient", "feign", "java-http")


def settings(workspace, system):
    """Write the part of harness.json that follows from the profile. An existing file is never overwritten."""
    adir, profile, _module, _module_dir = load(workspace, system)
    path = os.path.join(adir, "harness.json")
    rel = os.path.relpath(path, workspace).replace(os.sep, "/")
    if os.path.isfile(path):
        return {"file": rel, "written": False, "todo": (_lib.read_json(path, {}) or {}).get("todo", [])}
    apps = profile["framework"]["application_classes"]
    doubles = [{"type": MESSAGING_EDGE[m["kind"]], "kind": "messaging"}
               for m in profile.get("messaging", []) if m["kind"] in MESSAGING_EDGE and m.get("publishers")]
    http = sorted(o["kind"] for o in profile.get("outbound", []) if o["kind"] in HTTP_CLIENTS)
    todo = []
    if len(apps) != 1:
        todo.append("application: name the application class (%d candidates in the profile)" % len(apps))
    todo.append("database: 'project-container' (then name the test configuration that starts it in "
                "reuse_test_config) or 'environment' (then record the person's words offering the database)")
    if http:
        todo.append("http_doubles: one rule per outside HTTP call the application makes (%s): method, a URL "
                    "pattern, one example URL, status and a response body the application can read" % ", ".join(http))
    todo.append("rejections: the exception classes that mean a business refusal (the ones mapped to 4xx)")
    todo.append("base_data: SQL files with reference data that must exist before any command, if any")
    todo.append("schema: the project's SQL files that create the tables, only when nothing creates them at start-up")
    todo.append("result_code: only when the application answers HTTP 200 whatever happened and says in the response "
                "body whether it refused: the field that holds the code, the values that mean done, the values that "
                "mean a failure")
    todo.append("auth.sessions: only when requests are refused unless they say who is calling: the sign-in request "
                "of each kind of user and the headers built from its answer; a password that is not in the "
                "repository's own seed data is named as ${env.AUDIT_...}, never written")
    todo.append("properties: application properties the harness must set, if any")
    todo.append("clock: keep the instant below when the application reads time from a java.time.Clock bean; "
                "set it to null when the business rules need a particular date to start from, and name that date")
    draft = {
        "application": apps[0] if len(apps) == 1 else None,
        "database": {"source": None, "permission": None},
        "reuse_test_config": [],
        "profiles": [],
        "properties": {},
        "doubles": doubles,
        "http_doubles": [],
        "rejections": [],
        "base_data": [],
        "clock": DEFAULT_CLOCK,
        "todo": todo,
    }
    _lib.write_json(path, draft)
    return {"file": rel, "written": True, "todo": todo}


def command(workspace, system, run, mode=None, case=None, only_command=None, seed=None, sequences=None, steps=None, pairs=None):
    if run not in RUNS:
        _lib.die("unknown run %r: one of %s" % (run, ", ".join(RUNS)))
    if mode is not None and mode not in MODES:
        _lib.die("unknown mode %r: one of %s" % (mode, ", ".join(MODES)))
    for value in (case, only_command):                  # one id; for a command, several separated by commas
        if value is not None and not re.match(r"^[A-Za-z0-9_.\-]+(,[A-Za-z0-9_.\-]+)*$", value):
            _lib.die("not an id: %r" % value)
    entry = build_entry(workspace, system)            # keep the build entry in step with the project's build file
    p = plan(workspace, system)
    adir = os.path.abspath(_lib.audit_dir(workspace, system))
    props = "-Daudit.dir=%s -Daudit.run=%s" % (adir, run)
    for key, value in (("mode", mode), ("case", case), ("command", only_command)):
        if value is not None:
            props += " -Daudit.%s=%s" % (key, value)
    for key, value in (("seed", seed), ("sequences", sequences), ("steps", steps), ("pairs", pairs)):
        if value is not None:
            props += " -Daudit.%s=%d" % (key, int(value))
    if p["build_tool"] == "maven":
        tool = "./mvnw" if os.path.isfile(os.path.join(workspace, "mvnw")) else "mvn"
        if p.get("reactor"):
            # The module and the sibling modules it is built from, in one build; only the audit's tests run.
            return "%s -B -f %s -pl :%s -am -Paudit -DskipTests test %s" % (tool, p["reactor"]["file"], p["reactor"]["artifact"], props)
        return "%s -B -f %s -Paudit test %s" % (tool, entry["file"], props)
    tool = "./gradlew" if os.path.isfile(os.path.join(workspace, "gradlew")) else "gradle"
    task = "auditTest" if p["module"] == "." else ":%s:auditTest" % p["module"].replace("/", ":")
    return "%s --init-script %s %s %s" % (tool, entry["file"], task, props)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "copy", "build-entry", "settings", "command"):
        s = sub.add_parser(name)
        s.add_argument("system")
        s.add_argument("--workspace", default=".")
        if name == "command":
            s.add_argument("--run", required=True, help="one of: " + ", ".join(RUNS))
            s.add_argument("--mode", help="for the matrix: record (compare with what reading the code predicts) "
                                          "or verify (compare with what is pinned, and fail on a difference)")
            s.add_argument("--case", help="run one cell, by id")
            s.add_argument("--only", help="run the cells of one command, by id")
            s.add_argument("--seed", type=int, help="for sequences: the seed everything random follows from (default 1)")
            s.add_argument("--sequences", type=int, help="for sequences: how many (default 50)")
            s.add_argument("--steps", type=int, help="for sequences: calls in each (default 20)")
            s.add_argument("--pairs", type=int, help="for races: how many pairs of commands (default 10)")
    args = ap.parse_args(argv)
    workspace = os.path.abspath(args.workspace)

    if args.cmd == "plan":
        json.dump(plan(workspace, args.system), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    elif args.cmd == "copy":
        r = copy(workspace, args.system)
        print("copied %d files of auditkit (%s) to %s" % (len(r["copied"]), ", ".join(r["modules"]), r["to"]))
    elif args.cmd == "build-entry":
        r = build_entry(workspace, args.system)
        print("%s %s" % ("wrote" if r["written"] else "up to date:", r["file"]))
        print("it adds, for audit runs only: " + ", ".join(r["adds"]))
        print("no existing build file was changed")
    elif args.cmd == "settings":
        r = settings(workspace, args.system)
        print("%s %s" % ("wrote a draft:" if r["written"] else "kept the existing", r["file"]))
        for item in r["todo"]:
            print("  to settle: " + item)
    else:
        print(command(workspace, args.system, args.run, args.mode, args.case, args.only,
                      args.seed, args.sequences, args.steps, args.pairs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
