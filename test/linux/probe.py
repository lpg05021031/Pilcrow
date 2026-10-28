"""Drive the installed Qt client on the X server: every page, every side-panel tab, dialogs.

Run by test/linux/run.sh with PYTHONPATH pointing at the installed qt/ folder. The mock server
is the repo's (copied in with the installer), so the same data as the Windows tests is on screen.
Screenshots go to the folder given as the only argument, a crash leaves a traceback in
qt-crash.log through the client's own faulthandler.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

OUT = Path(sys.argv[1])
MOCK = Path.home() / "pilcrow" / "test" / "mock" / "mock-server.mjs"
PORT = 5300


def start_mock() -> subprocess.Popen:
    p = subprocess.Popen(
        ["node", str(MOCK), str(PORT)],
        cwd=str(Path.home() / "pilcrow"),
        env={**os.environ, "MOCK_LONG": "120"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    for _ in range(100):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/api/hello", timeout=1
            ):
                return p
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    p.kill()
    raise RuntimeError("mock server did not start")


def main() -> int:
    mock = start_mock()
    try:
        from pilcrow_qt import fonts
        from pilcrow_qt.app import _crash_log
        from pilcrow_qt.rpc import rpc
        from pilcrow_qt.store import PANEL_TABS, store
        from pilcrow_qt.theme import theme
        from pilcrow_qt.widgets.main_window import MainWindow
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication

        _crash_log()
        app = QApplication([])
        app.setStyle("Fusion")
        ui, mono = fonts.load()
        print(
            f"fonts: ui={ui!r} mono={mono!r}; families with Inter: {[f for f in __import__('PySide6.QtGui', fromlist=['QFontDatabase']).QFontDatabase.families() if 'Inter' in f or 'JetBrains' in f]}"
        )
        print(
            f"screen: {app.primaryScreen().logicalDotsPerInch():.0f} logical dpi, ratio {app.primaryScreen().devicePixelRatio()}"
        )
        theme.apply(True)
        store.reduced_motion = os.environ.get("PILCROW_MOTION", "1") != "1"
        store.init()
        win = MainWindow()
        win.resize(1480, 940)
        win.show()
        rpc.connect_to(f"http://127.0.0.1:{PORT}/")

        def pump(ms: int) -> None:
            end = time.monotonic() + ms / 1000
            while time.monotonic() < end:
                QCoreApplication.processEvents()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                time.sleep(0.01)

        def shot(name: str) -> None:
            pump(300)
            win.grab().save(str(OUT / f"linux-{name}.png"))
            print(f"shot {name}")

        def step(name: str, fn) -> None:
            try:
                fn()
                pump(700)
                shot(name)
            except Exception:  # noqa: BLE001
                print(f"FAILED {name}")
                traceback.print_exc()

        end = time.monotonic() + 20
        while time.monotonic() < end and not store.sessions_loaded:
            pump(100)
        print("sessions loaded:", store.sessions_loaded)
        from PySide6.QtGui import QFontInfo, QFontMetrics

        def describe(label: str, w) -> None:
            f = w.font()
            fi = QFontInfo(f)
            m = QFontMetrics(f)
            print(
                f"font of {label}: family={fi.family()!r} px={fi.pixelSize()} pt={fi.pointSizeF():.1f} weight={fi.weight()} h={m.height()} adv={m.horizontalAdvance('Fix the login redirect bug')} ; app font={QFontInfo(app.font()).family()!r} {QFontInfo(app.font()).pixelSize()}px"
            )

        describe("sidebar brand", win.sidebar)
        describe("search field", win.sidebar.search)
        shot("welcome")
        step("session", lambda: store.open_session("sess-login"))
        pump(1200)
        from pilcrow_qt.widgets.sidebar import SessionRow
        from PySide6.QtWidgets import QLabel

        rows = win.findChildren(SessionRow)
        if rows:
            describe("sidebar row title", rows[0].title)
        labels = [w for w in win.findChildren(QLabel) if "redirect target" in w.text()]
        if labels:
            describe("assistant text label", labels[0])
        for tab in PANEL_TABS:
            step(f"tab-{tab}", lambda t=tab: store.set_ui(panel=True, panel_tab=t))
        step("panel-closed", lambda: store.set_ui(panel=False))
        step("overview", lambda: store.show_page("overview"))
        step("inbox", lambda: store.show_page("inbox"))
        step("settings", lambda: store.set_dialog("settings", True))
        step("settings-closed", lambda: store.set_dialog("settings", False))
        step("palette", lambda: store.set_dialog("palette", True))
        step("palette-closed", lambda: store.set_dialog("palette", False))
        step("light", lambda: store.set_theme("light"))
        step(
            "long",
            lambda: (store.show_page("session"), store.open_session("sess-long")),
        )
        step("new-session-dialog", lambda: store.set_dialog("newSession", {}))
        print("probe finished without a crash")
        rpc.close()
        pump(200)
        return 0
    finally:
        mock.kill()


if __name__ == "__main__":
    sys.exit(main())
