#!/usr/bin/env python3
"""Build the map of the system from the facts the harness extracted.

    python3 build_map.py <system> [--workspace DIR]

Reads audit/<system>/facts/{context,jpa,schema,bytecode}.json and writes:

    audit/<system>/map.json      entry points, entities, the write-site table, orphans, what is unresolved
    audit/<system>/MAP.md        the same for people
    audit/<system>/shards.json   the slices audit-model will read, one per entry group and one per entity

If audit/<system>/map_resolved.json exists, the writes it settles are merged in: each row names a
site and line from `unresolved`, the table and columns a reader found there, and the lines it cites.

Everything here is mechanical: no judgement, the same facts always give the same map. The call graph
comes from compiled classes with interface calls expanded to every implementation, so it
over-approximates: a path shown here may never run, but a path that runs through ordinary calls is
not missed. Reflection, dynamic proxies and SQL the script cannot read are listed under
`unresolved` for a reader to settle; they are never silently dropped.

A write site is a place outside an entity class that changes a mapped column: a call to an entity
method that assigns a mapped field (a setter or a domain method), a constructor call (a row is
created), a call that saves or deletes through a repository, or a call to a MyBatis mapper method
whose statement (in a mapper XML file or an annotation) inserts, updates or deletes.

A table that statements write and no class is mapped to is still an entity here, known by its table
alone: its columns, keys and lifecycle columns come from the schema.
"""
import argparse
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402
import sql_statements  # noqa: E402
import status as status_mod  # noqa: E402

MAP_SCHEMA = 1
SHARD_FILE_CAP = 30
REPOSITORY_ROOT = "org.springframework.data.repository.Repository"
SAVE_NAMES = ("save", "saveAll", "saveAndFlush", "saveAllAndFlush", "persist", "merge")
DELETE_NAMES = ("delete", "deleteAll", "deleteById", "deleteAllById", "deleteAllInBatch", "deleteInBatch", "remove")
LIFECYCLE_NAME = re.compile(r"(?:^|_)(?:status|state|stage|phase)$", re.I)      # a column that names where a row is in its life
LIFECYCLE_EVENTS = re.compile(r"(?:Application\w*Event|Context\w*Event|\w*ReadyEvent|\w*StartedEvent)$")
# APIs through which SQL is written that this script cannot read the columns of.
OPAQUE_WRITERS = {
    "org.springframework.jdbc.core.JdbcTemplate": ("update", "batchUpdate", "execute"),
    "org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate": ("update", "batchUpdate", "execute"),
    "org.springframework.jdbc.core.simple.JdbcClient": ("sql",),
    "jakarta.persistence.EntityManager": ("createNativeQuery", "createQuery", "createNamedQuery"),
    "javax.persistence.EntityManager": ("createNativeQuery", "createQuery", "createNamedQuery"),
    "jakarta.persistence.Query": ("executeUpdate",),
    "javax.persistence.Query": ("executeUpdate",),
}


def simple(name):
    return name.rsplit(".", 1)[-1]


def method_id(cls, key):
    return cls + "#" + key


def class_of(mid):
    return mid.split("#", 1)[0]


def name_of(mid):
    return mid.split("#", 1)[1].split("(", 1)[0]


