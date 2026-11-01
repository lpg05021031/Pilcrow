"""One clock for everything that moves.

A single QTimer: 50 ms while only indicators are live (breathing marks, spinners, rails, elapsed
counters: a breath of 1.2–1.8 s reads the same at 20 frames a second as at 30, for two thirds of the
repaints), 16 ms while a tween runs, stopped when nothing subscribes. Subscribers are weak
references with a ``tick(now)`` method: the canvas views (each repaints its live blocks' rects) and
the indicator widgets of ``widgets/ui/bits.Indicator`` (each repaints itself while shown).

``Tween`` animates one number on the clock with a QEasingCurve and calls back each frame; it is what
the reveal of a fold, the easing of the scroll position and the composer's springs run on, so a frame
costs one callback and the canvas and the composer share one timer for animation.
"""

from __future__ import annotations

import time
import weakref
from typing import Any, Callable, List, Optional

from PySide6.QtCore import QEasingCurve, QObject, Qt, QTimer

from ..store import store

SLOW_MS = 50
FAST_MS = 16


class Clock(QObject):
    def __init__(self) -> None:
        super().__init__()
        self._timer = QTimer(self)
        # precise: Windows' default timer resolution is 15.6 ms, which would turn 16 ms into 31
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._subs: List[weakref.ReferenceType] = []
        # what holds the fast rate: weak, so a tween dropped (or torn down) mid-run gives it back
        self._fast: weakref.WeakSet = weakref.WeakSet()
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

    def hold_fast(self, obj: Any) -> None:
        self._fast.add(obj)
        self._arm()

    def release_fast(self, obj: Any) -> None:
        self._fast.discard(obj)
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
                self._fast.discard(s)
        self._arm()


clock = Clock()


class Tween:
    """One value eased on the clock; ``on_frame(value)`` each tick, ``on_done()`` once at each end.

    ``start(a, b, ms, curve)`` runs from ``a`` to ``b``. ``go(target)`` runs from where the value is
    to ``target`` with the tween's own ``ms`` and ``curve`` (a factory such as ``theme.spring_soft``),
    and lands at once under reduced motion. Starting again retargets from the current value."""

    def __init__(
        self,
        on_frame: Callable[[float], None],
        on_done: Optional[Callable[[], None]] = None,
        ms: int = 1,
        curve: Optional[Callable[[], QEasingCurve]] = None,
    ) -> None:
        self._on_frame = on_frame
        self._on_done = on_done
        self._go_ms = ms
        self._go_curve = curve
        self.value = 0.0
        self._a = 0.0
        self._b = 0.0
        self._t0 = 0.0
        self._ms = 1
        self._curve: Optional[QEasingCurve] = None
        self.running = False

    def start(self, a: float, b: float, ms: int, curve: Optional[QEasingCurve]) -> None:
        self._a, self._b, self._ms, self._curve = float(a), float(b), max(1, int(ms)), curve
        self.value = self._a
        self._t0 = time.monotonic()
        if not self.running:
            self.running = True
            clock.hold_fast(self)
            clock.subscribe(self)

    def go(self, target: float) -> None:
        if store.reduced_motion or abs(target - self.value) < 1e-6:
            self.jump(target)
            if self._on_done is not None:
                self._on_done()
            return
        self.start(self.value, target, self._go_ms, self._go_curve() if self._go_curve is not None else None)

    def jump(self, v: float) -> None:
        self.stop()
        self.value = float(v)
        self._on_frame(self.value)

    def stop(self) -> None:
        if self.running:
            self.running = False
            clock.release_fast(self)
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
