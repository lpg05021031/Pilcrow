#!/usr/bin/env python3
"""Static fingerprint of a Java repository for /java-logic-audit:audit-preflight.

    python3 preflight_scan.py scan <repo-root> [--module <path>] [--out <profile.json>] [--no-env]
    python3 preflight_scan.py baseline --profile <profile.json> --command "<cmd>" --junit <dir> [<dir> ...]

`scan` reads build files, Java sources, configuration and migrations and writes the technical
profile: build, framework, persistence, database, messaging, scheduling, outbound calls, test
assets, entity lifecycle candidates, connection endpoints, support (design section 15) and
applicability (design section 1). Every value is computed here by fixed rules; nothing in
profile.json is typed in by a model.

`baseline` parses the JUnit-style XML that a run of the existing test suite left behind and
records the counts in the profile (it updates profile.json in place). Status is `passing`,
`failing`, `none-executed`, or `blocked` when --blocked says the machine, not the code, stopped
the suite. Anything that looks like a credential in the recorded command is replaced.

Everything read is untrusted text: nothing is executed, sizes are capped, XML with a DOCTYPE or
an entity declaration is refused. With --no-env no process is started at all.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402

PROFILE_SCHEMA = 1
BUILD_NAMES = {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}

# ---- artifact rules: artifactId (exact or prefix*) -> (facet, value) ----------------------------
ARTIFACT_RULES = [
    ("spring-boot-starter-web", "web", "mvc"), ("spring-webmvc", "web", "mvc"),
    ("spring-boot-starter-webflux", "web", "webflux"), ("spring-webflux", "web", "webflux"),
    ("spring-boot-starter-data-jpa", "persistence", "jpa-hibernate"), ("hibernate-core", "persistence", "jpa-hibernate"),
    ("spring-data-jpa", "persistence", "jpa-hibernate"),
    ("mybatis*", "persistence", "mybatis"),
    ("spring-boot-starter-jdbc", "persistence", "spring-jdbc"), ("spring-jdbc", "persistence", "spring-jdbc"),
    ("spring-boot-starter-data-jdbc", "persistence", "spring-jdbc"),
    ("jooq", "persistence", "jooq"), ("spring-boot-starter-jooq", "persistence", "jooq"),
    ("spring-boot-starter-data-r2dbc", "persistence", "r2dbc"), ("r2dbc-*", "persistence", "r2dbc"),
    ("postgresql", "db", "postgresql"),
    ("mysql-connector-j", "db", "mysql"), ("mysql-connector-java", "db", "mysql"),
    ("mariadb-java-client", "db", "mariadb"),
    ("ojdbc*", "db", "oracle"), ("mssql-jdbc", "db", "sqlserver"),
    ("h2", "db", "h2"), ("hsqldb", "db", "hsqldb"), ("derby", "db", "derby"),
    ("flyway-*", "migrations", "flyway"), ("liquibase-core", "migrations", "liquibase"),
    ("spring-kafka", "messaging", "kafka"), ("kafka-clients", "messaging", "kafka"),
    ("kafka-streams", "streaming", "kafka-streams"), ("spring-cloud-stream*", "streaming", "spring-cloud-stream"),
    ("flink-*", "streaming", "flink"),
    ("spring-boot-starter-amqp", "messaging", "rabbitmq"), ("spring-rabbit", "messaging", "rabbitmq"),
    ("amqp-client", "messaging", "rabbitmq"),
    ("jnats", "messaging", "nats"),
    ("spring-jms", "messaging", "jms"), ("spring-boot-starter-activemq", "messaging", "jms"),
    ("spring-boot-starter-artemis", "messaging", "jms"),
    ("spring-boot-starter-quartz", "scheduling", "quartz"), ("quartz", "scheduling", "quartz"),
    ("spring-cloud-starter-openfeign", "outbound", "feign"), ("feign-core", "outbound", "feign"),
    ("grpc-stub", "outbound", "grpc"), ("grpc-netty*", "outbound", "grpc"),
    ("testcontainers", "tests", "testcontainers"), ("spring-boot-testcontainers", "tests", "testcontainers"),
    ("wiremock*", "tests", "wiremock"), ("spring-cloud-contract-wiremock", "tests", "wiremock"),
    ("junit", "testfw", "junit4"), ("junit-vintage-engine", "testfw", "junit4"),
    ("junit-jupiter*", "testfw", "junit5"), ("spring-boot-starter-test", "testfw", "junit5"),
    ("testng", "testfw", "testng"), ("spock-core", "testfw", "spock"),
    ("mockito-*", "testlib", "mockito"), ("spring-boot-starter-test", "testlib", "mockito"),
    ("drools-*", "engine", "drools"), ("camunda-*", "engine", "bpmn"), ("flowable-*", "engine", "bpmn"),
    ("activiti-*", "engine", "bpmn"),
    ("quarkus-*", "framework", "quarkus"), ("micronaut-*", "framework", "micronaut"),
    ("jakarta.ws.rs-api", "framework", "jax-rs"), ("javax.ws.rs-api", "framework", "jax-rs"),
    ("jersey-*", "framework", "jax-rs"), ("resteasy-*", "framework", "jax-rs"),
]
# Rules that need the groupId too, to avoid false hits on common artifact names.
GROUP_RULES = [
    ("org.testcontainers", "tests", "testcontainers"),
    ("org.postgresql", "db", "postgresql"),
    ("io.nats", "messaging", "nats"),
]

SUPPORTED = {
    "build": {"maven", "gradle"},
    "persistence": {"jpa-hibernate", "mybatis", "spring-jdbc"},
    "db": {"postgresql", "mysql"},
    "messaging": {"kafka", "rabbitmq", "nats", "spring-events"},
    "scheduling": {"spring-scheduled", "quartz"},
}
SUBSTITUTE_DBS = {"h2", "hsqldb", "derby"}
RELATIONAL = {"jpa-hibernate", "mybatis", "spring-jdbc", "jooq"}

# status, orderStatus, order_status, STATUS, ORDER_STATUS — but not "estate" or "backstage".
STATUS_NAME = re.compile(
    r"^(?:status|state|phase|stage)$|[a-z0-9](?:Status|State|Phase|Stage)$|_(?:status|state|phase|stage)$"
    r"|^(?:STATUS|STATE|PHASE|STAGE)$|_(?:STATUS|STATE|PHASE|STAGE)$")
STATUS_TYPE = re.compile(r"(?:Status|State|Phase|Stage)$")
FLAG_NAMES = {
    "cancelled", "canceled", "deleted", "closed", "locked", "active", "enabled", "archived", "approved",
    "confirmed", "completed", "paid", "shipped", "voided", "void", "frozen", "suspended", "expired",
}
STAMP_NAME = re.compile(
    r"^(?:cancell?ed|closed|deleted|approved|confirmed|completed|shipped|paid|voided|archived|expired)"
    r"(?:At|On|Date|Time)$")
STAMP_TYPES = {"LocalDateTime", "Instant", "OffsetDateTime", "ZonedDateTime", "Date", "LocalDate", "Timestamp"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0", "host.docker.internal"}
SECRET_KEY = re.compile(r"(?:pass(?:word)?|secret|token|credential|api[-_.]?key|private[-_.]?key)", re.I)


# ---- build files --------------------------------------------------------------------------------

def _safe_xml(text):
    if text is None or "<!DOCTYPE" in text or "<!ENTITY" in text:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    for el in root.iter():                       # drop the namespace so tags can be matched by plain name
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _txt(node, tag):
    child = node.find(tag) if node is not None else None
    return (child.text or "").strip() if child is not None and child.text else ""


def parse_pom(text):
    root = _safe_xml(text)
    out = {"parent": None, "props": {}, "deps": [], "plugins": [], "modules": [], "profiles": []}
    if root is None or root.tag != "project":
        return out
    parent = root.find("parent")
    if parent is not None:
        out["parent"] = (_txt(parent, "groupId"), _txt(parent, "artifactId"), _txt(parent, "version"))
    props = root.find("properties")
    if props is not None:
        for p in props:
            out["props"][p.tag] = (p.text or "").strip()

    def resolve(v):
        m = re.match(r"^\$\{([^}]+)\}$", v or "")
        return out["props"].get(m.group(1), "") if m else (v or "")

    for dep in root.iter("dependency"):
        out["deps"].append((_txt(dep, "groupId"), _txt(dep, "artifactId"), resolve(_txt(dep, "version")),
                            _txt(dep, "scope")))
    for plug in root.iter("plugin"):
        out["plugins"].append((_txt(plug, "groupId"), _txt(plug, "artifactId"), resolve(_txt(plug, "version"))))
    mods = root.find("modules")
    if mods is not None:
        out["modules"] = [(m.text or "").strip() for m in mods if (m.text or "").strip()]
    for prof in root.iter("profile"):
        pid = _txt(prof, "id")
        if pid:
            out["profiles"].append(pid)
    return out


_G_DEP = re.compile(r"""['"]([A-Za-z0-9_.\-]+):([A-Za-z0-9_.\-]+)(?::([^'"@:\s]+))?(?:@\w+)?['"]""")
_G_PLUGIN = re.compile(r"""\bid\s*\(?\s*['"]([\w.\-]+)['"]\s*\)?(?:\s*version\s*\(?\s*['"]([^'"]+)['"])?""")
_G_INCLUDE = re.compile(r"""['"]:?([\w.\-:/]+)['"]""")


def parse_gradle(text):
    out = {"deps": [], "plugins": [], "jdk": None}
    code = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    for m in _G_DEP.finditer(code):
        out["deps"].append((m.group(1), m.group(2), m.group(3) or "", ""))
    for m in _G_PLUGIN.finditer(code):
        out["plugins"].append((m.group(1), m.group(2) or ""))
    m = re.search(r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)", code)
    if not m:
        m = re.search(r"(?:sourceCompatibility|targetCompatibility)\s*=?\s*['\"]?(?:JavaVersion\.VERSION_)?([\d_.]+)", code)
    if m:
        out["jdk"] = _norm_jdk(m.group(1))
    return out


def parse_settings(text):
    code = re.sub(r"(?m)^\s*//.*$", "", text)
    mods = []
    for line in code.splitlines():
        if re.match(r"\s*include\b", line):
            mods += [m.group(1).lstrip(":").replace(":", "/") for m in _G_INCLUDE.finditer(line)]
    return mods


def _norm_jdk(v):
    v = (v or "").strip().replace("_", ".")
    if v.startswith("1."):
        v = v[2:]
    m = re.match(r"^(\d+)", v)
    return m.group(1) if m else None


def _match_artifact(pattern, artifact):
    return artifact.startswith(pattern[:-1]) if pattern.endswith("*") else artifact == pattern


# ---- java sources -------------------------------------------------------------------------------

_FIELD = re.compile(
    r"^\s*(?:(?:private|protected|public)\s+)?(?:static\s+)?(?:final\s+)?(?:transient\s+)?"
    r"([A-Za-z_][\w.]*(?:<[^;=()]*>)?(?:\[\])?)\s+([a-zA-Z_]\w*)\s*(?:=[^;]*)?;\s*$")
_NOT_TYPES = {"return", "throw", "new", "import", "package", "else", "break", "continue", "assert", "yield"}
_ANNOT_LINE = re.compile(r"^\s*@(\w+)")


def _class_fields(body):
    """Yield (type, name, annotations) for the fields declared directly in a class body.

    `body` starts right after the class name. Brace depth is tracked so that local variables
    inside methods and members of nested or following classes are not mistaken for fields.
    """
    pending, depth, opened = [], 0, False
    for line in body.splitlines():
        at_start = depth
        depth += line.count("{") - line.count("}")
        if depth >= 1:
            opened = True
        if opened and depth <= 0:
            return
        if at_start != 1:
            continue
        m = _FIELD.match(line)
        if m and m.group(1).split("<")[0].split(".")[-1] not in _NOT_TYPES and "(" not in line.split("=")[0]:
            inline = re.findall(r"@(\w+)", line.split(m.group(1))[0])
            yield m.group(1), m.group(2), pending + inline
            pending = []
            continue
        if _ANNOT_LINE.match(line):
            pending += re.findall(r"@(\w+)", line)
        elif re.search(r"[;{}]", line):
            pending = []


def _lifecycle_fields(fields, enums):
    out = []
    for ftype, name, annots in fields:
        base = ftype.split("<")[0].split(".")[-1]
        low = name.lower()
        is_enum = base in enums or "Enumerated" in annots
        if is_enum and (STATUS_NAME.search(name) or STATUS_TYPE.search(base)):
            out.append(name)
        elif base in ("String", "int", "Integer", "short", "Short", "char", "Character", "long", "Long") \
                and STATUS_NAME.search(name):
            out.append(name)
        elif base in ("boolean", "Boolean") and (low in FLAG_NAMES or (low.startswith("is") and low[2:] in FLAG_NAMES)):
            out.append(name)
        elif base in STAMP_TYPES and STAMP_NAME.match(name):
            out.append(name)
    return out


def _count(pattern, text):
    return len(re.findall(pattern, text))


def _method_level_request_mappings(code):
    n = 0
    for m in re.finditer(r"@RequestMapping\b", code):
        tail = code[m.end():m.end() + 600]
        cut = re.search(r"[{;]", tail)
        head = tail[:cut.start()] if cut else tail
        if not re.search(r"\b(class|interface|record|enum)\b", head):
            n += 1
    return n


# ---- configuration ------------------------------------------------------------------------------

def _flatten_yaml(text):
    """Flatten simple nested YAML mappings into dotted keys. Lists and anchors are ignored."""
    out, stack = {}, []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or raw.strip() == "---":
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if line.startswith("- "):
            continue
        m = re.match(r"^([^:#]+?):(?:\s+(.*))?$", line)
        if not m:
            continue
        key, val = m.group(1).strip().strip("'\""), (m.group(2) or "").strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        dotted = ".".join([k for _i, k in stack] + [key])
        if val and not val.startswith(("|", ">", "&", "*")):
            out[dotted] = re.sub(r"\s+#.*$", "", val).strip().strip("'\"")
        else:
            stack.append((indent, key))
    return out


def _flatten_properties(text):
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s[0] in "#!":
            continue
        m = re.match(r"^([^=:\s]+)\s*[=:]\s*(.*)$", s)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


_ENDPOINT_KEYS = re.compile(r"(?:url|uri|host|hosts|servers?|addresses|bootstrap[-.]servers|endpoint|base[-.]?url)$", re.I)
_PLACEHOLDER = re.compile(r"^\$\{[^:}]+(?::([^}]*))?\}$")
_PORT = re.compile(r":(?:\d+|\$\{[^}]*\})$")


def _endpoint(value):
    """Return (kind, host, local, placeholder) for a connection-looking value, or None.

    `local` is True, False, or None when the host cannot be known statically (a placeholder
    with no default). Credentials in the value are never returned.
    """
    v = value.strip().strip("'\"")
    if not v:
        return None
    if v.startswith("jdbc:tc:"):
        return ("jdbc-testcontainers", "(testcontainers)", True, False)
    if re.match(r"^jdbc:(h2|hsqldb|derby):", v) and "//" not in v:
        return ("jdbc-embedded", "(embedded)", True, False)
    if v.startswith(("jdbc:", "r2dbc:")):
        parts = v.split(":", 2)
        if len(parts) < 3:
            return None
        kind, rest = parts[0] + ":" + parts[1], parts[2]
    else:
        m = re.match(r"^([a-z][a-z0-9+.\-]*)://(.*)$", v)
        if m:
            kind, rest = m.group(1), m.group(2)
        else:
            kind, rest = "host", v
            if not re.match(r"^(?:[A-Za-z0-9_.\-]+|\$\{[^}]+\})(?::(?:\d+|\$\{[^}]+\}))?(?:,.*)?$", v):
                return None
    rest = re.sub(r"^(?:thin:)?@?/*", "", rest)
    # Split off path and parameters, but not inside a ${...} placeholder.
    authority = re.split(r"[/;?](?![^{]*\})", rest, maxsplit=1)[0]
    authority = authority.rsplit("@", 1)[-1].split(",")[0]
    host = authority[:authority.index("]") + 1] if authority.startswith("[") else _PORT.sub("", authority)
    placeholder = False
    pm = _PLACEHOLDER.match(host)
    if pm:                                       # ${NAME:default}: judge the default, e.g. localhost:9092
        placeholder = True
        host = _PORT.sub("", (pm.group(1) or "").strip().split(",")[0])
    if not host or "${" in host:
        return (kind, "(placeholder)", None, True)
    return (kind, host, host.lower() in LOCAL_HOSTS, placeholder)


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.]*)(?::[^}]*)?\}")