class Graph:
    """Methods of the application's classes and the calls between them."""

    def __init__(self, types):
        self.types = types
        self.by_class_name = collections.defaultdict(lambda: collections.defaultdict(list))   # class -> name -> [mid]
        for cls, t in types.items():
            for key in t["methods"]:
                self.by_class_name[cls][key.split("(", 1)[0]].append(method_id(cls, key))
        self.subtypes = collections.defaultdict(set)
        for cls, t in types.items():
            for sup in t["supertypes"]:
                self.subtypes[sup].add(cls)
        self.callers = collections.defaultdict(set)      # callee mid -> {(caller mid, line)}
        self.calls = collections.defaultdict(list)       # caller mid -> [(owner, name, line)]
        for cls, t in types.items():
            for key, m in t["methods"].items():
                caller = method_id(cls, key)
                for c in m["calls"]:
                    self.calls[caller].append((c["owner"], c["name"], c["line"]))
                    for target in self.resolve(c["owner"], c["name"]):
                        self.callers[target].add((caller, c["line"]))

    def resolve(self, owner, name):
        """Every application method a call to owner.name can reach: the owner's own, an inherited one,
        and every override or implementation in a subtype."""
        out = []
        candidates = [owner] + sorted(self.subtypes.get(owner, ()))
        if owner in self.types:
            candidates += self.types[owner]["supertypes"]
        for cls in candidates:
            out += self.by_class_name.get(cls, {}).get(name, [])
        return out

    def is_a(self, cls, ancestor):
        return cls == ancestor or (cls in self.types and ancestor in self.types[cls]["supertypes"])

    def reach_up(self, start, stop=None):
        """Methods from which `start` can be reached, with breadth-first distance. `stop(mid)` true
        means: include that method but do not look beyond it."""
        seen = {start: 0}
        queue = collections.deque([start])
        while queue:
            cur = queue.popleft()
            if stop and cur != start and stop(cur):
                continue
            for caller, _line in self.callers.get(cur, ()):
                if caller not in seen:
                    seen[caller] = seen[cur] + 1
                    queue.append(caller)
        return seen

    def reach_down(self, start):
        seen = {start: 0}
        queue = collections.deque([start])
        while queue:
            cur = queue.popleft()
            for owner, name, _line in self.calls.get(cur, ()):
                for target in self.resolve(owner, name):
                    if target not in seen:
                        seen[target] = seen[cur] + 1
                        queue.append(target)
        return seen


def build_entries(context, graph, base):
    entries = []

    def handler_methods(handler):
        cls, name = handler.split("#", 1)
        return graph.by_class_name.get(cls, {}).get(name, [])

    for row in context.get("http", []):
        if not row["handler"].startswith(base + "."):
            continue
        body = [p["type"] for p in row["parameters"] if "RequestBody" in p["annotations"]]
        for verb in (row["methods"] or ["ANY"]):
            for path in row["patterns"]:
                entries.append({
                    "kind": "http", "method": verb, "path": path, "handler": row["handler"],
                    "parameters": [{"name": p["name"], "type": p["type"],
                                    "in": ("body" if "RequestBody" in p["annotations"] else
                                           "path" if "PathVariable" in p["annotations"] else
                                           "query" if "RequestParam" in p["annotations"] else
                                           "header" if "RequestHeader" in p["annotations"] else "other")}
                                   for p in row["parameters"]],
                    "body_type": body[0] if body else None,
                    "returns": row["returns"],
                    "sort": "1 %s %s" % (path, verb),
                })

    # Subscriptions made on a messaging double at start-up: createDispatcher(handler) then subscribe(subject).
    subjects = collections.defaultdict(list)
    current = None
    for call in context.get("double_calls_at_startup", []):
        if call["method"] == "createDispatcher" and call["args"]:
            current = call["args"][0].replace("(object) ", "")
        elif call["method"] == "subscribe" and call["args"] and current:
            subjects[current].append(call["args"][0])
    for row in context.get("message_handlers", []):
        entries.append({
            "kind": "message", "source": simple(row["interface"]), "destinations": sorted(subjects.get(row["class"], [])),
            "handler": row["class"] + "#onMessage", "bean": row["bean"], "public_methods": row["public_methods"],
            "sort": "2 %s" % row["class"],
        })
    for row in context.get("listeners", []):
        attrs = row.get("attributes", {})
        annotation = simple(row["annotation"])
        events = attrs.get("classes") or attrs.get("value") or row.get("parameters") or []
        if annotation in ("EventListener", "TransactionalEventListener"):
            if events and all(LIFECYCLE_EVENTS.search(simple(e)) for e in events):
                continue                      # framework start-up plumbing, not a business entry point
            entries.append({"kind": "event", "source": annotation, "destinations": sorted(events),
                            "handler": row["method"], "bean": row["bean"], "sort": "3 %s" % row["method"]})
        else:
            dest = []
            for key in ("topics", "queues", "destination", "topicPattern", "value"):
                v = attrs.get(key)
                dest += v if isinstance(v, list) else ([v] if v else [])
            entries.append({"kind": "message", "source": annotation, "destinations": sorted(dest),
                            "handler": row["method"], "bean": row["bean"], "sort": "2 %s" % row["method"]})
    for row in context.get("scheduled", []):
        when = ("cron " + row["cron"]) if row.get("cron") else \
               ("fixed delay " + row["fixed_delay"]) if row.get("fixed_delay") not in (None, "-1") else \
               ("fixed rate " + row["fixed_rate"])
        entries.append({"kind": "scheduled", "schedule": when, "handler": row["method"], "bean": row["bean"],
                        "sort": "4 %s" % row["method"]})

    entries.sort(key=lambda e: e.pop("sort"))
    for i, e in enumerate(entries, 1):
        e["id"] = "EP-%03d" % i
        e["handler_methods"] = sorted(handler_methods(e["handler"]))
    return [dict([("id", e["id"])] + [(k, v) for k, v in e.items() if k != "id"]) for e in entries]


