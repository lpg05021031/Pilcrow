"""Small shared widgets (a port of web/src/components/ui/bits.tsx).

Everything here is painted from the theme tokens, or tinted from them (``theme.tint``), and follows a
theme change without touching a stylesheet.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Optional

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from ... import icons, theme
from ...canvas.pixels import hline, pen_width, snap, vline
from .button import Btn
from .signals import bind


class Hairline(QWidget):
    """A one-pixel line in the hair colour over a surface (``inset 0 -1px 0 var(--hair)``); ``over`` is
    the surface it lies on (a token name, by default the canvas; or any :data:`theme.Fill`)."""

    def __init__(self, vertical: bool = False, over: theme.Fill = "canvas", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._v = vertical
        self._over = over
        if vertical:
            self.setFixedWidth(1)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        else:
            self.setFixedHeight(1)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        c = theme.over(theme.qcolor(t.hair), theme.resolve(self._over, t))
        if self._v:
            vline(p, 0, 0, self.height(), c)
        else:
            hline(p, 0, 0, self.width(), c)


def hairline(vertical: bool = False, over: theme.Fill = "canvas") -> Hairline:
    return Hairline(vertical, over)


def tone(lb: QWidget, name: str) -> QWidget:
    """Colour a label's text by tone name (muted, faint, accent, err, ok) or token name; '' is the text colour."""
    theme.tint(lb, fg=theme.TONES.get(name, name) if name else "text")
    return lb


def label(text: str = "", tone: str = "", size: str = "", mono: bool = False, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if tone:
        theme.tint(lb, fg=theme.TONES.get(tone, tone))
    if size:
        lb.setProperty("fs", size)
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
) -> Btn:
    """``.btn``: painted (``ui/button.py``). ``kind``: '' | primary | ghost | outline | danger |
    danger-text | link; ``size``: '' | sm | xs | lg."""
    return Btn(
        text,
        icon=icon,
        kind=kind,
        size=size,
        icon_size=14 if size in ("", "lg") else 13,
        tooltip=tooltip,
        on_click=on_click,
    )


