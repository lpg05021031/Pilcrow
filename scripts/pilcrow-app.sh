#!/usr/bin/env bash
# ============================================================================
#  Pilcrow Desktop Host for Linux  (pilcrow-app.sh)
#
#  用 WebKitGTK（不是 Chromium）把 Pilcrow 包成一個 GNOME 風格的原生視窗：
#  GTK HeaderBar（顏色跟著 Pilcrow 主題）、沒有網址列、沒有分頁、沒有翻譯提示、沒有瀏覽器快捷鍵。
#  找不到 WebKitGTK 時退回 PyQt6 WebEngine（pip 就能裝，但那又是 Chromium）。
#
#  前提：已用 pilcrow-install.sh 裝好 Pilcrow（Electron 有沒有裝都無所謂）。
#
#  第一次（建立應用程式選單項目，之後從選單開即可）：
#    bash pilcrow-app.sh --install
#
#  依賴（Ubuntu 22.04 / 24.04）：
#    sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1
#  沒有 apt 鏡像時改走 pip：
#    pip install PyQt6 PyQt6-WebEngine
#
#  選項：
#    --dir PATH          Pilcrow 安裝位置（預設 ~/pilcrow，或環境變數 PILCROW_DIR）
#    --install           複製本檔到 ~/.local/share/pilcrow-host、改寫應用程式選單項目，然後啟動
#    --uninstall         移除本外殼（Pilcrow 本體不動；重跑 ~/pilcrow/scripts/install-desktop.sh 可恢復原選單項目）
#    --keep-server       關視窗後讓 Pilcrow 伺服器留在背景
#    --devtools          允許 WebKit 的開發者工具（右鍵 → 檢閱）
#    --qt                強制用 PyQt6 WebEngine
#    --check             只檢查依賴
# ============================================================================
set -euo pipefail

HOST_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/pilcrow-host"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/pilcrow-host"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$APPS_DIR/pilcrow.desktop"
APP_ID="app.pilcrow.Desktop"

DIR="${PILCROW_DIR:-$HOME/pilcrow}"
MODE=launch
PASS=()
for a in "$@"; do
  case "$a" in
    --dir=*) DIR="${a#--dir=}" ;;
    --install) MODE=install ;;
    --uninstall) MODE=uninstall ;;
    --check) MODE=check ;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,2\}//'; exit 0 ;;
    *) PASS+=("$a") ;;
  esac
done
prev=""
for a in "$@"; do [ "$prev" = "--dir" ] && DIR="$a"; prev="$a"; done
case "$DIR" in "~"*) DIR="$HOME${DIR#\~}" ;; esac