def build_entities(jpa, schema, profile):
    tables = schema.get("tables", {})
    lifecycle = {e["class"]: e["lifecycle_fields"] for e in (profile.get("entities", {}).get("jpa") or [])}
    by_class = {}
    for cls, e in sorted(jpa.get("entities", {}).items()):
        table = e["table"].split(".")[-1]
        t = tables.get(table, {})
        column_property = {}
        for prop, p in e["properties"].items():
            for col in p["columns"]:
                column_property[col] = prop
        for col in e["id"]["columns"]:
            column_property[col] = e["id"]["property"]
        refs = []
        for prop, p in sorted(e["properties"].items()):
            if p["association"] and not p["collection"] and p["type"] in jpa["entities"]:
                refs.append({"property": prop, "columns": p["columns"], "entity_class": p["type"]})
        children = sorted(prop for prop, p in e["properties"].items() if p["collection"])
        by_class[cls] = {
            "id": "ENT-" + table,
            "class": cls,
            "table": table,
            "primary_key": t.get("primary_key", e["id"]["columns"]),
            "version_property": e.get("version"),
            "columns": [{"name": c["name"], "property": column_property.get(c["name"]), "type": c["type"],
                         "nullable": c["nullable"]} for c in t.get("columns", [])],
            "unique": [u for u in t.get("unique", []) if u != t.get("primary_key")],
            "references": refs,
            "collections": children,
            "lifecycle_candidates": lifecycle.get(cls, []),
            "properties": dict({prop: p["columns"] for prop, p in e["properties"].items()},
                               **({e["id"]["property"]: e["id"]["columns"]} if e["id"].get("property") else {})),
        }
    for e in by_class.values():
        for r in e["references"]:
            r["entity"] = by_class[r.pop("entity_class")]["id"]
        # The parent is the one entity this one points to that also holds a collection of it.
        parents = [r for r in e["references"]
                   if any(c for c in by_class.values() if c["id"] == r["entity"] and c["collections"])]
        e["parent"] = {"entity": parents[0]["entity"], "columns": parents[0]["columns"]} if len(parents) == 1 else None
    return by_class


def table_entities(schema, profile, wanted, entities):
    """An entity for each wanted table that no class is mapped to, described by the schema alone.

    Its lifecycle candidates are the columns the project's own DDL was seen to constrain to a set of
    values, and character columns named like a status. They are keyed "table:<name>", which is no class.
    """
    tables = schema.get("tables", {})
    mapped = {e["table"]: e for e in entities.values()}
    declared = {t.get("table"): t.get("columns", []) for t in (profile.get("entities", {}).get("ddl_tables_with_lifecycle") or [])}
    out = {}
    for name in sorted(wanted):
        t = tables.get(name)
        if t is None or name in mapped:
            continue
        # A status is kept as text or as a small number; a column of that name with another type (a date, a
        # flag) is something else.
        lifecycle = [c["name"] for c in t.get("columns", [])
                     if c["name"] in declared.get(name, []) or
                     (LIFECYCLE_NAME.search(c["name"]) and re.search(r"char|text|enum|int", (c.get("type") or "").lower())
                      and not re.search(r"bigint|bit|bool", (c.get("type") or "").lower()))]
        out["table:" + name] = {
            "id": "ENT-" + name, "class": None, "table": name,
            "primary_key": t.get("primary_key", []), "version_property": None,
            "columns": [{"name": c["name"], "property": None, "type": c["type"], "nullable": c["nullable"]} for c in t.get("columns", [])],
            "unique": [u for u in t.get("unique", []) if u != t.get("primary_key")],
            "references": [], "collections": [], "lifecycle_candidates": lifecycle,
            "properties": {c["name"]: [c["name"]] for c in t.get("columns", [])},
            "parent": None, "foreign_keys": t.get("foreign_keys", []),
        }
    known = dict({e["table"]: e["id"] for e in entities.values()}, **{e["table"]: e["id"] for e in out.values()})
    for e in out.values():
        for fk in e.pop("foreign_keys"):
            if fk.get("references_table") in known:
                e["references"].append({"property": None, "columns": [fk["column"]], "entity": known[fk["references_table"]]})
        if len(e["references"]) == 1:           # the one row it belongs to
            e["parent"] = {"entity": e["references"][0]["entity"], "columns": e["references"][0]["columns"]}
    return out


