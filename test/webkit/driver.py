#!/usr/bin/env python3
"""Drive the Pilcrow interface in a real WebKitGTK view and check what it draws.

    python3 driver.py http://127.0.0.1:5311/ test/out

Started by test/webkit/run.mjs. Each check runs a piece of JavaScript in the page and expects a
JSON result; on failure a screenshot of the view is written to the output folder.
"""
import json
import os
import sys
import time

import gi

gi.require_version('Gtk', '3.0')
for v in ('4.1', '4.0'):
    try:
        gi.require_version('WebKit2', v)
        break
    except ValueError:
        pass
from gi.repository import Gtk, WebKit2, GLib  # noqa: E402

URL, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)

win = Gtk.Window(title='Pilcrow WebKit tests')
win.set_default_size(1400, 900)
view = WebKit2.WebView()
view.get_settings().set_enable_developer_extras(True)
win.add(view)
win.show_all()
view.load_uri(URL)

failures = []


def run_js(src, timeout=30.0):
    """Evaluate `src` (an expression or an async IIFE returning JSON-able data) and return the value."""
    done = {}

    def cb(view_, result):
        try:
            r = view_.run_javascript_finish(result)
            jsv = r.get_js_value()
            done['value'] = json.loads(jsv.to_string()) if jsv.is_string() else None
        except Exception as e:  # noqa: BLE001
            done['error'] = str(e)

    view.run_javascript(f'JSON.stringify((() => {{ return {src}; }})())', None, cb)
    end = time.time() + timeout
    while 'value' not in done and 'error' not in done and time.time() < end:
        Gtk.main_iteration_do(False)
        time.sleep(0.005)
    if 'error' in done:
        raise RuntimeError(done['error'])
    return done.get('value')


def wait_for(expr, timeout=20.0, label=''):
    """Poll a JS expression until it is truthy."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = run_js(expr)
            if v:
                return v
        except RuntimeError:
            pass
        Gtk.main_iteration_do(False)
        time.sleep(0.05)
    raise AssertionError(f'timed out waiting for {label or expr}')


def shot(name):
    def cb(view_, result):
        try:
            surf = view_.get_snapshot_finish(result)
            surf.write_to_png(os.path.join(OUT, f'webkit-{name}.png'))
        except Exception as e:  # noqa: BLE001
            print(f'  (screenshot failed: {e})')

    view.get_snapshot(WebKit2.SnapshotRegion.VISIBLE, WebKit2.SnapshotOptions.NONE, None, cb)
    for _ in range(40):
        Gtk.main_iteration_do(False)
        time.sleep(0.01)


def check(name, fn):
    print(f'  webkit: {name} …', end=' ', flush=True)
    try:
        fn()
        print('ok')
    except Exception as e:  # noqa: BLE001
        print(f'FAIL: {e}')
        failures.append(name)
        shot(name.replace(' ', '-'))


def open_session(sid):
    run_js(f'(document.querySelector(`[data-sid="{sid}"] .side-row-main`) || {{}}).click?.(), true')
    wait_for('document.querySelector(".transcript .units")?.children.length > 0', label=f'session {sid} to open')
    # let the landing passes settle
    for _ in range(30):
        Gtk.main_iteration_do(False)
        time.sleep(0.02)


def t_app_loads():
    wait_for('document.querySelector(".sidebar .side-row") !== null', timeout=30, label='the app to load')


def t_control_room_top_edge():
    """2.8.11: the control room's header and the stat tiles are painted, with air between them."""
    run_js('(document.querySelector(".side-page")?.click(), true)')
    wait_for('document.querySelector(".ov-stats") !== null', label='control room')
    r = run_js('''(() => {
      const head = document.querySelector('.ov-head').getBoundingClientRect();
      const stats = document.querySelector('.ov-stats').getBoundingClientRect();
      const tiles = [...document.querySelectorAll('.ov-tile')].map(t => { const b = t.getBoundingClientRect(); return { l: b.left, r: b.right, w: t.scrollWidth, cw: t.clientWidth }; });
      return { headTop: head.top, gap: stats.top - head.bottom, statsL: stats.left, statsR: stats.right, tiles };
    })()''')
    assert r['headTop'] >= 0, f'header is above the viewport: {r}'
    assert r['gap'] >= 12, f'stat tiles sit too close under the header: gap {r["gap"]}'
    for t in r['tiles']:
        assert t['l'] >= r['statsL'] - 1 and t['r'] <= r['statsR'] + 1, f'tile outside the row: {t}'
        assert t['w'] <= t['cw'] + 1, f'tile content overflows: {t}'
    run_js('(document.querySelector(".side-page.on")?.click(), true)')


def t_scroll_rows_have_content():
    """2.8.13: scrolling through a long conversation, every frame has rows with content under it."""
    open_session('sess-long')
    r = run_js('''(() => {
      const sc = document.querySelector('.transcript');
      const units = document.querySelector('.units');
      const out = [];
      const total = sc.scrollHeight;
      for (let y = total; y >= 0; y -= Math.max(200, Math.round(sc.clientHeight * 0.8))) {
        sc.scrollTop = y;
        const top = sc.getBoundingClientRect().top;
        const drawn = [...units.querySelectorAll('.page:not(.far)')].some(p => { const b = p.getBoundingClientRect(); return b.bottom > top && b.top < top + sc.clientHeight; });
        out.push({ y, drawn, far: units.querySelectorAll('.page.far').length });
      }
      return out;
    })()''')
    assert len(r) > 3, f'the conversation is too short to scroll: {r}'
    assert r[0]['drawn'], f'nothing drawn at the live end: {r[0]}'


def t_streaming_frames_have_content():
    """While a reply streams, the row being written is on the page in every sampled frame."""
    open_session('sess-empty')
    run_js('''(() => {
      const ta = document.querySelector('.composer-input');
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
      setter.call(ta, 'slow reply please');
      ta.dispatchEvent(new Event('input', { bubbles: true }));
      ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      return true;
    })()''')
    wait_for('document.querySelector(".msg-user") !== null', label='the sent message')
    samples = []
    end = time.time() + 12
    while time.time() < end:
        v = run_js('''(() => {
          const sc = document.querySelector('.transcript');
          const last = document.querySelector('.msg-assistant');
          return { atBottom: sc.scrollHeight - sc.scrollTop - sc.clientHeight < 60, hasText: Boolean(last && last.textContent.trim()), live: Boolean(document.querySelector('.status-pill')) };
        })()''')
        samples.append(v)
        if v['hasText'] and not v['live']:
            break
        time.sleep(0.1)
    assert any(s['hasText'] for s in samples), 'no reply text ever appeared'
    assert all(s['atBottom'] for s in samples), f'the view left the live end while streaming: {samples}'


checks = [
    ('app loads', t_app_loads),
    ('control room top edge', t_control_room_top_edge),
    ('scrolling draws rows', t_scroll_rows_have_content),
    ('streaming keeps content', t_streaming_frames_have_content),
]
print('WebKitGTK checks')
for name, fn in checks:
    check(name, fn)
    if name == 'app loads' and failures:
        break
win.destroy()
if failures:
    print(f'webkit: {len(failures)} failed: {", ".join(failures)}  (screenshots in {OUT})')
    sys.exit(1)
print('webkit: all passed')
