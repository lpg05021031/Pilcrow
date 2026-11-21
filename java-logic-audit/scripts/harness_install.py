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
    tests = profile.get("tests", {})
    if "junit5" not in tests.get("frameworks", []):
        deps.append({"ga": "org.junit.jupiter:junit-jupiter",
                     "version": None if managed else table["org.junit.jupiter:junit-jupiter"], "why": "the runners are JUnit 5 tests"})
    if not tests.get("mockito"):
        deps.append({"ga": "org.mockito:mockito-core",
                     "version": None if managed else table["org.mockito:mockito-core"], "why": "recording doubles for outside systems"})
    return {
        "system": system,
        "module": module,
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
allprojects {
    plugins.withId('java') {
        if (project.projectDir == new File('%(module_dir)s')) {
            sourceSets.test.java.srcDir 'src/audit/java'
            dependencies {
%(deps)s
            }
            tasks.withType(Test).configureEach {
                useJUnitPlatform()
                filter { includeTestsMatching 'auditkit.*Runner' }
                ['audit.dir', 'audit.run', 'audit.mode', 'audit.command', 'audit.case', 'audit.seed', 'audit.sequences', 'audit.steps', 'audit.pairs'].each { key ->
                    if (System.getProperty(key) != null) { systemProperty key, System.getProperty(key) }
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
    elif p["build_tool"] == "gradle":
        deps = "\n".join("                testImplementation '%s%s'" % (d["ga"], ":" + d["version"] if d.get("version") else "")
                         for d in p["dependencies"])
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
    for value in (case, only_command):
        if value is not None and not re.match(r"^[A-Za-z0-9_.\-]+$", value):
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
        return "%s -B -f %s -Paudit test %s" % (tool, entry["file"], props)
    tool = "./gradlew" if os.path.isfile(os.path.join(workspace, "gradlew")) else "gradle"
    task = "test" if p["module"] == "." else ":%s:test" % p["module"].replace("/", ":")
    return "%s --init-script %s %s --rerun-tasks %s" % (tool, entry["file"], task, props)


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
