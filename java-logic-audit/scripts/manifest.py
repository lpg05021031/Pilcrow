#!/usr/bin/env python3
"""Source manifest: prove that the audit changed nothing it must not change.

    python3 manifest.py build <repo-root> --out audit/<system>/manifest.json
    python3 manifest.py check <repo-root> --manifest audit/<system>/manifest.json [--json]

The manifest lists every file under any src/main or src/test directory, and every build file,
with its size and SHA-256. `check` walks the tree again and reports files added, removed or
modified. Exit 0 when the source is untouched, 1 when it changed, 2 for unusable input.

The plugin never edits a tracked file. Its own files are not tracked: everything under audit/ and
src/audit/, and the generated copy of the Maven build file, pom-audit.xml.

This is a plain file walk. No version-control tool and nothing from the repository is run.
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402

MANIFEST_SCHEMA = 1
BUILD_FILES = {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
               "gradle.properties"}
MAX_BUILD_FILE = 4 << 20


def tracked(rel):
    name = os.path.basename(rel)
    if name in BUILD_FILES:
        return "build"
    p = "/" + rel
    if "/src/main/" in p or "/src/test/" in p:
        return "source"
    return None


def digest(path, kind):
    return _lib.sha256_file(path)


def snapshot(root):
    root = os.path.abspath(root)
    files = {}
    for rel in _lib.walk_files(root):
        kind = tracked(rel)
        if not kind:
            continue
        full = os.path.join(root, rel)
        try:
            sha = digest(full, kind)
            size = os.path.getsize(full)
        except OSError:
            continue
        files[rel] = {"kind": kind, "sha256": sha}
        if kind == "source":
            files[rel]["size"] = size
    return files


def build(root, out):
    files = snapshot(root)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "recorded": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(files),
        "files": files,
    }
    _lib.write_json(out, manifest)
    return manifest


def check(root, manifest_path):
    manifest = _lib.read_json(manifest_path)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        _lib.die("cannot read manifest: %s" % manifest_path)
    then = manifest["files"]
    now = snapshot(root)
    added = sorted(f for f in now if f not in then)
    removed = sorted(f for f in then if f not in now)
    modified = sorted(f for f in now if f in then and now[f]["sha256"] != then[f].get("sha256"))
    return {
        "status": "untouched" if not (added or removed or modified) else "changed",
        "tracked": len(now),
        "added": added, "removed": removed, "modified": modified,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("root")
    b.add_argument("--out", required=True)
    c = sub.add_parser("check")
    c.add_argument("root")
    c.add_argument("--manifest", required=True)
    c.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "build":
        m = build(args.root, args.out)
        print("manifest written: %s (%d files)" % (args.out, m["count"]))
        return 0
    result = check(args.root, args.manifest)
    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("source %s: %d tracked, %d added, %d removed, %d modified" % (
            result["status"], result["tracked"], len(result["added"]), len(result["removed"]),
            len(result["modified"])))
        for label in ("added", "removed", "modified"):
            for f in result[label][:40]:
                print("  %s: %s" % (label, f))
    return 0 if result["status"] == "untouched" else 1


if __name__ == "__main__":
    sys.exit(main())
