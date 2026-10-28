"""QApplication setup: fonts, theme, the server to talk to, the main window."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import fonts, icons
from .rpc import rpc
from .store import store
from .theme import theme


# ---------------------------------------------------------------- finding or starting the server

def _state_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Pilcrow" / "state"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "pilcrow"


def _hello(url: str, timeout: float = 1.5) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/hello", timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8"))
            return j if j.get("app") == "pilcrow" else None
    except Exception:  # noqa: BLE001
        return None


def existing_server() -> Optional[str]:
    """The Pilcrow server already running for this user (its lock file names the port), if any."""
    lock = _state_dir() / "instance.json"
    try:
        info = json.loads(lock.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    port = info.get("port")
    if not port:
        return None
    url = f"http://127.0.0.1:{port}/"
    j = _hello(url)
    return url if j and (not info.get("pid") or j.get("pid") == info.get("pid")) else None


class ServerProcess:
    """A server started by this window (``node server/main.js --shell=none``), stopped with it."""

    def __init__(self, install_dir: str, node: str) -> None:
        self.dir = Path(os.path.expanduser(install_dir or "~/pilcrow"))
        self.node = node or self._node_from_env() or "node"
        self.proc: Optional[subprocess.Popen] = None
        self.url = ""

    def _node_from_env(self) -> str:
        env = self.dir / ".launch-env"
        if env.exists():
            for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("PILCROW_NODE="):
                    return line.split("=", 1)[1].strip().strip("'\"")
        return ""

    def start(self, timeout: float = 25.0) -> str:
        main_js = self.dir / "server" / "main.js"
        if not main_js.exists():
            raise RuntimeError(f"Pilcrow is not installed at {self.dir} (no server/main.js). Run pilcrow-install.sh, or pass --dir.")
        env = {**os.environ, "PILCROW_SHELL_ENV": "0"}
        self.proc = subprocess.Popen([self.node, str(main_js), "--shell=none"], cwd=str(self.dir), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + timeout
        while time.time() < deadline:
            url = existing_server()
            if url:
                self.url = url
                return url
            if self.proc.poll() is not None:
                out = self.proc.stdout.read() if self.proc.stdout else ""
                raise RuntimeError(f"The Pilcrow server exited right away:\n{out[-2000:]}")
            time.sleep(0.15)
        raise RuntimeError("The Pilcrow server did not come up within 25 seconds.")

    def stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self.proc.kill()


# ---------------------------------------------------------------- the app

def run(url: str = "", install_dir: str = "", node: str = "", keep_server: bool = False, title: str = "Pilcrow", lite: bool = False) -> int:
    # High-DPI is Qt 6's default; the rest is ours.
    QApplication.setApplicationName("Pilcrow")
    QApplication.setOrganizationName("Pilcrow")
    QApplication.setDesktopFileName("pilcrow")
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(True)
    fonts.load()
    theme.apply(True)  # the first frame is dark until the config says otherwise
    app.setWindowIcon(_app_icon())
    store.reduced_motion = lite or _system_reduced_motion()
    store.init()

    from .widgets.main_window import MainWindow

    win = MainWindow(title=title)
    win.show()

    server: Optional[ServerProcess] = None
    target = url or existing_server()
    if not target:
        server = ServerProcess(install_dir, node)
        try:
            target = server.start()
        except RuntimeError as e:
            win.show_fatal(str(e))
            return app.exec()
    rpc.connect_to(target)

    def shutdown() -> None:
        rpc.close()
        if server is not None and not keep_server:
            server.stop()

    app.aboutToQuit.connect(shutdown)
    return app.exec()


def _app_icon() -> QIcon:
    svg = Path(__file__).resolve().parents[2] / "assets" / "icon.svg"
    if svg.exists():
        return QIcon(str(svg))
    return QIcon(icons.pixmap("pilcrow", "#d97757", 64)) if "pilcrow" in icons.available() else QIcon()


def _system_reduced_motion() -> bool:
    return os.environ.get("PILCROW_LITE") == "1"


__all__ = ["run", "existing_server", "ServerProcess"]
_ = (QTimer, Qt)
