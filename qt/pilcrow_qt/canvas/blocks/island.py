"""A real widget in the scene: the permission prompt, the question form, the plan review, the empty
card. The engine measures it at the column width and places it; it keeps native focus and input."""

from __future__ import annotations

from typing import Any, Callable, Optional

import shiboken6
from PySide6.QtWidgets import QWidget

from ..paint import Measurer, PaintState
from ..scene import Block, Target


class IslandBlock(Block):
    KIND = "island"
    __slots__ = ("make", "widget", "host", "margin", "align", "max_w", "_last_geom", "owned")

    def __init__(
        self,
        key: str,
        make: Callable[[], QWidget],
        margin: int = 0,
        align: str = "fill",
        max_w: int = 0,
        owned: bool = True,
    ) -> None:
        super().__init__(key)
        self.make = make
        self.owned = owned  # an island that does not own its widget only hides it when it goes
        self.widget: Optional[QWidget] = None
        self.host: Optional[QWidget] = None  # the viewport to parent into (set by the view)
        self.margin = margin
        self.align = align  # 'fill' or 'center'
        self.max_w = max_w
        self._last_geom = None

    @property
    def alive(self) -> bool:
        """The widget exists and Qt has not deleted it (a scene outlives the view that hosted it)."""
        return self.widget is not None and shiboken6.isValid(self.widget)

    def ensure_widget(self) -> QWidget:
        if not self.alive:
            self.widget = self.make()
            if self.host is not None and shiboken6.isValid(self.host):
                self.widget.setParent(self.host)
            self.widget.hide()
        return self.widget

    def drop_widget(self) -> None:
        if not self.alive:
            self.widget = None
            self._last_geom = None
            return
        self.widget.hide()
        if self.owned:
            self.widget.setParent(None)
            self.widget.deleteLater()
            self.widget = None
        self._last_geom = None

    def inner_width(self, width: int) -> int:
        w = width - self.indent
        return min(w, self.max_w) if self.max_w else w

    def estimate(self, width: int, m: Measurer) -> int:
        return self.h or 120

    def measure(self, width: int, m: Measurer) -> int:
        w = self.ensure_widget()
        iw = self.inner_width(width)
        w.setFixedWidth(iw)
        lay = w.layout()
        if lay is not None:
            lay.activate()
        h = w.sizeHint().height()
        if h <= 0:
            h = w.height()
        self.warm = True
        return h + 2 * self.margin

    def cool(self) -> None:
        self.warm = False  # the widget stays; it is cheap to keep and holds the person's input

    def place(self, x: int, y: int, width: int, visible: bool) -> None:
        """Called by the view after every commit and scroll: viewport coordinates."""
        w = self.widget
        if w is None or not shiboken6.isValid(w):
            return
        iw = self.inner_width(width)
        if self.align == "center":
            x = x + (width - iw) // 2
        geom = (x, y + self.margin, iw, max(0, self.h - 2 * self.margin))
        if geom != self._last_geom:
            w.setGeometry(*geom)
            self._last_geom = geom
        if visible != w.isVisible():
            w.setVisible(visible)

    def paint(self, p: Any, x: int, y: int, w: int, st: PaintState) -> None:
        pass  # the widget paints itself

    def hit(self, x: int, y: int) -> Optional[Target]:
        return None

    @property
    def static(self) -> bool:
        return False
