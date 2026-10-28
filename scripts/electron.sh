#!/usr/bin/env bash
# Manage the Electron runtime that gives Pilcrow its own desktop window.
#
#   scripts/electron.sh status                   is Electron ready?
#   scripts/electron.sh download [MIRROR_URL]    download it (ELECTRON_MIRROR or MIRROR_URL picks the server)
#   scripts/electron.sh from-zip FILE.zip        use electron-vX.Y.Z-linux-<arch>.zip you copied in yourself
#
# Without Electron, Pilcrow opens in a Chrome/Chromium app window instead; nothing else changes.
set -eu
APP_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
PKG="$APP_DIR/node_modules/electron"
[ -f "$APP_DIR/.launch-env" ] && . "$APP_DIR/.launch-env"
NODE="${PILCROW_NODE:-node}"

if [ ! -f "$PKG/package.json" ]; then
  echo "The electron npm package is not installed. Run: (cd \"$APP_DIR\" && npm install)" >&2
  exit 1
fi
VERSION="$("$NODE" -p "require('$PKG/package.json').version")"
case "$(uname -m)" in
  x86_64 | amd64) ARCH=x64 ;;
  aarch64 | arm64) ARCH=arm64 ;;
  armv7l) ARCH=armv7l ;;
  *) ARCH="$(uname -m)" ;;
esac
ZIP_NAME="electron-v$VERSION-linux-$ARCH.zip"

ready() { [ -x "$PKG/dist/electron" ] && [ -f "$PKG/path.txt" ]; }

case "${1:-status}" in
  status)
    if ready; then
      echo "Electron $(cat "$PKG/dist/version" 2>/dev/null || echo "$VERSION") is ready: $PKG/dist/electron"
    else
      echo "Electron $VERSION is not downloaded yet."
      echo "  Download:      $0 download [mirror-url]"
      echo "  Or copy in:    $ZIP_NAME, then: $0 from-zip /path/to/$ZIP_NAME"
      exit 1
    fi
    ;;
  download)
    [ -n "${2:-}" ] && export ELECTRON_MIRROR="$2"
    unset ELECTRON_SKIP_BINARY_DOWNLOAD
    echo "Downloading Electron $VERSION${ELECTRON_MIRROR:+ from $ELECTRON_MIRROR}…"
    (cd "$PKG" && "$NODE" install.js)
    ready && echo "Electron is ready." || { echo "Download did not complete." >&2; exit 1; }
    ;;
  from-zip)
    ZIP="${2:-}"
    [ -f "$ZIP" ] || { echo "Usage: $0 from-zip /path/to/$ZIP_NAME" >&2; exit 2; }
    case "$(basename "$ZIP")" in
      "$ZIP_NAME") ;;
      *) echo "Note: expected $ZIP_NAME (the version must match the installed electron package)." >&2 ;;
    esac
    rm -rf "$PKG/dist"
    mkdir -p "$PKG/dist"
    if command -v unzip >/dev/null 2>&1; then
      unzip -q "$ZIP" -d "$PKG/dist"
    else
      # Python's zipfile drops permission bits, so restore them from the archive.
      python3 - "$ZIP" "$PKG/dist" <<'PY'
import os, sys, zipfile
src, dst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(src) as z:
    for info in z.infolist():
        path = z.extract(info, dst)
        mode = (info.external_attr >> 16) & 0o777
        if mode:
            os.chmod(path, mode)
PY
    fi
    printf 'electron' >"$PKG/path.txt"
    ready && echo "Electron is ready: $PKG/dist/electron" || { echo "The zip did not contain an electron binary." >&2; exit 1; }
    ;;
  *)
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
