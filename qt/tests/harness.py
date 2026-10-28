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
        from PySide6.QtWidgets import QApplication

        from pilcrow_qt import fonts
        from pilcrow_qt.rpc import rpc
        from pilcrow_qt.store import store
        from pilcrow_qt.theme import theme
        from pilcrow_qt.widgets.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        fonts.load()
        theme.apply(True)
        store.reduced_motion = True
        if not getattr(store, "_inited", False):
            store.init()
            store._inited = True  # type: ignore[attr-defined]
        self.store = store
        self.rpc = rpc
        self.win = MainWindow()
        self.win.resize(1480, 940)
        self.win.show()
        rpc.connect_to(f"http://127.0.0.1:{port}/")
        self.port = port

    # ---- helpers
    def wait_until(self, cond: Callable[[], Any], timeout: float = 10.0, what: str = "") -> Any:
        from PySide6.QtCore import QCoreApplication

        end = time.monotonic() + timeout
        while time.monotonic() < end:
            QCoreApplication.processEvents()
            v = cond()
            if v:
                return v
            time.sleep(0.01)
        raise AssertionError(f"timed out waiting for {what or cond}")

    def sleep(self, ms: int) -> None:
        from PySide6.QtCore import QCoreApplication

        end = time.monotonic() + ms / 1000
        while time.monotonic() < end:
            QCoreApplication.processEvents()
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
        self.rpc.close()
        self.win.close()
        self.app.processEvents()


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
