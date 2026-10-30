"""One clock for everything that moves.

A single QTimer: 33 ms while only indicators are live (breathing marks, spinners, rails, elapsed
counters), 16 ms while a tween runs, stopped when nothing subscribes. Subscribers are weak
references with a ``tick(now)`` method that returns the rectangle to repaint (or None).

``Tween`` animates one number on the clock with a QEasingCurve and calls back each frame; it is what
the reveal of a fold and the easing of the scroll position run on, so a frame costs one callback and
there is exactly one timer in the process for animation.
"""

from __future__ import annotations

import time
import weakref
from typing import Any, Callable, List, Optional

from PySide6.QtCore import QEasingCurve, QObject, QTimer

SLOW_MS = 33
FAST_MS = 16


class Clock(QObject):
    def __init__(self) -> None:
        super().__init__()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._subs: List[weakref.ReferenceType] = []
        self._fast = 0  # tweens holding the fast rate
        self._interval = 0

    # ---- subscribers
    def subscribe(self, obj: Any) -> None:
        for r in self._subs:
            if r() is obj:
                return
        self._subs.append(weakref.ref(obj))
        self._arm()

    def unsubscribe(self, obj: Any) -> None:
        self._subs = [r for r in self._subs if r() is not None and r() is not obj]
        self._arm()

    def hold_fast(self) -> None:
        self._fast += 1
        self._arm()

    def release_fast(self) -> None:
        self._fast = max(0, self._fast - 1)
        self._arm()

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def _arm(self) -> None:
        self._subs = [r for r in self._subs if r() is not None]
        if not self._subs:
            if self._timer.isActive():
                self._timer.stop()
            self._interval = 0
            return
        want = FAST_MS if self._fast else SLOW_MS
        if not self._timer.isActive() or want != self._interval:
            self._interval = want
            self._timer.start(want)

    def _tick(self) -> None:
        now = time.monotonic()
        for r in list(self._subs):
            s = r()
            if s is None:
                continue
            try:
                s.tick(now)
            except RuntimeError:  # a Qt object torn down under us
                self._subs = [x for x in self._subs if x is not r]
        self._arm()


clock = Clock()


class Tween:
    """One value from ``a`` to ``b`` over ``ms`` on ``curve``; ``on_frame(value)`` each tick,
    ``on_done()`` once at the end. Starting again retargets from the current value."""

    def __init__(self, on_frame: Callable[[float], None], on_done: Optional[Callable[[], None]] = None) -> None:
        self._on_frame = on_frame
        self._on_done = on_done
        self.value = 0.0
        self._a = 0.0
        self._b = 0.0
        self._t0 = 0.0
        self._ms = 1
        self._curve: Optional[QEasingCurve] = None
        self.running = False

    def start(self, a: float, b: float, ms: int, curve: QEasingCurve) -> None:
        self._a, self._b, self._ms, self._curve = float(a), float(b), max(1, int(ms)), curve
        self.value = self._a
        self._t0 = time.monotonic()
        if not self.running:
            self.running = True
            clock.hold_fast()
            clock.subscribe(self)

    def jump(self, v: float) -> None:
        self.stop()
        self.value = float(v)
        self._on_frame(self.value)

    def stop(self) -> None:
        if self.running:
            self.running = False
            clock.release_fast()
            clock.unsubscribe(self)

    def tick(self, now: float) -> None:
        if not self.running:
            return
        t = min(1.0, (now - self._t0) * 1000.0 / self._ms)
        k = self._curve.valueForProgress(t) if self._curve is not None else t
        self.value = self._a + (self._b - self._a) * k
        done = t >= 1.0
        if done:
            self.value = self._b
            self.stop()
        self._on_frame(self.value)
        if done and self._on_done is not None:
            self._on_done()
