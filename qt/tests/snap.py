"""Open the client against a mock server offscreen, run a scripted scenario, save screenshots.

    python qt/tests/snap.py [--port 5300] [--out test/out] [--scenario welcome|session|...] [--theme dark|light]

Used while building the client (the screenshots are how the work is checked without a display) and
by the pytest suite. Exits non-zero when the scenario raises.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The offscreen platform draws with FreeType and needs telling where the system fonts are on Windows.
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))


def start_mock(port: int, long: int = 60) -> subprocess.Popen:
    p = subprocess.Popen(["node", str(ROOT / "test" / "mock" / "mock-server.mjs"), str(port)], cwd=str(ROOT), env={**os.environ, "MOCK_LONG": str(long)}, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/hello", timeout=1):
                return p
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    p.kill()
    raise RuntimeError("mock server did not start")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5321)
    ap.add_argument("--out", default=str(ROOT / "test" / "out"))
    ap.add_argument("--scenario", default="welcome")
    ap.add_argument("--theme", default="dark")
    ap.add_argument("--size", default="1480x940")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    mock = start_mock(args.port)
    try:
        return run_scenario(args, out)
    finally:
        mock.kill()


def run_scenario(args, out: Path) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from pilcrow_qt import fonts
    from pilcrow_qt.rpc import rpc
    from pilcrow_qt.store import store
    from pilcrow_qt.theme import theme
    from pilcrow_qt.widgets.main_window import MainWindow

    app = QApplication([])
    fonts.load()
    theme.apply(args.theme == "dark")
    store.reduced_motion = True
    store.init()
    w, h = (int(x) for x in args.size.split("x"))
    win = MainWindow()
    win.resize(w, h)
    win.show()
    rpc.connect_to(f"http://127.0.0.1:{args.port}/")

    steps = scenario_steps(args.scenario, win, store, out)
    state = {"i": 0, "rc": 0}

    def step() -> None:
        if state["i"] >= len(steps):
            app.quit()
            return
        delay, fn = steps[state["i"]]
        state["i"] += 1
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            print(f"scenario failed at step {state['i']}: {e}")
            state["rc"] = 1
            app.quit()
            return
        QTimer.singleShot(delay, step)

    QTimer.singleShot(300, step)
    QTimer.singleShot(60_000, lambda: (print("timed out"), app.quit()))
    app.exec()
    return state["rc"]


def shot(win, out: Path, name: str) -> None:
    pm = win.grab()
    path = out / f"qt-{name}.png"
    pm.save(str(path))
    print(f"screenshot {path}")


def scenario_steps(name: str, win, store, out: Path):
    """A list of (delay_after_ms, fn). Each scenario waits for the sessions to load first."""
    wait_loaded = (1500, lambda: None)
    if name == "welcome":
        return [wait_loaded, (200, lambda: shot(win, out, "welcome"))]
    if name == "session":
        return [wait_loaded, (900, lambda: store.open_session("sess-login")), (300, lambda: shot(win, out, "session"))]
    if name == "overview":
        return [wait_loaded, (500, lambda: store.show_page("overview")), (300, lambda: shot(win, out, "overview"))]
    if name == "light":
        return [wait_loaded, (300, lambda: store.set_theme("light")), (700, lambda: store.open_session("sess-login")), (300, lambda: shot(win, out, "light"))]
    if name == "turn":
        return [
            wait_loaded,
            (900, lambda: store.open_session("sess-empty")),
            (200, lambda: store.send_message("sess-empty", "please look at the parser and fix the comment")),
            (1200, lambda: shot(win, out, "turn-live")),
            (6000, lambda: shot(win, out, "turn-done")),
        ]
    if name == "permission":
        return [
            wait_loaded,
            (900, lambda: store.open_session("sess-tests")),
            (200, lambda: store.send_message("sess-tests", "please ask permission before you run a command")),
            (2500, lambda: shot(win, out, "permission")),
        ]
    if name == "long":
        return [wait_loaded, (900, lambda: store.open_session("sess-long")), (600, lambda: shot(win, out, "long"))]
    raise SystemExit(f"unknown scenario {name}")


if __name__ == "__main__":
    sys.exit(main())