def _apply_environment(key, value, environ):
    """What the application would really use: an environment variable can replace a whole property
    (Spring's relaxed binding: spring.datasource.url <- SPRING_DATASOURCE_URL) or fill a ${NAME}."""
    if not environ:
        return value, False
    relaxed = re.sub(r"[^A-Za-z0-9]", "_", key.replace("-", "")).upper()
    if relaxed in environ:
        return environ[relaxed], True
    used = []

    def fill(m):
        if m.group(1) in environ:
            used.append(m.group(1))
            return environ[m.group(1)]
        return m.group(0)

    return _ENV_REF.sub(fill, value), bool(used)


def scan_connections(root, files, environ=None):
    out = []
    for rel in files:
        if "/resources/" not in "/" + rel:
            continue
        base = os.path.basename(rel)
        if not re.match(r"^(application|bootstrap)[\w\-]*\.(properties|ya?ml)$", base):
            continue
        text = _lib.read_text(os.path.join(root, rel))
        if text is None:
            continue
        kv = _flatten_properties(text) if base.endswith(".properties") else _flatten_yaml(text)
        scope = "test" if "/src/test/" in "/" + rel else "main"
        for key in sorted(kv):
            if SECRET_KEY.search(key) or not _ENDPOINT_KEYS.search(key):
                continue
            value, overridden = _apply_environment(key, kv[key], environ)
            ep = _endpoint(value)
            if ep is None:
                continue
            out.append({"file": rel, "scope": scope, "key": key, "kind": ep[0], "host": ep[1],
                        "local": ep[2], "placeholder": ep[3], "env_override": overridden})
    return out


