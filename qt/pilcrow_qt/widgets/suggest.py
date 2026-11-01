"""The composer's floating list of files and commands (``.suggest`` in Composer.tsx): typing ``@`` or
``/`` opens it above the field; the Composer drives it from the keyboard. Painted whole, on glass, it
arrives on the snappy spring and leaves as a snapshot that plays the exit."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import (
    QEvent,
    QPointF,
    QRect,
    QRectF,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
)

from .. import icons, theme
from ..canvas.clock import Tween
from ..store import store
from .ui.bits import draw_box_shadow, rounded_path
from .ui.signals import bind

Json = Dict[str, Any]

# ---------------------------------------------------------------- floating material


def draw_glass_panel(p: QPainter, panel: QRectF, radius: float, pop_t: float = 1.0) -> None:
    """A popover or suggest list (``.popover``, ``.suggest``): the glass fill and ``--shadow-glass``,
    a dark half-pixel edge, a light inner edge lit along the top, and two soft shadows beneath."""
    t = theme.tokens()
    draw_box_shadow(p, panel, 20, 50, -14, 0.8 * pop_t)
    draw_box_shadow(p, panel, 6, 16, -8, 0.5 * pop_t)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(theme.blend(t.glass, t.canvas))
    p.drawRoundedRect(panel, radius, radius)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(0, 0, 0, 77), 1))  # 0.5px at .6 → one pixel at .3
    p.drawRoundedRect(panel.adjusted(-0.5, -0.5, 0.5, 0.5), radius + 0.5, radius + 0.5)
    p.setPen(QPen(QColor(255, 255, 255, 12), 1))
    p.drawRoundedRect(panel.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
    p.setClipPath(rounded_path(panel, radius))
    p.fillRect(QRectF(panel.x(), panel.y(), panel.width(), 1), theme.qcolor(t.edge_hi))
    p.setClipping(False)


class _Ghost(QWidget):
    """A snapshot of a popup that plays its exit (``pop-out-down``) after the real widget has gone."""

    def __init__(self, parent: QWidget, pm: QPixmap, geometry: QRect, origin: QPointF) -> None:
        super().__init__(parent)
        self._pm = pm
        self._origin = origin
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setGeometry(geometry)
        self.show()
        self.raise_()
        self.t = Tween(lambda _v: self.update(), self.deleteLater, ms=theme.T_EXIT, curve=theme.ease_exit)
        self.t.go(1.0)

    def paintEvent(self, _e) -> None:
        k = self.t.value
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setOpacity(1 - k)
        p.translate(self._origin.x(), self._origin.y() + 2 * k)
        p.scale(1 - 0.03 * k, 1 - 0.03 * k)
        p.translate(-self._origin.x(), -self._origin.y())
        p.drawPixmap(0, 0, self._pm)


class _TextRef:
    """A text holder with a QLabel's ``text()``/``setText()``, for painted widgets."""

    def __init__(self) -> None:
        self._t = ""

    def text(self) -> str:
        return self._t

    def setText(self, t: str) -> None:
        self._t = t