def entity_mutators(graph, entities):
    """entity class -> method name -> sorted list of properties the method assigns (directly or through
    another method of the same class). Constructors are under the name <init>."""
    out = {}
    for cls, ent in entities.items():
        t = graph.types.get(cls)
        if not t:
            continue
        mapped = set(ent["properties"]) | {p for p in [ent["version_property"]] if p}
        direct = {}
        for key, m in t["methods"].items():
            name = key.split("(", 1)[0]
            direct.setdefault(name, set()).update(
                f["name"] for f in m["fields"] if f["mode"] == "set" and f["owner"] == cls and f["name"] in mapped)
        changed = True
        while changed:                                   # a method that calls another method of its own class
            changed = False
            for key, m in t["methods"].items():
                name = key.split("(", 1)[0]
                for c in m["calls"]:
                    if c["owner"] == cls and c["name"] in direct and c["name"] != "<init>":
                        before = len(direct[name])
                        direct[name] |= direct[c["name"]]
                        changed = changed or len(direct[name]) != before
        out[cls] = {name: sorted(props) for name, props in direct.items() if props}
    return out


def build_write_sites(graph, entities, mutators, repositories, resolved=None, statements=None):
    sites, unresolved = [], []
    statements = statements or {}
    by_table = {e["table"]: e for e in entities.values()}
    repo_domain = {r["interface"]: r.get("domain_type") for r in repositories}
    for cls in sorted(graph.types):
        t = graph.types[cls]
        for key in sorted(t["methods"]):
            site = method_id(cls, key)
            for owner, name, line in graph.calls.get(site, ()):
                if owner in entities and owner != cls and name in mutators.get(owner, {}):
                    ent = entities[owner]
                    props = mutators[owner][name]
                    columns = sorted({c for p in props for c in ent["properties"].get(p, [])})
                    if name == "<init>":
                        sites.append({"entity": ent["id"], "table": ent["table"], "kind": "create", "columns": columns,
                                      "via": "new %s(...)" % simple(owner), "site": site, "line": line})
                    elif columns:
                        sites.append({"entity": ent["id"], "table": ent["table"], "kind": "update", "columns": columns,
                                      "via": "%s.%s" % (simple(owner), name), "site": site, "line": line})
                elif graph.is_a(owner, REPOSITORY_ROOT) or owner in repo_domain:
                    domain = repo_domain.get(owner)
                    ent = entities.get(domain) if domain else None
                    if name in SAVE_NAMES or name in DELETE_NAMES:
                        sites.append({"entity": ent["id"] if ent else None, "table": ent["table"] if ent else None,
                                      "kind": "save" if name in SAVE_NAMES else "delete", "columns": [],
                                      "via": "%s.%s" % (simple(owner), name), "site": site, "line": line})
                    elif any(r["interface"] == owner and q["name"] == name and q.get("modifying")
                             for r in repositories for q in r.get("queries", [])):
                        unresolved.append({"what": "modifying repository query", "via": "%s.%s" % (simple(owner), name),
                                           "site": site, "line": line})
                st = statements.get((owner, name))
                if st and st.get("table"):
                    ent = by_table.get(st["table"])
                    row = {"entity": ent["id"] if ent else None, "table": st["table"], "kind": st["kind"], "columns": st["columns"],
                           "via": "%s.%s" % (simple(owner), name), "site": site, "line": line, "sql_at": st["cite"]}
                    if st.get("dynamic"):
                        row["dynamic"] = True         # the columns are those assigned on some path of the statement
                    sites.append(row)
                elif st:
                    unresolved.append({"what": "SQL of a mapper statement the script could not read with certainty",
                                       "via": "%s.%s" % (simple(owner), name), "site": site, "line": line, "sql_at": st["cite"]})
                for api, names in OPAQUE_WRITERS.items():
                    if owner == api and name in names:
                        unresolved.append({"what": "SQL written through %s" % simple(api),
                                           "via": "%s.%s" % (simple(api), name), "site": site, "line": line})
            # A mapped field assigned from outside its entity class (a public or package-visible field).
            for f in t["methods"][key]["fields"]:
                if f["mode"] == "set" and f["owner"] in entities and f["owner"] != cls:
                    ent = entities[f["owner"]]
                    columns = ent["properties"].get(f["name"], [])
                    if columns:
                        sites.append({"entity": ent["id"], "table": ent["table"], "kind": "update", "columns": sorted(columns),
                                      "via": "%s.%s (field)" % (simple(f["owner"]), f["name"]), "site": site, "line": f["line"]})
    # Writes a reader settled: (site, line) -> table, columns, kind, cite.
    by_place = {(r.get("site"), r.get("line")): r for r in resolved or [] if r.get("table")}
    still = []
    for u in unresolved:
        r = by_place.get((u["site"], u["line"]))
        ent = next((e for e in entities.values() if r and e["table"] == r["table"]), None)
        if r:
            sites.append({"entity": ent["id"] if ent else None, "table": r["table"],
                          "kind": r.get("kind", "update"), "columns": sorted(r.get("columns", [])),
                          "via": u["via"], "site": u["site"], "line": u["line"], "resolved_by": r.get("cite")})
        else:
            still.append(u)
    unresolved = still
    sites.sort(key=lambda s: (s["table"] or "", s["kind"], s["site"], s["line"], s["via"]))
    for i, s in enumerate(sites, 1):
        s["id"] = "WS-%04d" % i
    return sites, unresolved


