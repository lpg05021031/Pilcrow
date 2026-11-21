#!/usr/bin/env python3
"""Validate the audit's JSON artifacts against the schemas shipped with the plugin.

    python3 validate.py <system> [--workspace DIR]
    python3 validate.py --file <artifact.json> --schema <schema.json>

Exit 0 when every artifact that exists is valid, 1 when any is not, 2 for unusable input.
The validator supports the subset of JSON Schema the plugin's schemas use: type, properties,
required, additionalProperties, items, enum, pattern, minimum, minItems and anyOf.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True      # importing the sibling scripts must not leave __pycache__ in the plugin folder
sys.path.insert(0, HERE)
import _lib  # noqa: E402

SCHEMA_DIR = os.path.join(os.path.dirname(HERE), "schemas")
# artifact path under audit/<system>/ -> schema file
ARTIFACTS = [
    ("profile.json", "profile.schema.json"),
    ("manifest.json", "manifest.schema.json"),
]
_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "number": (int, float), "integer": int, "null": type(None),
}


def _is(value, tname):
    if tname in ("number", "integer") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[tname])


def validate(value, schema, path="$"):
    """Return a list of human-readable violations (empty when valid)."""
    errors = []
    if "anyOf" in schema:
        if not any(not validate(value, s, path) for s in schema["anyOf"]):
            errors.append("%s: matches none of the allowed shapes" % path)
        return errors
    t = schema.get("type")
    if t is not None:
        names = t if isinstance(t, list) else [t]
        if not any(_is(value, n) for n in names):
            return ["%s: expected %s, got %s" % (path, " or ".join(names), type(value).__name__)]
    if "enum" in schema and value not in schema["enum"]:
        errors.append("%s: %r is not one of %s" % (path, value, schema["enum"]))
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        errors.append("%s: %r does not match %s" % (path, value, schema["pattern"]))
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema \
            and value < schema["minimum"]:
        errors.append("%s: %r is below %r" % (path, value, schema["minimum"]))
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append("%s: missing required key %r" % (path, key))
        extra = schema.get("additionalProperties", True)
        for key, sub in value.items():
            if key in props:
                errors += validate(sub, props[key], "%s.%s" % (path, key))
            elif extra is False:
                errors.append("%s: unexpected key %r" % (path, key))
            elif isinstance(extra, dict):
                errors += validate(sub, extra, "%s.%s" % (path, key))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append("%s: fewer than %d items" % (path, schema["minItems"]))
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                errors += validate(item, schema["items"], "%s[%d]" % (path, i))
    return errors


def validate_file(artifact, schema_path):
    data = _lib.read_json(artifact, default=_lib)       # _lib is a sentinel no JSON value can equal
    if data is _lib:
        return ["cannot read or parse %s" % artifact]
    schema = _lib.read_json(schema_path)
    if not isinstance(schema, dict):
        _lib.die("cannot read schema: %s" % schema_path)
    return validate(data, schema)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("system", nargs="?")
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--file")
    ap.add_argument("--schema")
    args = ap.parse_args(argv)

    pairs = []
    if args.file:
        if not args.schema:
            _lib.die("--file needs --schema")
        pairs.append((args.file, args.schema))
    elif args.system:
        adir = _lib.audit_dir(os.path.abspath(args.workspace), args.system)
        for rel, schema in ARTIFACTS:
            p = os.path.join(adir, rel)
            if os.path.isfile(p):
                pairs.append((p, os.path.join(SCHEMA_DIR, schema)))
    else:
        _lib.die("give a system name, or --file with --schema")

    bad = 0
    for artifact, schema in pairs:
        errors = validate_file(artifact, schema)
        if errors:
            bad += 1
            print("INVALID %s" % artifact)
            for e in errors[:50]:
                print("  " + e)
        else:
            print("valid   %s" % artifact)
    if not pairs:
        print("nothing to validate")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
