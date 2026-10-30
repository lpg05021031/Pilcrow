"""Instruments for the benchmarks: what the Qt client costs while it is being used.

The rig boots the client offscreen against ``test/mock/mock-server.mjs`` (``harness.Client``) and
drives it with real input events (QTest: wheel, mouse, keys), the way Playwright drives the web
client. While a scenario runs it records:

- **stalls**: how long each turn of the event loop takes (the time the window cannot respond);
  p50 / p95 / max, and the count over 16.7 ms (a dropped frame) and over 50 ms (a "long task");
- **layout passes**: ``LayoutRequest`` events delivered inside a widget tree (one per QLayout
  activation), plus content-widget resizes (each one moves the scrollbar range);
- **paints**: paint events on a viewport, and the area they covered;
- **widgets made**: ``ChildAdded`` events under a tree (what a re-render rebuilt);
- **scroll sets**: how often the scroll position was changed by the program;
- **frames**: a grab of the viewport after each event-loop turn, reduced to a hash per scanline,
  from which it reads the vertical shift between consecutive frames (content that moved), rows
  that flashed blank and came back (flicker), and how many distinct frames an animation produced;
- CPU share and resident memory.

Everything here is measurement; ``scenarios.py`` says what to do, ``run.py`` prints the table.
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "qt"))
sys.path.insert(0, str(ROOT / "qt" / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

FRAME_MS = 1000 / 60
# True while a Film grabs a widget: the grab paints the whole tree, and those paints are not the client's.
GRABBING = False


# ---------------------------------------------------------------- numbers


def pct(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * q))]


def summary(values: List[float], unit: float = 1000.0) -> Dict[str, float]:
    """p50 / p95 / max of a series, in milliseconds when the series is seconds."""
    if not values:
        return {"n": 0, "p50": 0.0, "p95": 0.0, "max": 0.0, "mean": 0.0}
    return {
        "n": len(values),
        "p50": round(pct(values, 0.5) * unit, 2),
        "p95": round(pct(values, 0.95) * unit, 2),
        "max": round(max(values) * unit, 2),
        "mean": round(statistics.fmean(values) * unit, 2),
    }


def rss_mb() -> float:
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return 0.0


# ---------------------------------------------------------------- event counters


class Scope:
    """Counts for one widget tree (a transcript, a panel, the window)."""

    def __init__(self, name: str, root: QWidget) -> None:
        self.name = name
        self.root = root
        self.reset()

    def reset(self) -> None:
        self.layout = 0
        self.paint = 0
        self.paint_area = 0
        self.resize = 0
        self.move = 0
        self.children_added = 0
        self.cold_paints0 = self._cold_paints()

    def _cold_paints(self) -> int:
        """The canvas engine's count of placeholders painted (a block shown before it was measured);
        the widget transcript has no such thing and reads 0."""
        view = getattr(self.root, "view", None)
        return int(getattr(view, "cold_paints", 0) or 0)

    def snapshot(self) -> Dict[str, int]:
        return {
            "layout": self.layout,
            "paint": self.paint,
            "paint_px": self.paint_area,
            "resize": self.resize,
            "move": self.move,
            "widgets_made": self.children_added,
            "cold_paints": self._cold_paints() - self.cold_paints0,
        }


class Counters(QObject):
    """An application-wide event filter that attributes events to the scopes they fall in."""

    def __init__(self) -> None:
        super().__init__()
        self.scopes: Dict[str, Scope] = {}
        QApplication.instance().installEventFilter(self)

    def scope(self, name: str, root: QWidget) -> Scope:
        s = Scope(name, root)
        self.scopes[name] = s
        return s

    def drop(self, name: str) -> None:
        self.scopes.pop(name, None)

    def reset(self) -> None:
        for s in self.scopes.values():
            s.reset()

    def snapshot(self) -> Dict[str, Dict[str, int]]:
        return {k: v.snapshot() for k, v in self.scopes.items()}

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        t = e.type()
        if t == QEvent.Type.LayoutRequest:
            kind = "layout"
        elif t == QEvent.Type.Paint:
            if GRABBING:
                return False
            kind = "paint"
        elif t == QEvent.Type.Resize:
            kind = "resize"
        elif t == QEvent.Type.Move:
            kind = "move"
        elif t == QEvent.Type.ChildAdded:
            kind = "child"
        else:
            return False
        if not isinstance(obj, QWidget):
            return False
        for s in self.scopes.values():
            root = s.root
            try:
                inside = obj is root or root.isAncestorOf(obj)
            except RuntimeError:
                continue
            if not inside:
                continue
            if kind == "layout":
                s.layout += 1
            elif kind == "paint":
                s.paint += 1
                r = e.rect()
                s.paint_area += r.width() * r.height()
            elif kind == "resize":
                s.resize += 1
            elif kind == "move":
                s.move += 1
            elif kind == "child":
                child = e.child()
                if child is not None and child.isWidgetType():
                    s.children_added += 1
        return False


# ---------------------------------------------------------------- frames


@dataclass
class Frame:
    t: float
    rows: List[int]  # one hash per scanline
    blank: List[bool]


class Film:
    """Grabs of one viewport, one per event-loop turn, reduced to scanline hashes."""

    def __init__(self, widget: QWidget, bg: QColor, region: Optional[Tuple[int, int, int, int]] = None) -> None:
        self.widget = widget
        self.frames: List[Frame] = []
        self.region = region  # x, y, w, h inside the widget; None = all of it
        self._bg = bg
        self._bg_row: Optional[int] = None
        self._w = 0

    def _blank_row(self, img: QImage) -> int:
        if self._bg_row is None or self._w != img.width():
            one = QImage(img.width(), 1, img.format())
            one.fill(self._bg)
            self._bg_row = hash(bytes(one.constBits())[: img.width() * 4])
            self._w = img.width()
        return self._bg_row

    def grab(self) -> None:
        global GRABBING
        w = self.widget
        GRABBING = True
        try:
            pm = w.grab() if self.region is None else w.grab(_qrect(*self.region))
        finally:
            GRABBING = False
        img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        blank_hash = self._blank_row(img)
        bpl = img.bytesPerLine()
        width = img.width() * 4
        bits = bytes(img.constBits())
        rows = [hash(bits[y * bpl : y * bpl + width]) for y in range(img.height())]
        self.frames.append(Frame(time.perf_counter(), rows, [r == blank_hash for r in rows]))

    # ---- analysis
    def distinct(self) -> int:
        """How many frames differed from the one before: the frames an animation actually produced."""
        n = 0
        for a, b in zip(self.frames, self.frames[1:]):
            if a.rows != b.rows:
                n += 1
        return n

    def shifts(self, expected: Optional[Callable[[int], int]] = None, max_shift: int = 400) -> Dict[str, Any]:
        """The vertical shift of the content between consecutive frames (the offset most scanlines moved
        by), and the part of it that was not expected (``expected(i)`` is the shift the person caused
        between frame i-1 and i, e.g. their scrolling). Unexpected shift is the Qt analogue of the
        web's cumulative layout shift."""
        shifts: List[int] = []
        unexpected: List[int] = []
        for i in range(1, len(self.frames)):
            a, b = self.frames[i - 1], self.frames[i]
            where: Dict[int, List[int]] = {}
            for y, (h, blank) in enumerate(zip(a.rows, a.blank)):
                if not blank:
                    where.setdefault(h, []).append(y)
            votes: Dict[int, int] = {}
            for y, (h, blank) in enumerate(zip(b.rows, b.blank)):
                if blank:
                    continue
                ys = where.get(h)
                if not ys or len(ys) > 8:
                    continue  # a repeated row (a hairline, a blank-ish band) votes for nothing
                for ya in ys:
                    d = ya - y
                    if -max_shift <= d <= max_shift:
                        votes[d] = votes.get(d, 0) + 1
            if not votes:
                shifts.append(0)
                unexpected.append(0)
                continue
            d = max(votes.items(), key=lambda kv: kv[1])[0]
            shifts.append(d)
            exp = expected(i) if expected else 0
            unexpected.append(d - exp)
        return {
            "frames": len(self.frames),
            "moved_frames": sum(1 for d in shifts if d),
            "unexpected_frames": sum(1 for d in unexpected if d),
            "unexpected_px": sum(abs(d) for d in unexpected),
            "max_jump_px": max((abs(d) for d in unexpected), default=0),
        }

    def flashes(self, min_rows: int = 24) -> int:
        """Frames in which a band of at least ``min_rows`` scanlines went blank and the same content was
        back in the same place by the next frame: what the eye sees as a flicker. (Content moving past
        a gap is not one: the rows that come back are different rows.)"""
        n = 0
        for i in range(1, len(self.frames) - 1):
            a, b, c = self.frames[i - 1], self.frames[i], self.frames[i + 1]
            rows = sum(
                1
                for y in range(len(b.blank))
                if b.blank[y] and not a.blank[y] and not c.blank[y] and a.rows[y] == c.rows[y]
            )
            if rows >= min_rows:
                n += 1
        return n

    def blank_fraction(self) -> float:
        """The share of scanlines that were background, averaged over the frames (a fling that outruns
        the drawing shows as a high number)."""
        if not self.frames:
            return 0.0
        return round(statistics.fmean(sum(f.blank) / max(1, len(f.blank)) for f in self.frames), 3)


