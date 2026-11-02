"""Small shared widgets (a port of web/src/components/ui/bits.tsx).

Everything here is painted from the theme tokens, or tinted from them (``theme.tint``), and follows a
theme change without touching a stylesheet.
"""

from __future__ import annotations

import math
import time
from functools import lru_cache
from typing import Any, Callable, List, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from ... import icons, theme
from ...canvas.clock import clock
from ...canvas.pixels import hline, pen_width, snap, vline
from ...lib.fmt import tokens as fmt_tokens
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

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        c = theme.over(theme.qcolor(t.hair), theme.resolve(self._over, t))
        if self._v:
            vline(p, 0, 0, self.height(), c)
        else:
            hline(p, 0, 0, self.width(), c)


def hairline(vertical: bool = False, over: theme.Fill = "canvas") -> Hairline:
    return Hairline(vertical, over)


class IconLabel(QWidget):
    """A tinted Lucide icon that repaints from the tokens when the theme changes."""

    def __init__(self, name: str, color: str = "text_2", size: int = 14, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._name, self._color, self._size = name, color, size
        self.setFixedSize(size, size)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set(self, name: str, color: Optional[str] = None) -> None:
        self._name = name
        if color:
            self._color = color
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        pm = icons.pixmap(self._name, getattr(theme.tokens(), self._color), self._size)
        p.drawPixmap(0, 0, pm)


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
    """A quiet square button with only an icon (``.icon-btn``, ``.icon-btn.sm``): a fill under the
    pointer, kept while ``active``. The icon (``size`` px) is painted in ``color`` (text-2 unless
    given), text under the pointer. ``box`` is the square (the icon and 6px around unless given),
    ``width`` a wider one; ``over`` the surface the fill lies on. With ``reveal`` it keeps its place
    but shows nothing until ``set_shown(True)`` (the buttons that appear when the row around them is
    hovered). ``last_modifiers`` are the keys held at the last press (Shift+click for options)."""

    def __init__(
        self,
        name: str,
        tooltip: str = "",
        size: int = 15,
        color: str = "",
        parent: Optional[QWidget] = None,
        *,
        box: int = 0,
        width: int = 0,
        over: str = "canvas",
        reveal: bool = False,
    ) -> None:
        super().__init__(parent)
        self._name, self._size, self._color, self._over, self._reveal = name, size, color, over, reveal
        self._shown = not reveal
        self._active = False
        self.last_modifiers = Qt.KeyboardModifier.NoModifier
        self.setFlat(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        box = box or size + 12
        self.setFixedSize(width or box, box)
        self.setIconSize(QSize(size, size))
        if tooltip:
            self.setToolTip(tooltip)
            self.setAccessibleName(tooltip)
        bind(theme.theme.changed, self, lambda _t: self.update())

    @property
    def shown(self) -> bool:
        return self._shown

    def set_shown(self, on: bool) -> None:
        if self._shown != on:
            self._shown = on
            self.update()

    def mousePressEvent(self, e) -> None:
        self.last_modifiers = e.modifiers()
        super().mousePressEvent(e)

    def set_icon_name(self, name: str) -> None:
        self._name = name
        self.update()

    def set_active(self, on: bool) -> None:
        if on != self._active:
            self._active = on
            self.update()

    def paintEvent(self, _e) -> None:
        if not self._shown:
            return
        t = theme.tokens()
        lit = (self.underMouse() and self.isEnabled()) or self._active
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if lit:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.blend(t.fill_2, getattr(t, self._over)))
            r = min(theme.R2, self.width() / 2)
            p.drawRoundedRect(QRectF(self.rect()), r, r)
        if not self.isEnabled():
            p.setOpacity(0.45)
        # ``color`` is a token name ("text_3"), a fill spec or a literal: resolved like every tint
        if lit:
            color = QColor(t.text)
        elif self._color:
            color = theme.resolve(self._color, t)
        else:
            color = QColor(t.text_2)  # an invalid QColor is truthy in PySide: no `or` fallback here
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

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(self.BOX + self.GAP + fm.horizontalAdvance(self.text()) + 2, max(self.BOX + 4, fm.height() + 4))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _e) -> None:
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


def rounded_path(r: QRectF, radius: float) -> QPainterPath:
    """A rounded rectangle as a path, to clip to."""
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    return path


