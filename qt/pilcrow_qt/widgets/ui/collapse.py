"""Height-animated disclosure (a port of web/src/components/ui/Collapse.tsx).

Opening grows from nothing to the content's height on the soft spring; closing shrinks it back
before the content is hidden, so collapsing feels as deliberate as expanding. Content that is
already open when it first shows appears without animation. The body is built lazily the first
time it opens (``builder``), like the function children in the React version.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QPropertyAnimation, Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ... import theme
from ...store import store


class Collapse(QWidget):
    def __init__(self, builder: Callable[[], QWidget], open_: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._builder = builder
        self._body: Optional[QWidget] = None
        self._open = open_
        self._anim: Optional[QPropertyAnimation] = None
        self._first = True
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self.setVisible(open_)
        if open_:
            self._ensure_body()

    @property
    def is_open(self) -> bool:
        return self._open

    def body(self) -> Optional[QWidget]:
        return self._body

    def _ensure_body(self) -> QWidget:
        if self._body is None:
            self._body = self._builder()
            self._lay.addWidget(self._body)
        return self._body

    def set_open(self, open_: bool, animate: bool = True) -> None:
        if open_ == self._open and (self.isVisible() == open_):
            return
        self._open = open_
        first, self._first = self._first, False
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if open_:
            self._ensure_body()
        if not animate or store.reduced_motion or (first and open_) or not self.isVisibleTo(self.parentWidget()) and not open_:
            self.setMaximumHeight(16777215)
            self.setVisible(open_)
            return
        if open_:
            self.setMaximumHeight(0)
            self.setVisible(True)
            target = self._body.sizeHint().height() if self._body is not None else 0
            self._run(0, target, theme.spring_soft(), min(420, 240 + target // 5), lambda: self.setMaximumHeight(16777215))
        else:
            start = self.height()
            self.setMaximumHeight(start)
            self._run(start, 0, theme.ease_exit(), min(260, 160 + start // 10), lambda: (self.setVisible(False), self.setMaximumHeight(16777215)))

    def _run(self, a: int, b: int, curve, ms: int, done: Callable[[], None]) -> None:
        anim = QPropertyAnimation(self, b"maximumHeight", self)
        anim.setStartValue(a)
        anim.setEndValue(b)
        anim.setDuration(ms)
        anim.setEasingCurve(curve)

        def finished() -> None:
            self._anim = None
            done()

        anim.finished.connect(finished)
        anim.start()
        self._anim = anim

    def toggle(self) -> None:
        self.set_open(not self._open)


_ = Qt