class IconButton(QPushButton):
    """A quiet square button with only an icon (``.icon-btn``): a fill under the pointer, and kept
    while ``active``. The icon is painted in text-2 (or ``color``), text under the pointer."""

    def __init__(
        self, name: str, tooltip: str = "", size: int = 15, color: str = "", parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._name, self._size, self._color = name, size, color
        self._active = False
        self._hover = False
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(size + 12, size + 12)
        self.setIconSize(QSize(size, size))
        if tooltip:
            self.setToolTip(tooltip)
            self.setAccessibleName(tooltip)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_icon_name(self, name: str) -> None:
        self._name = name
        self.update()

    def set_active(self, on: bool) -> None:
        if on != self._active:
            self._active = on
            self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        lit = (self._hover and self.isEnabled()) or self._active
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if lit:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.blend(t.fill_2, t.canvas))
            p.drawRoundedRect(QRectF(self.rect()), theme.R2, theme.R2)
        if not self.isEnabled():
            p.setOpacity(0.45)
        color = t.text if lit else (self._color or t.text_2)
        px = self.iconSize().width()
        pm = icons.pixmap(self._name, color, px)
        p.drawPixmap((self.width() - px) // 2, (self.height() - px) // 2, pm)


class Check(QCheckBox):
    """A check box painted from the tokens: a 15px inset square, the accent when checked."""

    BOX, GAP = 15, 8

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = self.fontMetrics()
        return QSize(self.BOX + self.GAP + fm.horizontalAdvance(self.text()) + 2, max(self.BOX + 4, fm.height() + 4))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(0.5)
        y = (self.height() - self.BOX) / 2
        box = QRectF(0.5, y + 0.5, self.BOX - 1, self.BOX - 1)
        on = self.isChecked()
        p.setPen(QPen(QColor(t.accent if on else t.text_4), 1))
        p.setBrush(QColor(t.accent) if on else QColor(t.inset))
        p.drawRoundedRect(box, theme.R1, theme.R1)
        if on:
            p.drawPixmap(int(box.x() + 2), int(box.y() + 2), icons.pixmap("check", t.accent_ink, 11))
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        p.drawText(
            QRectF(self.BOX + self.GAP, 0, self.width() - self.BOX - self.GAP, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )


def paint_box(
    p: QPainter,
    r: QRectF,
    bg: Optional[QColor],
    line: Optional[QColor] = None,
    radius: float = 0.0,
    line_w: float = 1.0,
) -> None:
    """A rounded box: a fill and, inside its edge, a hairline. Either may be None. The edges sit on
    the device grid and a one-pixel line is one device pixel at a fractional ratio."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = snap(p, r)
    if line_w <= 1.0:
        line_w = pen_width(p)
    if bg is not None and bg.alpha() > 0:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, radius, radius)
    if line is not None and line.alpha() > 0:
        p.setPen(QPen(line, line_w))
        p.setBrush(Qt.BrushStyle.NoBrush)
        h = line_w / 2
        p.drawRoundedRect(r.adjusted(h, h, -h, -h), max(0.0, radius - h), max(0.0, radius - h))


class Box(QFrame):
    """A rounded box painted from the tokens (the card, chip, well and notice materials): a fill, a
    hairline, and another fill under the pointer when ``hover`` is given. Children lay out inside it."""

    def __init__(
        self,
        bg: theme.Fill = None,
        line: theme.Fill = None,
        radius: float = 0.0,
        hover: theme.Fill = None,
        line_w: float = 1.0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.bg, self.line, self.radius, self.hover, self.line_w = bg, line, radius, hover, line_w
        self._hover = False
        if hover is not None:
            self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_look(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        bg = theme.resolve(self.hover if (self._hover and self.hover is not None) else self.bg, t)
        p = QPainter(self)
        paint_box(p, QRectF(self.rect()), bg, theme.resolve(self.line, t), self.radius, self.line_w)


class Pill(QLabel):
    """A label on a painted pill or chip: a fill and a hairline under the text, rounded. Padding is the
    label's contents margins; the text colour is a tint."""

    def __init__(
        self,
        text: str = "",
        bg: theme.Fill = None,
        line: theme.Fill = None,
        radius: float = 0.0,
        line_w: float = 1.0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(text, parent)
        self.bg, self.line, self.radius, self.line_w = bg, line, radius, line_w
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_look(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)
        self.update()

    def paintEvent(self, e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        paint_box(
            p, QRectF(self.rect()), theme.resolve(self.bg, t), theme.resolve(self.line, t), self.radius, self.line_w
        )
        p.end()
        super().paintEvent(e)


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


class LayoutButton(QPushButton):
    """A QPushButton built from child widgets in a layout: its size comes from the layout, not from its
    own text. It paints nothing of its own unless given a look (``set_look``): a fill, another under the
    pointer (``hover``) or while ``look_active`` (``on``), a hairline and rounded corners, all from the
    tokens, so the stylesheet is not involved."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.look_bg: theme.Fill = None
        self.look_hover: theme.Fill = None
        self.look_on: theme.Fill = None
        self.look_line: theme.Fill = None
        self.look_radius = 0.0
        self.look_active = False
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_look(
        self,
        bg: theme.Fill = None,
        hover: theme.Fill = None,
        on: theme.Fill = None,
        line: theme.Fill = None,
        radius: float = 0.0,
    ) -> None:
        self.look_bg, self.look_hover, self.look_on, self.look_line, self.look_radius = bg, hover, on, line, radius
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        if self.look_active and self.look_on is not None:
            bg = self.look_on
        elif self.underMouse() and self.isEnabled() and self.look_hover is not None:
            bg = self.look_hover
        else:
            bg = self.look_bg
        if bg is None and self.look_line is None:
            return
        p = QPainter(self)
        paint_box(p, QRectF(self.rect()), theme.resolve(bg, t), theme.resolve(self.look_line, t), self.look_radius)

    def sizeHint(self):  # noqa: N802
        lay = self.layout()
        return lay.sizeHint() if lay is not None else super().sizeHint()

    def minimumSizeHint(self):  # noqa: N802
        lay = self.layout()
        return lay.minimumSize() if lay is not None else super().minimumSizeHint()
