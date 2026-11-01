"""Finding and starting the server without blocking: ``find_server`` asks ``/api/hello`` on the event
loop (the window paints while a slow server answers), ``ServerProcess.start`` returns at once and
``wait_for_server`` reports when it answers, exits or runs out of time."""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from pilcrow_qt.app import ServerProcess, find_server, wait_for_server


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _until(cond, seconds: float) -> int:
    """Run the event loop until ``cond()``; the number of turns it took (the loop never stood still)."""
    turns = 0
    end = time.monotonic() + seconds
    while not cond() and time.monotonic() < end:
        QCoreApplication.processEvents()
        turns += 1
        time.sleep(0.005)
    return turns


def _state(tmp_path, monkeypatch, port: int = 0, pid: int = 0) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    d = tmp_path / "state" / ("Pilcrow/state" if sys.platform == "win32" else "pilcrow")
    d.mkdir(parents=True, exist_ok=True)
    if port:
        (d / "instance.json").write_text(json.dumps({"port": port, "pid": pid}), encoding="utf-8")


def _serve(delay: float, body: dict) -> HTTPServer:
    class Hello(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            time.sleep(delay)
            data = json.dumps(body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_a) -> None:
            pass

    srv = HTTPServer(("127.0.0.1", 0), Hello)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_a_slow_server_is_asked_without_blocking_the_window(app, tmp_path, monkeypatch):
    srv = _serve(0.4, {"app": "pilcrow", "pid": 42})
    _state(tmp_path, monkeypatch, srv.server_address[1], 42)
    try:
        got: list = []
        t0 = time.monotonic()
        find_server(got.append)
        assert time.monotonic() - t0 < 0.1, "the question goes out; nothing waits for the answer"
        turns = _until(lambda: got, 5)
        assert got == [f"http://127.0.0.1:{srv.server_address[1]}/"]
        assert turns > 20, "the event loop kept turning while the server took its time"
    finally:
        srv.shutdown()


def test_no_lock_another_pid_or_nothing_listening_is_no_server(app, tmp_path, monkeypatch):
    got: list = []
    _state(tmp_path, monkeypatch)
    find_server(got.append)
    assert got == [None]
    srv = _serve(0, {"app": "pilcrow", "pid": 7})
    _state(tmp_path, monkeypatch, srv.server_address[1], 42)  # a lock left by another process
    try:
        find_server(got.append)
        _until(lambda: len(got) == 2, 5)
        assert got[1] is None
    finally:
        srv.shutdown()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    _state(tmp_path, monkeypatch, port)
    find_server(got.append, 500)
    _until(lambda: len(got) == 3, 5)
    assert got[2] is None


def _install(tmp_path, monkeypatch, script: str) -> ServerProcess:
    """A fake install whose ``server/main.js`` is a Python script, run by this interpreter as "node"."""
    _state(tmp_path, monkeypatch)  # no lock file: nothing answers
    (tmp_path / "server").mkdir(exist_ok=True)
    (tmp_path / "server" / "main.js").write_text(script, encoding="utf-8")
    return ServerProcess(str(tmp_path), sys.executable)


def test_start_returns_at_once_and_the_wait_gives_up_after_the_timeout(app, tmp_path, monkeypatch):
    s = _install(tmp_path, monkeypatch, "import time\ntime.sleep(30)\n")
    t0 = time.monotonic()
    s.start(timeout=0.4)
    failed: list = []
    try:
        assert time.monotonic() - t0 < 1.0
        s.check()  # still booting
        timer = wait_for_server(s, app, lambda url: failed.append(("found", url)), failed.append)
        _until(lambda: failed, 5)
        assert failed and "did not come up within 0.4 seconds" in failed[0] and not timer.isActive()
    finally:
        s.stop()


def test_the_wait_says_when_the_server_exited(app, tmp_path, monkeypatch):
    s = _install(tmp_path, monkeypatch, "print('no port')\nraise SystemExit(1)\n")
    s.start()
    try:
        s.proc.wait(10)
        with pytest.raises(RuntimeError, match="exited right away"):
            s.check()
    finally:
        s.stop()


def test_reduced_motion_follows_the_system_setting(monkeypatch):
    """The OS's own preference, where it is cheap to read; PILCROW_LITE=1 asks for it regardless."""

    from pilcrow_qt import app as app_mod

    said: dict = {}
    monkeypatch.delenv("PILCROW_LITE", raising=False)
    monkeypatch.setattr(app_mod, "_setting", lambda cmd: said.get(cmd[0], ""))
    monkeypatch.setattr(sys, "platform", "linux")
    assert not app_mod._system_reduced_motion()  # no gsettings: motion
    said["gsettings"] = "false"
    assert app_mod._system_reduced_motion()  # GNOME: enable-animations false
    said["gsettings"] = "true"
    assert not app_mod._system_reduced_motion()
    monkeypatch.setattr(sys, "platform", "darwin")
    said["defaults"] = "1"
    assert app_mod._system_reduced_motion()  # macOS: Reduce motion on
    said["defaults"] = "0"
    assert not app_mod._system_reduced_motion()
    monkeypatch.setenv("PILCROW_LITE", "1")
    assert app_mod._system_reduced_motion()
    monkeypatch.undo()
    assert app_mod._setting(["pilcrow-no-such-command-anywhere"]) == ""
