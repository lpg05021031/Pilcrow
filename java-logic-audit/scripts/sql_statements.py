#!/usr/bin/env python3
"""Read which table and columns a statement writes, for SQL that is written out in the project: MyBatis
mapper XML files and MyBatis annotations.

    python3 sql_statements.py <module dir>

Used by build_map.py. Nothing here runs SQL or parses XML with a parser: mapper files are read as text
(their DOCTYPE names a DTD on the network, which is never fetched), tags are removed, and what is left
is matched against the three shapes of a write:

    insert into T (a, b, ...) ...          a row is created; the columns are the ones listed
    update T set a = ..., b = ... where    columns a and b change
    delete from T ...                      a row is deleted

For dynamic SQL (<if>, <set>, <choose>, <foreach>, <trim>) the text of every branch is kept, so the
columns are the ones that can be assigned on some path, and the statement is marked `dynamic`.

A statement is returned without a table (and the map lists it as unresolved, for a reader) whenever
reading it with certainty is not possible: text substituted at run time (${...}), an insert without a
column list, a join in an update, a fragment that cannot be found, several statements in one.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import _lib  # noqa: E402

STATEMENT = re.compile(r"<(insert|update|delete|select)\b([^>]*)>(.*?)</\1\s*>", re.S)
FRAGMENT = re.compile(r"<sql\b[^>]*\bid\s*=\s*\"([^\"]+)\"[^>]*>(.*?)</sql\s*>", re.S)
ANNOTATION = re.compile(
    r"@(Insert|Update|Delete)\s*\(\s*(?:value\s*=\s*)?("
    r"\"\"\".*?\"\"\""                                              # a text block
    r"|\{\s*\"(?:[^\"\\]|\\.)*\"(?:\s*,\s*\"(?:[^\"\\]|\\.)*\")*\s*\}"      # { "...", "..." }
    r"|\"(?:[^\"\\]|\\.)*\"(?:\s*\+\s*\"(?:[^\"\\]|\\.)*\")*"         # "..." + "..."
    r")\s*\)", re.S)
METHOD_AFTER = re.compile(r"\s*(?:@\w+(?:\([^)]*\))?\s*)*[\w<>\[\],.?\s]+?\b(\w+)\s*\(", re.S)
DYNAMIC_TAGS = ("if", "choose", "when", "otherwise", "foreach", "trim", "set", "where", "bind")
NAME = r"[`\"\[]?([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)[`\"\]]?"


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def strip_tags(body, fragments):
    """The SQL text of a mapper statement: (text, dynamic), or (None, ...) when a fragment it includes is unknown."""
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    body = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", body, flags=re.S)
    body = re.sub(r"<selectKey\b.*?</selectKey\s*>", " ", body, flags=re.S)
    dynamic = False
    for _ in range(5):                                   # fragments can include fragments
        missing = []

        def include(m):
            if m.group(1) not in fragments:
                missing.append(m.group(1))
                return " "
            return " " + fragments[m.group(1)] + " "
        new = re.sub(r"<include\b[^>]*\brefid\s*=\s*\"([^\"]+)\"[^>]*/?>(?:\s*</include\s*>)?", include, body, flags=re.S)
        if missing:
            return None, dynamic
        if new == body:
            break
        body = new
    if re.search(r"<(?:%s)\b" % "|".join(DYNAMIC_TAGS), body):
        dynamic = True
    body = re.sub(r"<trim\b[^>]*\bprefix\s*=\s*\"([^\"]*)\"[^>]*>", r" \1 ", body, flags=re.S)
    body = re.sub(r"<set\b[^>]*>", " set ", body)
    body = re.sub(r"<where\b[^>]*>", " where ", body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = body.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return " ".join(body.split()), dynamic


def top_level(text, sep=","):
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append(text[start:i])
            start = i + 1
    out.append(text[start:])
    return [p.strip() for p in out if p.strip()]


def column(name):
    return name.strip().strip("`\"[]").split(".")[-1].lower()


def read_write(sql):
    """{"kind", "table", "columns"} of one write statement, or None when it cannot be read with certainty."""
    if sql is None or "${" in sql:
        return None
    sql = re.sub(r"#\{[^}]*\}", "?", sql).strip().rstrip(";").strip()
    if ";" in sql:
        return None
    low = sql.lower()
    m = re.match(r"(?:insert|replace)\s+(?:ignore\s+)?into\s+%s\s*\(([^)]*)\)" % NAME, sql, re.I)
    if m:
        columns = [column(c) for c in m.group(2).split(",") if c.strip()]
        again = re.search(r"\bon\s+duplicate\s+key\s+update\s+(.*)$", sql, re.I | re.S)
        if again:
            columns += [column(a.split("=", 1)[0]) for a in top_level(again.group(1)) if "=" in a]
        return {"kind": "create", "table": m.group(1).split(".")[-1].lower(), "columns": sorted(set(columns))}
    m = re.match(r"update\s+%s\s+(?:(?:as\s+)?(?!set\b)\w+\s+)?set\s+(.*)$" % NAME, sql, re.I | re.S)
    if m:
        rest = re.split(r"\swhere\s", " " + m.group(2) + " ", maxsplit=1, flags=re.I)[0]
        columns = [column(a.split("=", 1)[0]) for a in top_level(rest) if "=" in a]
        if not columns or re.search(r"\b(join|from)\b", rest, re.I):
            return None
        return {"kind": "update", "table": m.group(1).split(".")[-1].lower(), "columns": sorted(set(columns))}
    m = re.match(r"delete\s+from\s+%s(?:\s|$)" % NAME, sql, re.I)
    if m and " join " not in low:
        return {"kind": "delete", "table": m.group(1).split(".")[-1].lower(), "columns": []}
    return None


def from_xml(text, path):
    ns = re.search(r"<mapper\b[^>]*\bnamespace\s*=\s*\"([^\"]+)\"", text)
    if not ns:
        return {}
    clean = re.sub(r"<!--.*?-->", lambda m: " " * len(m.group(0)) if "\n" not in m.group(0) else re.sub(r"[^\n]", " ", m.group(0)),
                   text, flags=re.S)                       # comments blanked, positions and lines kept
    fragments = {m.group(1): m.group(2) for m in FRAGMENT.finditer(clean)}
    out = {}
    for m in STATEMENT.finditer(clean):
        verb = m.group(1)
        ident = re.search(r"\bid\s*=\s*\"([^\"]+)\"", m.group(2))
        if verb == "select" or not ident:
            continue
        sql, dynamic = strip_tags(m.group(3), fragments)
        entry = {"cite": "%s:%d-%d" % (path, line_of(clean, m.start()), line_of(clean, m.end())), "verb": verb, "dynamic": dynamic}
        entry.update(read_write(sql) or {"kind": None, "table": None, "columns": []})
        out[(ns.group(1), ident.group(1))] = entry
    return out


def literal(java):
    """The text of a Java string expression made of literals: "a" + "b", { "a", "b" } or a text block."""
    if java.startswith('"""'):
        return java[3:-3]
    parts = re.findall(r"\"((?:[^\"\\]|\\.)*)\"", java)
    return " ".join(p.replace('\\"', '"').replace("\\n", " ").replace("\\t", " ") for p in parts)


