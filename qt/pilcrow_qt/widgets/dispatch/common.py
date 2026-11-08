"""What the dispatch screens share: the colour and word of each run state, the segmented progress bar,
the state glyph, and the small words that describe a job."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ... import theme
from ...lib.fmt import cost as fmt_cost
from ...lib.fmt import duration, plural
from ..ui.bits import Indicator

Json = Dict[str, Any]

# a run's state → the token it is drawn in (the session status pill's colours everywhere else)
STATE_TONE = {
    "queued": "text_4",
    "preparing": "accent",
    "running": "accent",
    "needs_action": "warn",
    "done": "ok",
    "failed": "err",
    "stopped": "text_3",
    "skipped": "text_4",
}
STATE_WORD = {
    "queued": "queued",
    "preparing": "preparing",
    "running": "running",
    "needs_action": "needs action",
    "done": "done",
    "failed": "failed",
    "stopped": "stopped",
    "skipped": "skipped",
}
# the order rows settle into: what needs a hand first, then the live ones, then the rest
STATE_RANK = {
    "needs_action": 0,
    "running": 1,
    "preparing": 1,
    "queued": 2,
    "done": 3,
    "failed": 4,
    "stopped": 5,
    "skipped": 6,
}
ACTIVE = ("preparing", "running", "needs_action")
TERMINAL = ("done", "failed", "stopped", "skipped")


def clock_mmss(ms: float) -> str:
    s = max(0, int(ms // 1000))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def counts_text(job: Json) -> str:
    """``7 done · 2 ask · 2 running · 1 queued``: the states that have runs, in the order of the bar."""
    c = job.get("counts") or {}
    parts = []
    for key, word in (
        ("done", "done"),
        ("needs_action", "ask"),
        ("running", "running"),
        ("preparing", "preparing"),
        ("queued", "queued"),
        ("failed", "failed"),
        ("stopped", "stopped"),
        ("skipped", "skipped"),
    ):
        n = int(c.get(key) or 0)
        if n:
            parts.append(f"{n} {word}")
    return " · ".join(parts) or f"{plural(int(job.get('total') or 0), 'repo')}"


def job_state_text(job: Json, now: float) -> str:
    """The job's one-line state: ``running 14:02``, ``canary done — read it and continue``, ``finished``."""
    st = str(job.get("state") or "")
    if st == "running":
        if job.get("paused"):
            return "paused"
        if job.get("capReached"):
            return "cost cap reached"
        started = float(job.get("startedAt") or 0)
        return f"running {clock_mmss(now - started)}" if started else "running"
    if st == "reviewing":
        return "canary done — read it and continue" if job.get("canaryWaiting") else "review"
    if st == "finished":
        return "finished"
    if st == "stopped":
        return str(job.get("stoppedReason") or "stopped").lower()
    if st == "planned":
        return "planned"
    return "draft"


def job_cost_text(job: Json) -> str:
    c = float(job.get("cost") or 0)
    cap = float(((job.get("settings") or {}).get("costCap")) or 0)
    return f"{fmt_cost(c)} of {fmt_cost(cap)}" if cap else fmt_cost(c)


def elapsed_text(job: Json, now: float) -> str:
    started = float(job.get("startedAt") or 0)
    ended = float(job.get("finishedAt") or 0)
    if not started:
        return ""
    return duration((ended or now) - started)


def group_requests(runs: List[Json]) -> List[Json]:
    """The waiting requests across a job's runs, one entry per key: ``{key, label, interactive, runs:
    [(run, pending)], names}``; the one asked by most runs first, then the oldest."""
    by_key: Dict[str, Json] = {}
    for r in runs:
        for p in r.get("pending") or []:
            k = str(p.get("key") or p.get("requestId") or "")
            e = by_key.get(k)
            if e is None:
                e = by_key[k] = {
                    "key": k,
                    "label": str(p.get("label") or p.get("toolName") or ""),
                    "interactive": bool(p.get("interactive")),
                    "at": float(p.get("at") or 0),
                    "asks": [],
                }
            e["asks"].append((r, p))
            e["at"] = min(e["at"], float(p.get("at") or e["at"]))
    out = list(by_key.values())
    for e in out:
        e["names"] = [str(r.get("name") or "") for r, _p in e["asks"]]
    out.sort(key=lambda e: (-len(e["asks"]), e["at"]))
    return out


def asked_by(names: List[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return f"asked by {names[0]}"
    if len(names) == 2:
        return f"asked by {names[0]} and {names[1]}"
    return f"asked by {names[0]}, {names[1]} and {len(names) - 2} more"


class SegmentBar(Indicator):
    """The job's progress: one segment per run, in the colour of its state, in the bar's own order
    (done first, then the ones that ask, the live ones, the queued, the failed). The live segments
    breathe on the clock."""

    ORDER = ("done", "needs_action", "running", "preparing", "queued", "failed", "stopped", "skipped")

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._states: List[str] = []
        self.setFixedHeight(6)
        self.setMinimumWidth(80)

    def set_counts(self, counts: Json) -> None:
        states: List[str] = []
        for k in self.ORDER:
            states.extend([k] * int(counts.get(k) or 0))
        if states != self._states:
            self._states = states
            self.update()
        self.set_live(any(s in ("running", "preparing") for s in states))

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        n = len(self._states)
        w, h = self.width(), self.height()
        if not n:
            p.setBrush(theme.blend(t.fill_2, t.canvas))
            p.drawRoundedRect(QRectF(0, 0, w, h), 2, 2)
            return
        gap = 2 if n <= 40 else 1
        seg = (w - gap * (n - 1)) / n
        alpha = theme.breath(time.monotonic(), 1.6, 0.55) if self.moving else 1.0
        x = 0.0
        for s in self._states:
            tone = STATE_TONE.get(s, "text_4")
            c = QColor(theme.blend(getattr(t, tone), t.canvas)) if tone.startswith("text") else QColor(getattr(t, tone))
            if s in ("running", "preparing"):
                c.setAlphaF(alpha)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x, 0, max(1.0, seg), h), 2, 2)
            x += seg + gap


def paint_glyph(p: QPainter, r: QRectF, state: str, t: theme.Tokens, breathing: bool) -> None:
    """The run glyph, drawn into ``r`` (14px square); a live run's dot breathes (the row is live on
    the canvas clock)."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    cx, cy = r.center().x(), r.center().y()
    tone = STATE_TONE.get(state, "text_4")
    c = QColor(theme.blend(getattr(t, tone), t.canvas)) if tone.startswith("text") else QColor(getattr(t, tone))
    if state in ("queued", "skipped"):
        p.setPen(QPen(c, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, cy), 3.75, 3.75)
        return
    if state in ("running", "preparing") and breathing:
        c.setAlphaF(theme.breath(time.monotonic(), 1.6, 0.5))
    p.setBrush(c)
    p.drawEllipse(QPointF(cx, cy), 4.5, 4.5)