# ---- the scan -----------------------------------------------------------------------------------

def sibling_modules(root, base):
    """Directories of the other modules of the same build that the module at `base` depends on, directly or not.

    An application is often one thin module that only starts what its sibling modules hold (the controllers
    in one, the persistence in another). The audit is about the application, so what it is made of belongs
    to the scan. Maven: dependencies on artifacts that sibling modules produce. Gradle: project(':x').
    """
    by_artifact, by_project = {}, {}
    cur = base
    while True:
        pom = _lib.read_text(os.path.join(cur, "pom.xml"))
        if pom:
            for rel in parse_pom(pom)["modules"]:
                d = os.path.normpath(os.path.join(cur, rel))
                if os.path.isfile(d):                      # a module may be named by its POM file
                    d = os.path.dirname(d)
                text = _lib.read_text(os.path.join(d, "pom.xml")) if _lib.inside(root, d) else None
                r = _safe_xml(text) if text else None
                if r is not None and _txt(r, "artifactId"):
                    by_artifact[_txt(r, "artifactId")] = d
        for name in ("settings.gradle", "settings.gradle.kts"):
            settings = _lib.read_text(os.path.join(cur, name))
            if settings:
                for rel in parse_settings(settings):
                    d = os.path.normpath(os.path.join(cur, rel))
                    if _lib.inside(root, d) and os.path.isdir(d):
                        by_project[":" + rel.replace("/", ":")] = d
        if cur == root:
            break
        cur = os.path.dirname(cur)
    found, todo = [], [base]
    while todo:
        d = todo.pop()
        wanted = []
        pom = _lib.read_text(os.path.join(d, "pom.xml"))
        if pom:
            wanted += [by_artifact[a] for _g, a, _v, _s in parse_pom(pom)["deps"] if a in by_artifact]
        for name in ("build.gradle", "build.gradle.kts"):
            text = _lib.read_text(os.path.join(d, name))
            if text:
                wanted += [by_project[m.group(1)] for m in re.finditer(r"project\s*\(\s*(?:path\s*:\s*)?['\"](:[\w.\-:]+)['\"]", text)
                           if m.group(1) in by_project]
        for w in wanted:
            if w != base and w not in found:
                found.append(w)
                todo.append(w)
    return sorted(found)


