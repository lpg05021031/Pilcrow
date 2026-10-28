"""Small shared widgets (a port of web/src/components/ui/bits.tsx).

Everything here is painted from the theme tokens and repaints when the theme changes.
"""

from __future__ import annotations

import math
import time
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from ... import icons, theme
from .signals import bind


def hairline(vertical: bool = False) -> QFrame:
    f = QFrame()
    f.setProperty("hairline-v" if vertical else "hairline", "true")
    f.setFrameShape(QFrame.Shape.NoFrame)
    return f


def label(text: str = "", tone: str = "", size: str = "", mono: bool = False, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if tone:
        lb.setProperty("tone", tone)
    if size:
        lb.setProperty("size", size)
    if mono:
        lb.setProperty("mono", "true")
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse if wrap else Qt.TextInteractionFlag.NoTextInteraction
    )
    return lb


def button(
    text: str = "",
    kind: str = "",
    size: str = "",
    icon: str = "",
    on_click: Optional[Callable[[], None]] = None,
    tooltip: str = "",
) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if size:
        b.setProperty("size", size)
    if icon:
        t = theme.tokens()
        b.setIcon(icons.icon(icon, t.accent_ink if kind == "primary" else t.text_2, 14, hover=t.text))
        b.setIconSize(QSize(14, 14))
    if tooltip:
        b.setToolTip(tooltip)
        b.setAccessibleName(tooltip)
    if on_click:
        b.clicked.connect(on_click)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class IconButton(QPushButton):
    """A quiet square button with only an icon (``.icon-btn``). ``active`` tints it as pressed-in."""

    def __init__(
        self, name: str, tooltip: str = "", size: int = 15, color: str = "", parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._name, self._size, self._color = name, size, color
        self.setProperty("icon-only", "true")
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(size + 12, size + 12)
        self.setIconSize(QSize(size, size))
        if tooltip:
            self.setToolTip(tooltip)
            self.setAccessibleName(tooltip)
        self._paint_icon()
        bind(theme.theme.changed, self, lambda _t: self._paint_icon())

    def set_icon_name(self, name: str) -> None:
        self._name = name
        self._paint_icon()

    def set_active(self, on: bool) -> None:
        self.setProperty("active", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def _paint_icon(self) -> None:
        t = theme.tokens()
        self.setIcon(icons.icon(self._name, self._color or t.text_2, self._size, hover=t.text))


class Mark(QWidget):
    """The pilcrow mark ¶: an orange bowl with two stems (assets/icon.svg, cropped to the ink)."""

    def __init__(self, size: int = 17, color: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._size = size
        self._color = color
        self.setFixedSize(size, size)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _draw_mark(p, QRectF(0, 0, self._size, self._size), theme.tokens(), ink=self._color or None)


def _draw_mark(p: QPainter, r: QRectF, t: theme.Tokens, bowl_alpha: float = 1.0, ink: Optional[str] = None) -> None:
    """Geometry from the brand SVG (viewBox 4.5 4 14 16): bowl at (10.6, 9.2) r 4.1, bar and two stems."""
    s = r.height() / 16.0
    x0, y0 = r.x() - 4.5 * s, r.y() - 4 * s
    bowl = QColor(t.accent)
    bowl.setAlphaF(bowl_alpha)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bowl)
    p.drawEllipse(QPointF(x0 + 10.6 * s, y0 + 9.2 * s), 4.1 * s, 4.1 * s)
    p.setBrush(QColor(ink or t.text_2))
    rx = 0.5 * s
    p.drawRoundedRect(QRectF(x0 + 10.6 * s, y0 + 5.1 * s, 6.6 * s, 1.9 * s), rx, rx)
    p.drawRoundedRect(QRectF(x0 + 12.4 * s, y0 + 5.1 * s, 1.9 * s, 13.8 * s), rx, rx)
    p.drawRoundedRect(QRectF(x0 + 15.3 * s, y0 + 5.1 * s, 1.9 * s, 13.8 * s), rx, rx)


class WorkingMark(QWidget):
    """The breathing ¶ that marks work under way: the bowl brightens and dims on a slow sine."""

    def __init__(self, size: int = 15, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self.update)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def showEvent(self, e) -> None:  # noqa: N802
        from ...store import store

        if not store.reduced_motion:
            self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        phase = (time.monotonic() - self._t0) % 1.8 / 1.8
        alpha = 0.55 + 0.45 * (0.5 - 0.5 * math.cos(phase * 2 * math.pi))
        _draw_mark(p, QRectF(0, 0, self._size, self._size), theme.tokens(), bowl_alpha=alpha)


class StatusGlyph(QWidget):
    """The small status dot of a session row: breathing orange while working, amber when it needs you,
    red on error, a dot when unread."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._status = ""
        self._pending = 0
        self._unread = False
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self.update)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_state(self, status: str, pending: int = 0, unread: bool = False) -> None:
        self._status, self._pending, self._unread = status, pending, unread
        from ...store import store

        live = status in ("running", "starting") and not store.reduced_motion
        if live and not self._timer.isActive():
            self._timer.start()
        elif not live:
            self._timer.stop()
        self.setVisible(bool(status in ("running", "starting", "requires_action", "error") or pending or unread))
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        c: Optional[QColor] = None
        r = 3.0
        if self._status in ("requires_action",) or self._pending:
            c = QColor(t.warn)
        elif self._status == "error":
            c = QColor(t.err)
        elif self._status in ("running", "starting"):
            c = QColor(t.accent)
            phase = (time.monotonic() - self._t0) % 1.6 / 1.6
            c.setAlphaF(0.5 + 0.5 * (0.5 - 0.5 * math.cos(phase * 2 * math.pi)))
        elif self._unread:
            c = QColor(t.accent)
            r = 2.5
        if c is None:
            return
        p.setBrush(c)
        p.drawEllipse(QPointF(8, 8), r, r)


class Spinner(QWidget):
    """A thin arc that turns: for places still loading."""

    def __init__(self, size: int = 14, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self.update)
        self._timer.start()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(t.text_3), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        ang = int(((time.monotonic() - self._t0) % 1.0) * 360)
        p.drawArc(QRectF(1.5, 1.5, self._size - 3, self._size - 3), -ang * 16, 270 * 16)


class ContextRing(QWidget):
    """How full the context window is, as a ring; turns to the accent past 85%."""

    def __init__(self, size: int = 16, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.used = 0
        self.total = 0

    def set_values(self, used: int, total: int) -> None:
        self.used, self.total = used, total
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(1.5, 1.5, self._size - 3, self._size - 3)
        p.setPen(QPen(theme.blend(t.fill_2, t.canvas), 2))
        p.drawEllipse(rect)
        if self.total > 0:
            pct = min(1.0, self.used / self.total)
            p.setPen(QPen(QColor(t.accent if pct > 0.85 else t.text_2), 2))
            p.drawArc(rect, 90 * 16, -int(pct * 360 * 16))


def context_label(used: int, total: int) -> str:
    from ...lib.fmt import tokens as fmt_tokens

    return f"{fmt_tokens(used)} of {fmt_tokens(total)}"


class Row(QWidget):
    """A horizontal box with no margins: the layout helper most composite widgets start from."""

    def __init__(self, spacing: int = 8, margins: tuple = (0, 0, 0, 0), parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.box = QHBoxLayout(self)
        self.box.setContentsMargins(*margins)
        self.box.setSpacing(spacing)

    def add(self, w: QWidget, stretch: int = 0) -> QWidget:
        self.box.addWidget(w, stretch)
        return w

    def stretch(self, n: int = 1) -> None:
        self.box.addStretch(n)


class Clickable(QObject):
    """Makes any widget clickable: ``Clickable(widget).clicked.connect(fn)``."""

    clicked = Signal()

    def __init__(self, w: QWidget) -> None:
        super().__init__(w)
        self._w = w
        w.installEventFilter(self)
        w.setCursor(Qt.CursorShape.PointingHandCursor)

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        if obj is self._w and e.type() == QEvent.Type.MouseButtonRelease and e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            return True
        return False


def copy_text(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


def expanding(w: QWidget, h: bool = True, v: bool = False) -> QWidget:
    w.setSizePolicy(
        QSizePolicy.Policy.Expanding if h else QSizePolicy.Policy.Preferred,
        QSizePolicy.Policy.Expanding if v else QSizePolicy.Policy.Preferred,
    )
    return w


def rounded_path(r: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    return path
