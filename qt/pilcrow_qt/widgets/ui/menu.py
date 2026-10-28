"""Popovers and menus (a port of ``Popover``/``Menu`` in web/src/components/ui/Popover.tsx).

A :class:`Popover` is a rounded floating frame drawn *inside* the window (a child of the main
window's central widget, raised above everything), not a separate top-level window: that keeps the
rounded corners and the hairline right without a compositor, lets ``window.grab()`` include it, and
costs nothing on a CPU-only machine. It closes on a click outside, on Escape, and when the window
resizes.

A :class:`Menu` is the list inside: rows of icon, label, hint and key cap, one of them active under
the pointer or the arrow keys. An item is a dict: ``{"label", "icon"?, "hint"?, "mono"?, "checked"?,
"danger"?, "disabled"?, "kbd"?, "on_select"}``, ``{"separator": True}`` or ``{"heading": str}``.
``show_menu(items, anchor)`` opens one under a widget; ``show_menu_at(items, global_pos)`` at a point
(context menus). Both return at once; the menu lives until it is closed.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QMainWindow, QSizePolicy, QVBoxLayout, QWidget

from ... import icons, theme
from .signals import bind

Item = Dict[str, Any]


def popover_bg(t: theme.Tokens) -> str:
    """The popover material: ``#252528`` on dark (app.css ``[data-theme='dark'] .popover``), raised on light."""
    return "#252528" if t.dark else t.raised


def host_for(w: Optional[QWidget]) -> Optional[QWidget]:
    """Where overlays live: the main window's central widget, so a popover covers pages and sheets alike."""
    win = w.window() if w is not None else QApplication.activeWindow()
    if win is None:
        for top in QApplication.topLevelWidgets():
            if isinstance(top, QMainWindow) and top.isVisible():
                win = top
                break
    if isinstance(win, QMainWindow) and win.centralWidget() is not None:
        return win.centralWidget()
    return win


