"""The adversarial harness: a transcript fed through the store's own path (model push + dirty flush),
with the main thread's stalls, the paints and the Markdown renders counted while it happens."""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

from pilcrow_qt import fonts, markdown, theme  # noqa: E402
from pilcrow_qt.canvas.transcript import Transcript, forget_scenes  # noqa: E402
from pilcrow_qt.canvas.view import CanvasView  # noqa: E402
from pilcrow_qt.lib.transcript_model import TranscriptModel  # noqa: E402
from pilcrow_qt.store import Runtime, store  # noqa: E402

CWD = "/proj"
_seq = 0


def uid() -> str:
    global _seq
    _seq += 1
    return f"s-{_seq}"


def app() -> QApplication:
    a = QApplication.instance() or QApplication([])
    fonts.load()
    theme.theme.apply(True)
    store.reduced_motion = False  # the real thing animates
    return a


def pump(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.0005)


# ---------------------------------------------------------------- messages


def user(text: str) -> Dict[str, Any]:
    return {"type": "user", "uuid": uid(), "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def assistant(content: List[Dict[str, Any]], parent: Optional[str] = None) -> Dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uid(),
        "message": {"id": f"m-{uid()}", "role": "assistant", "content": content},
        "parent_tool_use_id": parent,
    }


def tool_use(tid: str, name: str, inp: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def tool_result(tid: str, content: Any, structured: Any = None, parent: Optional[str] = None) -> Dict[str, Any]:
    return {
        "type": "user",
        "uuid": uid(),
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": content}]},
        "tool_use_result": structured,
        "parent_tool_use_id": parent,
    }


def stream(event: Dict[str, Any], parent: Optional[str] = None) -> Dict[str, Any]:
    return {"type": "stream_event", "uuid": uid(), "event": event, "parent_tool_use_id": parent}


# ---------------------------------------------------------------- a session on screen


class Session:
    """A Transcript widget over a model in the store, fed the way the server feeds it."""

    def __init__(self, sid: str, msgs: List[Dict[str, Any]], width: int = 1220, height: int = 900) -> None:
        self.sid = sid
        forget_scenes()
        self.model = store.models[sid] = TranscriptModel()
        self.model.push_all(msgs)
        store.rt[sid] = Runtime(loaded=True, st={"status": "idle", "cwd": CWD})
        self.win = QWidget()
        self.win.setObjectName("canvas")
        self.win.resize(width, height)
        lay = QVBoxLayout(self.win)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tr = Transcript(sid, CWD)
        lay.addWidget(self.tr)
        self.win.show()
        pump(300)

    @property
    def view(self) -> CanvasView:
        return self.tr.view

    def feed(self, msg: Dict[str, Any]) -> None:
        """What store._on_msgs does with one message."""
        self.model.push(msg)
        store.mark_dirty(self.sid)

    def set_status(self, status: str) -> None:
        store.rt[self.sid].st["status"] = status
        store.rt_changed.emit(self.sid)

    def close(self) -> None:
        self.tr.close()
        self.win.close()
        store.models.pop(self.sid, None)
        store.rt.pop(self.sid, None)
        pump(30)


class Streamer:
    """One assistant message streaming into a session: text, thinking or a tool's input."""

    def __init__(self, s: Session, kind: str = "text", index: int = 0) -> None:
        self.s = s
        self.index = index
        self.kind = kind
        self.msg_id = f"m-{uid()}"
        s.set_status("running")
        s.feed(stream({"type": "message_start", "message": {"id": self.msg_id}}))
        block = {"type": kind, "text": ""} if kind == "text" else {"type": "thinking", "thinking": ""}
        s.feed(stream({"type": "content_block_start", "index": index, "content_block": block}))

    def delta(self, text: str) -> None:
        d = (
            {"type": "text_delta", "text": text}
            if self.kind == "text"
            else {"type": "thinking_delta", "thinking": text}
        )
        self.s.feed(stream({"type": "content_block_delta", "index": self.index, "delta": d}))

    def stop(self) -> None:
        self.s.feed(stream({"type": "content_block_stop", "index": self.index}))
        self.s.set_status("idle")


# ---------------------------------------------------------------- instrumentation


class Probe:
    """Counts what the main thread does: stalls (gaps between 1 ms timer ticks), paints and their
    time, Markdown renders and their time."""

    def __init__(self, view: Optional[CanvasView] = None, stall_ms: float = 20.0) -> None:
        self.stalls: List[float] = []
        self.ticks = 0
        self.stall_ms = stall_ms
        self.paints = 0
        self.paint_ms: List[float] = []
        self.paint_rects: List[int] = []
        self.renders = 0
        self.render_ms = 0.0
        self._last = time.perf_counter()
        self._timer = QTimer()
        self._timer.setInterval(1)
        self._timer.timeout.connect(self._tick)
        self._view = view
        self._orig_render = markdown.render_html
        probe = self

        def render(*a: Any, **k: Any) -> str:
            t0 = time.perf_counter()
            try:
                return probe._orig_render(*a, **k)
            finally:
                probe.renders += 1
                probe.render_ms += (time.perf_counter() - t0) * 1000

        markdown.render_html = render  # type: ignore[assignment]
        self._filter: Optional[QObject] = None
        if view is not None:
            # paints are counted by an event filter on the viewport (a Python override of paintEvent
            # set after construction is not seen by Qt); their time is the stall probe's business
            class F(QObject):
                def eventFilter(self, obj: Any, e: Any) -> bool:  # noqa: N802
                    if e.type() == QEvent.Type.Paint:
                        probe.paints += 1
                        probe.paint_rects.append(e.rect().height())
                    return False

            self._filter = F()
            view.viewport().installEventFilter(self._filter)

    def _tick(self) -> None:
        now = time.perf_counter()
        gap = (now - self._last) * 1000
        self._last = now
        self.ticks += 1
        if gap > self.stall_ms:
            self.stalls.append(gap)

    def start(self) -> None:
        self._last = time.perf_counter()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        markdown.render_html = self._orig_render  # type: ignore[assignment]
        if self._view is not None and self._filter is not None:
            self._view.viewport().removeEventFilter(self._filter)

    def report(self, label: str) -> str:
        st = sorted(self.stalls)
        s_max = st[-1] if st else 0.0
        full = sum(1 for h in self.paint_rects if h >= 600)
        return (
            f"{label}: stalls>{self.stall_ms:.0f}ms n={len(st)} max={s_max:.0f}ms | paints={self.paints} "
            f"(full-height {full}) | renders={self.renders} total={self.render_ms:.0f}ms"
        )


def timed(fn: Callable[[], Any]) -> float:
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000