def attach_reachability(graph, sites, entries, transactions):
    """For each write site: the entries it can be reached from, and the transaction it would run in."""
    handler_to_entries = collections.defaultdict(list)
    for e in entries:
        for mid in e["handler_methods"]:
            handler_to_entries[mid].append(e["id"])
    tx = {}
    for t in transactions:
        cls, name = t["method"].split("#", 1)
        for mid in graph.by_class_name.get(cls, {}).get(name, []):
            tx[mid] = t

    for s in sites:
        up = graph.reach_up(s["site"])
        s["entries"] = sorted({ep for mid in up for ep in handler_to_entries.get(mid, [])})
        inside, bare = effective_transactions(graph, s["site"], tx, handler_to_entries)
        s["transaction"] = sorted("%s#%s %s%s" % (simple(class_of(m)), name_of(m), tx[m]["propagation"],
                                                 " read-only" if tx[m]["read_only"] else "") for m in inside)
        s["outside_transaction_from"] = sorted(bare)      # entries from which this write is reached with no transaction
    return sites


def effective_transactions(graph, site, tx, handler_to_entries):
    """Walk up from a write site and find the transaction it runs in on each path.

    A method's transaction settings apply only when the method is entered through its proxy: from
    another class, or as an entry point called by the framework. Called from a method of its own
    class, they do not apply, and the walk goes on upward. Returns (transactional methods that do
    take effect, entries from which the site is reached with no transaction at all).
    """
    inside, bare = set(), set()
    seen = {site}
    queue = collections.deque([site])
    while queue:
        cur = queue.popleft()
        is_entry = cur in handler_to_entries
        if is_entry:
            if cur in tx:
                inside.add(cur)
            else:
                bare.update(handler_to_entries[cur])
        for caller, _line in graph.callers.get(cur, ()):
            if cur in tx and class_of(caller) != class_of(cur):
                inside.add(cur)                           # entered through the proxy: covered, stop here
                continue
            if caller not in seen:
                seen.add(caller)
                queue.append(caller)
    return inside, bare


def lifecycle_writers(entities, sites):
    """For each lifecycle candidate of each entity: the classes that set it when a row is created, and
    the classes that change it afterwards. A column changed from several classes is where the
    conditions checked before the change are most likely to differ."""
    out = []
    for ent in sorted(entities.values(), key=lambda e: e["id"]):
        for prop in ent["lifecycle_candidates"]:
            for column in ent["properties"].get(prop, []):
                rows = [s for s in sites if s["entity"] == ent["id"] and column in s["columns"]]
                changed = collections.OrderedDict()
                for s in rows:
                    if s["kind"] != "create":
                        changed.setdefault(class_of(s["site"]), []).append(s["id"])
                out.append({
                    "entity": ent["id"], "column": column,
                    "set_at_creation_by": sorted({class_of(s["site"]) for s in rows if s["kind"] == "create"}),
                    "changed_by": [{"class": c, "write_sites": ids} for c, ids in sorted(changed.items())],
                })
    return out