def _qrect(x: int, y: int, w: int, h: int):
    from PySide6.QtCore import QRect

    return QRect(x, y, w, h)


# ---------------------------------------------------------------- the clock


@dataclass
class Pump:
    """One run of the event loop under observation."""

    stalls: List[float] = field(default_factory=list)
    wall: float = 0.0
    cpu: float = 0.0

    def report(self) -> Dict[str, Any]:
        s = summary(self.stalls)
        s["over_16ms"] = sum(1 for x in self.stalls if x * 1000 > FRAME_MS)
        s["over_50ms"] = sum(1 for x in self.stalls if x * 1000 > 50)
        s["wall_s"] = round(self.wall, 2)
        s["cpu_pct"] = round(self.cpu / self.wall * 100, 1) if self.wall else 0.0
        return s


def pump(
    seconds: float,
    until: Optional[Callable[[], bool]] = None,
    on_frame: Optional[Callable[[], None]] = None,
    film: Optional[Film] = None,
    idle_sleep: float = 0.002,
) -> Pump:
    """Run the event loop for ``seconds`` (or until ``until()``), timing each turn of it. ``on_frame``
    and the film run between turns and are not counted as stalls."""
    out = Pump()
    app = QCoreApplication.instance()
    end = time.monotonic() + seconds
    cpu0, wall0 = time.process_time(), time.perf_counter()
    while True:
        t0 = time.perf_counter()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        out.stalls.append(time.perf_counter() - t0)
        if film is not None:
            film.grab()
        if on_frame is not None:
            on_frame()
        if until is not None and until():
            break
        if time.monotonic() >= end:
            break
        if idle_sleep:
            time.sleep(idle_sleep)
    out.wall = time.perf_counter() - wall0
    out.cpu = time.process_time() - cpu0
    return out


