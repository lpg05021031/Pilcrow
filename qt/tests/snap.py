"""Open the client against a mock server offscreen, run a scripted scenario, save screenshots.

    python qt/tests/snap.py [--out test/out] [--scenario welcome|session|board|board-panel|board-new|board-wait|...] [--theme dark|light]

Used while building the client (the screenshots are how the work is checked without a display) and
by the pytest suite. Exits non-zero when the scenario raises.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
sys.path.insert(0, str(ROOT / "qt" / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The offscreen platform draws with FreeType and needs telling where the system fonts are on Windows.
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "test" / "out"))
    ap.add_argument("--scenario", default="welcome")
    ap.add_argument("--theme", default="dark")
    ap.add_argument("--size", default="1480x940")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    from harness import free_port, start_mock

    args.port = free_port()
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
    store.reduced_motion = os.environ.get("PILCROW_MOTION") != "1"
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
        except Exception as e:
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
    if name == "board":
        return [wait_loaded, (500, lambda: store.show_page("board")), (700, lambda: shot(win, out, "board"))]
    if name == "board-panel":
        return [
            wait_loaded,
            (500, lambda: store.show_page("board")),
            (700, lambda: win.board.select("sess-login")),
            (400, lambda: shot(win, out, "board-panel")),
        ]
    if name == "board-new":

        def fill() -> None:
            win.board.new_card.title.setText("Rename the config file")
            win.board.new_card.intent.setPlainText(
                "config.json becomes pilcrow.json; the loader reads both for a version."
            )

        return [
            wait_loaded,
            (500, lambda: store.show_page("board")),
            (700, lambda: win.board.set_creating(True)),
            (300, fill),
            (300, lambda: shot(win, out, "board-new")),
        ]
    if name == "board-wait":
        return [
            wait_loaded,
            (500, lambda: store.show_page("board")),
            (700, lambda: win.board.select("sess-empty")),
            (300, lambda: win.board.set_mode("wait")),
            (400, lambda: shot(win, out, "board-wait")),
        ]
    if name == "light":
        return [
            wait_loaded,
            (300, lambda: store.set_theme("light")),
            (700, lambda: store.open_session("sess-login")),
            (300, lambda: shot(win, out, "light")),
        ]
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