class Popover(QFrame):
    """``.popover``: a floating frame, 5px padding, 10px radius, that lays its content out in a column."""

    closed = Signal()

    def __init__(self, host: QWidget, width: int = 0, min_width: int = 180) -> None:
        super().__init__(host)
        self._host = host
        self._width = width
        self._min_width = min_width
        self._closed = False
        self._anchor: Optional[QWidget] = None
        self.setObjectName("popover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(5, 5, 5, 5)
        self.lay.setSpacing(0)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()
        host.installEventFilter(self)
        QApplication.instance().installEventFilter(self)

    def _paint(self) -> None:
        t = theme.tokens()
        bg = popover_bg(t)
        self.setStyleSheet(
            f"#popover {{ background: {bg}; border: 1px solid {theme.blend(t.hair_2, bg).name()}; border-radius: {theme.R3}px; }}"
        )

    def set_content(self, w: QWidget) -> QWidget:
        self.lay.addWidget(w)
        return w

    # ---- placing
    def open_at(self, anchor: QWidget, placement: str = "bottom-start") -> None:
        """Below (or above, when there is no room) an anchor widget, 6px away, like the web."""
        self._anchor = anchor
        tl = anchor.mapTo(self._host, QPoint(0, 0))
        self._place(QRect(tl, anchor.size()), placement)

    def open_at_point(self, pos: QPoint) -> None:
        """At a point in the host's coordinates (context menus)."""
        self._place(QRect(pos, pos), "bottom-start", gap=0)

    def _place(self, a: QRect, placement: str, gap: int = 6) -> None:
        w = self._width or max(self._min_width, self.lay.sizeHint().width())
        self.setFixedWidth(w)
        want = self.lay.totalHeightForWidth(w) if self.lay.hasHeightForWidth() else self.lay.sizeHint().height()
        h = min(want, min(int(self._host.height() * 0.7), 520))
        vw, vh = self._host.width(), self._host.height()
        left = a.right() + 1 - w if placement.endswith("end") else a.left()
        top = a.top() - h - gap if placement.startswith("top") else a.bottom() + 1 + gap
        if top + h > vh - 8:
            top = max(8, a.top() - h - gap)
        if top < 8:
            top = min(vh - h - 8, a.bottom() + 1 + gap)
        left = max(8, min(left, vw - w - 8))
        self.setGeometry(left, top, w, h)
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    # ---- leaving
    def dismiss(self) -> None:
        if self._closed:
            return
        self._closed = True
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        try:
            self._host.removeEventFilter(self)
        except RuntimeError:
            pass
        self.hide()
        self.closed.emit()
        self.deleteLater()

    @property
    def is_open(self) -> bool:
        return not self._closed

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        if self._closed:
            return False
        t = e.type()
        if obj is self._host and t == QEvent.Type.Resize:
            self.dismiss()
            return False
        if t == QEvent.Type.MouseButtonPress:
            w = QApplication.widgetAt(e.globalPosition().toPoint()) if hasattr(e, "globalPosition") else None
            inside = w is not None and (self.isAncestorOf(w) or w is self)
            on_anchor = (
                w is not None and self._anchor is not None and (w is self._anchor or self._anchor.isAncestorOf(w))
            )
            if not inside and not on_anchor:
                self.dismiss()
            return False
        if t == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape:
            self.dismiss()
            return True
        if t == QEvent.Type.ShortcutOverride and e.key() == Qt.Key.Key_Escape:
            e.accept()
            return True
        return False


class MenuRow(QWidget):
    """``.menu-item``, painted by hand: a 16px icon column, the label on an 18px line, the hint under it
    at 12px with the web's 1.4 line-height, a bare key cap at the end."""

    entered = Signal()
    chosen = Signal()
    LABEL_H = 18
    HINT_LH = 16.8  # 12px × 1.4
    PAD_X, PAD_Y, GAP = 9, 6, 9

    def __init__(self, it: Item, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.it = it
        self._active = False
        self.disabled = bool(it.get("disabled"))
        self._hint_lines: List[str] = []
        self._kbd_w = 0
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setAccessibleName(str(it.get("label") or ""))
        if not self.disabled:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.kbd: Optional[QWidget] = None
        if it.get("kbd"):
            from ..dialogs.parts import Keys

            self.kbd = Keys(str(it["kbd"]), bare=True, parent=self)
            self._kbd_w = self.kbd.sizeHint().width()
        self.setMinimumHeight(30)
        bind(theme.theme.changed, self, lambda _t: self.update())

    @property
    def label(self) -> str:
        return str(self.it.get("label") or "")

    @property
    def hint(self) -> str:
        return str(self.it.get("hint") or "")

    def _hint_font(self):
        from ..dialogs.parts import web_font

        return web_font(theme.font_mono(11.5) if self.it.get("mono") else theme.font_ui(theme.FS_SMALL))

    def _label_font(self):
        from ..dialogs.parts import web_font

        return web_font(theme.font_ui(theme.FS_UI))

    def _text_x(self) -> int:
        return self.PAD_X + 16 + self.GAP

    def _text_w(self, width: int) -> int:
        return max(10, width - self._text_x() - self.PAD_X - (self._kbd_w + self.GAP if self._kbd_w else 0))

    def _wrap(self, width: int) -> List[str]:
        if not self.hint:
            return []
        from PySide6.QtGui import QTextLayout

        lay = QTextLayout(self.hint, self._hint_font())
        lay.beginLayout()
        lines: List[str] = []
        while True:
            line = lay.createLine()
            if not line.isValid():
                break
            line.setLineWidth(self._text_w(width))
            lines.append(self.hint[line.textStart() : line.textStart() + line.textLength()].rstrip())
        lay.endLayout()
        return lines

    def height_for_f(self, width: int) -> float:
        n = len(self._wrap(width))
        return self.PAD_Y * 2 + self.LABEL_H + (2 + n * self.HINT_LH if n else 0)

    def height_for(self, width: int) -> int:
        return int(round(self.height_for_f(width)))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        return self.height_for(w)

    def resizeEvent(self, e) -> None:  # noqa: N802
        self._hint_lines = self._wrap(self.width())
        if self.kbd is not None:
            self.kbd.move(self.width() - self.PAD_X - self._kbd_w, self.PAD_Y)
        super().resizeEvent(e)

    def sizeHint(self) -> QSize:  # noqa: N802
        from PySide6.QtGui import QFontMetrics

        fm = QFontMetrics(self._label_font())
        w = (
            self._text_x()
            + fm.horizontalAdvance(self.label)
            + self.PAD_X
            + (self._kbd_w + self.GAP if self._kbd_w else 0)
        )
        return QSize(w, self.height_for(max(w, 200)))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(60, 30)

    def set_active(self, on: bool) -> None:
        if on != self._active:
            self._active = on
            self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        from PySide6.QtGui import QPainter

        t = theme.tokens()
        it = self.it
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self._active and not self.disabled:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.blend(t.fill_2, popover_bg(t)))
            p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), theme.R2, theme.R2)
        ico_c = t.err if it.get("danger") else t.text_2 if self._active else t.text_3
        name = "check" if it.get("checked") else str(it.get("icon") or "")
        if name:
            pm = icons.pixmap(name, ico_c, 14)
            p.drawPixmap(self.PAD_X + 1, self.PAD_Y + 2, pm)
        x = self._text_x()
        w = self._text_w(self.width())
        fg = t.err if it.get("danger") else t.text_3 if self.disabled else t.text
        p.setFont(self._label_font())
        p.setPen(QColor(fg))
        fm = p.fontMetrics()
        p.drawText(
            QRectF(x, self.PAD_Y, w, self.LABEL_H),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            fm.elidedText(self.label, Qt.TextElideMode.ElideRight, w),
        )
        if self._hint_lines:
            p.setFont(self._hint_font())
            p.setPen(QColor(t.text_3))
            y = self.PAD_Y + self.LABEL_H + 2
            for line in self._hint_lines:
                p.drawText(
                    QRectF(x, y, w, self.HINT_LH),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    line,
                )
                y += self.HINT_LH

    def enterEvent(self, e) -> None:  # noqa: N802
        if not self.disabled:
            self.entered.emit()
        super().enterEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and not self.disabled:
            self.chosen.emit()


