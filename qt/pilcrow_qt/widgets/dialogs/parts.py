"""Small parts the pages and the dialogs share: the card surface, count badges, key caps, the switch and
labels that elide. Everything paints from the theme tokens and follows a theme change."""

from __future__ import annotations

import math
from typing import List, Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QFontMetricsF, QPainter, QPalette
from PySide6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from ... import theme
from ...canvas.pixels import hline
from ..ui.bits import Box, LayoutButton, Pill, paint_box  # noqa: F401 - LayoutButton is re-exported
from ..ui.signals import bind


def mix(a: str, b: str, weight: float) -> QColor:
    """``color-mix(in srgb, a weight%, b)`` for two tokens (translucent ones are first laid on the canvas)."""
    t = theme.tokens()
    ca, cb = theme.blend(a, t.canvas), theme.blend(b, t.canvas)
    return QColor(
        int(round(ca.red() * weight + cb.red() * (1 - weight))),
        int(round(ca.green() * weight + cb.green() * (1 - weight))),
        int(round(ca.blue() * weight + cb.blue() * (1 - weight))),
    )


class Card(QFrame):
    """The card surface (``--card`` with ``--card-shadow``): raised, a hairline all round, rounded corners.
    ``set_focused`` draws the keyboard ring; ``set_tone("needs")`` tints it with the accent wash."""

    def __init__(self, radius: int = 14, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._radius = radius
        self._focused = False
        self._tone = ""
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_focused(self, on: bool) -> None:
        if on != self._focused:
            self._focused = on
            self._paint()

    def set_tone(self, tone: str) -> None:
        if tone != self._tone:
            self._tone = tone
            self._paint()

    def _paint(self) -> None:
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        bg = mix(t.accent_wash, t.raised, 0.3) if self._tone == "needs" else QColor(t.raised)
        if self._focused:
            line = QColor(t.text_3)
        else:
            line = theme.blend(t.hair_3 if self.underMouse() else t.hair_2, t.canvas)
        p = QPainter(self)
        paint_box(p, QRectF(self.rect()), bg, line, self._radius)


class Well(QFrame):
    """An inset box inside a card (``.ovc-well``, ``.confirm-list``): the darker surface with a half hairline."""

    def __init__(self, radius: int = 8, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._radius = radius
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        paint_box(p, QRectF(self.rect()), QColor(t.inset), theme.blend(t.hair, t.inset), self._radius)


class Badge(Pill):
    """A count in a pill (``.ov-count``)."""

    def __init__(self, n: int = 0, parent: Optional[QWidget] = None) -> None:
        super().__init__(bg="fill_2/canvas", radius=10, parent=parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(20)
        self.setMinimumWidth(20)
        self.setContentsMargins(6, 0, 6, 0)
        self.setFont(theme.font_ui(11, 600))
        theme.tint(self, fg="text_2")
        self.set_count(n)

    def set_count(self, n: int) -> None:
        self.setText(str(n))


class Kbd(QLabel):
    """One key cap (``kbd``). ``quiet`` is the palette's outlined look (``.pal-keys kbd``); ``bare`` the
    menu's (``.menu-item kbd``: no background, text-4)."""

    def __init__(self, text: str, quiet: bool = False, bare: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._quiet = quiet
        self._bare = bare
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(18)
        self.setMinimumWidth(18)
        self.setContentsMargins(5, 0, 5, 0)
        self.setFont(theme.font_ui(10.5 if quiet else 11, 500))
        theme.tint(self, fg="text_4" if bare else "text_3")
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, e) -> None:  # noqa: N802
        if not self._bare:
            t = theme.tokens()
            p = QPainter(self)
            r = QRectF(self.rect())
            if self._quiet:
                paint_box(p, r, None, theme.blend(t.hair_2, t.canvas), theme.R1)
            else:
                fill = theme.blend(t.fill_2, t.canvas)
                paint_box(p, r, fill, None, theme.R1)
                # the web's inset 0 -1px hair under the cap: a one-pixel darker bottom edge
                hline(p, theme.R1, r.height() - 1, r.width() - 2 * theme.R1, theme.blend(t.hair, fill.name()))
            p.end()
        super().paintEvent(e)


class Keys(QWidget):
    """A key combination as caps: ``Keys("Ctrl+Shift+N")``. ``quiet`` is the palette's outlined look,
    ``bare`` the menu's."""

    def __init__(self, combo: str, quiet: bool = False, bare: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        parts = (
            ["+"]
            if combo == "+"
            else [p for p in combo.split("+")]
            if not combo.endswith("+")
            else combo[:-1].split("+") + ["+"]
        )
        for p in parts:
            if p:
                lay.addWidget(Kbd(p, quiet, bare))


class Switch(QAbstractButton):
    """The iOS-style switch (``.switch``): 34×20, a lit accent track when on, ``#3a3a3e`` when off on dark
    (fill-3 on light); the knob stays white either way, with the web's small drop shadow."""

    def __init__(self, on: bool = False, label: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(on)
        self.setFixedSize(34, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        if label:
            self.setAccessibleName(label)
            self.setToolTip(label)
        self.toggled.connect(lambda _c: self.update())
        bind(theme.theme.changed, self, lambda _t: self.update())

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(34, 20)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        on = self.isChecked()
        track = QColor(t.accent) if on else switch_off(t)
        if not self.isEnabled():
            track.setAlphaF(0.5)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 34, 20), 10, 10)
        x = 16 if on else 2
        shadow = QColor(0, 0, 0, 77)  # 0 1px 3px rgba(0,0,0,.3), as two soft rings
        p.setBrush(shadow)
        p.drawEllipse(QRectF(x - 0.5, 2.5, 17, 17))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, 2, 16, 16))


def switch_off(t: theme.Tokens) -> QColor:
    """The off track: ``[data-theme='dark'] .switch:not(.on)`` is ``#3a3a3e``; light uses fill-3 over the page."""
    return QColor("#3a3a3e") if t.dark else theme.blend(t.fill_3, t.canvas)


# ---------------------------------------------------------------- the floating materials
# What sheets, popovers and toasts are made of: the web lays a translucent glass over a blurred page
# (motion.css). On a CPU-only machine the blur is skipped and the glass is laid over the dimmed page,
# which is what the eye sees under a sheet anyway.


def sheet_bg(t: theme.Tokens) -> QColor:
    """``.dialog`` on the scrim: glass-2 over the scrimmed canvas, at the web's own opacity (0.86 dark,
    0.88 light; tokens.css) rather than the near-opaque one the client's menus use."""
    g = theme.qcolor(t.glass_2)
    g.setAlphaF(0.86 if t.dark else 0.88)
    return theme.blend(
        f"rgba({g.red()}, {g.green()}, {g.blue()}, {g.alphaF():.2f})", theme.blend(t.scrim, t.canvas).name()
    )


def sheet_line(t: theme.Tokens) -> QColor:
    """The sheet's edge: the hair over its material."""
    return theme.over(theme.qcolor(t.hair), sheet_bg(t))


def well_line(t: theme.Tokens) -> QColor:
    """A well's edge: the hair over its surface."""
    return theme.over(theme.qcolor(t.hair), well_bg(t))


def popover_bg(t: theme.Tokens) -> QColor:
    """``.popover``: glass over the blurred page (what the eye sees is close to glass over raised)."""
    return theme.blend(t.glass, t.raised)


def toast_bg(t: theme.Tokens) -> QColor:
    """``.toast``: glass over the page."""
    return theme.blend(t.glass, t.canvas)


def well_bg(t: theme.Tokens, surface: Optional[str] = None) -> QColor:
    """``.radio-list``, ``.browser``, ``.confirm-list`` inside a dark sheet read through the material
    (``rgba(0, 0, 0, 0.22)``); elsewhere they are the inset surface."""
    if t.dark:
        return theme.blend("rgba(0, 0, 0, 0.22)", surface or sheet_bg(t).name())
    return theme.qcolor(t.inset)


class ElideLabel(QLabel):
    """A single-line label that elides at the end instead of widening its row (``.ellipsis``)."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(24)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(24, super().minimumSizeHint().height())

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        fm = QFontMetrics(self.font())
        r = self.contentsRect()
        p.drawText(
            r,
            int(self.alignment()) | Qt.TextFlag.TextSingleLine,
            fm.elidedText(self.text(), Qt.TextElideMode.ElideRight, r.width()),
        )


def page_column(width: int, padding: int = 0) -> int:
    """``--col`` as the web resolves it inside a pane: clamp(640px, min(61.8%, 100% - 48px), 1080px) of
    the pane less ``padding`` on each side (the welcome pads 24px; the control room and inbox none)."""
    inner = width - 2 * padding
    return max(640, min(int(round(inner * 0.618)), inner - 48, 1080))


def half_hairline(surface: str) -> QWidget:
    """A 0.5px CSS hairline (``.ovr + .ovr``, ``.cust-row``): one pixel of the hair at half strength."""

    def colour(t: theme.Tokens) -> QColor:
        c = theme.qcolor(t.hair)
        c.setAlphaF(c.alphaF() / 2)
        return theme.over(c, theme.qcolor(getattr(t, surface, surface)))

    f = Box(bg=colour)
    f.setFixedHeight(1)
    return f


def hint(text: str, wrap: bool = True) -> QLabel:
    """A quiet explanatory line (``.hint``)."""
    lb = QLabel(text)
    lb.setWordWrap(wrap)
    theme.tint(lb, fg="text_3")
    lb.setProperty("fs", "small")
    return lb


def field_label(text: str) -> QLabel:
    """The label above a form field (``.field > label``)."""
    lb = QLabel(text)
    lb.setFont(theme.font_ui(theme.FS_SMALL, 550))
    theme.tint(lb, fg="text_2")
    return lb


def styled(lb: QLabel, color: str = "", size: float = 0, weight: int = 0, mono: bool = False) -> QLabel:
    """Give a label an explicit colour (a token name or a literal), size and weight (for the handful of
    one-off styles)."""
    if color:
        theme.tint(lb, fg=color)
    f = theme.font_mono() if mono else lb.font()
    if size:
        f.setPixelSize(max(1, int(size + 0.5)))
    if weight:
        f.setWeight(theme._weight(weight))
    if mono or size or weight:
        lb.setFont(f)
    return lb


# ---------------------------------------------------------------- the scrim
# The web dims the page under a sheet and blurs it (``.scrim``: the scrim colour over blur(10px)). On a
# machine without a GPU a real blur of the whole window is too slow for an opening dialog, so the page
# is grabbed once, scaled down and back up with smooth filtering (a cheap wide blur) and dimmed. If
# even that takes over ~40 ms the first time, later sheets fall back to the flat scrim.

_BLUR_OK: Optional[bool] = None
BLUR_BUDGET_MS = 40.0


class Backdrop:
    """What lies under a sheet: a blurred, dimmed snapshot of ``host`` taken before the sheet shows."""

    def __init__(self, host: Optional[QWidget]) -> None:
        global _BLUR_OK
        self.pixmap = None
        self.ms = 0.0
        if host is None or _BLUR_OK is False or host.width() < 2 or host.height() < 2:
            return
        import time

        from PySide6.QtCore import Qt as _Qt

        t0 = time.perf_counter()
        try:
            pm = host.grab()
            small = pm.scaled(
                max(1, pm.width() // 8),
                max(1, pm.height() // 8),
                _Qt.AspectRatioMode.IgnoreAspectRatio,
                _Qt.TransformationMode.SmoothTransformation,
            )
            self.pixmap = small.scaled(
                pm.size(), _Qt.AspectRatioMode.IgnoreAspectRatio, _Qt.TransformationMode.SmoothTransformation
            )
        except Exception:  # noqa: BLE001 — no snapshot, no blur
            self.pixmap = None
        self.ms = (time.perf_counter() - t0) * 1000
        if _BLUR_OK is None:
            _BLUR_OK = self.ms <= BLUR_BUDGET_MS
        if not _BLUR_OK:
            self.pixmap = None

    def paint(self, p: QPainter, w: QWidget) -> None:
        t = theme.tokens()
        if self.pixmap is not None and not self.pixmap.isNull():
            p.drawPixmap(0, 0, self.pixmap)
        p.fillRect(w.rect(), theme.qcolor(t.scrim))


def blur_enabled() -> Optional[bool]:
    """Whether the blurred backdrop is on (None until the first sheet measured it)."""
    return _BLUR_OK


# ---------------------------------------------------------------- text laid out like the browser
# A QLabel sets its own line height from the font (about 1.2×); the web lays text on 1.45× lines
# (``.hint`` 1.5×, prose 1.55×), which is where most of the vertical rhythm of a page comes from.
# :class:`Text` draws its lines itself at the CSS line height, centring the glyph box in each line
# the way a browser does, and reports its height for a width so layouts give it exactly that room.


def web_font(f: QFont) -> QFont:
    """A font with the browser's glyph advances: hinting only vertically, so widths are fractional
    (full hinting widens Inter by about 4%, and lines wrap earlier than on the web)."""
    f = QFont(f)
    f.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)
    return f


class Text(QWidget):
    """Text on CSS line boxes. ``size``/``weight`` are the font, ``color`` a token name (``"text_2"``)
    or a colour string, ``lh`` the line-height factor, ``wrap`` whether lines break at the width."""

    def __init__(
        self,
        text: str = "",
        size: float = theme.FS_UI,
        weight: int = 400,
        color: str = "text",
        lh: float = 1.45,
        wrap: bool = True,
        mono: bool = False,
        align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
        letter_spacing: float = 0.0,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._size, self._weight, self._mono = size, weight, mono
        self._letter_spacing = letter_spacing  # em, like CSS letter-spacing
        self._color = color
        self._lh = lh
        self._wrap = wrap
        self._align = align
        self._lines: List[str] = []
        self._laid_w = -1
        # Vertically Preferred, not Fixed: a box layout caps a Fixed item at its one-line size hint
        # before it asks for the height-for-width, and the extra lines would be cut off.
        sp = QSizePolicy(
            QSizePolicy.Policy.Expanding if wrap else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Preferred if wrap else QSizePolicy.Policy.Fixed,
        )
        sp.setHeightForWidth(wrap)
        self.setSizePolicy(sp)
        self._max_w = 0
        bind(theme.theme.changed, self, lambda _t: self.update())

    # ---- the QLabel-shaped bits callers use
    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802
        if text != self._text:
            self._text = text
            self._laid_w = -1
            self.updateGeometry()
            self.update()

    def setAlignment(self, a: Qt.AlignmentFlag) -> None:  # noqa: N802
        self._align = a
        self.update()

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def set_max_width(self, w: int) -> None:
        """``max-width`` on a block of text: lines wrap at ``w`` even when the column is wider."""
        self._max_w = w
        self._laid_w = -1
        self.updateGeometry()
        self.update()

    def set_font(self, size: Optional[float] = None, weight: Optional[int] = None) -> None:
        if size is not None:
            self._size = size
        if weight is not None:
            self._weight = weight
        self._laid_w = -1
        self.updateGeometry()
        self.update()

    # ---- metrics
    def qfont(self) -> QFont:
        f = web_font(
            theme.font_mono(self._size, self._weight) if self._mono else theme.font_ui(self._size, self._weight)
        )
        if self._letter_spacing:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, self._letter_spacing * self._size)
        return f

    def line_px(self) -> float:
        return self._size * self._lh

    def _layout(self, width: int) -> List[str]:
        if not self._wrap:
            return [self._text] if self._text else []
        if self._max_w:
            width = min(width, self._max_w)
        if width == self._laid_w:
            return self._lines
        from PySide6.QtGui import QTextLayout, QTextOption

        lay = QTextLayout(self._text, self.qfont())
        opt = QTextOption()
        opt.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        lay.setTextOption(opt)
        lay.beginLayout()
        lines: List[str] = []
        while True:
            line = lay.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1, width))
            lines.append(self._text[line.textStart() : line.textStart() + line.textLength()].rstrip("\n"))
        lay.endLayout()
        self._lines, self._laid_w = lines, width
        return lines

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return self._wrap

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        n = max(1, len(self._layout(w))) if self._text else 0
        return int(round(n * self.line_px()))

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetricsF(self.qfont())
        w = int(math.ceil(fm.horizontalAdvance(self._text))) + 1
        return QSize(w, int(round(self.line_px())) if self._text or not self._wrap else 0)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        if self._wrap:
            return QSize(24, int(round(self.line_px())) if self._text else 0)
        return self.sizeHint()

    def resizeEvent(self, e) -> None:  # noqa: N802
        if self._wrap:
            self._layout(self.width())
            h = self.heightForWidth(self.width())
            if h != self.height():
                self.updateGeometry()
        super().resizeEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        if not self._text:
            return
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        font = self.qfont()
        p.setFont(font)
        c = getattr(t, self._color, None) if not self._color.startswith(("#", "rgb")) else self._color
        p.setPen(QColor(c or t.text))
        fm = QFontMetrics(font)
        lh = self.line_px()
        box = fm.ascent() + fm.descent()
        lines = self._layout(self.width())
        w = self.width()
        for i, line in enumerate(lines):
            if not self._wrap:
                line = fm.elidedText(line, Qt.TextElideMode.ElideRight, w)
            tw = fm.horizontalAdvance(line)
            x = 0.0
            if self._align & Qt.AlignmentFlag.AlignHCenter:
                x = max(0.0, (w - tw) / 2)
            elif self._align & Qt.AlignmentFlag.AlignRight:
                x = max(0.0, w - tw)
            y = i * lh + (lh - box) / 2 + fm.ascent()
            p.drawText(QPointF(x, y), line)
