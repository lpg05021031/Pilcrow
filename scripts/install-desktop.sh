#!/usr/bin/env bash
# Adds Pilcrow to the desktop: an application-menu entry, its icon, and a `pilcrow` command in ~/.local/bin.
# Run again any time; it only rewrites its own files. `--uninstall` removes them.
set -eu

APP_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS="$DATA/applications"
ICONS="$DATA/icons/hicolor"
BIN="$HOME/.local/bin"

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$APPS/pilcrow.desktop" "$ICONS/scalable/apps/pilcrow.svg"
  for s in 64 128 256 512; do rm -f "$ICONS/${s}x${s}/apps/pilcrow.png"; done
  if [ -L "$BIN/pilcrow" ]; then rm -f "$BIN/pilcrow"; fi
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$APPS" 2>/dev/null || true
  echo "Removed the Pilcrow menu entry, icons and command."
  exit 0
fi

mkdir -p "$APPS" "$ICONS/scalable/apps" "$BIN"
for s in 64 128 256 512; do
  if [ -f "$APP_DIR/assets/icon-$s.png" ]; then
    mkdir -p "$ICONS/${s}x${s}/apps"
    cp "$APP_DIR/assets/icon-$s.png" "$ICONS/${s}x${s}/apps/pilcrow.png"
  fi
done
cp "$APP_DIR/assets/icon.svg" "$ICONS/scalable/apps/pilcrow.svg"

cat >"$APPS/pilcrow.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Pilcrow
GenericName=Claude Code Desktop
Comment=Work with Claude Code in a desktop window
Exec="$APP_DIR/bin/pilcrow" %U
TryExec=$APP_DIR/bin/pilcrow
Icon=pilcrow
Terminal=false
StartupNotify=true
StartupWMClass=pilcrow
Categories=Development;IDE;
Keywords=claude;ai;code;agent;
Actions=browser;

[Desktop Action browser]
Name=Open in Browser Window
Exec="$APP_DIR/bin/pilcrow" --chrome

EOF
chmod +x "$APPS/pilcrow.desktop" "$APP_DIR/bin/pilcrow"
ln -sfn "$APP_DIR/bin/pilcrow" "$BIN/pilcrow"

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$APPS" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$ICONS" 2>/dev/null || true

echo "Added Pilcrow to the applications menu."
case ":$PATH:" in
  *":$BIN:"*) echo "Run it from a terminal with: pilcrow" ;;
  *) echo "Run it from a terminal with: $BIN/pilcrow   (add ~/.local/bin to PATH to type just 'pilcrow')" ;;
esac
