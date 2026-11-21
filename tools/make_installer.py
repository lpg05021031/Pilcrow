#!/usr/bin/env python3
"""Pack the plugin into one shell script that unpacks it as a local Claude Code marketplace.

    python3 tools/make_installer.py [--out dist]

Writes dist/java-logic-audit-<version>.sh. Run on another machine, that script unpacks a directory with
.claude-plugin/marketplace.json and java-logic-audit/ in it, checks the content against a hash it
carries, and prints the two commands that add the directory as a marketplace and install the plugin.
It needs sh, tar and base64 (or openssl), nothing else, and it writes only inside the directory it is
given.

What goes in: the plugin as Claude Code loads it (commands, agents, scripts, the harness sources, the
model format) and the marketplace manifest. What stays out: the tests of the scripts, the samples under
fixtures/, and the design document; they are for working on the plugin, not for using it.

A development tool for the plugin itself; it is not part of the plugin.
"""
import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = "java-logic-audit"
LEAVE_OUT_DIRS = {"__pycache__", "tests"}

HEADER = r'''#!/bin/sh
# java-logic-audit %(version)s: a Claude Code plugin, packed as a local marketplace.
#
#   sh %(name)s [directory]        unpack (default: ~/claude-marketplaces/java-logic-audit)
#   sh %(name)s --list             show what is inside, unpack nothing
#
# It writes only inside the directory it unpacks to. An existing copy there is moved aside
# (<directory>.previous) before the new one is written. Nothing is installed into Claude Code
# by this script: it prints the two commands that do that.
set -eu

NAME="java-logic-audit"
VERSION="%(version)s"
SHA256="%(sha256)s"
TARGET="${1:-$HOME/claude-marketplaces/$NAME}"

payload() {
    sed '1,/^__PAYLOAD_BELOW__$/d' "$0"
}

decode() {
    if base64 -d </dev/null >/dev/null 2>&1; then
        base64 -d
    elif base64 -D </dev/null >/dev/null 2>&1; then
        base64 -D
    elif command -v openssl >/dev/null 2>&1; then
        openssl base64 -d -A
    else
        echo "need base64 or openssl to unpack" >&2
        exit 1
    fi
}

digest() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 | cut -d' ' -f1
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 | sed 's/^.*= *//'
    else
        echo "unchecked"
    fi
}

if [ "$TARGET" = "--list" ]; then
    payload | tr -d '\n' | decode | tar tzf -
    exit 0
fi
case "$TARGET" in
    -*) echo "usage: sh $0 [directory | --list]" >&2; exit 2 ;;
esac

GOT=$(payload | tr -d '\n' | decode | digest)
if [ "$GOT" != "unchecked" ] && [ "$GOT" != "$SHA256" ]; then
    echo "the content of this file does not match its own hash: it was changed or cut off in transfer." >&2
    echo "nothing was unpacked." >&2
    exit 1
fi

if [ -e "$TARGET" ]; then
    if [ ! -f "$TARGET/.claude-plugin/marketplace.json" ]; then
        echo "$TARGET exists and is not an earlier copy of this marketplace: choose another directory." >&2
        exit 1
    fi
    rm -rf "$TARGET.previous"
    mv "$TARGET" "$TARGET.previous"
    echo "an earlier copy was moved to $TARGET.previous"
fi
mkdir -p "$TARGET"
payload | tr -d '\n' | decode | tar xzf - -C "$TARGET"
TARGET=$(cd "$TARGET" && pwd)

cat <<DONE
$NAME $VERSION unpacked to $TARGET

To use it, in Claude Code:

    /plugin marketplace add $TARGET
    /plugin install $NAME@$NAME

or from a terminal:

    claude plugin marketplace add "$TARGET"
    claude plugin install $NAME@$NAME

Then, in the repository you want to audit, on a branch made for the audit:

    /$NAME:audit

After unpacking a newer version to the same directory:

    claude plugin marketplace update $NAME
    claude plugin update $NAME@$NAME

The plugin's scripts need python3 (3.8 or later) on the PATH. The audited project needs what it
needs to build and test itself (a JDK, Maven or Gradle) and a database that may be emptied.
DONE
exit 0
__PAYLOAD_BELOW__
'''


