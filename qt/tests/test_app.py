"""Starting the server: ``ServerProcess.start`` returns at once and ``poll`` reports, so the window's
event loop runs while the server boots (app.run polls on a timer)."""

from __future__ import annotations

import sys
import time

import pytest

from pilcrow_qt.app import ServerProcess


def _install(tmp_path, monkeypatch, script: str) -> ServerProcess:
    """A fake install whose ``server/main.js`` is a Python script, run by this interpreter as "node"."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # no lock file: nothing answers
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    (tmp_path / "server").mkdir(exist_ok=True)
    (tmp_path / "server" / "main.js").write_text(script, encoding="utf-8")
    return ServerProcess(str(tmp_path), sys.executable)


def test_start_returns_at_once_and_poll_gives_up_after_the_timeout(tmp_path, monkeypatch):
    s = _install(tmp_path, monkeypatch, "import time\ntime.sleep(30)\n")
    t0 = time.monotonic()
    s.start(timeout=0.4)
    try:
        assert time.monotonic() - t0 < 1.0
        assert s.poll() is None  # still booting
        time.sleep(0.5)
        with pytest.raises(RuntimeError, match="did not come up within 0.4 seconds"):
            s.poll()
    finally:
        s.stop()


def test_poll_says_when_the_server_exited(tmp_path, monkeypatch):
    s = _install(tmp_path, monkeypatch, "print('no port')\nraise SystemExit(1)\n")
    s.start()
    try:
        s.proc.wait(10)
        with pytest.raises(RuntimeError, match="exited right away"):
            s.poll()
    finally:
        s.stop()