def scan(root, module=None, env=True):
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        _lib.die("not a directory: %s" % root)
    mod_rel = (module or ".").strip("/") or "."
    if mod_rel != "." and (mod_rel.startswith("..") or os.path.isabs(mod_rel)):
        _lib.die("--module must be a path inside the repository")
    base = os.path.normpath(os.path.join(root, mod_rel))
    if not _lib.inside(root, base) or not os.path.isdir(base):
        _lib.die("module directory not found inside the repository: %s" % mod_rel)

    files = list(_lib.walk_files(base))
    # The sibling modules the application is made of are part of it: their files are scanned too, named by
    # their path from the module ("../orders-db/src/main/...").
    siblings = sibling_modules(root, base) if mod_rel != "." else []
    for d in siblings:
        files += [os.path.relpath(os.path.join(d, f), base).replace(os.sep, "/") for f in _lib.walk_files(d)]

    # Build files: the module's own, and those of every ancestor up to the repository root
    # (parent POMs and root Gradle files carry versions and dependency management).
    build_paths = [os.path.join(base, f) for f in files if os.path.basename(f) in BUILD_NAMES]
    cur = base
    while cur != root:
        cur = os.path.dirname(cur)
        for name in sorted(BUILD_NAMES):
            p = os.path.join(cur, name)
            if os.path.isfile(p):
                build_paths.append(p)

    deps, plugins, modules, jdk, boot_version, maven_profiles = [], [], [], None, None, []
    has_maven = has_gradle = False
    jupiter_excluded = False
    for p in build_paths:
        name = os.path.basename(p)
        text = _lib.read_text(p)
        if text is None:
            continue
        # A project that stays on JUnit 4 takes JUnit 5 out of the test starter: then it is not there to build on.
        if re.search(r"exclude\s+group\s*:\s*['\"]org\.junit\.jupiter['\"]|exclude\s*\(\s*group\s*=\s*\"org\.junit\.jupiter\"", text) or \
                re.search(r"<exclusion>\s*<groupId>org\.junit\.jupiter</groupId>", text):
            jupiter_excluded = True
        if name == "pom.xml":
            has_maven = True
            pom = parse_pom(text)
            deps += pom["deps"]
            plugins += [(g + ":" + a if g else a, v) for g, a, v in pom["plugins"]]
            maven_profiles += pom["profiles"]
            if os.path.dirname(p) == base:
                modules += pom["modules"]
            if pom["parent"] and pom["parent"][1] == "spring-boot-starter-parent" and pom["parent"][2]:
                boot_version = boot_version or pom["parent"][2]
            for key in ("maven.compiler.release", "java.version", "maven.compiler.source", "maven.compiler.target"):
                if not jdk and pom["props"].get(key):
                    jdk = _norm_jdk(pom["props"][key])
            for g, a, v, _s in pom["deps"]:
                if a == "spring-boot-dependencies" and v:
                    boot_version = boot_version or v
        elif name.startswith("build.gradle"):
            has_gradle = True
            gr = parse_gradle(text)
            deps += gr["deps"]
            plugins += gr["plugins"]
            jdk = jdk or gr["jdk"]
            for pid, ver in gr["plugins"]:
                if pid == "org.springframework.boot" and ver:
                    boot_version = boot_version or ver
            for g, a, v, _s in gr["deps"]:
                if a == "spring-boot-dependencies" and v:
                    boot_version = boot_version or v
        elif name.startswith("settings.gradle"):
            has_gradle = True
            if os.path.dirname(p) == base:
                modules += parse_settings(text)

    facets = {}
    artifacts = set()
    for g, a, _v, _s in deps:
        if not a:
            continue
        artifacts.add(a)
        for pattern, facet, value in ARTIFACT_RULES:
            if _match_artifact(pattern, a):
                if pattern == "postgresql" and g and g != "org.postgresql":
                    continue
                if pattern == "junit" and g and g != "junit":
                    continue
                facets.setdefault(facet, set()).add(value)
        for group, facet, value in GROUP_RULES:
            if g == group:
                facets.setdefault(facet, set()).add(value)

    # ---- sources
    main_java = [f for f in files if "/src/main/java/" in "/" + f and f.endswith(".java")]
    test_java = [f for f in files if "/src/test/java/" in "/" + f and f.endswith(".java")]
    enums, sources = set(), {}
    main_loc = test_loc = 0
    for rel in main_java + test_java:
        text = _lib.read_text(os.path.join(base, rel))
        if text is None:
            continue
        code = _lib.strip_java(text)
        sources[rel] = (code, _lib.strip_java(text, keep_strings=True))
        if rel in main_java:
            main_loc += _lib.count_loc(code)
            enums.update(re.findall(r"\benum\s+(\w+)", code))
        else:
            test_loc += _lib.count_loc(code)

    main_code = "\n".join(sources[f][0] for f in main_java if f in sources)
    test_code = "\n".join(sources[f][0] for f in test_java if f in sources)
    all_imports = main_code + "\n" + test_code

    app_classes, sibling_apps, entities = [], [], []
    for rel in main_java:
        if rel not in sources:
            continue
        code, code_s = sources[rel]
        pkg = re.search(r"^\s*package\s+([\w.]+)\s*;", code, re.M)
        pkg = pkg.group(1) + "." if pkg else ""
        m = re.search(r"@SpringBootApplication\b[\s\S]{0,600}?\bclass\s+(\w+)", code)
        if m:
            # The application is the one the audited module starts; a sibling module that can also be started
            # on its own is not a second candidate.
            (sibling_apps if rel.startswith("../") else app_classes).append(pkg + m.group(1))
        for m in re.finditer(r"@Entity\b", code):
            cm = re.search(r"\bclass\s+(\w+)", code[m.end():m.end() + 800])
            if not cm:
                continue
            start = m.end() + cm.end()
            tm = re.search(r"@Table\s*\(\s*(?:[^)]*?\bname\s*=\s*)?\"([\w.]+)\"", code_s[max(0, m.start() - 400):start])
            body = code[start:]
            fields = list(_class_fields(body))
            entities.append({"class": pkg + cm.group(1), "table": tm.group(1) if tm else None,
                             "lifecycle_fields": _lifecycle_fields(fields, enums)})
    entities.sort(key=lambda e: e["class"])
    app_classes.sort()

    jakarta = bool(re.search(r"\bimport\s+jakarta\.(persistence|servlet|validation|transaction|annotation)\b", all_imports))
    javax = bool(re.search(r"\bimport\s+javax\.(persistence|servlet|validation|transaction)\b", all_imports))

    # ---- resources: mappers, migrations, engines
    mapper_xml = mapper_stmts = 0
    ddl_lifecycle, triggers, procedures, migration_files = [], 0, 0, 0
    drl = bpmn = 0
    for rel in files:
        low = rel.lower()
        if low.endswith(".drl"):
            drl += 1
        elif low.endswith(".bpmn") or low.endswith(".bpmn20.xml"):
            bpmn += 1
        # Schema scripts are kept in many places (sql/, db/, doc/): any .sql file of the application that is not
        # a test's counts. Mapper files are only looked for among the main resources.
        if "/src/test/" in "/" + rel or ("/src/main/" not in "/" + rel and not low.endswith(".sql")):
            continue
        if low.endswith(".xml") and "/resources/" in "/" + rel:
            text = _lib.read_text(os.path.join(base, rel))
            if text and re.search(r"<mapper\b[^>]*\bnamespace\s*=", text):
                mapper_xml += 1
                mapper_stmts += _count(r"<(?:select|insert|update|delete)\b", text)
        elif low.endswith(".sql"):
            text = _lib.read_text(os.path.join(base, rel))
            if text is None:
                continue
            if re.search(r"/db/(migration|changelog)/", "/" + rel) or re.match(r"^[VRU]\d", os.path.basename(rel)):
                migration_files += 1
            sql = re.sub(r"--[^\n]*", "", re.sub(r"/\*.*?\*/", " ", text, flags=re.S))
            triggers += _count(r"(?i)\bcreate\s+(?:or\s+replace\s+)?(?:constraint\s+)?trigger\b", sql)
            procedures += _count(r"(?i)\bcreate\s+(?:or\s+replace\s+)?(?:procedure|function)\b", sql)
            # The closing bracket of the column list is the one after which nothing bracketed follows before the
            # semicolon: MySQL writes the table's options there (ENGINE=..., COMMENT='...').
            for tm in re.finditer(r"(?is)\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?[`\"]?([\w.]+)[`\"]?\s*\((.*?)\)[^;()]*;", sql):
                cols = [c.strip().split()[0].strip('`"') for c in re.split(r",(?![^(]*\))", tm.group(2)) if c.strip()]
                life = [c for c in cols if STATUS_NAME.search(c)]
                if life:
                    ddl_lifecycle.append({"table": tm.group(1), "columns": life})
    ddl_lifecycle.sort(key=lambda t: t["table"])

    # ---- facets from source signals
    http_entries = _count(r"@(?:Get|Post|Put|Delete|Patch)Mapping\b", main_code) + _method_level_request_mappings(main_code)
    controllers = _count(r"@(?:Rest)?Controller\b", main_code)
    nats_files = [f for f in main_java if f in sources and re.search(r"\bimport\s+io\.nats\.client\b", sources[f][0])]
    nats_code = "\n".join(sources[f][0] for f in nats_files)

    messaging = {}

    def msg(kind, listeners, publishers):
        if kind in facets.get("messaging", set()) or listeners or publishers:
            messaging[kind] = {"kind": kind, "listeners": listeners, "publishers": publishers}

    def files_using(pattern):
        return sum(1 for f in main_java if f in sources and re.search(pattern, sources[f][0]))

    # listeners: annotated handler methods (NATS: subscribe calls). publishers: files that hold the
    # sending template (NATS and Spring events: publish call sites).
    msg("kafka", _count(r"@KafkaListener\b", main_code), files_using(r"\bKafkaTemplate\b"))
    msg("rabbitmq", _count(r"@RabbitListener\b", main_code), files_using(r"\b(?:RabbitTemplate|AmqpTemplate)\b"))
    msg("nats", _count(r"\.subscribe\s*\(", nats_code), _count(r"\.publish\s*\(", nats_code))
    msg("jms", _count(r"@JmsListener\b", main_code), files_using(r"\bJmsTemplate\b"))
    # A listener for the framework's own lifecycle (application ready, context refreshed) is start-up
    # plumbing, not a business event.
    ev_l = 0
    for m in re.finditer(r"@(?:Transactional)?EventListener\b", main_code):
        tail = main_code[m.end():m.end() + 400]
        cut = tail.find("{")
        head = tail if cut < 0 else tail[:cut]
        if not re.search(r"\b(?:Application\w*Event|Context\w*Event|\w*ReadyEvent|\w*StartedEvent)\b", head):
            ev_l += 1
    ev_p = _count(r"\.publishEvent\s*\(", main_code)
    if ev_l or ev_p:
        messaging["spring-events"] = {"kind": "spring-events", "listeners": ev_l, "publishers": ev_p}

    scheduling = []
    n_sched = _count(r"@Scheduled\b", main_code)
    if n_sched:
        scheduling.append({"kind": "spring-scheduled", "count": n_sched})
    n_quartz = _count(r"\b(?:implements\s+(?:[\w.,\s]*\b)?Job\b|extends\s+QuartzJobBean\b)", main_code)
    if "quartz" in facets.get("scheduling", set()) or n_quartz:
        scheduling.append({"kind": "quartz", "count": n_quartz})

    outbound = []
    for kind, pattern in (("feign", r"@FeignClient\b"), ("resttemplate", r"\bRestTemplate\b"),
                          ("restclient", r"\bRestClient\b"), ("webclient", r"\bWebClient\b"),
                          ("java-http", r"\bHttpClient\.new"), ("grpc", r"\bManagedChannel\b")):
        n = sum(1 for f in main_java if f in sources and re.search(pattern, sources[f][0]))
        if n:
            outbound.append({"kind": kind, "files": n})

    persistence = set(facets.get("persistence", set()))
    if entities:
        persistence.add("jpa-hibernate")
    if mapper_xml or re.search(r"@Mapper\b", main_code):
        persistence.add("mybatis")
    if re.search(r"\b(?:NamedParameter)?JdbcTemplate\b|\bJdbcClient\b", main_code):
        persistence.add("spring-jdbc")

    dbs = sorted(facets.get("db", set()))
    engines = [d for d in dbs if d not in SUBSTITUTE_DBS]
    migrations = sorted(facets.get("migrations", set()))

    # ---- framework
    fw = facets.get("framework", set())
    if app_classes or any(a.startswith("spring-boot") for a in artifacts) or \
            any(p[0].endswith("spring-boot-maven-plugin") or p[0] == "org.springframework.boot" for p in plugins):
        fw_name = "spring-boot"
    elif "quarkus" in fw:
        fw_name = "quarkus"
    elif "micronaut" in fw:
        fw_name = "micronaut"
    elif "jax-rs" in fw:
        fw_name = "jakarta-ee"
    elif any(a.startswith("spring-") for a in artifacts):
        fw_name = "spring"
    else:
        fw_name = "none"
    web = facets.get("web", set())
    web_kind = "mvc" if "mvc" in web else ("webflux" if "webflux" in web else "none")

    # ---- tests
    testfw = set(facets.get("testfw", set()))
    # The test starter brings JUnit 5 from Spring Boot 2.2 on; before that it brings JUnit 4.
    old_boot = bool(re.match(r"^(1\.|2\.[01]\.)", boot_version or ""))
    if "spring-boot-starter-test" in artifacts and old_boot and not any(a.startswith("junit-jupiter") for a in artifacts):
        testfw.discard("junit5")
        testfw.add("junit4")
    if jupiter_excluded and not any(a.startswith("junit-jupiter") for a in artifacts):
        testfw.discard("junit5")
    if re.search(r"\bimport\s+org\.junit\.jupiter\b", test_code):
        testfw.add("junit5")
    if re.search(r"\bimport\s+org\.junit\.(?:Test|Before|After|runner|Assert)\b", test_code):
        testfw.add("junit4")
    if not test_java:
        testfw = set()
    mocked = sorted(set(
        m.group(1).split(".")[-1] for m in re.finditer(
            r"@(?:MockBean|MockitoBean|SpyBean|MockitoSpyBean|Mock)\b(?:\([^)]*\))?\s+"
            r"(?:(?:private|protected|public)\s+)?([\w.]+)(?:<[^;]*>)?\s+\w+\s*;", test_code)))
    # Images the tests start, e.g. new PostgreSQLContainer<>("postgres:16-alpine"): the engine version
    # the existing tests really run against.
    test_code_s = "\n".join(sources[f][1] for f in test_java if f in sources)
    images = sorted(set(re.findall(
        r'(?:Container(?:<[^>()]*>)?\s*\(\s*|DockerImageName\.parse\(\s*)"([A-Za-z0-9][\w./\-]*:[\w.\-]+)"', test_code_s)))
    tests = {
        "files": len(test_java),
        "frameworks": sorted(testfw),
        "mockito": bool(test_java) and ("mockito" in facets.get("testlib", set()) or bool(re.search(r"\bimport\s+(?:static\s+)?org\.mockito\b", test_code))),
        "spring_boot_tests": _count(r"@SpringBootTest\b", test_code),
        "testcontainers": "testcontainers" in facets.get("tests", set()) or bool(re.search(r"\bimport\s+org\.testcontainers\b", test_code)),
        "wiremock": "wiremock" in facets.get("tests", set()),
        "substitute_db": sorted(d for d in dbs if d in SUBSTITUTE_DBS),
        "container_images": images,
        "mocked_types": mocked,
    }
    connections = scan_connections(base, files, os.environ if env else None)
    jdbc = [c for c in connections if c["kind"].startswith(("jdbc", "r2dbc"))]
    if tests["testcontainers"] or any(c["kind"] == "jdbc-testcontainers" for c in jdbc):
        tests["database_source"] = "container"
    elif tests["substitute_db"] or any(c["kind"] == "jdbc-embedded" and c["scope"] == "test" for c in jdbc):
        tests["database_source"] = "substitute"
    elif any(c["scope"] == "test" for c in jdbc):
        tests["database_source"] = "test-config"
    elif any(c["scope"] == "main" for c in jdbc) and test_java:
        tests["database_source"] = "main-config"
    else:
        tests["database_source"] = "none"

    profile = {
        "schema": PROFILE_SCHEMA,
        "module": mod_rel,
        "build": {
            "tool": "maven" if has_maven else ("gradle" if has_gradle else "unknown"),
            "wrapper": os.path.isfile(os.path.join(root, "mvnw")) or os.path.isfile(os.path.join(root, "gradlew")),
            "jdk": jdk,
            "modules": sorted(set(modules)),
            "audit_profile_present": "audit" in maven_profiles,
            **({"sibling_modules": [os.path.relpath(d, root).replace(os.sep, "/") for d in siblings]} if siblings else {}),
        },
        "framework": {
            "name": fw_name,
            "version": boot_version if fw_name == "spring-boot" else None,
            "web": web_kind,
            "namespace": "jakarta" if jakarta else ("javax" if javax else "none"),
            "application_classes": app_classes or sibling_apps,
        },
        "persistence": sorted(persistence),
        "database": {
            "engines": engines,
            "migrations": migrations,
            "migration_files": migration_files,
            "db_side_logic": {"triggers": triggers, "procedures": procedures},
        },
        "messaging": [messaging[k] for k in sorted(messaging)],
        "streaming": sorted(facets.get("streaming", set())),
        "scheduling": scheduling,
        "outbound": outbound,
        "entry_points": {
            "controllers": controllers,
            "http": http_entries,
            "message": sum(m["listeners"] for m in messaging.values() if m["kind"] != "spring-events"),
            "scheduled": sum(s["count"] for s in scheduling),
        },
        "entities": {
            "count": len(entities),
            "with_lifecycle": sum(1 for e in entities if e["lifecycle_fields"]),
            "jpa": entities,
            "mapper_xml": mapper_xml,
            "mapper_statements": mapper_stmts,
            "ddl_tables_with_lifecycle": ddl_lifecycle,
        },
        "transactions": {"annotated": _count(r"@Transactional\b", main_code)},
        "tests": tests,
        "non_java_logic": {"drools_files": drl, "bpmn_files": bpmn, "engines": sorted(facets.get("engine", set()))},
        "connections": connections,
        "size": {"main_java_files": len(main_java), "test_java_files": len(test_java),
                 "main_loc": main_loc, "test_loc": test_loc},
    }
    profile["support"] = support(profile)
    profile["applicability"] = applicability(profile)
    if env:
        profile["environment"] = environment(root)
    return profile