def self_invocations(graph, transactions):
    """A transactional method called from another method of its own class: the call does not go through
    the proxy, so the annotation has no effect on that path."""
    out = []
    for t in transactions:
        cls, name = t["method"].split("#", 1)
        for mid in graph.by_class_name.get(cls, {}).get(name, []):
            for caller, line in sorted(graph.callers.get(mid, ())):
                if class_of(caller) == cls and caller != mid:
                    out.append({"method": mid, "called_from": caller, "line": line,
                                "propagation": t["propagation"], "read_only": t["read_only"]})
    return out


def source_path(workspace, module, cls, types):
    """Where a class is written: in the audited module, or in one of the sibling modules it is built from."""
    t = types.get(cls) or {}
    src = t.get("source")
    if not src:
        return None
    outer = cls.split("$", 1)[0]
    pkg = outer.rsplit(".", 1)[0].replace(".", "/") if "." in outer else ""
    modules = module if isinstance(module, (list, tuple)) else [module]
    for mod in modules:
        for root in ("src/main/java", "src/main/kotlin"):
            rel = os.path.normpath(os.path.join(mod, root, pkg, src)).replace(os.sep, "/")
            if os.path.isfile(os.path.join(workspace, rel)):
                return rel
    return None


def build_shards(workspace, module, graph, entries, entities, sites):
    def files_of(mids):
        seen, out = set(), []
        for mid in mids:
            path = source_path(workspace, module, class_of(mid), graph.types)
            if path and path not in seen:
                seen.add(path)
                out.append(path)
        return out

    shards = []
    groups = collections.OrderedDict()
    for e in entries:
        groups.setdefault(class_of(e["handler"]), []).append(e)
    for cls, group in groups.items():
        reach = {}
        for e in group:
            for mid in e["handler_methods"]:
                for m, d in graph.reach_down(mid).items():
                    reach[m] = min(d, reach.get(m, d))
        ordered = sorted(reach, key=lambda m: (reach[m], m))
        files = files_of(ordered)
        # A statement kept outside the classes (a mapper XML file) belongs to the slice of every entry that reaches it.
        ids = {e["id"] for e in group}
        for s in sites:
            path = (s.get("sql_at") or "").rsplit(":", 1)[0]
            if path and path not in files and ids & set(s["entries"]):
                files.append(path)
        shards.append({"name": "entries:" + simple(cls), "direction": "top-down", "entries": [e["id"] for e in group],
                       "files": files[:SHARD_FILE_CAP], "files_left_out": max(0, len(files) - SHARD_FILE_CAP)})
    for cls, ent in sorted(entities.items(), key=lambda kv: kv[1]["id"]):
        own = [s for s in sites if s["entity"] == ent["id"]]
        mids = [method_id(cls, k) for k in sorted(graph.types.get(cls, {}).get("methods", {}))]
        for s in own:
            mids.append(s["site"])
            mids += sorted(c for c, _l in graph.callers.get(s["site"], ()))
        files = files_of(mids)
        for s in own:
            path = (s.get("sql_at") or "").rsplit(":", 1)[0]
            if path and path not in files:
                files.append(path)
        shards.append({"name": "entity:" + ent["table"], "direction": "bottom-up", "entity": ent["id"],
                       "write_sites": [s["id"] for s in own],
                       "files": files[:SHARD_FILE_CAP], "files_left_out": max(0, len(files) - SHARD_FILE_CAP)})
    return shards


