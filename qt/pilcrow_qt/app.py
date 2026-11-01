"""QApplication setup: fonts, theme, the server to talk to, the main window."""

from __future__ import annotations

import faulthandler
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QApplication

from . import fonts, icons
from .rpc import rpc
from .store import store
from .theme import DARK, theme

# ---------------------------------------------------------------- finding or starting the server


def _state_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Pilcrow" / "state"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "pilcrow"


_nam: Optional[QNetworkAccessManager] = None


def ask_hello(url: str, timeout_ms: int, done: Callable[[Optional[dict]], None]) -> None:
    """``GET /api/hello`` without blocking the event loop: ``done`` gets a Pilcrow server's answer, or
    None (nothing listens, another program answered, the time ran out)."""
    global _nam
    if _nam is None:
        _nam = QNetworkAccessManager()
        _nam.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))  # a server on this machine
    req = QNetworkRequest(QUrl(url.rstrip("/") + "/api/hello"))
    req.setTransferTimeout(timeout_ms)
    reply = _nam.get(req)

    def finished() -> None:
        j = None
        if reply.error() == QNetworkReply.NetworkError.NoError:
            try:
                j = json.loads(bytes(reply.readAll().data()).decode("utf-8"))
            except ValueError:
                j = None
        reply.deleteLater()
        done(j if isinstance(j, dict) and j.get("app") == "pilcrow" else None)

    reply.finished.connect(finished)


def find_server(done: Callable[[Optional[str]], None], timeout_ms: int = 1500) -> None:
    """The Pilcrow server already running for this user (its lock file names the port): ``done`` gets
    its URL, or None. The question goes out on the event loop; the window paints meanwhile."""
    try:
        info = json.loads((_state_dir() / "instance.json").read_text(encoding="utf-8"))
    except Exception:
        info = {}
    port = info.get("port") if isinstance(info, dict) else None
    if not port:
        done(None)
        return
    url = f"http://127.0.0.1:{port}/"
    pid = info.get("pid")
    ask_hello(url, timeout_ms, lambda j: done(url if j and (not pid or j.get("pid") == pid) else None))