class _FloatingPanel(QWidget):
    """A painted floating panel with shadow margins around it. Presses in the margins go to whatever
    is beneath (the margins are transparent, like a CSS shadow)."""

    M_X = 28
    M_TOP = 20
    M_BOTTOM = 36
    RADIUS = theme.R3

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pop_t = Tween(lambda _v: self.update(), ms=theme.SPRING_SNAPPY_T, curve=theme.spring_snappy)
        self.hide()

    def panel_rect(self) -> QRectF:
        return QRectF(self.M_X, self.M_TOP, self.width() - 2 * self.M_X, self.height() - self.M_TOP - self.M_BOTTOM)

    def place(self, panel: QRect) -> None:
        self.setGeometry(panel.adjusted(-self.M_X, -self.M_TOP, self.M_X, self.M_BOTTOM))

    def pop_in(self) -> None:
        self.pop_t.jump(0.0)
        self.show()
        self.raise_()
        self.pop_t.go(1.0)

    def pop_out(self) -> None:
        """Hide now; a ghost plays the exit."""
        if not self.isVisible():
            return
        parent = self.parentWidget()
        if not store.reduced_motion and parent is not None and self.pop_t.value > 0.5:
            pr = self.panel_rect()
            _Ghost(parent, self.grab(), self.geometry(), QPointF(pr.center().x(), pr.bottom()))
        self.hide()

    def forward_press(self, e: QMouseEvent) -> None:
        """A press in the shadow margin: give it to the widget underneath."""
        parent = self.parentWidget()
        if parent is None:
            return
        gp = e.globalPosition().toPoint()
        # widgetAt looks through mouse-transparent widgets: step aside for the lookup
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        target = QApplication.widgetAt(gp)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        if target is None or target is self or self.isAncestorOf(target):
            return
        local = target.mapFromGlobal(gp)
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            ev = QMouseEvent(kind, QPointF(local), QPointF(gp), e.button(), e.buttons(), e.modifiers())
            QApplication.sendEvent(target, ev)

    def apply_pop(self, p: QPainter) -> None:
        """``pop-spring-up``: scale .94 → 1 and 3px up, from the bottom centre, with the opacity."""
        k = self.pop_t.value
        pr = self.panel_rect()
        p.setOpacity(max(0.0, min(1.0, k)))
        ox, oy = pr.center().x(), pr.bottom()
        p.translate(ox, oy + 3 * (1 - k))
        s = 0.94 + 0.06 * k
        p.scale(s, s)
        p.translate(-ox, -oy)


# ---------------------------------------------------------------- the @ and / popup


def word_match(q: str, words: List[Any]) -> bool:
    """Whether a typed fragment is part of one of the person's names for a repository, loosely (case,
    spaces, hyphens and underscores do not count)."""
    n = re.sub(r"[\s_\-]+", "", q.lower())
    return bool(n) and any(n in re.sub(r"[\s_\-]+", "", str(w).lower()) for w in words if w)