def support(p):
    """Design section 15: which detected technologies are inside the v1 support range."""
    out_of = []
    if p["build"]["tool"] not in SUPPORTED["build"]:
        out_of.append("build:" + p["build"]["tool"])
    jdk = p["build"]["jdk"]
    if jdk and not (8 <= int(jdk) <= 21):
        out_of.append("jdk:" + jdk)
    fw = p["framework"]
    if fw["name"] != "spring-boot":
        out_of.append("framework:" + fw["name"])
    else:
        major = (fw["version"] or "").split(".")[0]
        if major and major not in ("2", "3"):
            out_of.append("framework:spring-boot-" + major)
        if fw["web"] == "webflux":
            out_of.append("web:webflux")
    out_of += ["persistence:" + k for k in p["persistence"] if k not in SUPPORTED["persistence"]]
    out_of += ["database:" + d for d in p["database"]["engines"] if d not in SUPPORTED["db"]]
    out_of += ["messaging:" + m["kind"] for m in p["messaging"] if m["kind"] not in SUPPORTED["messaging"]]
    out_of += ["streaming:" + s for s in p["streaming"]]
    out_of += ["engine:" + e for e in p["non_java_logic"]["engines"]]
    return {"verdict": "supported" if not out_of else "static-only", "out_of_scope": out_of}


