"""Shared helpers for the java-logic-audit scripts.

Everything these scripts read from the audited repository is untrusted text: it is parsed,
never executed, sizes are capped, and symlinks that leave the workspace are refused.
Python 3.8+, standard library only.
"""
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True

SYSTEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
MAX_TEXT = 2 << 20          # 2 MiB: larger files are skipped by the scanners
MAX_JSON = 16 << 20

# Directories that never hold audited source.
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".gradle", ".mvn", "node_modules",
    "target", "build", "out", "bin", "dist", "audit", ".claude",
}


def die(msg, code=2):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.exit(code)


def check_system(name):
    if not isinstance(name, str) or not SYSTEM_RE.match(name):
        die("unsafe system name %r: use letters, digits, hyphen and underscore" % (name,))
    return name


def audit_dir(workspace, system):
    return os.path.join(workspace, "audit", check_system(system))


def read_text(path, limit=MAX_TEXT):
    """Return the file's text, or None when it is missing, too large or unreadable."""
    try:
        if os.path.getsize(path) > limit:
            return None
        with open(path, "rb") as fh:
            raw = fh.read(limit + 1)
    except OSError:
        return None
    if len(raw) > limit:
        return None
    return raw.decode("utf-8", errors="replace")


def read_json(path, default=None):
    try:
        if os.path.getsize(path) > MAX_JSON:
            return default
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def canonical(obj):
    """One text for one value, whatever the order of its keys: for comparing and hashing."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def inside(root, path):
    """True when path, with symlinks resolved, stays under root."""
    root_real = os.path.realpath(root)
    real = os.path.realpath(path)
    return real == root_real or real.startswith(root_real + os.sep)


def walk_files(root, skip_dirs=SKIP_DIRS, skip_audit_sources=True):
    """Yield paths relative to root, with forward slashes, in sorted order.

    Symlinked directories are not followed, and src/audit (the plugin's own test sources)
    is left out so the audit never analyses itself.
    """
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in skip_dirs
            and not os.path.islink(os.path.join(dirpath, d))
            and not (skip_audit_sources and d == "audit" and rel_dir.endswith("src"))
        )
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full) and not inside(root, full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            yield rel


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_STRING = re.compile(r'"(?:\\.|[^"\\\n])*"')


def strip_java(text, keep_strings=False):
    """Remove comments (and string literal contents) so regexes only see code.

    Line structure is preserved: every removed character that is a newline stays a newline.
    """
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))

    out = _BLOCK_COMMENT.sub(blank, text)
    if not keep_strings:
        out = _STRING.sub(lambda m: '"' + " " * (len(m.group(0)) - 2) + '"', out)
    out = _LINE_COMMENT.sub(blank, out)
    return out


def count_loc(text):
    return sum(1 for line in text.splitlines() if line.strip())


# ---- JUnit-style XML ----------------------------------------------------------------------

def parse_junit_dir(path):
    """Parse every JUnit-style XML report under path.

    Returns {"files": n, "tests": n, "failures": n, "errors": n, "skipped": n, "time": s,
             "cases": [{"class", "name", "status"}]}. Unreadable or non-report files are ignored.
    """
    total = {"files": 0, "tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0, "cases": []}
    paths = []
    if os.path.isfile(path):
        paths = [path]
    elif os.path.isdir(path):
        for dirpath, _dirs, files in os.walk(path):
            for name in sorted(files):
                if name.lower().endswith(".xml"):
                    paths.append(os.path.join(dirpath, name))
    for p in sorted(paths):
        text = read_text(p, MAX_JSON)
        if text is None or "<!DOCTYPE" in text or "<!ENTITY" in text:
            continue
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        if not suites:
            continue
        total["files"] += 1
        for suite in suites:
            # A class that fails in setup reports its time on the suite, not on its cases.
            case_time = 0.0
            for case in suite.findall("testcase"):
                status = "passed"
                if case.find("failure") is not None:
                    status = "failed"
                elif case.find("error") is not None:
                    status = "error"
                elif case.find("skipped") is not None:
                    status = "skipped"
                total["tests"] += 1
                if status == "failed":
                    total["failures"] += 1
                elif status == "error":
                    total["errors"] += 1
                elif status == "skipped":
                    total["skipped"] += 1
                try:
                    case_time += float(case.get("time") or 0)
                except ValueError:
                    pass
                total["cases"].append({
                    "class": case.get("classname") or suite.get("name") or "",
                    "name": case.get("name") or "",
                    "status": status,
                })
            try:
                suite_time = float(suite.get("time") or 0)
            except ValueError:
                suite_time = 0.0
            total["time"] += max(suite_time, case_time)
    total["time"] = round(total["time"], 3)
    return total