say()  { printf '%s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

if [ "$MODE" = uninstall ]; then
  rm -f "$DESKTOP_FILE" "$HOME/.local/bin/pilcrow-app"
  rm -rf "$HOST_DIR" "$STATE_DIR"
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$APPS_DIR" 2>/dev/null || true
  say "已移除 Pilcrow 桌面外殼。Pilcrow 本體（$DIR）沒有動；執行 bash $DIR/scripts/install-desktop.sh 可恢復原本的選單項目。"
  exit 0
fi

[ -f "$DIR/server/main.js" ] || die "在 $DIR 找不到 Pilcrow（server/main.js）。請先執行 pilcrow-install.sh，或用 --dir 指定安裝位置。"

# Pilcrow 安裝時記下的 Node 與 PATH（從選單啟動時沒有登入 shell 的環境）
[ -f "$DIR/.launch-env" ] && . "$DIR/.launch-env"
NODE="${PILCROW_NODE:-$(command -v node || true)}"
[ -n "$NODE" ] || die "找不到 node。請把 Node.js 加進 PATH，或設定 PILCROW_NODE。"

# ---------------------------------------------------------------- 依賴檢查
# 系統的 /usr/bin/python3 才有 apt 裝的 GTK / WebKit 綁定；pyenv / conda 的 python3 排在 PATH 前面時要跳過它。
BACKEND=""
PY=""
has_gtk() {
  "$1" - <<'PYCHK' >/dev/null 2>&1
import gi
gi.require_version('Gtk', '3.0')
ok = False
for v in ('4.1', '4.0'):
    try:
        gi.require_version('WebKit2', v); ok = True; break
    except ValueError:
        pass
from gi.repository import Gtk
raise SystemExit(0 if ok else 1)
PYCHK
}
has_qt() { "$1" -c 'import PyQt6.QtWebEngineWidgets' >/dev/null 2>&1; }
python_candidates() {
  [ -n "${PILCROW_PYTHON:-}" ] && echo "$PILCROW_PYTHON"
  [ -x /usr/bin/python3 ] && echo /usr/bin/python3
  command -v python3 2>/dev/null || true
  ls /usr/bin/python3.[0-9]* 2>/dev/null | grep -v config || true
}
check_deps() {
  local want_qt=0 c
  for a in "${PASS[@]+"${PASS[@]}"}"; do [ "$a" = --qt ] && want_qt=1; done
  if [ "$want_qt" = 0 ]; then
    for c in $(python_candidates); do if has_gtk "$c"; then PY="$c"; BACKEND=gtk; ok "WebKitGTK（python3-gi + gir1.2-webkit2）可用：$c"; return 0; fi; done
  fi
  for c in $(python_candidates); do if has_qt "$c"; then PY="$c"; BACKEND=qt; ok "PyQt6 WebEngine 可用：$c"; return 0; fi; done
  command -v python3 >/dev/null 2>&1 || die "找不到 python3。"
  warn "沒有 WebKitGTK 的 Python 綁定，也沒有 PyQt6 WebEngine。"
  say  "    apt（建議，體積小、不是 Chromium）：sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1"
  say  "    pip（沒有 apt 鏡像時）：pip install PyQt6 PyQt6-WebEngine"
  if [ -t 0 ] && command -v apt-get >/dev/null 2>&1; then
    read -r -p "  現在用 apt 安裝 WebKitGTK 綁定？[Y/n] " ans
    if [ -z "$ans" ] || [[ "$ans" =~ ^[Yy] ]]; then
      sudo apt-get install -y python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1 || sudo apt-get install -y python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.0
      check_deps; return
    fi
  fi
  die "缺少依賴。"
}
check_deps
[ "$MODE" = check ] && exit 0

# ---------------------------------------------------------------- 安裝：複製自己、改寫選單項目
mkdir -p "$HOST_DIR" "$STATE_DIR"
if [ "$MODE" = install ]; then
  if [ "$(readlink -f "$0")" != "$HOST_DIR/pilcrow-app.sh" ]; then cp "$0" "$HOST_DIR/pilcrow-app.sh"; fi
  chmod +x "$HOST_DIR/pilcrow-app.sh"
  mkdir -p "$APPS_DIR" "$HOME/.local/bin"
  ICON=pilcrow
  [ -f "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps/pilcrow.png" ] || { [ -f "$DIR/assets/icon-256.png" ] && ICON="$DIR/assets/icon-256.png"; }
  cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Pilcrow
Comment=Claude Code desktop
Exec=$HOST_DIR/pilcrow-app.sh
Icon=$ICON
Terminal=false
Categories=Development;IDE;
StartupNotify=true
StartupWMClass=$APP_ID
X-GNOME-UsesNotifications=true
EOF
  ln -sf "$HOST_DIR/pilcrow-app.sh" "$HOME/.local/bin/pilcrow-app"
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$APPS_DIR" 2>/dev/null || true
  ok "選單項目：Pilcrow（$DESKTOP_FILE）；指令：pilcrow-app"
  say "  外殼程式放在 $HOST_DIR；之後要更新本檔，重新執行一次 --install 即可。"
fi

# ---------------------------------------------------------------- 視窗程式（Python）
cat > "$HOST_DIR/pilcrow-host.py" <<'PY'
#!/usr/bin/env python3
# Pilcrow desktop host — WebKitGTK (GTK3) 主要後端，PyQt6 WebEngine 備援。由 pilcrow-app.sh 產生與啟動。
import json, os, sys, time, subprocess, pathlib, urllib.request, re, shutil

APP_ID = 'app.pilcrow.Desktop'
HOME = pathlib.Path.home()
DIR = pathlib.Path(os.environ.get('PILCROW_DIR') or HOME / 'pilcrow')
NODE = os.environ.get('PILCROW_NODE') or shutil.which('node')
STATE = pathlib.Path(os.environ.get('XDG_STATE_HOME') or HOME / '.local' / 'state')
PILCROW_STATE = STATE / 'pilcrow'
HOST_STATE = STATE / 'pilcrow-host'
HOST_DATA = pathlib.Path(os.environ.get('XDG_DATA_HOME') or HOME / '.local' / 'share') / 'pilcrow-host'
ARGS = set(sys.argv[1:])
KEEP = '--keep-server' in ARGS
DEVTOOLS = '--devtools' in ARGS
HOST_STATE.mkdir(parents=True, exist_ok=True)
HOST_DATA.mkdir(parents=True, exist_ok=True)
LOG = HOST_STATE / 'host.log'

def log(*a):
    try:
        with open(LOG, 'a') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S') + '  ' + ' '.join(str(x) for x in a) + '\n')
    except Exception:
        pass

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 不走 proxy

def hello(port):
    with _opener.open(f'http://127.0.0.1:{port}/api/hello', timeout=2) as r:
        return json.load(r)

def running_server():
    try:
        info = json.loads((PILCROW_STATE / 'instance.json').read_text())
        os.kill(info['pid'], 0)
        h = hello(info['port'])
        if h.get('app') == 'pilcrow' and h.get('pid') == info['pid']:
            return {'pid': info['pid'], 'port': info['port'], 'url': f"http://127.0.0.1:{info['port']}/", 'version': h.get('version')}
    except Exception:
        pass
    return None

def start_server():
    proc = subprocess.Popen([NODE, str(DIR / 'server' / 'main.js'), '--shell=none'], cwd=str(DIR),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.time() + 40
    while time.time() < deadline:
        time.sleep(0.25)
        if proc.poll() is not None:
            break
        s = running_server()
        if s and s['pid'] == proc.pid:
            return proc, s
    if proc.poll() is None:
        proc.kill()
    tail = ''
    try:
        tail = ''.join((PILCROW_STATE / 'pilcrow.log').read_text().splitlines(True)[-15:])
    except Exception:
        pass
    fatal(f"Pilcrow 伺服器沒有在 40 秒內就緒。\n記錄檔：{PILCROW_STATE / 'pilcrow.log'}\n{tail}")

def fatal(msg):
    log('ERROR', msg)
    print(msg, file=sys.stderr)
    try:
        subprocess.run(['notify-send', '-a', 'Pilcrow', '-i', 'dialog-error', 'Pilcrow', msg.split('\n')[0]], timeout=5)
    except Exception:
        pass
    sys.exit(1)

def server_busy(url):
    try:
        with _opener.open(url.rstrip('/') + '/api/hello', timeout=2) as r:
            return int(json.load(r).get('busy', 0) or 0)
    except Exception:
        return 0

def quit_server(url):
    """用 Pilcrow 的 WebSocket 協定送 app.quit（純 stdlib 的最小 WebSocket client）。"""
    import socket, base64, struct
    from urllib.parse import urlparse
    try:
        u = urlparse(url)
        s = socket.create_connection((u.hostname, u.port), timeout=3)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f'GET /ws HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                   f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        resp = s.recv(4096)
        if b' 101 ' not in resp.split(b'\r\n')[0]:
            return False
        payload = json.dumps({'t': 'req', 'id': 1, 'm': 'app.quit', 'p': {}}).encode()
        mask = os.urandom(4)
        head = bytes([0x81]) + (bytes([0x80 | len(payload)]) if len(payload) < 126 else bytes([0x80 | 126]) + struct.pack('>H', len(payload)))
        s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        try:
            s.settimeout(2); s.recv(4096)
        except Exception:
            pass
        s.close()
        return True
    except Exception as e:
        log('quit_server failed', e)
        return False

def finish(proc, server, decision):
    """decision: 'quit' | 'keep' | 'page'（頁面自己 Quit，伺服器正在結束）"""
    log('finish', decision, 'spawned' if proc is not None else 'external server')
    if proc is None:
        return
    if decision == 'page' or decision == 'quit':
        if decision == 'quit':
            quit_server(server['url'])
        try:
            proc.wait(6)
        except subprocess.TimeoutExpired:
            log('server did not exit in 6s, killing')
            proc.kill()
    else:
        log('window closed; server left running', server['pid'])

def luma(hexcolor):
    m = re.match(r'#([0-9a-fA-F]{6})$', hexcolor.strip())
    if not m:
        m2 = re.match(r'rgba?\((\d+),\s*(\d+),\s*(\d+)', hexcolor)
        if not m2:
            return 0
        r, g, b = (int(m2.group(i)) for i in (1, 2, 3))
    else:
        v = int(m.group(1), 16); r, g, b = v >> 16, (v >> 8) & 255, v & 255
    return 0.299 * r + 0.587 * g + 0.114 * b

WATCH_JS = r"""
(function(){
  if (window.top !== window) return;
  var last = '';
  function post(m){ try { window.webkit.messageHandlers.host.postMessage(m); } catch(e){} }
  function tick(){
    var c = null;
    try { c = getComputedStyle(document.documentElement).getPropertyValue('--chrome').trim(); } catch(e){}
    if (!c && document.body) { try { c = getComputedStyle(document.body).backgroundColor; } catch(e){} }
    if (c && c !== last) { last = c; post('bg:' + c); }
  }
  function watch(){ try { var mo = new MutationObserver(tick); mo.observe(document.documentElement, {attributes:true}); if (document.body) mo.observe(document.body, {attributes:true}); } catch(e){} tick(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watch); else watch();
  window.addEventListener('load', tick);
  setInterval(tick, 1500);
})();
"""

# ---------------------------------------------------------------- GTK / WebKitGTK
def run_gtk(server, proc):
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    for v in ('4.1', '4.0'):
        try:
            gi.require_version('WebKit2', v); break
        except ValueError:
            pass
    from gi.repository import Gtk, Gdk, Gio, GLib, WebKit2
    log('gtk backend: WebKit2', WebKit2.get_major_version(), WebKit2.get_minor_version())
    GLib.set_prgname(APP_ID)
    GLib.set_application_name('Pilcrow')

    origin = re.match(r'https?://[^/]+', server['url']).group(0)
    def same_origin(uri):
        return bool(uri) and (uri.startswith(origin + '/') or uri == origin)
    def local_dev(uri):
        return bool(re.match(r'https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(/|$)', uri or ''))
    def open_external(uri):
        try:
            if re.match(r'^(https?|mailto):', uri or ''):
                Gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception as e:
            log('open_external failed', e)

    state = {'decision': 'keep' if (proc is None or KEEP) else 'quit', 'page_quit': False, 'fullscreen': False}
    win_state_file = HOST_STATE / 'window.json'

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
            self.win = None

        def do_activate(self):
            if self.win:
                self.win.present(); return
            self.build()

        def build(self):
            css = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self.css = css

            win = Gtk.ApplicationWindow(application=self, title='Pilcrow')
            self.win = win
            win.get_style_context().add_class('pilcrow')
            win.set_default_size(1480, 940)
            try:
                win.set_icon_name('pilcrow')
                ico = DIR / 'assets' / 'icon-256.png'
                if ico.exists():
                    win.set_icon_from_file(str(ico))
            except Exception:
                pass
            try:
                st = json.loads(win_state_file.read_text())
                win.set_default_size(int(st.get('w', 1480)), int(st.get('h', 940)))
                if 'x' in st and 'y' in st:
                    win.move(int(st['x']), int(st['y']))
                if st.get('max'):
                    win.maximize()
            except Exception:
                pass

            hb = Gtk.HeaderBar()
            hb.set_show_close_button(True)
            hb.set_title('Pilcrow')
            hb.get_style_context().add_class('pilcrow')
            win.set_titlebar(hb)
            self.hb = hb

            # 持久化的 localStorage / cookies，放在自己的資料夾
            dm = WebKit2.WebsiteDataManager(base_data_directory=str(HOST_DATA / 'webkit' / 'data'),
                                            base_cache_directory=str(HOST_DATA / 'webkit' / 'cache'))
            ctx = WebKit2.WebContext.new_with_website_data_manager(dm)
            ucm = WebKit2.UserContentManager()
            ucm.register_script_message_handler('host')
            ucm.connect('script-message-received::host', self.on_message)
            ucm.add_script(WebKit2.UserScript(WATCH_JS, WebKit2.UserContentInjectedFrames.TOP_FRAME, WebKit2.UserScriptInjectionTime.START, None, None))
            web = WebKit2.WebView(web_context=ctx, user_content_manager=ucm)
            self.web = web
            s = web.get_settings()
            s.set_enable_developer_extras(DEVTOOLS)
            s.set_enable_smooth_scrolling(True)
            s.set_enable_back_forward_navigation_gestures(False)
            s.set_enable_fullscreen(True)
            try:
                s.set_enable_write_console_messages_to_stdout(False)
            except Exception:
                pass
            web.connect('decide-policy', self.on_policy)
            web.connect('context-menu', self.on_context_menu)
            web.connect('permission-request', self.on_permission)
            web.connect('show-notification', self.on_notification)
            web.connect('notify::title', lambda w, p: self.set_title(w.get_title()))
            web.connect('close', self.on_page_close)
            web.connect('load-failed', self.on_load_failed)
            web.connect('web-process-terminated', lambda w, r: GLib.timeout_add(800, lambda: (w.load_uri(server['url']), False)[1]))
            web.set_background_color(Gdk.RGBA(0.075, 0.075, 0.082, 1))
            win.add(web)
            win.connect('delete-event', self.on_delete)
            win.connect('key-press-event', self.on_key)
            win.connect('window-state-event', self.on_window_state)
            self.apply_color('#131315')
            win.show_all()
            web.load_uri(server['url'])
            web.grab_focus()
            log('window shown')
            if os.environ.get('PILCROW_HOST_AUTOCLOSE'):   # 測試用：N 秒後自動關閉視窗
                def _autoclose():
                    log('autoclose firing'); win.close(); return False
                GLib.timeout_add(int(os.environ['PILCROW_HOST_AUTOCLOSE']) * 1000, _autoclose)

        # ---- 外觀
        def apply_color(self, c):
            dark = luma(c) < 128
            fg = '#a8a8b0' if dark else '#55555e'
            fg2 = '#ececee' if dark else '#1b1b1f'
            line = 'rgba(255,255,255,0.06)' if dark else 'rgba(0,0,0,0.08)'
            try:
                Gtk.Settings.get_default().set_property('gtk-application-prefer-dark-theme', dark)
            except Exception:
                pass
            css = (f'headerbar.pilcrow {{ background: {c}; background-image: none; color: {fg}; border-bottom: 1px solid {line}; box-shadow: none; text-shadow: none; }}'
                   f'headerbar.pilcrow:backdrop {{ background: {c}; color: {fg}; }}'
                   f'headerbar.pilcrow .title {{ color: {fg2}; font-weight: 500; }}'
                   f'headerbar.pilcrow .title:backdrop {{ color: {fg}; }}'
                   f'headerbar.pilcrow button.titlebutton {{ color: {fg}; background: none; border: none; box-shadow: none; text-shadow: none; -gtk-icon-shadow: none; }}'
                   f'headerbar.pilcrow button.titlebutton:hover {{ background: rgba(128,128,128,0.22); }}'
                   f'window.pilcrow {{ background: {c}; }}')
            try:
                self.css.load_from_data(css.encode())
            except Exception as e:
                log('css failed', e)
            try:
                m = re.match(r'#([0-9a-fA-F]{6})$', c.strip())
                if m:
                    v = int(m.group(1), 16)
                    self.web.set_background_color(Gdk.RGBA((v >> 16) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255, 1))
            except Exception:
                pass

        def set_title(self, t):
            t = t or 'Pilcrow'
            self.hb.set_title(t)
            self.win.set_title(t)

        # ---- 事件
        def on_message(self, ucm, result):
            try:
                val = result.get_js_value().to_string() if hasattr(result, 'get_js_value') else result.get_value().to_string()
            except Exception:
                return
            if val.startswith('bg:'):
                self.apply_color(val[3:].strip())

        def on_policy(self, web, decision, dtype):
            if dtype == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
                open_external(decision.get_navigation_action().get_request().get_uri())
                decision.ignore(); return True
            if dtype == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
                na = decision.get_navigation_action()
                uri = na.get_request().get_uri()
                if na.get_navigation_type() == WebKit2.NavigationType.LINK_CLICKED and not same_origin(uri) and not local_dev(uri):
                    open_external(uri); decision.ignore(); return True
            return False

        # 只留剪下／複製／貼上／全選；哪些動作存在依 WebKitGTK 版本而定（例如沒有 UNDO／REDO），用 getattr 取。
        KEEP_ACTIONS = {getattr(WebKit2.ContextMenuAction, n) for n in ('CUT', 'COPY', 'PASTE', 'PASTE_AS_PLAIN_TEXT', 'SELECT_ALL', 'UNDO', 'REDO')
                        if hasattr(WebKit2.ContextMenuAction, n)}

        def on_context_menu(self, web, menu, event, hit):
            if DEVTOOLS:
                return False
            try:
                for item in list(menu.get_items()):
                    if item.is_separator() or item.get_stock_action() not in self.KEEP_ACTIONS:
                        menu.remove(item)
                return menu.get_n_items() == 0
            except Exception as e:
                log('context menu', e)
                return True

        def on_permission(self, web, req):
            uri = web.get_uri() or ''
            if isinstance(req, WebKit2.NotificationPermissionRequest) and same_origin(uri):
                req.allow(); return True
            req.deny(); return True

        def on_notification(self, web, note):
            try:
                if not same_origin(web.get_uri() or ''):
                    return True
                subprocess.Popen(['notify-send', '-a', 'Pilcrow', '-i', 'pilcrow', note.get_title() or 'Pilcrow', note.get_body() or ''])
                if not self.win.is_active():
                    self.win.set_urgency_hint(True)
            except Exception as e:
                log('notify failed', e)
            return True

        def on_load_failed(self, web, event, uri, error):
            if same_origin(uri):
                GLib.timeout_add(1500, lambda: (web.load_uri(server['url']), False)[1])
            return True

        def on_page_close(self, web):
            state['page_quit'] = True
            self.win.destroy()

        def on_key(self, win, ev):
            if ev.keyval == Gdk.KEY_F11:
                if state['fullscreen']: win.unfullscreen()
                else: win.fullscreen()
                return True
            return False

        def on_window_state(self, win, ev):
            state['fullscreen'] = bool(ev.new_window_state & Gdk.WindowState.FULLSCREEN)
            state['maximized'] = bool(ev.new_window_state & Gdk.WindowState.MAXIMIZED)
            return False

        def on_delete(self, win, ev):
            log('delete-event')
            try:
                x, y = win.get_position(); w, h = win.get_size()
                win_state_file.write_text(json.dumps({'x': x, 'y': y, 'w': w, 'h': h, 'max': bool(state.get('maximized'))}))
            except Exception:
                pass
            if proc is not None and not KEEP and not state['page_quit']:
                busy = server_busy(server['url'])
                if busy:
                    d = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.NONE,
                                          text='結束 Pilcrow？', secondary_text=f'Claude 還有 {busy} 個 session 正在工作。結束會中斷它們（對話會保留）。')
                    d.add_button('取消', Gtk.ResponseType.CANCEL)
                    d.add_button('留在背景', Gtk.ResponseType.NO)
                    d.add_button('結束', Gtk.ResponseType.YES)
                    d.set_default_response(Gtk.ResponseType.CANCEL)
                    r = d.run(); d.destroy()
                    if r == Gtk.ResponseType.CANCEL:
                        return True
                    state['decision'] = 'quit' if r == Gtk.ResponseType.YES else 'keep'
                else:
                    state['decision'] = 'quit'
            log('window closing, decision', state['decision'])
            return False

    app = App()
    rc = app.run([])
    decision = 'page' if state['page_quit'] else state['decision']
    finish(proc, server, decision)
    return rc

# ---------------------------------------------------------------- PyQt6 WebEngine 備援
def run_qt(server, proc):
    from PyQt6.QtCore import QUrl, Qt
    from PyQt6.QtGui import QIcon, QDesktopServices
    from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings

    origin = re.match(r'https?://[^/]+', server['url']).group(0)
    state = {'decision': 'keep' if (proc is None or KEEP) else 'quit', 'page_quit': False}
    app = QApplication(sys.argv)
    app.setApplicationName('Pilcrow'); app.setDesktopFileName(APP_ID)
    ico = DIR / 'assets' / 'icon-256.png'
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, url, ntype, is_main):
            u = url.toString()
            if is_main and ntype == QWebEnginePage.NavigationType.NavigationTypeLinkClicked and not u.startswith(origin):
                QDesktopServices.openUrl(url); return False
            return True
        def createWindow(self, t):
            tmp = QWebEnginePage(self.profile(), self)
            tmp.urlChanged.connect(lambda u: (QDesktopServices.openUrl(u), tmp.deleteLater()))
            return tmp

    class Win(QMainWindow):
        def closeEvent(self, ev):
            if proc is not None and not KEEP and not state['page_quit']:
                busy = server_busy(server['url'])
                if busy:
                    r = QMessageBox.question(self, '結束 Pilcrow？', f'Claude 還有 {busy} 個 session 正在工作。結束會中斷它們（對話會保留）。\n\n是：結束　否：留在背景',
                                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
                    if r == QMessageBox.StandardButton.Cancel:
                        ev.ignore(); return
                    state['decision'] = 'quit' if r == QMessageBox.StandardButton.Yes else 'keep'
                else:
                    state['decision'] = 'quit'
            ev.accept()

    profile = QWebEngineProfile('pilcrow', app)
    profile.setPersistentStoragePath(str(HOST_DATA / 'qtwebengine'))
    profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
    win = Win(); win.setWindowTitle('Pilcrow'); win.resize(1480, 940)
    view = QWebEngineView(win); page = Page(profile, view); view.setPage(page)
    page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True)
    page.titleChanged.connect(lambda t: win.setWindowTitle(t or 'Pilcrow'))
    page.windowCloseRequested.connect(lambda: (state.__setitem__('page_quit', True), win.close()))
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
    win.setCentralWidget(view)
    view.load(QUrl(server['url']))
    win.show()
    rc = app.exec()
    finish(proc, server, 'page' if state['page_quit'] else state['decision'])
    return rc