def build(workspace, system):
    adir = _lib.audit_dir(workspace, system)
    facts = os.path.join(adir, "facts")
    context = _lib.read_json(os.path.join(facts, "context.json"))
    bytecode = _lib.read_json(os.path.join(facts, "bytecode.json"))
    if not isinstance(context, dict) or not isinstance(bytecode, dict):
        _lib.die("facts not found under audit/%s/facts: run audit-harness first" % system)
    jpa = _lib.read_json(os.path.join(facts, "jpa.json"), {}) or {}
    schema = _lib.read_json(os.path.join(facts, "schema.json"), {}) or {}
    profile = _lib.read_json(os.path.join(adir, "profile.json"), {}) or {}
    intent = status_mod.parse_intent(os.path.join(adir, "INTENT.md"))
    module = (intent.get("module") or profile.get("module") or ".").strip("/") or "."
    base = context["base_package"]

    graph = Graph(bytecode["types"])
    entries = build_entries(context, graph, base)
    entities = build_entities(jpa, schema, profile)
    # The application is its own module and the sibling modules it is built from (profile.json names them).
    modules = [module] + [m for m in (profile.get("build", {}).get("sibling_modules") or []) if m != module]
    statements = {}
    for mod in modules:
        statements.update(sql_statements.statements(workspace, mod))
    entities.update(table_entities(schema, profile, {s["table"] for s in statements.values() if s.get("table")}, entities))
    mutators = entity_mutators(graph, entities)
    repositories = jpa.get("repositories", [])
    resolved = (_lib.read_json(os.path.join(adir, "map_resolved.json"), {}) or {}).get("resolved", [])
    sites, unresolved = build_write_sites(graph, entities, mutators, repositories, resolved, statements)
    attach_reachability(graph, sites, entries, context.get("transactions", []))

    for e in entries:                                   # what each entry can write, statically
        e["write_sites"] = [s["id"] for s in sites if e["id"] in s["entries"]]
        e["writes_tables"] = sorted({s["table"] for s in sites if e["id"] in s["entries"] and s["table"]})
    orphans = [s["id"] for s in sites if not s["entries"]]
    mapped_tables = {e["table"] for e in entities.values()}
    unmapped_tables = sorted(t for t in schema.get("tables", {}) if t not in mapped_tables)

    result = {
        "schema": MAP_SCHEMA,
        "base_package": base,
        "module": module,
        "entries": entries,
        "entities": sorted(({k: v for k, v in e.items() if k != "properties"} for e in entities.values()),
                           key=lambda e: e["id"]),
        "mutators": {entities[c]["id"]: m for c, m in sorted(mutators.items())},
        "statements": len(statements),
        "write_sites": sites,
        "orphan_write_sites": orphans,
        "unresolved": unresolved,
        "lifecycle_writers": lifecycle_writers(entities, sites),
        "self_invocations": self_invocations(graph, context.get("transactions", [])),
        "tables_without_entity": unmapped_tables,
        "stats": {
            "entries": len(entries),
            "entries_by_kind": dict(collections.Counter(e["kind"] for e in entries)),
            "entities": len(entities),
            "write_sites": len(sites),
            "orphan_write_sites": len(orphans),
            "write_sites_outside_transaction": sum(1 for s in sites if s["outside_transaction_from"]),
            "unresolved": len(unresolved),
        },
    }
    shards = build_shards(workspace, modules, graph, entries, entities, sites)
    _lib.write_json(os.path.join(adir, "map.json"), result)
    _lib.write_json(os.path.join(adir, "shards.json"), {"schema": 1, "shards": shards})
    with open(os.path.join(adir, "MAP.md"), "w", encoding="utf-8") as fh:
        fh.write(render(result, shards))
    return result, shards


