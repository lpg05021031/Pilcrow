"""Small parts the pages and the dialogs share: the card surface, count badges, key caps, the switch and
labels that elide. Everything paints from the theme tokens and follows a theme change."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from ... import theme
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
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def set_focused(self, on: bool) -> None:
        if on != self._focused:
            self._focused = on
            self._paint()

    def set_tone(self, tone: str) -> None:
        if tone != self._tone:
            self._tone = tone
            self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        bg = t.raised
        if self._tone == "needs":
            bg = mix(t.accent_wash, t.raised, 0.3).name()
        elif self._tone == "decided":
            bg = t.raised
        border = t.text_3 if self._focused else theme.blend(t.hair_2, t.canvas).name()
        hover = t.text_3 if self._focused else theme.blend(t.hair_3, t.canvas).name()
        name = self.metaObject().className()
        self.setStyleSheet(
            f"{name} {{ background: {bg}; border: 1px solid {border}; border-radius: {self._radius}px; }} "
            f"{name}:hover {{ border-color: {hover}; }}"
        )


class Well(QFrame):
    """An inset box inside a card (``.ovc-well``, ``.confirm-list``): the darker surface with a half hairline."""

    def __init__(self, radius: int = 8, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._radius = radius
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        name = self.metaObject().className()
        self.setStyleSheet(
            f"{name} {{ background: {t.inset}; border: 1px solid {theme.blend(t.hair, t.inset).name()}; border-radius: {self._radius}px; }}"
        )


class Badge(QLabel):
    """A count in a pill (``.ov-count``)."""

    def __init__(self, n: int = 0, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(20)
        self.setMinimumWidth(20)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()
        self.set_count(n)

    def set_count(self, n: int) -> None:
        self.setText(str(n))

    def _paint(self) -> None:
        t = theme.tokens()
        self.setStyleSheet(
            f"QLabel {{ background: {theme.blend(t.fill_2, t.canvas).name()}; color: {t.text_2}; border-radius: 10px; padding: 0 6px; font-size: 11px; font-weight: 600; }}"
        )


class Kbd(QLabel):
    """One key cap (``kbd``)."""

    def __init__(self, text: str, quiet: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._quiet = quiet
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(18)
        self.setMinimumWidth(18)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        bg = "transparent" if self._quiet else theme.blend(t.fill_2, t.canvas).name()
        border = f"border: 1px solid {theme.blend(t.hair_2, t.canvas).name()};" if self._quiet else ""
        self.setStyleSheet(
            f"QLabel {{ background: {bg}; {border} color: {t.text_3}; border-radius: {theme.R1}px; padding: 0 5px; font-size: 11px; font-weight: 500; }}"
        )


class Keys(QWidget):
    """A key combination as caps: ``Keys("Ctrl+Shift+N")``. ``quiet`` is the palette's outlined look."""

    def __init__(self, combo: str, quiet: bool = False, parent: Optional[QWidget] = None) -> None:
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
                lay.addWidget(Kbd(p, quiet))


class Switch(QAbstractButton):
    """The iOS-style switch (``.switch``): a lit track when on; the knob stays white either way."""

    def __init__(self, on: bool = False, label: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(on)
        self.setFixedSize(34, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
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
        p.setBrush(QColor(t.accent) if self.isChecked() else theme.blend(t.fill_3, t.canvas))
        p.drawRoundedRect(QRectF(0, 0, 34, 20), 10, 10)
        p.setBrush(QColor(t.accent_ink))
        p.drawEllipse(QRectF(16 if self.isChecked() else 2, 2, 16, 16))


class LayoutButton(QPushButton):
    """A QPushButton sized by the widgets laid out inside it (a plain QPushButton ignores its layout, and
    would never shrink below its size hint)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802
        lay = self.layout()
        return lay.sizeHint() if lay is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        lay = self.layout()
        return lay.minimumSize() if lay is not None else super().minimumSizeHint()


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


def hint(text: str, wrap: bool = True) -> QLabel:
    """A quiet explanatory line (``.hint``)."""
    lb = QLabel(text)
    lb.setWordWrap(wrap)
    lb.setProperty("tone", "faint")
    lb.setProperty("size", "small")
    return lb


def field_label(text: str) -> QLabel:
    """The label above a form field (``.field > label``)."""
    lb = QLabel(text)
    t = theme.tokens()
    lb.setStyleSheet(f"color: {t.text_2}; font-size: {theme.FS_SMALL}px; font-weight: 550;")
    return lb


def styled(lb: QLabel, color: str = "", size: float = 0, weight: int = 0, mono: bool = False) -> QLabel:
    """Give a label an explicit colour, size and weight from the tokens (for the handful of one-off styles)."""
    parts: List[str] = []
    if color:
        parts.append(f"color: {color};")
    if size:
        parts.append(f"font-size: {size}px;")
    if weight:
        parts.append(f"font-weight: {weight};")
    if mono:
        lb.setFont(theme.font_mono())
    lb.setStyleSheet(" ".join(parts))
    return lb
