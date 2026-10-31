"""The button, painted (the ``.btn`` family of base.css and panels.css).

One class covers the quiet fill, the one primary action per surface, ghost and outline, the two
reds, and the small text-only links. It paints from the tokens on every frame, so a theme change is
a repaint; nothing here goes through the stylesheet.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from ... import icons, theme
from .signals import bind

Look = Tuple[Optional[QColor], QColor, float, Optional[QColor]]  # background, ink, icon opacity, border


class Btn(QPushButton):
    """``kind``: '' (quiet fill) | 'primary' | 'ghost' | 'outline' | 'danger' (filled red) |
    'danger-text' (ghost, red) | 'link' | 'foot' | 'toggle' | 'more'.
    ``size``: '' (30px) | 'sm' (26px) | 'xs' (24px) | 'lg' (36px).

    The icon is named (``icons``) and painted in the text's colour; a QIcon set with ``setIcon`` is
    painted as given. Content is centred when the button is wider than its hint, unless ``pad_left``
    pins it to the left edge."""

    def __init__(
        self,
        text: str = "",
        icon: str = "",
        kind: str = "",
        size: str = "sm",
        icon_size: int = 13,
        tooltip: str = "",
        on_click: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(text, parent)
        self.kind, self.size_, self.icon_name, self.icon_px = kind, size, icon, icon_size
        self.setProperty("kind", kind)  # readable by callers; nothing in the stylesheet keys on it
        self._hover = False
        self._icon_end = False
        self._on = False
        self.pad_left: Optional[int] = None  # overrides the variant's left padding (a negative CSS margin)
        self.override: Dict[str, float] = {}  # per-instance geometry (h, px, gap, fs, fw, r) over the variant's
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFlat(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        if tooltip:
            self.setToolTip(tooltip)
        self.setAccessibleName(tooltip or text)
        if on_click:
            self.clicked.connect(on_click)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        bind(theme.theme.changed, self, lambda _t: self.update())

    # ---- geometry per variant
    def _metrics(self) -> Dict[str, float]:
        m = self._variant()
        m.update(self.override)
        if self.pad_left is not None:
            m["pl"] = self.pad_left
        return m

    def _variant(self) -> Dict[str, float]:
        k, s = self.kind, self.size_
        if k in ("link", "toggle"):
            fs = theme.FS_SMALL if s in ("sm", "xs") else theme.FS_UI
            return {"h": 18 if fs == theme.FS_SMALL else 20, "px": 0, "gap": 4, "fs": fs, "fw": 400, "r": 0}
        if k == "foot":
            return {"h": 22, "px": 7, "pl": 4, "gap": 4, "fs": theme.FS_SMALL, "fw": 400, "r": theme.R1}
        if k == "more":
            return {"h": 24, "px": 8, "gap": 4, "fs": theme.FS_SMALL, "fw": 400, "r": theme.R2}
        if s == "xs":
            return {"h": 24, "px": 9, "gap": 5, "fs": theme.FS_SMALL, "fw": 500, "r": theme.R2}
        if s == "sm":
            return {"h": 26, "px": 10, "gap": 6, "fs": theme.FS_SMALL, "fw": 500, "r": theme.R2}
        if s == "lg":
            return {"h": 36, "px": 16, "gap": 6, "fs": 13.5, "fw": 500, "r": theme.R3}
        return {"h": 30, "px": 12, "gap": 6, "fs": theme.FS_UI, "fw": 500, "r": theme.R2}

    def set_kind(self, kind: str) -> None:
        if kind != self.kind:
            self.kind = kind
            self.setProperty("kind", kind)
            self.updateGeometry()
            self.update()

    def set_icon_name(self, name: str) -> None:
        self.icon_name = name
        self.updateGeometry()
        self.update()

    def set_icon_at_end(self, end: bool) -> None:
        self._icon_end = end
        self.update()

    def set_on(self, on: bool) -> None:
        """Pressed in (``.btn.on``): the fill of a hovered button, kept."""
        self._on = on
        self.update()

    def setIconSize(self, size: QSize) -> None:  # noqa: N802
        super().setIconSize(size)
        self.icon_px = size.width()
        self.updateGeometry()

    def setIcon(self, icon: QIcon) -> None:  # noqa: N802
        super().setIcon(icon)
        self.updateGeometry()
        self.update()

    def _has_icon(self) -> bool:
        return bool(self.icon_name) or not self.icon().isNull()

    def sizeHint(self) -> QSize:  # noqa: N802
        m = self._metrics()
        fm = QFontMetrics(theme.font_ui(m["fs"], int(m["fw"])))
        w = m.get("pl", m["px"]) + m["px"]
        if self._has_icon():
            w += self.icon_px
            if self.text():
                w += m["gap"]
        w += fm.horizontalAdvance(self.text()) if self.text() else 0
        return QSize(int(w), int(m["h"]))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self.updateGeometry()
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def _colors(self) -> Look:
        """(background, ink, icon opacity, border) for the current state."""
        t = theme.tokens()
        k = self.kind
        hover = self._hover and self.isEnabled()
        if k == "primary":
            if not self.isEnabled():
                return theme.blend(t.fill_2, t.canvas), QColor(t.text_4), 1.0, None
            return QColor(t.accent_2 if hover else t.accent), QColor(t.accent_ink), 1.0, None
        if k == "ghost":
            lit = hover or self._on
            return (theme.blend(t.fill_2, t.canvas) if lit else None), QColor(t.text if lit else t.text_2), 0.85, None
        if k == "outline":
            bg = theme.blend(t.fill_1, t.canvas) if hover else None
            return bg, QColor(t.text), 0.85, theme.blend(t.hair_2, t.raised)
        if k == "danger":
            return QColor(t.err), QColor("#ffffff"), 1.0, None
        if k == "danger-text":
            return (theme.blend(t.err_wash, t.canvas) if hover else None), QColor(t.err), 0.85, None
        if k in ("link", "toggle"):
            return None, QColor(t.text if hover else t.text_2), 1.0, None
        if k == "foot":
            return (
                (theme.blend(t.fill_1, t.canvas) if hover else None),
                QColor(t.text if hover else t.text_3),
                0.8,
                None,
            )
        if k == "more":
            return (
                (theme.blend(t.fill_2, t.canvas) if hover else None),
                QColor(t.text if hover else t.text_3),
                1.0,
                None,
            )
        if not self.isEnabled():
            return theme.blend(t.fill_1, t.canvas), QColor(t.text_4), 1.0, None
        return theme.blend(t.fill_3 if (hover or self._on) else t.fill_2, t.canvas), QColor(t.text), 0.85, None

    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        m = self._metrics()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled() and self.kind not in ("primary", ""):
            p.setOpacity(0.45)
        bg, fg, icon_alpha, line = self._colors()
        if bg is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(self.rect()), m["r"], m["r"])
            if self.kind == "primary" and self.isEnabled():
                # the web's inset highlight along the top edge
                p.setPen(QPen(QColor(255, 255, 255, 46), 1))
                p.drawLine(
                    QRectF(m["r"], 0.5, self.width() - 2 * m["r"], 0).topLeft(),
                    QRectF(m["r"], 0.5, self.width() - 2 * m["r"], 0).topRight(),
                )
        if line is not None:
            p.setPen(QPen(line, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), m["r"], m["r"])
        font = theme.font_ui(m["fs"], int(m["fw"]))
        p.setFont(font)
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(self.text()) if self.text() else 0
        has_icon = self._has_icon()
        content = tw + (self.icon_px + (m["gap"] if self.text() else 0) if has_icon else 0)
        if self.pad_left is not None:
            x = float(self.pad_left)
        else:
            x = max(float(m.get("pl", m["px"])), (self.width() - content) / 2)
        icon_x = x
        text_x = x
        if has_icon and self.text():
            if self._icon_end:
                text_x = x
                icon_x = x + tw + m["gap"]
            else:
                text_x = x + self.icon_px + m["gap"]
        if has_icon:
            if self.icon_name:
                pm = icons.pixmap(self.icon_name, fg, self.icon_px)
            else:
                pm = self.icon().pixmap(self.icon_px, self.icon_px)
            base = p.opacity()
            p.setOpacity(base * icon_alpha)
            p.drawPixmap(int(icon_x), (self.height() - self.icon_px) // 2, pm)
            p.setOpacity(base)
        if self.text():
            p.setPen(fg)
            p.drawText(
                QRect(int(text_x), 0, tw + 2, self.height()),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self.text(),
            )
