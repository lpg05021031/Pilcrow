#!/usr/bin/env bash
# Inside the container: install Pilcrow from the real installer, then open the Qt window on an X
# server and drive it. Everything it finds goes to /out (screenshots, logs, the pip log).
set -u
OUT=/out
IN=/in
mkdir -p "$OUT"
log() { printf '\n== %s\n' "$*" | tee -a "$OUT/linux-run.log"; }

if [ "${QUICK:-0}" = 1 ] && [ -x /home/tester/pilcrow/qt/.venv/bin/python ]; then
  log "QUICK=1: keeping the earlier install, running the repo's qt/ code"
  QT_CODE=/repo/qt
else
  QT_CODE=/home/tester/pilcrow/qt
log "installer (--skip-electron, no desktop entry)"
cp "$IN/pilcrow-install.sh" /home/tester/pilcrow-install.sh
# The npm step needs the registry; the Qt deps step needs PyPI. Both are reachable here.
bash /home/tester/pilcrow-install.sh --skip-electron --no-desktop 2>&1 | tee -a "$OUT/linux-install.log" | tail -40
echo "installer exit: ${PIPESTATUS[0]}" | tee -a "$OUT/linux-run.log"
[ -f ~/pilcrow/.qt-pip.log ] && cp ~/pilcrow/.qt-pip.log "$OUT/linux-qt-pip.log"
[ -f ~/pilcrow/.launch-env ] && { log ".launch-env"; cat ~/pilcrow/.launch-env | tee -a "$OUT/linux-run.log"; }
fi

log "which python the launcher would pick"
PY=""
for c in ~/pilcrow/qt/.venv/bin/python /usr/bin/python3; do
  if "$c" -c 'import PySide6' 2>/dev/null; then PY="$c"; break; fi
done
echo "python: ${PY:-none with PySide6}" | tee -a "$OUT/linux-run.log"
[ -n "$PY" ] || { log "no PySide6: stopping"; exit 2; }
"$PY" -c 'import PySide6, sys; print("PySide6", PySide6.__version__, "python", sys.version.split()[0])' | tee -a "$OUT/linux-run.log"

log "mock server + Qt window on Xvfb (xcb platform, the real Linux path)"
# The mock server is a test file, not part of the install; it runs from the install folder so it
# serves the installed web/dist and uses the installed node_modules (ws).
mkdir -p ~/pilcrow/test/mock && cp /repo/test/mock/mock-server.mjs ~/pilcrow/test/mock/
export DISPLAY=:99
Xvfb :99 -screen 0 1480x940x24 -nolisten tcp >/dev/null 2>&1 &
sleep 1
export PYTHONPATH="$QT_CODE"
export QT_QPA_PLATFORM=xcb
export PILCROW_TITLEBAR=custom
"$PY" "$( [ -f /repo/test/linux/probe.py ] && echo /repo/test/linux/probe.py || echo /opt/pilcrow-test/probe.py )" "$OUT" 2>&1 | tee -a "$OUT/linux-run.log"
echo "probe exit: ${PIPESTATUS[0]}" | tee -a "$OUT/linux-run.log"
[ -f ~/.local/state/pilcrow-host/qt-crash.log ] && cp ~/.local/state/pilcrow-host/qt-crash.log "$OUT/linux-qt-crash.log"
log "done"