class Menu(QWidget):
    """``.menu``: the rows, 1px apart, placed by hand so their fractional CSS heights add up the way
    the browser's do; arrows move the active row, Enter picks it."""

    picked = Signal(object)  # the item dict
    GAP = 1

    def __init__(self, items: List[Item], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.items = items
        self.rows: List[Optional[MenuRow]] = []
        self.parts: List[QWidget] = []
        self._active = -1
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        t = theme.tokens()
        for i, it in enumerate(items):
            if it.get("separator"):
                sep = QFrame(self)
                sep.setObjectName("menu-sep")
                sep.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                sep.setStyleSheet(f"#menu-sep {{ background: {theme.blend(t.hair, popover_bg(t)).name()}; }}")
                self.parts.append(sep)
                self.rows.append(None)
                continue
            if it.get("heading"):
                head = QLabel(str(it["heading"]), self)
                head.setContentsMargins(9, 8, 9, 4)
                head.setStyleSheet(f"color: {t.text_3}; font-size: {theme.FS_MICRO}px; font-weight: 600;")
                self.parts.append(head)
                self.rows.append(None)
                continue
            row = MenuRow(it, self)
            row.entered.connect(lambda i=i: self.set_active(i))
            row.chosen.connect(lambda i=i: self.choose(i))
            self.parts.append(row)
            self.rows.append(row)

    def _heights(self, w: int) -> List[float]:
        out: List[float] = []
        for part, it in zip(self.parts, self.items):
            if it.get("separator"):
                out.append(9.0)  # 1px line with 4px margins
            elif it.get("heading"):
                out.append(float(part.sizeHint().height()))
            else:
                out.append(part.height_for_f(w))  # type: ignore[union-attr]
        return out

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, w: int) -> int:  # noqa: N802
        hs = self._heights(w)
        return int(round(sum(hs) + self.GAP * max(0, len(hs) - 1)))

    def sizeHint(self) -> QSize:  # noqa: N802
        w = max((p.sizeHint().width() for p in self.parts), default=120)
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(60, 0)

    def resizeEvent(self, e) -> None:  # noqa: N802
        w = self.width()
        y = 0.0
        for part, it, h in zip(self.parts, self.items, self._heights(w)):
            top = int(round(y))
            bottom = int(round(y + h))
            if it.get("separator"):
                part.setGeometry(6, top + 4, max(0, w - 12), 1)
            else:
                part.setGeometry(0, top, w, bottom - top)
            y += h + self.GAP
        super().resizeEvent(e)

    def selectable(self) -> List[int]:
        return [i for i, r in enumerate(self.rows) if r is not None and not r.disabled]

    def set_active(self, i: int) -> None:
        self._active = i
        for k, r in enumerate(self.rows):
            if r is not None:
                r.set_active(k == i)

    def step(self, delta: int) -> None:
        sel = self.selectable()
        if not sel:
            return
        idx = sel.index(self._active) if self._active in sel else -1
        self.set_active(sel[(idx + delta) % len(sel)])

    def choose(self, i: int) -> None:
        if 0 <= i < len(self.items):
            self.picked.emit(self.items[i])

    def choose_active(self) -> bool:
        if self._active >= 0:
            self.choose(self._active)
            return True
        return False