class ServerProcess:
    """A server started by this window (``node server/main.js --shell=none``), stopped with it."""

    def __init__(self, install_dir: str, node: str) -> None:
        self.dir = Path(os.path.expanduser(install_dir or "~/pilcrow"))
        self.node = node or self._node_from_env() or "node"
        self.proc: Optional[subprocess.Popen] = None
        self.url = ""
        self.log_path: Optional[Path] = None
        self.log = None
        self.timeout = 0.0
        self.deadline = 0.0

    def _node_from_env(self) -> str:
        env = self.dir / ".launch-env"
        if env.exists():
            for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("PILCROW_NODE="):
                    return line.split("=", 1)[1].strip().strip("'\"")
        return ""

    def start(self, timeout: float = 25.0) -> None:
        """Launch the server and return at once; ``wait_for_server`` asks when it answers."""
        main_js = self.dir / "server" / "main.js"
        if not main_js.exists():
            raise RuntimeError(
                f"Pilcrow is not installed at {self.dir} (no server/main.js). Run pilcrow-install.sh, or pass --dir."
            )
        env = {**os.environ, "PILCROW_SHELL_ENV": "0"}
        # Its output goes to a file: a pipe nobody reads fills up and blocks the server's writes
        # (Node's stdout is synchronous on a pipe on Linux). On Windows, no console window either.
        log_dir = _state_dir()
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            self.log_path = log_dir / "qt-server.log"
            self.log = open(self.log_path, "a", encoding="utf-8", errors="replace")
        except OSError:
            self.log_path, self.log = None, open(os.devnull, "w")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        self.proc = subprocess.Popen(
            [self.node, str(main_js), "--shell=none"],
            cwd=str(self.dir),
            env=env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        self.timeout = timeout
        self.deadline = time.monotonic() + timeout

    def check(self) -> None:
        """Raise RuntimeError when the server exited or the time to come up is over."""
        if self.proc is not None and self.proc.poll() is not None:
            raise RuntimeError(f"The Pilcrow server exited right away:\n{self._log_tail()}")
        if time.monotonic() > self.deadline:
            raise RuntimeError(
                f"The Pilcrow server did not come up within {self.timeout:g} seconds.\n{self._log_tail()}"
            )

    def _log_tail(self) -> str:
        try:
            if self.log_path is not None:
                return self.log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        except OSError:
            pass
        return ""

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
        if self.log is not None:
            try:
                self.log.close()
            except OSError:
                pass
            self.log = None


def wait_for_server(
    server: ServerProcess, parent: QApplication, found: Callable[[str], None], failed: Callable[[str], None]
) -> QTimer:
    """Ask every 150 ms, one question at a time, until the server we started answers (``found``) or
    it exits or runs out of time (``failed``). Nothing blocks: the window paints while it boots."""
    timer = QTimer(parent)
    timer.setInterval(150)
    asking: List[bool] = [False]

    def answered(url: Optional[str]) -> None:
        asking[0] = False
        if url and timer.isActive():
            timer.stop()
            server.url = url
            found(url)

    def tick() -> None:
        if asking[0]:
            return
        try:
            server.check()
        except RuntimeError as e:
            timer.stop()
            failed(str(e))
            return
        asking[0] = True
        find_server(answered, 500)

    timer.timeout.connect(tick)
    timer.start()
    return timer


# ---------------------------------------------------------------- the app


def run(
    url: str = "",
    install_dir: str = "",
    node: str = "",
    keep_server: bool = False,
    title: str = "Pilcrow",
    lite: bool = False,
) -> int:
    # High-DPI is Qt 6's default; the rest is ours.
    # Fonts are sized in pixels like the CSS; a stray Xft.dpi must not scale what the default
    # font does either. HiDPI (device pixel ratio) scaling is unaffected.
    os.environ.setdefault("QT_FONT_DPI", "96")
    _crash_log()
    QApplication.setApplicationName("Pilcrow")
    QApplication.setOrganizationName("Pilcrow")
    QApplication.setDesktopFileName("pilcrow")
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(True)
    # One style everywhere: the stylesheet is written against Fusion's metrics, and the platform
    # themes (gtk, windowsvista) each ignore a different part of it.
    app.setStyle("Fusion")
    fonts.load()
    theme.apply(True)  # the first frame is dark until the config says otherwise
    app.setWindowIcon(_app_icon())
    store.reduced_motion = lite or _system_reduced_motion()
    store.init()

    from .widgets.main_window import MainWindow

    win = MainWindow(title=title)
    win.show()

    started: List[ServerProcess] = []  # the server this window launched, if it had to

    def connect_or_start(target: Optional[str]) -> None:
        if target:
            rpc.connect_to(target)
            return
        # The server takes a few seconds to boot: wait for it on the event loop, so the window
        # paints and answers meanwhile instead of sitting there frozen until it is up.
        server = ServerProcess(install_dir, node)
        try:
            server.start()
        except RuntimeError as e:
            win.show_fatal(str(e))
            return
        started.append(server)
        wait_for_server(server, app, rpc.connect_to, win.show_fatal)

    if url:
        rpc.connect_to(url)
    else:
        find_server(connect_or_start)

    def shutdown() -> None:
        rpc.close()
        if started and not keep_server:
            started[0].stop()

    app.aboutToQuit.connect(shutdown)
    return app.exec()


def _crash_log() -> None:
    """A native crash (segfault in Qt) leaves a Python traceback in the host log instead of nothing."""
    try:
        log_dir = _state_dir().parent / "pilcrow-host" if sys.platform != "win32" else _state_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        f = open(log_dir / "qt-crash.log", "a", encoding="utf-8")  # kept open for the process's life
        f.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} pilcrow_qt started (pid {os.getpid()})\n")
        f.flush()
        faulthandler.enable(file=f, all_threads=True)

        def hook(exc_type, exc, tb) -> None:
            traceback.print_exception(exc_type, exc, tb, file=f)
            f.flush()
            traceback.print_exception(exc_type, exc, tb)

        sys.excepthook = hook
    except OSError:
        pass


def _app_icon() -> QIcon:
    svg = Path(__file__).resolve().parents[2] / "assets" / "icon.svg"
    if svg.exists():
        return QIcon(str(svg))
    return QIcon(icons.pixmap("pilcrow", DARK.accent, 64)) if "pilcrow" in icons.available() else QIcon()


def _system_reduced_motion() -> bool:
    """Whether the system asks for reduced motion, where that is cheap to read: Windows' "Show
    animations" (SPI_GETCLIENTAREAANIMATION), macOS's Reduce motion, GNOME's enable-animations.
    ``PILCROW_LITE=1`` asks for it whatever the system says."""
    if os.environ.get("PILCROW_LITE") == "1":
        return True
    try:
        if sys.platform == "win32":
            import ctypes  # Windows only

            on = ctypes.c_int(1)
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0):
                return not on.value
            return False
        if sys.platform == "darwin":
            cmd = ["defaults", "read", "com.apple.universalaccess", "reduceMotion"]
            return _setting(cmd) == "1"
        return _setting(["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"]) == "false"
    except (OSError, AttributeError, ValueError):
        return False


def _setting(cmd: List[str]) -> str:
    """What a settings command prints, or '' when it is missing, fails or takes more than a second."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


__all__ = ["run", "find_server", "wait_for_server", "ServerProcess"]