# ---------------------------------------------------------------- box shadows (CSS ``box-shadow``)


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@lru_cache(maxsize=48)
def _shadow_pixmap(w: int, h: int, blur: int, alpha: float, rgb: Tuple[int, int, int] = (0, 0, 0)) -> QPixmap:
    """A CSS box shadow of a ``w``×``h`` box blurred by ``blur`` (σ = blur/2), as a pixmap with ``blur``
    pixels of margin on every side. The Gaussian of a box is separable, so it is one row profile and
    one column profile multiplied: a few hundred ``erf`` calls and one blit per row, not a blur pass.
    The corners come out square rather than rounded; under a 16px radius and an 18px sigma the
    difference is a level or two in the darkest corner."""
    ext = blur
    tw, th = w + 2 * ext, h + 2 * ext
    sigma = max(0.5, blur / 2.0)

    def profile(n: int, x0: float, x1: float) -> List[float]:
        return [_phi((i + 0.5 - x0) / sigma) - _phi((i + 0.5 - x1) / sigma) for i in range(n)]

    fx = profile(tw, ext, ext + w)
    fy = profile(th, ext, ext + h)
    r, g, b = rgb
    row = bytearray()
    for v in fx:
        a = max(0, min(255, int(round(255 * alpha * v))))
        # premultiplied BGRA, little-endian ARGB32
        row += bytes((b * a // 255, g * a // 255, r * a // 255, a))
    row_img = QImage(bytes(row), tw, 1, tw * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
    img = QImage(tw, th, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    for y, v in enumerate(fy):
        if v < 0.002:
            continue
        p.setOpacity(v)
        p.drawImage(0, y, row_img)
    p.end()
    return QPixmap.fromImage(img)


def draw_box_shadow(
    p: QPainter,
    rect: QRectF,
    dy: float,
    blur: int,
    spread: float,
    alpha: float,
    opacity: float = 1.0,
    rgb: Tuple[int, int, int] = (0, 0, 0),
) -> None:
    """``box-shadow: 0 dy blur spread rgba(rgb, alpha)`` under ``rect`` (the element's border box)."""
    if opacity <= 0.003 or alpha <= 0:
        return
    sr = rect.adjusted(-spread, -spread, spread, spread).translated(0, dy)
    w, h = int(round(sr.width())), int(round(sr.height()))
    if w <= 0 or h <= 0:
        return
    pm = _shadow_pixmap(w, h, blur, alpha, rgb)
    p.setOpacity(opacity)
    p.drawPixmap(QPointF(sr.x() - blur, sr.y() - blur), pm)
    p.setOpacity(1.0)


# ---------------------------------------------------------------- <kbd>


def paint_kbd(p: QPainter, x: float, y: float, text: str, small: bool = False) -> float:
    """Draw a ``<kbd>`` chip with its left edge at ``x`` and its top at ``y``; returns its width.
    ``small`` is the composer-foot flavour (16px, outlined); the base one is 18px on a fill."""
    t = theme.tokens()
    h = 16 if small else 18
    font = theme.font_ui(10 if small else 11, 500)
    fm = QFontMetrics(font)
    w = max(h, fm.horizontalAdvance(text) + 10)
    r = QRectF(x, y, w, h)
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    if small:
        p.setPen(QPen(theme.qcolor(t.hair_2), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), theme.R1, theme.R1)
    else:
        p.setBrush(theme.qcolor(t.fill_2))
        p.drawRoundedRect(r, theme.R1, theme.R1)
        p.setClipPath(rounded_path(r, theme.R1))
        hline(p, x, y + h - 1, w, theme.qcolor(t.hair))
        p.setClipping(False)
    p.setFont(font)
    p.setPen(QColor(t.text_3))
    p.drawText(QRect(int(x), int(y), int(w), h), Qt.AlignmentFlag.AlignCenter, text)
    p.restore()
    return w


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
        if hover is not None:
            self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_look(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)
        self.update()

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        bg = theme.resolve(self.hover if (self.underMouse() and self.hover is not None) else self.bg, t)
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

    def paintEvent(self, e) -> None:
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

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        draw_mark(p, QRectF(0, 0, self._size, self._size), theme.tokens(), ink=self._color or None)


def draw_mark(p: QPainter, r: QRectF, t: theme.Tokens, bowl_alpha: float = 1.0, ink: Optional[str] = None) -> None:
    """The pilcrow ¶ drawn into ``r``, for widgets and canvas blocks alike. Geometry from the brand SVG
    (viewBox 4.5 4 14 16): bowl at (10.6, 9.2) r 4.1, a bar and two stems."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
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


class Indicator(QWidget):
    """A small painted widget that moves (a breathing dot, a turning arc). It repaints on the one
    clock (``canvas/clock``), and only while it is live, shown and motion is allowed: it subscribes
    on show and leaves on hide, so a hidden tab or a scrolled-away row costs nothing. ``moving``
    says whether to paint the animated look or the resting one."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._live = False
        bind(theme.theme.changed, self, lambda _t: self.update())

    @property
    def moving(self) -> bool:
        return self._live and not clock.reduced_motion

    def set_live(self, on: bool) -> None:
        if on != self._live:
            self._live = on
            self._arm()
        self.update()

    def _arm(self) -> None:
        if self.moving and self.isVisible():
            clock.subscribe(self)
        else:
            clock.unsubscribe(self)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._arm()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        clock.unsubscribe(self)

    def tick(self, _now: float) -> None:
        self.update()


class WorkingMark(Indicator):
    """The breathing ¶ that marks work under way: the bowl brightens and dims on a slow sine."""

    def __init__(self, size: int = 15, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.set_live(True)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        alpha = theme.breath(time.monotonic(), 1.8, 0.55) if self.moving else 1.0
        draw_mark(p, QRectF(0, 0, self._size, self._size), theme.tokens(), bowl_alpha=alpha)


class StatusGlyph(Indicator):
    """The small status dot of a session row: breathing orange while working, amber when it needs you,
    red on error, a dot when unread."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._status = ""
        self._pending = 0
        self._unread = False

    def set_state(self, status: str, pending: int = 0, unread: bool = False) -> None:
        self._status, self._pending, self._unread = status, pending, unread
        self.setVisible(bool(status in ("running", "starting", "requires_action", "error") or pending or unread))
        self.set_live(status in ("running", "starting"))

    def paintEvent(self, _e) -> None:
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
            if self.moving:
                c.setAlphaF(theme.breath(time.monotonic(), 1.6, 0.5))
        elif self._unread:
            c = QColor(t.accent)
            r = 2.5
        if c is None:
            return
        p.setBrush(c)
        p.drawEllipse(QPointF(8, 8), r, r)


class Spinner(Indicator):
    """A thin arc that turns once a second, in a token's colour: for places still loading."""

    def __init__(self, size: int = 14, tone: str = "text_3", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._size = size
        self._tone = tone
        self.setFixedSize(size, size)
        self.set_live(True)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(getattr(theme.tokens(), self._tone)), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        ang = int((time.monotonic() % 1.0) * 360)
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

    def paintEvent(self, _e) -> None:
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

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if obj is self._w and e.type() == QEvent.Type.MouseButtonRelease and e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            return True
        return False


class _Reveal(QObject):
    """Shows its widgets while the pointer is on the widget it watches (:func:`reveal_on_hover`)."""

    def __init__(self, owner: QWidget, widgets: List[QWidget]) -> None:
        super().__init__(owner)
        self.widgets = widgets
        owner.installEventFilter(self)

    def eventFilter(self, _obj: QObject, e: QEvent) -> bool:
        if e.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            for w in self.widgets:
                _reveal(w, e.type() == QEvent.Type.Enter)
        return False


def _reveal(w: QWidget, on: bool) -> None:
    # an IconButton keeps its place and paints nothing (``opacity: 0``): no layout pass on a hover
    if isinstance(w, IconButton):
        w.set_shown(on)
    else:
        w.setVisible(on)


def reveal_on_hover(owner: QWidget, widgets: List[QWidget]) -> None:
    """Show ``widgets`` only while the pointer is on ``owner`` (``opacity: 0`` until the row or the
    card is hovered): an IconButton keeps its place and paints nothing, anything else (placed by
    hand, outside a layout) is hidden. The list may grow later; it is read at each enter."""
    for w in widgets:
        _reveal(w, False)
    _Reveal(owner, widgets)


def copy_text(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


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

    def paintEvent(self, _e) -> None:
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

    def sizeHint(self):
        lay = self.layout()
        return lay.sizeHint() if lay is not None else super().sizeHint()

    def minimumSizeHint(self):
        lay = self.layout()
        return lay.minimumSize() if lay is not None else super().minimumSizeHint()