class MenuPopover(Popover):
    """A popover holding a menu: picking a row runs its ``on_select`` and closes."""

    def __init__(self, host: QWidget, items: List[Item], width: int = 0) -> None:
        super().__init__(host, width)
        self.menu = Menu(items)
        self.menu.picked.connect(self._picked)
        self.set_content(self.menu)

    def _picked(self, it: Item) -> None:
        fn = it.get("on_select")
        self.dismiss()
        if callable(fn):
            fn()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        k = e.key()
        if k == Qt.Key.Key_Down:
            self.menu.step(1)
        elif k == Qt.Key.Key_Up:
            self.menu.step(-1)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not self.menu.choose_active():
                self.dismiss()
        elif k == Qt.Key.Key_Escape:
            self.dismiss()
        else:
            super().keyPressEvent(e)


_current: List[MenuPopover] = []


def show_menu(items: List[Item], anchor: QWidget, width: int = 0, align_right: bool = False) -> Optional[MenuPopover]:
    """Open a menu under ``anchor`` (``MenuButton`` in the web); ``align_right`` is placement ``bottom-end``."""
    host = host_for(anchor)
    if host is None:
        return None
    close_menus()
    m = MenuPopover(host, items, width)
    _current.append(m)
    m.closed.connect(lambda: m in _current and _current.remove(m))
    m.open_at(anchor, "bottom-end" if align_right else "bottom-start")
    return m


def show_menu_at(
    items: List[Item], pos: QPoint, parent: Optional[QWidget] = None, width: int = 0
) -> Optional[MenuPopover]:
    """Open a menu at a global point (context menus)."""
    host = host_for(parent)
    if host is None:
        return None
    close_menus()
    m = MenuPopover(host, items, width)
    _current.append(m)
    m.closed.connect(lambda: m in _current and _current.remove(m))
    m.open_at_point(host.mapFromGlobal(pos))
    return m


def close_menus() -> None:
    for m in list(_current):
        m.dismiss()


def open_menu() -> Optional[MenuPopover]:
    """The menu on screen, if any (tests look here)."""
    return _current[-1] if _current else None


def build_menu(items: List[Item], parent: Optional[QWidget] = None, width: int = 0) -> MenuPopover:
    """Kept for callers that build first and place later: a menu popover not yet shown."""
    host = host_for(parent)
    return MenuPopover(host if host is not None else QWidget(), items, width)


_ = Callable