def applicability(p):
    """Design section 1: does the audit method apply at all?"""
    relational = [k for k in p["persistence"] if k in RELATIONAL]
    ent = p["entities"]
    lifecycle = ent["with_lifecycle"] + len(ent["ddl_tables_with_lifecycle"])
    reasons = []
    if not relational:
        reasons.append("no-relational-persistence")
        if p["streaming"]:
            reasons.append("stream-processing")
        if p["framework"]["name"] == "none" and not p["entry_points"]["http"] and not p["entry_points"]["message"]:
            reasons.append("library-without-entry-points")
        return {"verdict": "not-applicable", "reasons": reasons}
    if not lifecycle:
        return {"verdict": "not-applicable", "reasons": ["no-entity-with-lifecycle"]}
    db = p["database"]["db_side_logic"]
    if db["triggers"] or db["procedures"]:
        reasons.append("logic-in-database")
    nj = p["non_java_logic"]
    if nj["drools_files"] or nj["bpmn_files"] or nj["engines"]:
        reasons.append("logic-in-engine")
    return {"verdict": "partial" if reasons else "full", "reasons": reasons}


def environment(root):
    def run(cmd, timeout=15):
        try:
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
            return r.returncode, r.stdout.decode("utf-8", "replace")
        except (OSError, subprocess.TimeoutExpired):
            return None, ""

    rc, out = run(["java", "-version"])
    m = re.search(r'version "(\d+)(?:\.(\d+))?', out)
    java = (m.group(2) if m and m.group(1) == "1" else m.group(1)) if m else None
    crc, _ = run(["docker", "--version"])
    drc, _ = run(["docker", "info", "--format", "{{.ServerVersion}}"])
    return {
        "python": "%d.%d" % sys.version_info[:2],
        "java": java,
        "docker_client": crc == 0,          # the docker command exists
        "docker_daemon": drc == 0,          # a daemon answered: containers can actually start
        "maven_wrapper": os.path.isfile(os.path.join(root, "mvnw")),
        "gradle_wrapper": os.path.isfile(os.path.join(root, "gradlew")),
    }


