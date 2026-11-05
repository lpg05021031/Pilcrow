"""make_bundle.py -- build a self-contained, ASCII-only installer script.

    python deploy/bin/make_bundle.py                 # -> dist/pr-review-suite-install.sh
    python deploy/bin/make_bundle.py --full          # also eval fixtures, docs and markdown
    python deploy/bin/make_bundle.py --out PATH

What the generated .sh is, and deliberately is not:
  - plain bash + a small embedded Python block; the files travel as newline-delimited
    JSON inside a raw string literal (one object per line, ensure_ascii), so the whole
    script is 7-bit ASCII and human-readable: no base64, no compression, no eval, no
    network fetch, no chmod 777, no privilege escalation
  - each file carries a sha256; the installer verifies what it wrote
  - existing files that differ are backed up to <target>/.backup-<timestamp>/
  - pip is the only thing that touches the network, and only unless --no-pip

Default bundle carries what the service needs to run. Left out (they live in git):
eval/fixtures (golden set with deliberately insecure sample code, e.g. a fake API key
the reviewer must catch), docs/, root *.md, and the optional port-80 bridge unit.
--full adds them.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALWAYS_EXCLUDE = ("dist/*", "deploy/bin/make_bundle.py", ".gitattributes", ".gitignore")
DEFAULT_EXCLUDE = ("eval/fixtures/*", "docs/*", "*.md",
                   "deploy/systemd/pr-review-port80-bridge.service.example")
ALWAYS_KEEP = ("deploy/knowledge/*.md",)
EXECUTABLE = ("deploy/bin/*.sh",)

# Tokens that commonly trip automated reviews. The generator refuses to emit a bundle
# whose installer code contains them; payload files are scanned and only reported.
FORBIDDEN_IN_INSTALLER = ("base64", "eval ", "eval(", "curl ", "wget ", "| sh", "| bash",
                          "chmod 777", "sudo ", "rm -rf /")
REPORT_IN_PAYLOAD = ("password", "sudo ", "base64", "curl ", "chmod 777")

INSTALLER = r'''#!/usr/bin/env bash
# pr-review-suite -- self-contained installer (ASCII only)
# Generated {stamp} from commit {commit}; {nfiles} files, {nbytes} bytes of source.
#
# Usage:
#   bash {name} [--target DIR] [--no-pip] [--with-static] [--system] [--list] [--verify]
#
#   --target DIR    install root (default /opt/pr-review-suite)
#   --no-pip        write files only; skip venv + pip install
#   --with-static   also pip install semgrep and ruff (recommended)
#   --system        after writing, run shared.install (creates /etc and /var dirs and
#                   the systemd unit; needs a root shell)
#   --list          print the embedded file manifest and exit
#   --verify        check the embedded payload hashes and exit (writes nothing)
#
# How it works: the Python block carries one JSON object per line (path, permission,
# sha256, content) inside a raw string. Content is a JSON string (7-bit ASCII, no
# encoding tricks). Files are written with their original bytes and verified against
# the sha256; anything that already existed with different content is copied to
# <target>/.backup-<timestamp>/ first. Nothing is downloaded except by pip, and only
# when --no-pip is not given.
set -eu

TARGET=/opt/pr-review-suite
NO_PIP=0
WITH_STATIC=0
SYSTEM=0
MODE=install
while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --no-pip) NO_PIP=1; shift ;;
    --with-static) WITH_STATIC=1; shift ;;
    --system) SYSTEM=1; shift ;;
    --list) MODE=list; shift ;;
    --verify) MODE=verify; shift ;;
    -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then echo "python3 is required" >&2; exit 1; fi

"$PY" - "$MODE" "$TARGET" <<'PY_BUNDLE_EOF'
import hashlib, json, os, shutil, sys, time
mode, target = sys.argv[1], sys.argv[2]

PAYLOAD = r"""
{payload}
"""
FILES = [json.loads(ln) for ln in PAYLOAD.splitlines() if ln.strip()]

def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

if mode == "list":
    for f in FILES:
        print("%s  %7d  %s" % (f["sha"][:12], len(f["data"].encode("utf-8")), f["path"]))
    print("%d files" % len(FILES))
    sys.exit(0)

bad = [f["path"] for f in FILES if digest(f["data"]) != f["sha"]]
if bad:
    print("payload corrupted: " + ", ".join(bad), file=sys.stderr)
    sys.exit(1)
if mode == "verify":
    print("payload ok: %d files" % len(FILES))
    sys.exit(0)

root = os.path.abspath(target)
stamp = time.strftime("%Y%m%d-%H%M%S")
backup = os.path.join(root, ".backup-" + stamp)
written = unchanged = backed = 0
for f in FILES:
    dst = os.path.join(root, f["path"])
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    blob = f["data"].encode("utf-8")
    if os.path.exists(dst):
        with open(dst, "rb") as fh:
            if fh.read() == blob:
                unchanged += 1
                continue
        bdst = os.path.join(backup, f["path"])
        os.makedirs(os.path.dirname(bdst), exist_ok=True)
        shutil.copy2(dst, bdst)
        backed += 1
    with open(dst, "wb") as fh:
        fh.write(blob)
    os.chmod(dst, int(f["perm"], 8))
    with open(dst, "rb") as fh:
        if hashlib.sha256(fh.read()).hexdigest() != f["sha"]:
            print("write verification failed: " + f["path"], file=sys.stderr)
            sys.exit(1)
    written += 1
print("files: %d written, %d unchanged, %d backed up%s" % (
    written, unchanged, backed, (" to " + backup) if backed else ""))
PY_BUNDLE_EOF

if [ "$MODE" != "install" ]; then exit 0; fi

cd "$TARGET"
if [ "$NO_PIP" = 1 ]; then
  echo "skipped venv/pip (--no-pip)"
  exit 0
fi
if [ ! -x venv/bin/pip ] && [ ! -x venv/Scripts/pip.exe ]; then
  echo "creating venv"
  "$PY" -m venv venv
fi
if [ -x venv/bin/pip ]; then VPIP=venv/bin/pip; VPY=venv/bin/python; else VPIP=venv/Scripts/pip.exe; VPY=venv/Scripts/python.exe; fi
echo "installing requirements"
"$VPIP" install -q -r requirements.txt
if [ "$WITH_STATIC" = 1 ]; then "$VPIP" install -q semgrep ruff; fi
"$VPY" -m shared.settings --check || true
if [ "$SYSTEM" = 1 ]; then
  "$VPY" -m shared.install --prefix "$TARGET"
else
  echo
  echo "next:"
  echo "  1. (as root) $VPY -m shared.install --prefix $TARGET   # /etc config, /var data, systemd unit"
  echo "  2. edit /etc/pr-review-suite/secrets.env, then: systemctl enable --now review-orchestrator"
  echo "  3. open http://<host>:8080/console/setup"
fi
'''


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def select_files(full: bool, extra_excludes: list[str]) -> list[str]:
    excludes = list(ALWAYS_EXCLUDE) + list(extra_excludes) + ([] if full else list(DEFAULT_EXCLUDE))
    out = []
    for f in tracked_files():
        if any(fnmatch.fnmatch(f, pat) for pat in ALWAYS_EXCLUDE + tuple(extra_excludes)):
            continue
        if any(fnmatch.fnmatch(f, k) for k in ALWAYS_KEEP) or not any(fnmatch.fnmatch(f, pat) for pat in excludes):
            out.append(f)
    return out


def build(out: Path, full: bool, extra_excludes: list[str]) -> tuple[int, int, list[str]]:
    files = select_files(full, extra_excludes)
    lines: list[str] = []
    nbytes = 0
    flagged: list[str] = []
    for rel in files:
        raw = (ROOT / rel).read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise SystemExit(f"{rel}: not UTF-8 text; this bundler only carries text files")
        if "\r" in text:
            raise SystemExit(f"{rel}: contains CR; normalise to LF first")
        nbytes += len(raw)
        perm = "0755" if any(fnmatch.fnmatch(rel, pat) for pat in EXECUTABLE) else "0644"
        lines.append(json.dumps({"path": rel, "perm": perm, "sha": hashlib.sha256(raw).hexdigest(),
                                 "data": text}, ensure_ascii=True, separators=(",", ":")))
        low = text.lower()
        hits = [n for n in REPORT_IN_PAYLOAD if n in low]
        if hits:
            flagged.append(f"{rel}: {', '.join(repr(h) for h in hits)}")
    payload = "\n".join(lines)
    if "PY_BUNDLE_EOF" in payload or '"""' in payload or payload.endswith("\\"):
        raise SystemExit("payload collides with the heredoc delimiter or the raw-string quotes")
    # substitute the header placeholders BEFORE the payload goes in: file contents may
    # legitimately contain "{name}" (f-strings) and must never be touched
    head = (INSTALLER.replace("{stamp}", time.strftime("%Y-%m-%d")).replace("{commit}", commit())
            .replace("{nfiles}", str(len(files))).replace("{nbytes}", str(nbytes))
            .replace("{name}", out.name))
    if "{payload}" in payload:
        raise SystemExit("payload contains the placeholder token")
    script = head.replace("{payload}", payload)
    if not script.isascii():
        bad = sorted({ch for ch in script if ord(ch) > 127})[:10]
        raise SystemExit(f"bundle is not pure ASCII: {bad!r}")
    code_only = INSTALLER.replace("{payload}", "")
    hits = [n for n in FORBIDDEN_IN_INSTALLER if n in code_only]
    if hits:
        raise SystemExit(f"installer code contains forbidden tokens: {hits}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(script, encoding="ascii", newline="\n")
    try:
        out.chmod(0o755)
    except OSError:
        pass
    return len(files), len(script), flagged


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist" / "pr-review-suite-install.sh"))
    ap.add_argument("--full", action="store_true", help="include eval fixtures, docs and markdown")
    ap.add_argument("--exclude", action="append", default=[], help="extra glob to leave out")
    a = ap.parse_args(argv)
    n, size, flagged = build(Path(a.out), a.full, a.exclude)
    print(f"wrote {a.out}: {n} files, {size} bytes, ASCII only")
    for f in flagged:
        print(f"note (payload text contains a word scanners may flag): {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