def from_java(text, path):
    if "@Insert" not in text and "@Update" not in text and "@Delete" not in text:
        return {}
    package = re.search(r"^\s*package\s+([\w.]+)\s*;", text, re.M)
    owner = re.search(r"\binterface\s+(\w+)", text)
    if not owner:
        return {}
    fqn = (package.group(1) + "." if package else "") + owner.group(1)
    out = {}
    for m in ANNOTATION.finditer(text):
        after = METHOD_AFTER.match(text, m.end())
        if not after:
            continue
        sql, dynamic = literal(m.group(2)), False
        if "<script" in sql:
            sql, dynamic = strip_tags(re.sub(r"</?script\s*>", " ", sql), {})
        else:
            sql = " ".join(sql.split())
        entry = {"cite": "%s:%d-%d" % (path, line_of(text, m.start()), line_of(text, after.end())), "verb": m.group(1).lower(),
                 "dynamic": dynamic}
        entry.update(read_write(sql) or {"kind": None, "table": None, "columns": []})
        out[(fqn, after.group(1))] = entry
    return out


def statements(workspace, module="."):
    """(mapper type, method) -> what the statement writes, for every write statement written out in the module's main sources."""
    root = os.path.normpath(os.path.join(workspace, module))
    out = {}
    for inside_module in _lib.walk_files(root):
        path = os.path.join(root, inside_module)
        rel = os.path.relpath(path, workspace).replace(os.sep, "/")
        if "/src/test/" in "/" + rel or not (rel.endswith(".xml") or rel.endswith(".java")):
            continue
        text = _lib.read_text(path)
        if not text:
            continue
        if rel.endswith(".xml"):
            if "<mapper" in text:
                out.update(from_xml(text, rel))
        else:
            out.update(from_java(text, rel))
    return out


if __name__ == "__main__":
    found = statements(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "."))
    json.dump({"%s#%s" % k: v for k, v in sorted(found.items())}, sys.stdout, indent=1)
    sys.stdout.write("\n")