# ---- baseline -----------------------------------------------------------------------------------

_SECRET_ARG = re.compile(r"(?i)((?:pass(?:word|wd)?|secret|token|credential|api[-_.]?key)[\w.\-]*\s*[=:]\s*)(\S+)")


def redact(text):
    return _SECRET_ARG.sub(lambda m: m.group(1) + "[redacted]", text or "")


def baseline(profile_path, command, junit_dirs, blocked=None, note=None):
    profile = _lib.read_json(profile_path)
    if not isinstance(profile, dict):
        _lib.die("cannot read profile: %s" % profile_path)
    total = {"files": 0, "tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    for d in junit_dirs:
        r = _lib.parse_junit_dir(d)
        for k in total:
            total[k] += r[k]
    total["time"] = round(total["time"], 3)
    if total["tests"] == 0:
        state = "none-executed"
    elif blocked:
        state = "blocked"
    else:
        state = "failing" if total["failures"] or total["errors"] else "passing"
    profile["baseline"] = {
        "command": redact(command),
        "recorded": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reports": list(junit_dirs),
        "result_files": total["files"],
        "tests": total["tests"], "failures": total["failures"], "errors": total["errors"],
        "skipped": total["skipped"], "seconds": total["time"],
        "status": state,
    }
    if blocked:
        profile["baseline"]["blocked_by"] = redact(blocked)[:400]
    if note:
        profile["baseline"]["note"] = redact(note)[:400]
    _lib.write_json(profile_path, profile)
    return profile["baseline"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("root")
    s.add_argument("--module", default=".")
    s.add_argument("--out")
    s.add_argument("--no-env", action="store_true")
    b = sub.add_parser("baseline")
    b.add_argument("--profile", required=True)
    b.add_argument("--command", required=True)
    b.add_argument("--junit", nargs="+", required=True, help="one or more result folders, separated by spaces")
    b.add_argument("--blocked", help="the suite could not run properly because of this machine, not the code: "
                                     "say what was missing (database unreachable, no Docker). Status becomes 'blocked'.")
    b.add_argument("--note", help="anything a reader needs to interpret the counts")
    args = ap.parse_args(argv)

    if args.cmd == "scan":
        profile = scan(args.root, args.module, env=not args.no_env)
        if args.out:
            _lib.write_json(args.out, profile)
            a, s_ = profile["applicability"], profile["support"]
            print("profile written: %s" % args.out)
            print("applicability: %s%s" % (a["verdict"], " (" + ", ".join(a["reasons"]) + ")" if a["reasons"] else ""))
            print("support: %s%s" % (s_["verdict"], " (" + ", ".join(s_["out_of_scope"]) + ")" if s_["out_of_scope"] else ""))
        else:
            json.dump(profile, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
    else:
        r = baseline(args.profile, args.command, args.junit, args.blocked, args.note)
        print("baseline: %(status)s, %(tests)d tests, %(failures)d failures, %(errors)d errors, %(skipped)d skipped" % r)
        if r["status"] == "blocked":
            print("blocked by: %s" % r["blocked_by"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
