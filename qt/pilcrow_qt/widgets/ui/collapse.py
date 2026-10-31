"""Height-animated disclosure (a port of web/src/components/ui/Collapse.tsx).

Opening grows from nothing to the content's height on the soft spring; closing shrinks it back
before the content is hidden, so collapsing feels as deliberate as expanding. Content that is
already open when it first shows appears without animation. The body is built lazily the first
time it opens (``builder``), like the function children in the React version.

The target height is the body's height at the real width (``heightForWidth`` when the body wraps),
so the reveal lands where the content ends; the frames run on the shared clock (one timer in the
process), not on a QPropertyAnimation of their own.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtWidgets import QVBoxLayout, QWidget

from ... import theme
from ...canvas.clock import Tween
from ...store import store

NO_MAX = 16777215


class Collapse(QWidget):
    def __init__(self, builder: Callable[[], QWidget], open_: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._builder = builder
        self._body: Optional[QWidget] = None
        self._open = open_
        self._tween: Optional[Tween] = None
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

    def _target_height(self) -> int:
        """What the body wants at the width it will be laid out at."""
        body = self._body
        if body is None:
            return 0
        width = self.width() or (self.parentWidget().width() if self.parentWidget() is not None else 0)
        m = self.contentsMargins()
        inner = max(1, width - m.left() - m.right()) if width else 0
        if inner and (body.hasHeightForWidth() or (body.layout() is not None and body.layout().hasHeightForWidth())):
            h = body.heightForWidth(inner)
            if h > 0:
                return h + m.top() + m.bottom()
        return body.sizeHint().height() + m.top() + m.bottom()

    def set_open(self, open_: bool, animate: bool = True) -> None:
        if open_ == self._open and (self.isVisible() == open_):
            return
        self._open = open_
        first, self._first = self._first, False
        if self._tween is not None:
            self._tween.stop()
            self._tween = None
        if open_:
            self._ensure_body()
        if (
            not animate
            or store.reduced_motion
            or (first and open_)
            or not self.isVisibleTo(self.parentWidget())
            and not open_
        ):
            self.setMaximumHeight(NO_MAX)
            self.setVisible(open_)
            return
        if open_:
            self.setMaximumHeight(0)
            self.setVisible(True)
            target = self._target_height()
            self._run(
                0, target, theme.spring_soft(), min(420, 240 + target // 5), lambda: self.setMaximumHeight(NO_MAX)
            )
        else:
            start = self.height()
            self.setMaximumHeight(start)
            self._run(
                start,
                0,
                theme.ease_exit(),
                min(260, 160 + start // 10),
                lambda: (self.setVisible(False), self.setMaximumHeight(NO_MAX)),
            )

    def _run(self, a: int, b: int, curve, ms: int, done: Callable[[], None]) -> None:
        def frame(v: float) -> None:
            self.setMaximumHeight(int(round(v)))

        def finished() -> None:
            self._tween = None
            done()

        tw = Tween(frame, finished)
        self._tween = tw
        tw.start(a, b, ms, curve)

    def toggle(self) -> None:
        self.set_open(not self._open)