def members():
    """(name in the archive, path on disk), sorted, for everything that goes in."""
    out = [(".claude-plugin/marketplace.json", os.path.join(ROOT, ".claude-plugin", "marketplace.json"))]
    base = os.path.join(ROOT, PLUGIN)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in LEAVE_OUT_DIRS)
        for name in sorted(filenames):
            if name.endswith(".pyc"):
                continue
            full = os.path.join(dirpath, name)
            out.append((os.path.relpath(full, ROOT).replace(os.sep, "/"), full))
    return sorted(out)


def not_plain(data):
    """Why a file is not plain ASCII text, or None. Line number and what was found.

    Everything packed is meant to be read by whoever reviews the installer before running it: no byte
    above 0x7E (so no character that looks like another, and none that cannot be seen), and no control
    character except the line feed and the tab.
    """
    line = 1
    for b in data:
        if b == 0x0A:
            line += 1
        elif b > 0x7E:
            return "line %d: a byte that is not ASCII (0x%02X)" % (line, b)
        elif b < 0x20 and b != 0x09:
            return "line %d: a control character (0x%02X)" % (line, b)
    return None


HOSTED = re.compile(r"\b(?:github|gitlab|gitee)\.com[/:][\w.-]+|\bbitbucket\.org[/:][\w.-]+", re.I)
MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")


def origin_owner():
    """The account this checkout's `origin` belongs to, or None. Asked of git at build time, written nowhere."""
    try:
        url = subprocess.run(["git", "-C", ROOT, "remote", "get-url", "origin"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, universal_newlines=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    parts = [p for p in re.split(r"[/:]", re.sub(r"\.git$", "", url)) if p]
    return parts[-2] if len(parts) >= 2 and len(parts[-2]) >= 3 else None


def identifying(text, owner=None):
    """What in a text names an account or a person: the first such thing, or None.

    The installer is passed from hand to hand; it should say what the plugin is, not whose it is. Looked
    for: an e-mail address, an address on a code hosting site with a path behind it, and the account
    the checkout it is built from belongs to.
    """
    for line_no, line in enumerate(text.split("\n"), 1):
        m = MAIL.search(line) or HOSTED.search(line)
        if m:
            return "line %d: %s" % (line_no, m.group(0))
        if owner and owner.lower() in line.lower():
            return "line %d: the account this checkout belongs to" % line_no
    return None


def archive(files):
    """A tar.gz with nothing in it that changes from one build to the next."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, path in files:
            info = tarfile.TarInfo(name)
            with open(path, "rb") as fh:
                data = fh.read()
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    packed = io.BytesIO()
    with gzip.GzipFile(fileobj=packed, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    return packed.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    args = ap.parse_args()
    with open(os.path.join(ROOT, PLUGIN, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
        version = json.load(fh)["version"]
    files = members()
    odd = []
    for name, path in files:
        with open(path, "rb") as fh:
            why = not_plain(fh.read())
        if why:
            odd.append("  %s, %s" % (name, why))
    if odd:
        raise SystemExit("not packed: every file must be plain ASCII text\n" + "\n".join(odd))
    owner = origin_owner()
    for name, path in files:
        with open(path, encoding="ascii") as fh:
            why = identifying(fh.read(), owner)
        if why:
            odd.append("  %s, %s" % (name, why))
    if odd:
        raise SystemExit("not packed: a file names an account or a person\n" + "\n".join(odd))
    data = archive(files)
    name = "%s-%s.sh" % (PLUGIN, version)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, name)
    text = base64.encodebytes(data).decode("ascii")
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write(HEADER % {"version": version, "name": name, "sha256": hashlib.sha256(data).hexdigest()})
        fh.write(text)
    os.chmod(path, 0o755)
    print("%s: %d files, %d KB packed, %d KB as a script" % (
        os.path.relpath(path, ROOT), len(files), len(data) // 1024, os.path.getsize(path) // 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