class SuggestPopup(_FloatingPanel):
    """The list of files or commands above the field (``.suggest``). Keyboard-driven by the Composer;
    rows light under the pointer, a click picks. Painted whole, so it arrives on the snappy spring."""

    ROW = 32
    HEAD = 27  # padding 4px 8px 6px around an 11px line
    PAD = 6

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("suggest")
        self.items: List[Json] = []
        self.index = 0
        self.scroll = 0
        self.head = _TextRef()
        self.name_w = 0
        self.on_pick: Optional[Callable[[Json], None]] = None
        self.on_hover: Optional[Callable[[int], None]] = None
        bind(theme.theme.changed, self, lambda _t: self.update())

    # ---- data
    def set_items(self, head: str, items: List[Json], index: int) -> None:
        self.head.setText(head)
        self.items = items[:40]
        name_ch = min(30, max([8] + [len(x["label"]) for x in self.items if x["kind"] == "cmd"]))
        fm = QFontMetrics(theme.font_mono(12.5))
        self.name_w = int((name_ch + 1) * fm.horizontalAdvance("0"))
        self.scroll = 0
        self.set_index(index)
        self.update()

    def list_height(self) -> int:
        return min(320, self.ROW * len(self.items))

    def total_height(self) -> int:
        """The panel's height (the widget is taller by its shadow margins)."""
        return self.PAD * 2 + self.HEAD + self.list_height()

    def set_index(self, index: int) -> None:
        self.index = index
        if 0 <= index < len(self.items):
            top, bottom = index * self.ROW, (index + 1) * self.ROW
            if top < self.scroll:
                self.scroll = top
            elif bottom > self.scroll + self.list_height():
                self.scroll = bottom - self.list_height()
        self.update()

    # ---- geometry
    def list_rect(self) -> QRectF:
        pr = self.panel_rect()
        return QRectF(pr.x() + self.PAD, pr.y() + self.PAD + self.HEAD, pr.width() - 2 * self.PAD, self.list_height())

    def row_at(self, pos: QPointF) -> int:
        lr = self.list_rect()
        if not lr.contains(pos):
            return -1
        i = int((pos.y() - lr.y() + self.scroll) // self.ROW)
        return i if 0 <= i < len(self.items) else -1

    # ---- events
    def mouseMoveEvent(self, e) -> None:
        i = self.row_at(e.position())
        if i >= 0 and i != self.index and self.on_hover:
            self.on_hover(i)
        self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0 else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, e) -> None:
        if not self.panel_rect().contains(e.position()):
            self.forward_press(e)
            return
        i = self.row_at(e.position())
        if i >= 0 and self.on_pick:
            self.on_pick(self.items[i])

    def wheelEvent(self, e) -> None:
        over = self.ROW * len(self.items) - self.list_height()
        if over <= 0:
            return
        self.scroll = max(0, min(over, self.scroll - int(e.angleDelta().y() / 120 * 48)))
        self.update()
        e.accept()

    # ---- paint
    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.apply_pop(p)
        pr = self.panel_rect()
        draw_glass_panel(p, pr, self.RADIUS, self.pop_t.value)
        # head
        p.setFont(theme.font_ui(theme.FS_MICRO, 600))
        p.setPen(QColor(t.text_3))
        p.drawText(
            QRect(int(pr.x() + self.PAD + 8), int(pr.y() + self.PAD + 4), int(pr.width()) - 28, self.HEAD - 10),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.head.text(),
        )
        lr = self.list_rect()
        p.save()
        p.setClipRect(lr)
        mono = theme.font_mono(12.5)
        small = theme.font_ui(theme.FS_SMALL)
        mfm = QFontMetrics(mono)
        sfm = QFontMetrics(small)
        y0 = lr.y() - self.scroll
        first = max(0, int(self.scroll // self.ROW))
        last = min(len(self.items), int((self.scroll + lr.height()) // self.ROW) + 1)
        for i in range(first, last):
            it = self.items[i]
            ry = y0 + i * self.ROW
            row = QRectF(lr.x(), ry, lr.width(), self.ROW)
            if i == self.index:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.qcolor(t.fill_2))
                p.drawRoundedRect(row, theme.R2, theme.R2)
            x = row.x() + 10
            kind = it["kind"]
            if kind in ("dir", "file", "repo"):
                ico = "folder-git-2" if kind == "repo" else "folder" if kind == "dir" else "file"
                p.drawPixmap(QPointF(x, ry + 9), icons.pixmap(ico, t.plan if kind == "repo" else t.text_3, 14))
                x += 14 + 10
            p.setFont(mono)
            label = str(it["label"])
            if kind == "cmd":
                p.setPen(QColor(t.text_3))
                p.drawText(QRect(int(x), int(ry), 20, self.ROW), Qt.AlignmentFlag.AlignVCenter, "/")
                slash_w = mfm.horizontalAdvance("/")
                p.setPen(QColor(t.text))
                p.drawText(
                    QRect(int(x + slash_w), int(ry), int(lr.width()), self.ROW),
                    Qt.AlignmentFlag.AlignVCenter,
                    label[1:],
                )
                x += max(self.name_w, mfm.horizontalAdvance(label)) + 10
            else:
                max_w = int(row.width() * 0.6)
                shown = mfm.elidedText(label, Qt.TextElideMode.ElideRight, max_w)
                p.setPen(QColor(t.text))
                p.drawText(QRect(int(x), int(ry), max_w + 4, self.ROW), Qt.AlignmentFlag.AlignVCenter, shown)
                x += mfm.horizontalAdvance(shown) + 10
            hint = str(it.get("hint") or "")
            if hint:
                p.setFont(small)
                p.setPen(QColor(t.text_3))
                avail = int(row.right() - 10 - x)
                p.drawText(
                    QRect(int(x), int(ry), max(0, avail) + 2, self.ROW),
                    Qt.AlignmentFlag.AlignVCenter,
                    sfm.elidedText(hint, Qt.TextElideMode.ElideRight, max(0, avail)),
                )
        p.restore()
        over = self.ROW * len(self.items) - self.list_height()
        if over > 0:
            # the slim overlay thumb of the web's scrollbar
            frac = lr.height() / (self.ROW * len(self.items))
            th = max(28, lr.height() * frac)
            ty = lr.y() + (lr.height() - th) * (self.scroll / over)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor(t.fill_3))
            p.drawRoundedRect(QRectF(lr.right() - 7, ty, 4, th), 2, 2)