def main():
    if not (DIR / 'server' / 'main.js').exists():
        fatal(f'在 {DIR} 找不到 Pilcrow')
    if not NODE:
        fatal('找不到 node')
    proc = None
    server = running_server()
    if server:
        # A server left from before an update serves the old interface; when it has nothing in
        # flight, replace it, otherwise keep it (the window says what to do once Claude is done).
        installed = None
        try:
            installed = json.loads((DIR / 'package.json').read_text()).get('version')
        except Exception:
            pass
        if installed and server.get('version') and server['version'] != installed and not server_busy(server['url']):
            log('server', server['version'], 'is older than the installed', installed, '- replacing it')
            quit_server(server['url'])
            deadline = time.time() + 8
            while time.time() < deadline:
                time.sleep(0.25)
                try:
                    os.kill(server['pid'], 0)
                except OSError:
                    break
            server = running_server()
    if server:
        log('reusing server', server)
    else:
        proc, server = start_server()
        log('started server', server)
    backend = 'qt' if '--qt' in ARGS else 'gtk'
    if backend == 'gtk':
        try:
            import gi
            gi.require_version('Gtk', '3.0')
            try:
                gi.require_version('WebKit2', '4.1')
            except ValueError:
                gi.require_version('WebKit2', '4.0')
        except Exception as e:
            log('gtk backend unavailable', e)
            backend = 'qt'
    sys.exit(run_gtk(server, proc) if backend == 'gtk' else run_qt(server, proc))

if __name__ == '__main__':
    main()
PY
chmod +x "$HOST_DIR/pilcrow-host.py"

# ---------------------------------------------------------------- 啟動
export PILCROW_DIR="$DIR" PILCROW_NODE="$NODE"
ARGS=()
for a in "${PASS[@]+"${PASS[@]}"}"; do ARGS+=("$a"); done
[ "$BACKEND" = qt ] && ARGS+=(--qt)
if [ -t 1 ]; then
  exec "$PY" "$HOST_DIR/pilcrow-host.py" "${ARGS[@]+"${ARGS[@]}"}"
else
  nohup "$PY" "$HOST_DIR/pilcrow-host.py" "${ARGS[@]+"${ARGS[@]}"}" >>"$STATE_DIR/host.log" 2>&1 &
fi
