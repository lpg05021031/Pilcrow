"""WebSocket JSON-RPC client with automatic reconnect (a port of web/src/lib/rpc.ts).

The wire: a request is ``{t:'req', id, m, p}``; a response ``{t:'res', id, ok, r | e}``; an event
``{t:'ev', e, d}``. ``call()`` returns a :class:`Call` whose ``then``/``catch`` run on the Qt thread
when the answer arrives; ``on()`` subscribes to an event by name.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebSockets import QWebSocket

log = logging.getLogger(__name__)

try:  # a history snapshot is megabytes of JSON parsed on the GUI thread; orjson does it several times faster
    from orjson import loads as _loads
except ImportError:  # in requirements.txt; an install without it parses with the standard library
    _loads = json.loads

Params = Dict[str, Any]


class RpcError(Exception):
    """What the server said when a call failed (its ``e.message``)."""


class Call:
    """A pending call. ``then(fn)`` gets the result, ``catch(fn)`` the RpcError; both optional. A
    plain object: the socket's handler resolves it on the Qt thread, so it needs no Qt of its own."""

    __slots__ = ("method", "_ok", "_err", "_done", "_value", "_error")

    def __init__(self, method: str) -> None:
        self.method = method
        self._ok: List[Callable[[Any], None]] = []
        self._err: List[Callable[[RpcError], None]] = []
        self._done = False
        self._value: Any = None
        self._error: Optional[RpcError] = None

    def then(self, fn: Callable[[Any], None]) -> Call:
        if self._done and self._error is None:
            fn(self._value)
        else:
            self._ok.append(fn)
        return self

    def catch(self, fn: Callable[[RpcError], None]) -> Call:
        if self._done and self._error is not None:
            fn(self._error)
        else:
            self._err.append(fn)
        return self

    def _resolve(self, value: Any) -> None:
        self._done, self._value = True, value
        for fn in self._ok:
            _safely(fn, value)

    def _reject(self, error: RpcError) -> None:
        self._done, self._error = True, error
        if not self._err:
            log.warning("%s failed: %s", self.method, error)
        for fn in self._err:
            _safely(fn, error)


def _safely(fn: Callable, arg: Any) -> None:
    try:
        fn(arg)
    except Exception:  # a handler must never take the socket down
        log.warning("an rpc handler failed", exc_info=True)


class Rpc(QObject):
    """The connection to the Pilcrow server. One per application."""

    connected_changed = Signal(bool)
    event = Signal(str, object)  # (name, payload) for anything not subscribed by name

    def __init__(self) -> None:
        super().__init__()
        self.url = ""
        self.connected = False
        self._ws: Optional[QWebSocket] = None
        self._next_id = 1
        self._pending: Dict[int, Call] = {}
        self._queue: List[str] = []
        self._retry = 0
        self._handlers: Dict[str, List[Callable[[Any], None]]] = {}
        self._closing = False

    # ---- lifecycle
    def connect_to(self, http_url: str) -> None:
        """``http://127.0.0.1:47117/`` → ``ws://127.0.0.1:47117/ws``, and keep it connected."""
        u = QUrl(http_url)
        u.setScheme("wss" if u.scheme() == "https" else "ws")
        u.setPath("/ws")
        self.url = u.toString()
        self._closing = False
        self._open()

    def close(self) -> None:
        self._closing = True
        if self._ws is not None:
            try:
                self._ws.close()
            except RuntimeError:
                self._ws = None

    def _open(self) -> None:
        if self._ws is not None or self._closing:
            return
        ws = QWebSocket()
        self._ws = ws
        ws.connected.connect(self._on_open)
        ws.disconnected.connect(self._on_close)
        ws.textMessageReceived.connect(self._on_message)
        ws.errorOccurred.connect(lambda _e: None)  # the close that follows does the work
        ws.open(QUrl(self.url))

    def _on_open(self) -> None:
        self.connected = True
        self._retry = 0
        ws = self._ws
        if ws is not None:
            for raw in self._queue:
                ws.sendTextMessage(raw)
            self._queue.clear()
        self.connected_changed.emit(True)
        self._emit("connection", {"connected": True})

    def _on_close(self) -> None:
        was = self.connected
        self.connected = False
        if self._ws is not None:
            try:
                self._ws.deleteLater()
            except RuntimeError:  # already torn down by Qt at exit
                pass
            self._ws = None
        pending = list(self._pending.values())
        self._pending.clear()
        for c in pending:
            c._reject(RpcError("Connection to Pilcrow was lost"))
        if was:
            self.connected_changed.emit(False)
            self._emit("connection", {"connected": False})
        if not self._closing:
            delay = min(5000, 300 * 2**self._retry)
            self._retry += 1
            QTimer.singleShot(delay, self._open)

    def _on_message(self, text: str) -> None:
        try:
            msg = _loads(text)
        except ValueError:
            return
        t = msg.get("t")
        if t == "res" and isinstance(msg.get("id"), int):
            call = self._pending.pop(msg["id"], None)
            if call is None:
                return
            if msg.get("ok"):
                call._resolve(msg.get("r"))
            else:
                e = msg.get("e") or {}
                call._reject(RpcError((e.get("message") if isinstance(e, dict) else None) or f"{call.method} failed"))
        elif t == "ev" and isinstance(msg.get("e"), str):
            self._emit(msg["e"], msg.get("d"))

    # ---- calls and events
    def call(self, method: str, params: Optional[Params] = None) -> Call:
        cid = self._next_id
        self._next_id += 1
        raw = json.dumps({"t": "req", "id": cid, "m": method, "p": params or {}}, ensure_ascii=False)
        call = Call(method)
        self._pending[cid] = call
        if self._ws is not None and self.connected:
            self._ws.sendTextMessage(raw)
        else:
            self._queue.append(raw)
        return call

    def on(self, name: str, fn: Callable[[Any], None]) -> Callable[[], None]:
        """Subscribe to a server event; returns the unsubscribe."""
        self._handlers.setdefault(name, []).append(fn)

        def off() -> None:
            lst = self._handlers.get(name, [])
            if fn in lst:
                lst.remove(fn)

        return off

    def _emit(self, name: str, data: Any) -> None:
        for fn in list(self._handlers.get(name, [])):
            _safely(fn, data)
        self.event.emit(name, data)


rpc = Rpc()