def settle(ms: int = 300) -> None:
    """Let the client finish what it started (layouts, deferred deletes, the mock's replies)."""
    pump(ms / 1000)


class Paced:
    """Input at a steady rate by the clock, not by the frame: ``due()`` says how many steps the person
    would have made by now, so a slow client gets the same input as a fast one."""

    def __init__(self, per_second: float, total: int) -> None:
        self.per_second = per_second
        self.total = total
        self.sent = 0
        self.t0 = time.perf_counter()

    def due(self) -> int:
        n = min(self.total, int((time.perf_counter() - self.t0) * self.per_second)) - self.sent
        self.sent += max(0, n)
        return max(0, n)

    @property
    def done(self) -> bool:
        return self.sent >= self.total


# ---------------------------------------------------------------- input


def wheel(widget: QWidget, notches: float, pos: Optional[QPoint] = None) -> None:
    """A mouse-wheel step on ``widget``: positive notches scroll up, like a real wheel."""
    p = pos or QPoint(widget.width() // 2, widget.height() // 2)
    gp = widget.mapToGlobal(p)
    ev = QWheelEvent(
        QPointF(p),
        QPointF(gp),
        QPoint(0, 0),
        QPoint(0, int(round(notches * 120))),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, ev)


def click(widget: QWidget, pos: Optional[QPoint] = None) -> None:
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos or widget.rect().center())


def move(widget: QWidget, pos: QPoint) -> None:
    QTest.mouseMove(widget, pos)


def type_text(widget: QWidget, text: str) -> None:
    QTest.keyClicks(widget, text)


def visible_in(widget: QWidget, viewport: QWidget) -> bool:
    """Is a row widget (anywhere under the viewport) inside the viewport's visible rectangle?"""
    if not widget.isVisible():
        return False
    try:
        top = widget.mapTo(viewport, QPoint(0, 0))
    except RuntimeError:
        return False
    return 0 <= top.y() and top.y() + widget.height() <= viewport.height()


def y_in(widget: QWidget, viewport: QWidget) -> int:
    return widget.mapTo(viewport, QPoint(0, 0)).y()


__all__ = [
    "Counters",
    "Film",
    "Paced",
    "Pump",
    "click",
    "move",
    "pct",
    "pump",
    "rss_mb",
    "settle",
    "summary",
    "type_text",
    "visible_in",
    "wheel",
    "y_in",
]
