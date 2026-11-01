"""Test harness: the client booted offscreen against test/mock/mock-server.mjs.

    from harness import app_client

    def test_sidebar(app_client):
        c = app_client          # c.win, c.store, c.rpc, c.wait_until(cond), c.call(method, params)
        c.wait_until(lambda: c.store.sessions_loaded)
        ...

Each test module should pick its own mock port (``MOCK_PORT`` module attribute) so modules can run
in parallel. ``c.call`` talks to the mock through the client's own socket (the ``mock.*`` methods
reset it, set the streaming pace, touch a session).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qt"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))


def start_mock(port: int, long: int = 60) -> subprocess.Popen:
    p = subprocess.Popen(
        ["node", str(ROOT / "test" / "mock" / "mock-server.mjs"), str(port)],
        cwd=str(ROOT),
        env={**os.environ, "MOCK_LONG": str(long)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    for _ in range(150):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/hello", timeout=1):
                return p
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    p.kill()
    raise RuntimeError("mock server did not start")


class Client:
    def __init__(self, port: int) -> None:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication

        from pilcrow_qt import fonts
        from pilcrow_qt.rpc import rpc
        from pilcrow_qt.store import store
        from pilcrow_qt.theme import theme
        from pilcrow_qt.widgets.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        fonts.load()
        theme.apply(True)
        # PILCROW_MOTION=1 runs the suite with animations on
        store.reduced_motion = os.environ.get("PILCROW_MOTION") != "1"
        # The tests measure the window against the web's CSS, where the title bar is the platform's and
        # outside the client area; Linux's own bar (custom_titlebar) has a test of its own that asks for it.
        os.environ.setdefault("PILCROW_TITLEBAR", "native")
        if not getattr(store, "_inited", False):
            store.init()
            store._inited = True  # type: ignore[attr-defined]
        self.store = store
        self.rpc = rpc
        # A widget a test showed on its own (a label, a view) is a top-level window at (0, 0): left
        # open, it sits over the next client's window and takes the simulated pointer's enter events.
        for w in QApplication.topLevelWidgets():
            w.close()
            w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.win = MainWindow()
        self.win.resize(1480, 940)
        self.win.show()
        rpc.connect_to(f"http://127.0.0.1:{port}/")
        self.port = port

    # ---- helpers
    def wait_until(self, cond: Callable[[], Any], timeout: float = 10.0, what: str = "") -> Any:
        from PySide6.QtCore import QCoreApplication, QEvent

        end = time.monotonic() + timeout
        while time.monotonic() < end:
            QCoreApplication.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            v = cond()
            if v:
                return v
            time.sleep(0.01)
        raise AssertionError(f"timed out waiting for {what or cond}")

    def sleep(self, ms: int) -> None:
        from PySide6.QtCore import QCoreApplication, QEvent

        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            QCoreApplication.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            time.sleep(0.005)

    def call(self, method: str, params: Optional[dict] = None, timeout: float = 10.0) -> Any:
        box: dict = {}
        self.rpc.call(method, params or {}).then(lambda r: box.__setitem__("r", r)).catch(
            lambda e: box.__setitem__("e", e)
        )
        self.wait_until(lambda: "r" in box or "e" in box, timeout, method)
        if "e" in box:
            raise box["e"]
        return box.get("r")

    def open(self, sid: str) -> None:
        self.store.open_session(sid)
        self.wait_until(
            lambda: (self.store.rt.get(sid) or None) is not None and self.store.rt[sid].loaded, 10, f"session {sid}"
        )
        self.sleep(200)

    def shot(self, name: str) -> Path:
        out = ROOT / "test" / "out"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"qt-{name}.png"
        self.win.grab().save(str(path))
        return path

    def find(self, cls, parent=None):
        """All widgets of a class under the window (or ``parent``)."""
        return (parent or self.win).findChildren(cls)

    def texts(self, cls, parent=None) -> list:
        out = []
        for w in self.find(cls, parent):
            t = w.text() if hasattr(w, "text") else ""
            if t:
                out.append(t)
        return out

    def close(self) -> None:
        """Close the window and the socket, and put the store back to its blank state for the next test."""
        st = self.store
        st._ui_save_timer.stop()  # a layout change still waiting to be saved would land in the next test's mock
        try:
            self.call("mock.reset", timeout=3)  # the mock's saved config goes back to its defaults too
        except Exception:  # noqa: BLE001
            pass
        self.rpc.close()
        self.win.close()
        self.win.deleteLater()
        self.sleep(150)  # the socket's close round-trip; a new client must not inherit a closing one
        st.active_id = None
        st.rt.clear()
        st.models.clear()
        st.sessions = []
        st.sessions_loaded = False
        st.page = "session"
        for k in list(st.dialogs):
            st.dialogs[k] = None if k not in ("settings", "palette", "shortcuts") else False
        st.toasts = []
        # The layout a test dragged or opened (panel, widths) must not leak into the next one.
        for k, v in (("panel", False), ("sidebar", True), ("sidebar_w", 268), ("panel_w", 560)):
            if hasattr(st.ui, k):
                setattr(st.ui, k, v)
        st.unread.clear()
        st.timeline.clear()
        st.jobs.clear()
        st.job_runs.clear()
        st.jobs_loaded = False
        st.dispatch_view = None
        st.playbooks = []
        st._buffered.clear()
        st._opening.clear()
        # Drafts and history are kept per session for the life of the app; a test must not inherit them.
        try:
            from pilcrow_qt.widgets import composer

            composer._drafts.clear()
            composer._histories.clear()
        except Exception:  # noqa: BLE001
            pass
        self.sleep(50)


@pytest.fixture(scope="module")
def mock_server(request):
    port = getattr(request.module, "MOCK_PORT", 5330)
    p = start_mock(port, getattr(request.module, "MOCK_LONG", 60))
    yield port
    p.kill()


@pytest.fixture()
def app_client(mock_server):
    c = Client(mock_server)
    try:
        c.wait_until(lambda: c.store.sessions_loaded, 15, "sessions to load")
        c.call("mock.reset")
        c.wait_until(lambda: c.store.sessions_loaded, 15, "sessions to reload")
        yield c
    finally:
        c.close()
