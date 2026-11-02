"""App-wide tooltips (a port of web/src/lib/tooltips.ts and ``.tip`` in menus.css).

Qt's own tooltip is a top-level window of its own: the stylesheet can round its border, but the window
around it stays a square, so on Linux every hover drew a rectangle with a rounded line inside it. This
one is a translucent window painted like the web's: a dark pill with 7px corners in both themes, a
soft shadow, keyboard shortcuts as keycaps — "New session (Ctrl+N)" reads "New session  [Ctrl] [N]" —
placed under the control (above it near the bottom of the screen), never over the pointer.

``install(app)`` takes over every widget's ``toolTip()``; ``show_text`` / ``hide_text`` are for the
places that show one themselves (the conversation canvas).
"""

from __future__ import annotations

import atexit
import re
from typing import List, Optional, Tuple

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from ... import theme

#: "(Ctrl+N)" or "(Shift+Tab to switch)" at the end of a tip: the combo becomes keycaps
SHORTCUT = re.compile(r"^(.*?)\s*\(((?:Ctrl|Alt|Shift|Esc|Enter|Tab|Space|⌘|F\d{1,2})[^()]*)\)\s*$", re.S)
TIP = "#2b2b30"  # --tip on dark; light's is #1d1d21: tooltips are dark in both themes
TIP_LIGHT = "#1d1d21"
INK = "#f1f1f3"
RADIUS = 7
PAD_X, PAD_Y = 8, 5
SHADOW = 10  # room around the pill for its shadow
MAX_W = 320
GAP = 8  # from the control


def _split(text: str) -> Tuple[str, List[str], str]:
    """(label, keys, note) of a tip: "Permission mode (Shift+Tab to switch)" → label, [Shift, Tab], "to switch"."""
    m = SHORTCUT.match(text)
    if not m:
        return text, [], ""
    combo = m.group(2).split(" ")[0]
    note = m.group(2)[len(combo) :].strip()
    keys = [k.strip() for k in re.split(r"\+(?!$)", combo) if k.strip()]
    return m.group(1), keys, note


#: a tooltip window: no frame, no shadow of the window manager's, never takes the focus
TIP_FLAGS = (
    Qt.WindowType.ToolTip
    | Qt.WindowType.FramelessWindowHint
    | Qt.WindowType.NoDropShadowWindowHint
    | Qt.WindowType.WindowDoesNotAcceptFocus
)