def render(m, shards):
    out = ["# Map of %s" % m["base_package"], "",
           "Built by `build_map.py` from the facts the harness extracted. Nothing in this file was written by hand.",
           "", "Line numbers are the ones the compiled classes report. For a call written over several lines, the",
           "line shown can be the last line of the call instead of the first.",
           "", "## Entry points", "", "| Id | Kind | What | Handler | Tables it can write |", "|---|---|---|---|---|"]
    for e in m["entries"]:
        what = ("%s %s" % (e["method"], e["path"])) if e["kind"] == "http" else \
               e["schedule"] if e["kind"] == "scheduled" else \
               "%s %s" % (e["source"], ", ".join(e.get("destinations") or []) or "(destination not found)")
        handler = "%s#%s" % (simple(class_of(e["handler"])), e["handler"].split("#", 1)[1])
        out.append("| %s | %s | `%s` | `%s` | %s |" % (e["id"], e["kind"], what, handler,
                                                       ", ".join(e["writes_tables"]) or "none"))
    out += ["", "## Entities", "", "| Id | Class | Parent | Lifecycle candidates | Version |", "|---|---|---|---|---|"]
    for e in m["entities"]:
        out.append("| %s | `%s` | %s | %s | %s |" % (
            e["id"], simple(e["class"]) if e["class"] else "(no class: the table alone)", e["parent"]["entity"] if e["parent"] else "—",
            ", ".join(e["lifecycle_candidates"]) or "—", e["version_property"] or "—"))
    out += ["", "Tables with no mapped entity: " + (", ".join("`%s`" % t for t in m["tables_without_entity"]) or "none")]
    out += ["", "## Lifecycle columns and who writes them", "",
            "A column changed from more than one class is where the conditions checked before the change can differ.",
            "", "| Entity | Column | Set at creation by | Changed by |", "|---|---|---|---|"]
    for w in m["lifecycle_writers"]:
        changed = "; ".join("`%s` (%s)" % (simple(c["class"]), ", ".join(c["write_sites"])) for c in w["changed_by"])
        out.append("| %s | %s | %s | %s |" % (
            w["entity"], w["column"], ", ".join("`%s`" % simple(c) for c in w["set_at_creation_by"]) or "—",
            changed or "nothing changes it after creation"))
    out += ["", "## Write sites", "",
            "One row per place outside an entity class that changes its columns. A row with no entry is an orphan:",
            "nothing known can reach it. `outside a transaction` names the entries from which the write is reached",
            "without passing a transactional method.", ""]
    by_entity = collections.OrderedDict()
    for s in m["write_sites"]:
        by_entity.setdefault(s["entity"] or "(entity unknown)", []).append(s)
    for entity, rows in by_entity.items():
        out += ["### %s" % entity, "", "| Id | Kind | Columns | Via | Site | Entries | Transaction | Outside a transaction |",
                "|---|---|---|---|---|---|---|---|"]
        for s in rows:
            site = "%s#%s:%s" % (simple(class_of(s["site"])), s["site"].split("#", 1)[1].split("(", 1)[0], s["line"])
            where = site + (", SQL at %s" % s["sql_at"].rsplit("/", 1)[-1] if s.get("sql_at") else "")
            out.append("| %s | %s | %s | `%s` | `%s` | %s | %s | %s |" % (
                s["id"], s["kind"], (", ".join(s["columns"]) or "(row)") + (" (on some path)" if s.get("dynamic") else ""), s["via"], where,
                ", ".join(s["entries"]) or "**orphan**", "; ".join(s["transaction"]) or "—",
                ", ".join(s["outside_transaction_from"]) or "—"))
        out.append("")
    out += ["## Transactional methods called from their own class", ""]
    if m["self_invocations"]:
        out += ["The call does not go through the proxy, so the method's own transaction settings do not apply on that path.",
                "", "| Method | Called from | Line | Settings that do not apply |", "|---|---|---|---|"]
        for s in m["self_invocations"]:
            out.append("| `%s#%s` | `%s` | %s | %s%s |" % (
                simple(class_of(s["method"])), name_of(s["method"]), name_of(s["called_from"]), s["line"],
                s["propagation"], " read-only" if s["read_only"] else ""))
    else:
        out.append("None.")
    out += ["", "## Unresolved", ""]
    if m["unresolved"]:
        out += ["Writes the script could see but not read the columns of. A reader settles each one.", "",
                "| What | Via | Site | Line |", "|---|---|---|---|"]
        for u in m["unresolved"]:
            out.append("| %s | `%s` | `%s#%s` | %s |" % (u["what"], u["via"], simple(class_of(u["site"])),
                                                        name_of(u["site"]), u["line"]))
    else:
        out.append("None.")
    out += ["", "## Slices for the model step", "", "| Slice | Direction | Files | Left out |", "|---|---|---|---|"]
    for s in shards:
        out.append("| %s | %s | %d | %d |" % (s["name"], s["direction"], len(s["files"]), s["files_left_out"]))
    st = m["stats"]
    out += ["", "## Counts", "",
            "%d entry points (%s), %d entities, %d write sites, %d orphan, %d reached outside a transaction, %d unresolved." % (
                st["entries"], ", ".join("%d %s" % (n, k) for k, n in sorted(st["entries_by_kind"].items())),
                st["entities"], st["write_sites"], st["orphan_write_sites"], st["write_sites_outside_transaction"],
                st["unresolved"]), ""]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("system")
    ap.add_argument("--workspace", default=".")
    args = ap.parse_args(argv)
    result, shards = build(os.path.abspath(args.workspace), args.system)
    st = result["stats"]
    print("map written: %d entry points, %d entities, %d write sites (%d orphan, %d outside a transaction), "
          "%d unresolved, %d slices" % (st["entries"], st["entities"], st["write_sites"], st["orphan_write_sites"],
                                        st["write_sites_outside_transaction"], st["unresolved"], len(shards)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