class Tip(QWidget):
    """The tooltip window of one top-level window. It is that window's child: Qt makes it a transient of
    the window, which a tooltip has to be — on Wayland a parentless one is a toplevel of its own (a
    window popping up, with an entry in the window switcher), and some X11 window managers treat it the
    same way."""

    def __init__(self, host: Optional[QWidget] = None) -> None:
        super().__init__(host, TIP_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setObjectName("pilcrow-tip")
        self.label = ""
        self.keys: List[str] = []
        self.note = ""
        self.anchor: Optional[QWidget] = None
        self._lines: List[str] = []

    # ---- content
    def _fonts(self) -> Tuple[QFont, QFont, QFont]:
        main = theme.font_ui(theme.FS_SMALL, 500)
        key = theme.font_ui(11, 500)
        note = theme.font_ui(theme.FS_SMALL, 400)
        return main, key, note

    def set_text(self, text: str) -> None:
        self.label, self.keys, self.note = _split(text.strip())
        main = self._fonts()[0]
        fm = QFontMetrics(main)
        keys_w = self._keys_width()
        # wrap the label (pre-line, like the web) to what fits beside the keys
        avail = max(60, MAX_W - (8 + keys_w if self.keys else 0))
        lines: List[str] = []
        for para in self.label.split("\n"):
            words = para.split(" ")
            line = ""
            for w in words:
                cand = f"{line} {w}" if line else w
                if line and fm.horizontalAdvance(cand) > avail:
                    lines.append(line)
                    line = w
                else:
                    line = cand
            lines.append(line)
        self._lines = lines
        text_w = max((fm.horizontalAdvance(ln) for ln in lines), default=0)
        width = min(MAX_W, text_w + (8 + keys_w if self.keys else 0))
        line_h = max(fm.height(), 18) if self.keys else fm.height()
        height = len(lines) * fm.height() + (max(0, line_h - fm.height()) if len(lines) == 1 else 0)
        self.resize(QSize(int(width) + 2 * PAD_X + 2 * SHADOW, int(height) + 2 * PAD_Y + 2 * SHADOW))
        self.update()

    def _keys_width(self) -> int:
        if not self.keys:
            return 0
        _main, key, note = self._fonts()
        kf = QFontMetrics(key)
        w = sum(max(18, kf.horizontalAdvance(k) + 10) for k in self.keys) + 3 * (len(self.keys) - 1)
        if self.note:
            w += 3 + QFontMetrics(note).horizontalAdvance(self.note)
        return w

    # ---- placement
    def place(self, rect: QRect, screen_rect: QRect) -> None:
        """Under ``rect`` (global), centred on it, kept 8px inside the screen; above it when there is no room below."""
        w, h = self.width() - 2 * SHADOW, self.height() - 2 * SHADOW
        below = rect.bottom() + GAP + h < screen_rect.bottom() - 8
        top = rect.bottom() + GAP if below else rect.top() - GAP - h
        left = max(screen_rect.left() + 8, min(screen_rect.right() - w - 8, rect.center().x() - w // 2))
        self.move(QPoint(int(left) - SHADOW, int(top) - SHADOW))

    # ---- paint
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        dark = theme.tokens() is theme.DARK
        body = QRectF(SHADOW, SHADOW, self.width() - 2 * SHADOW, self.height() - 2 * SHADOW)
        # 0 8px 24px -8px: a soft shadow under the pill, stronger below than above
        for i in range(SHADOW, 0, -1):
            a = int(28 * (1 - i / SHADOW) ** 2) + 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(body.adjusted(-i * 0.6, -i * 0.3, i * 0.6, i * 0.9), RADIUS + i * 0.6, RADIUS + i * 0.6)
        path = QPainterPath()
        path.addRoundedRect(body, RADIUS, RADIUS)
        p.fillPath(path, QColor(TIP if dark else TIP_LIGHT))
        if dark:
            # 0 0 0 1px rgba(255,255,255,0.08): a hairline so the pill does not dissolve into a dark page
            p.setPen(QColor(255, 255, 255, 20))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS - 0.5, RADIUS - 0.5)
        main, key, note = self._fonts()
        fm = QFontMetrics(main)
        ink = QColor(INK)
        x = body.left() + PAD_X
        y = body.top() + PAD_Y
        p.setFont(main)
        p.setPen(ink)
        line_h = fm.height()
        single = len(self._lines) == 1
        row_h = body.height() - 2 * PAD_Y
        for i, ln in enumerate(self._lines):
            r = QRectF(x, y + i * line_h, body.width() - 2 * PAD_X, line_h if not single else row_h)
            p.drawText(r, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), ln)
        if not self.keys:
            return
        # the keycaps after the last line
        last = self._lines[-1] if self._lines else ""
        kx = x + fm.horizontalAdvance(last) + 8
        cy = y + (len(self._lines) - 1) * line_h + (row_h if single else line_h) / 2
        kf = QFontMetrics(key)
        p.setFont(key)
        for k in self.keys:
            kw = max(18, kf.horizontalAdvance(k) + 10)
            cap = QRectF(kx, cy - 9, kw, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawRoundedRect(cap, 4, 4)
            p.setPen(QColor(255, 255, 255, 24))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(cap.adjusted(0.5, 0.5, -0.5, -0.5), 3.5, 3.5)
            p.setPen(ink)
            p.drawText(cap, int(Qt.AlignmentFlag.AlignCenter), k)
            kx += kw + 3
        if self.note:
            p.setFont(note)
            c = QColor(INK)
            c.setAlphaF(0.62)
            p.setPen(c)
            p.drawText(QRectF(kx, cy - line_h / 2, 400, line_h), int(Qt.AlignmentFlag.AlignVCenter), self.note)


_tip: Optional[Tip] = None


def _live_tip() -> Optional[Tip]:
    """The tip window if it exists and Qt has not deleted it (it can be, with the application)."""
    if _tip is not None and shiboken6.isValid(_tip):
        return _tip
    return None


def _the_tip(host: Optional[QWidget] = None) -> Tip:
    """The tip, belonging to ``host`` (the anchor's top-level window): made for it, or moved to it when
    the last one showed over another window."""
    global _tip
    tip = _live_tip()
    if tip is None:
        tip = _tip = Tip(host)
    elif host is not None and tip.parentWidget() is not host:
        tip.hide()
        tip.setParent(host, TIP_FLAGS)
    return tip


def show_text(rect: QRect, text: str, anchor: Optional[QWidget] = None) -> None:
    """Show ``text`` beside ``rect`` (global coordinates)."""
    if not text.strip():
        hide_text()
        return
    tip = _the_tip(anchor.window() if anchor is not None else QApplication.activeWindow())
    tip.anchor = anchor
    tip.set_text(text)
    screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
    tip.place(rect, screen.availableGeometry() if screen is not None else QRect(0, 0, 10000, 10000))
    tip.show()


def hide_text() -> None:
    tip = _live_tip()
    if tip is not None and tip.isVisible():
        tip.hide()
        tip.anchor = None


def visible_text() -> str:
    """What the tip shows now ("" when hidden), for tests."""
    tip = _live_tip()
    if tip is None or not tip.isVisible():
        return ""
    if not tip.keys:
        return tip.label
    return f"{tip.label} ({'+'.join(tip.keys)}{' ' + tip.note if tip.note else ''})"


_ToolTip = QEvent.Type.ToolTip
#: what puts the tip away when it happens to the control it belongs to
_AWAY = frozenset(
    (QEvent.Type.Leave, QEvent.Type.MouseButtonPress, QEvent.Type.Hide, QEvent.Type.Wheel, QEvent.Type.Destroy)
)
#: and what puts it away wherever it happens
_AWAY_ANYWHERE = frozenset((QEvent.Type.WindowDeactivate, QEvent.Type.KeyPress))


class _Filter(QObject):
    """Turns each widget's tooltip event into the painted tip, and puts it away on leave, press or hide.
    It sees every event in the application, so it does as little as it can with the others."""

    def eventFilter(self, obj, e) -> bool:
        t = e.type()
        if t == _ToolTip:
            if not isinstance(obj, QWidget):
                return False
            text = obj.toolTip()
            if not text:
                return False
            show_text(QRect(obj.mapToGlobal(QPoint(0, 0)), obj.size()), text, obj)
            return True
        if t in _AWAY or t in _AWAY_ANYWHERE:
            tip = _live_tip()
            if tip is not None and tip.isVisible() and (t in _AWAY_ANYWHERE or obj is tip.anchor):
                hide_text()
        return False


_filter: Optional[_Filter] = None


def install(app: Optional[QApplication] = None) -> None:
    """Take over every tooltip in the application (once). The filter comes off when the application
    quits and again at interpreter exit (before PySide's own shutdown, atexit being last in, first out):
    a Python filter still on the application while the interpreter is torn down is called by Qt's last
    events and crashes the process."""
    global _filter
    app = app or QApplication.instance()  # type: ignore[assignment]
    if app is None or _filter is not None:
        return
    _filter = _Filter(app)
    app.installEventFilter(_filter)
    app.aboutToQuit.connect(uninstall)
    atexit.register(uninstall)


def uninstall() -> None:
    global _filter
    f, _filter = _filter, None
    app = QApplication.instance()
    if f is not None and app is not None and shiboken6.isValid(f) and shiboken6.isValid(app):
        app.removeEventFilter(f)
