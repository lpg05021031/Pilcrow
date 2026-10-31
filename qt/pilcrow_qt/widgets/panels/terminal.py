"""The Terminal panel (a port of web/src/components/panels/TerminalPanel.tsx).

xterm.js in the web interface; here :class:`TerminalView` paints a cell grid kept by pyte (a
``HistoryScreen`` fed through a ``ByteStream``) in the mono face with the theme's ANSI palette: a
block cursor, mouse selection and copy, paste, wheel scrolling through the history, keys mapped to
VT sequences, and a debounced resize that tells the server the new grid. :class:`TerminalPanel`
holds the terminals opened from one session as tabs: ``term.open`` → ``term.input`` /
``term.resize`` / ``term.close``, output through the ``term.data`` and ``term.exit`` events.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import pyte
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QWheelEvent
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QStackedWidget, QVBoxLayout, QWidget

from ... import icons, theme
from ...lib.fmt import basename, err_text
from ...rpc import rpc
from ...store import store
from ..ui.bits import IconButton, Row
from ..ui.menu import show_menu_at
from ..ui.signals import bind
from .right_panel import EmptyState, Hairline

Json = Dict[str, Any]

SCROLLBACK = 8000
RESIZE_DEBOUNCE_MS = 120
BLINK_MS = 530
DECCKM = 1 << 5  # pyte keeps private modes shifted by 5; DEC private mode 1 is application cursor keys

# pyte's colour names → theme token names.
_NAMED = {
    "black": "ansi_black",
    "red": "ansi_red",
    "green": "ansi_green",
    "brown": "ansi_yellow",
    "blue": "ansi_blue",
    "magenta": "ansi_magenta",
    "cyan": "ansi_cyan",
    "white": "ansi_white",
    "brightblack": "ansi_bright_black",
    "brightred": "ansi_bright_red",
    "brightgreen": "ansi_bright_green",
    "brightbrown": "ansi_bright_yellow",
    "brightblue": "ansi_bright_blue",
    "brightmagenta": "ansi_bright_magenta",
    "brightcyan": "ansi_bright_cyan",
    "brightwhite": "ansi_bright_white",
}

_KEYS = {
    Qt.Key.Key_Return: b"\r",
    Qt.Key.Key_Enter: b"\r",
    Qt.Key.Key_Backspace: b"\x7f",
    Qt.Key.Key_Tab: b"\t",
    Qt.Key.Key_Escape: b"\x1b",
    Qt.Key.Key_Insert: b"\x1b[2~",
    Qt.Key.Key_Delete: b"\x1b[3~",
    Qt.Key.Key_PageUp: b"\x1b[5~",
    Qt.Key.Key_PageDown: b"\x1b[6~",
    Qt.Key.Key_F1: b"\x1bOP",
    Qt.Key.Key_F2: b"\x1bOQ",
    Qt.Key.Key_F3: b"\x1bOR",
    Qt.Key.Key_F4: b"\x1bOS",
    Qt.Key.Key_F5: b"\x1b[15~",
    Qt.Key.Key_F6: b"\x1b[17~",
    Qt.Key.Key_F7: b"\x1b[18~",
    Qt.Key.Key_F8: b"\x1b[19~",
    Qt.Key.Key_F9: b"\x1b[20~",
    Qt.Key.Key_F10: b"\x1b[21~",
    Qt.Key.Key_F11: b"\x1b[23~",
    Qt.Key.Key_F12: b"\x1b[24~",
}
_CURSOR_KEYS = {
    Qt.Key.Key_Up: b"A",
    Qt.Key.Key_Down: b"B",
    Qt.Key.Key_Right: b"C",
    Qt.Key.Key_Left: b"D",
    Qt.Key.Key_Home: b"H",
    Qt.Key.Key_End: b"F",
}


def key_to_bytes(e: QKeyEvent, app_cursor: bool = False) -> Optional[bytes]:
    """The VT input for a key press, or None when the key is not something a terminal takes."""
    key, mods = e.key(), e.modifiers()
    shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
    alt = bool(mods & Qt.KeyboardModifier.AltModifier)
    ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
    mod = 1 + (1 if shift else 0) + (2 if alt else 0) + (4 if ctrl else 0)
    if key in _CURSOR_KEYS:
        final = _CURSOR_KEYS[key]
        if mod > 1:
            return b"\x1b[1;" + str(mod).encode() + final
        return (b"\x1bO" if app_cursor else b"\x1b[") + final
    if key in _KEYS:
        seq = _KEYS[key]
        if mod > 1 and seq.endswith(b"~"):
            return seq[:-1] + b";" + str(mod).encode() + b"~"
        if key == Qt.Key.Key_Tab and shift:
            return b"\x1b[Z"
        if key == Qt.Key.Key_Backspace and alt:
            return b"\x1b\x7f"
        return seq
    text = e.text()
    if ctrl and not alt:
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return bytes([key - Qt.Key.Key_A + 1])
        ctrl_punct = {
            Qt.Key.Key_Space: 0,
            Qt.Key.Key_At: 0,
            Qt.Key.Key_BracketLeft: 27,
            Qt.Key.Key_Backslash: 28,
            Qt.Key.Key_BracketRight: 29,
            Qt.Key.Key_AsciiCircum: 30,
            Qt.Key.Key_Underscore: 31,
            Qt.Key.Key_Slash: 31,
        }
        if key in ctrl_punct:
            return bytes([ctrl_punct[key]])
        return None
    if not text or text in ("\x00",):
        return None
    if alt and not ctrl:
        return b"\x1b" + text.encode("utf-8")
    return text.encode("utf-8")


class TerminalView(QAbstractScrollArea):
    """A terminal grid: feed it bytes, it paints them; it hands typed bytes to ``on_output``."""

    output = Signal(object)  # bytes typed or pasted
    resized = Signal(int, int)  # cols, rows (debounced)
    title_changed = Signal(str)

    def __init__(
        self, cols: int = 80, rows: int = 24, scrollback: int = SCROLLBACK, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.screen = pyte.HistoryScreen(cols, rows, history=scrollback, ratio=0.5)
        self.stream = pyte.ByteStream(self.screen)
        self.on_output: Optional[Callable[[bytes], None]] = None
        self._title = ""
        self._offset = 0  # lines scrolled up from the live screen
        self._sel: Optional[Tuple[Tuple[int, int], Tuple[int, int]]] = None  # (anchor, end) as (abs row, col)
        self._selecting = False
        self._cursor_on = True
        self._cell_w = 8.0
        self._cell_h = 16
        self._ascent = 12.0
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self.setAccessibleName("Terminal")
        self.verticalScrollBar().valueChanged.connect(self._on_scrollbar)
        self._paint_timer = QTimer(self)
        self._paint_timer.setSingleShot(True)
        self._paint_timer.setInterval(16)
        self._paint_timer.timeout.connect(self.viewport().update)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(RESIZE_DEBOUNCE_MS)
        self._resize_timer.timeout.connect(lambda: self.resized.emit(self.cols, self.rows))
        self._blink = QTimer(self)
        self._blink.setInterval(BLINK_MS)
        self._blink.timeout.connect(self._toggle_cursor)
        self._measure()
        bind(theme.theme.changed, self, lambda _t: self._on_theme())

    # ---------------------------------------------------------------- grid
    @property
    def cols(self) -> int:
        return self.screen.columns

    @property
    def rows(self) -> int:
        return self.screen.lines

    def _measure(self) -> None:
        self._font = theme.font_mono()
        fm = QFontMetricsF(self._font)
        self._cell_w = max(1.0, fm.horizontalAdvance("M"))
        self._cell_h = max(1, int(round(fm.height() * 1.3)))  # xterm lineHeight 1.3
        self._ascent = fm.ascent() + (self._cell_h - fm.height()) / 2
        self.setFont(self._font)

    def _on_theme(self) -> None:
        self._measure()
        self._fit()
        self.viewport().update()

    def cell_size(self) -> Tuple[float, int]:
        return self._cell_w, self._cell_h

    def _fit(self) -> bool:
        vp = self.viewport()
        cols = max(2, int(vp.width() // self._cell_w))
        rows = max(1, int(vp.height() // self._cell_h))
        if (cols, rows) == (self.cols, self.rows):
            return False
        self._resize_screen(rows, cols)
        self._offset = min(self._offset, len(self.screen.history.top))
        self._sync_scrollbar()
        self._resize_timer.start()
        return True

    def _resize_screen(self, rows: int, cols: int) -> None:
        """Resize like a terminal rather than pyte's ``Screen.resize``, which drops lines from the top
        even when they would fit: lines above the cursor that no longer fit move into the history, and
        a taller screen pulls them back."""
        s = self.screen
        old = s.lines
        if rows < old:
            drop = max(0, s.cursor.y + 1 - rows)
            for y in range(drop):
                s.history.top.append(s.buffer[y])
            kept = {y - drop: s.buffer[y] for y in range(drop, min(old, drop + rows)) if y in s.buffer}
            s.buffer.clear()
            s.buffer.update(kept)
            s.cursor.y -= drop
        elif rows > old:
            pull = min(rows - old, len(s.history.top))
            if pull:
                moved = {y + pull: line for y, line in s.buffer.items()}
                s.buffer.clear()
                s.buffer.update(moved)
                for y in range(pull - 1, -1, -1):
                    s.buffer[y] = s.history.top.pop()
                s.cursor.y += pull
        if cols < s.columns:
            for line in s.buffer.values():
                for x in range(cols, s.columns):
                    line.pop(x, None)
        s.lines, s.columns = rows, cols
        s.set_margins()
        s.cursor.x = min(s.cursor.x, cols - 1)
        s.dirty.update(range(rows))

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._fit()
        self.viewport().update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(int(80 * self._cell_w) + 12, 24 * self._cell_h)

    # ---------------------------------------------------------------- data
    def feed(self, data: Any) -> None:
        """Bytes (or text) from the process."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.stream.feed(data)
        if self.screen.title != self._title:
            self._title = self.screen.title
            self.title_changed.emit(self._title)
        self._offset = min(self._offset, len(self.screen.history.top))
        self._sync_scrollbar()
        if not self._paint_timer.isActive():
            self._paint_timer.start()

    def text(self) -> str:
        """The live screen as text, trailing blanks trimmed: what the tests read."""
        return "\n".join(line.rstrip() for line in self.screen.display).rstrip("\n")

    def send(self, data: bytes) -> None:
        if self.on_output:
            self.on_output(data)
        self.output.emit(data)

    # ---------------------------------------------------------------- lines and colours
    def _history_len(self) -> int:
        return len(self.screen.history.top)

    def _line(self, abs_row: int):
        h = self._history_len()
        if abs_row < h:
            return self.screen.history.top[abs_row]
        return self.screen.buffer[abs_row - h]

    def _first_row(self) -> int:
        return self._history_len() - self._offset

    def colors_for(self, ch, t: Optional[theme.Tokens] = None) -> Tuple[QColor, Optional[QColor]]:
        """The (foreground, background) a cell is painted with; background None means the surface."""
        t = t or theme.tokens()
        fg = self._color(ch.fg, True, ch.bold, t)
        bg = self._color(ch.bg, False, False, t)
        if ch.reverse:
            fg, bg = (bg or theme.qcolor(t.chrome)), (fg if ch.fg != "default" else theme.qcolor(t.text))
        return fg, bg

    def cell_colors(self, row: int, col: int) -> Tuple[QColor, Optional[QColor]]:
        """Colours of a cell on the live screen (row and col 0-based)."""
        return self.colors_for(self.screen.buffer[row][col])

    @staticmethod
    def _color(name: str, is_fg: bool, bold: bool, t: theme.Tokens) -> Optional[QColor]:
        if name == "default":
            return theme.qcolor(t.text) if is_fg else None
        token = _NAMED.get(name)
        if token:
            if bold and is_fg and not token.startswith("ansi_bright_"):
                token = token.replace("ansi_", "ansi_bright_")  # bold text in bright colours, as xterm does
            return theme.qcolor(getattr(t, token))
        if len(name) == 6:
            return QColor("#" + name)
        return theme.qcolor(t.text) if is_fg else None

    # ---------------------------------------------------------------- painting
    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        vp = self.viewport()
        p = QPainter(vp)
        p.fillRect(vp.rect(), theme.qcolor(t.chrome))
        p.setFont(self._font)
        bold_font = theme.font_mono(weight=QFont.Weight.Bold)
        cw, chh = self._cell_w, self._cell_h
        first = self._first_row()
        sel = self._norm_sel()
        sel_color = theme.qcolor(t.selection)
        live_cursor = self._offset == 0 and not self.screen.cursor.hidden
        cx, cy = self.screen.cursor.x, self.screen.cursor.y
        for r in range(self.rows):
            abs_row = first + r
            line = self._line(abs_row)
            y = r * chh
            # Backgrounds and selection first, then text in runs of one style.
            run_text: List[str] = []
            run_x = 0
            run_style: Optional[Tuple] = None
            for c in range(self.cols):
                ch = line[c]
                fg, bg = self.colors_for(ch, t)
                is_cursor = live_cursor and abs_row - first == cy and c == cx
                selected = sel is not None and sel[0] <= (abs_row, c) <= sel[1]
                if is_cursor and self._cursor_on and self.hasFocus():
                    bg, fg = theme.qcolor(t.text_2), theme.qcolor(t.chrome)
                if bg is not None:
                    p.fillRect(QRectF(c * cw, y, cw, chh), bg)
                if selected:
                    p.fillRect(QRectF(c * cw, y, cw, chh), sel_color)
                if is_cursor and not self.hasFocus():
                    p.setPen(theme.qcolor(t.text_2))
                    p.drawRect(QRectF(c * cw + 0.5, y + 0.5, cw - 1, chh - 1))
                if not ch.data:  # the second cell of a wide character
                    self._draw_run(p, run_x, y, run_text, run_style, bold_font)
                    run_text, run_style = [], None
                    continue
                style = (fg, ch.bold, ch.italics, ch.underscore, ch.strikethrough)
                if style != run_style:
                    self._draw_run(p, run_x, y, run_text, run_style, bold_font)
                    run_text, run_x, run_style = [ch.data], c, style
                else:
                    run_text.append(ch.data)
            self._draw_run(p, run_x, y, run_text, run_style, bold_font)
        p.end()

    def _draw_run(
        self, p: QPainter, col: int, y: int, text: List[str], style: Optional[Tuple], bold_font: QFont
    ) -> None:
        """One run of cells sharing a style, drawn as a single string at the baseline."""
        if not text or style is None:
            return
        fg, bold, italic, underline, strike = style
        f = QFont(bold_font if bold else self._font)
        f.setItalic(italic)
        f.setUnderline(underline)
        f.setStrikeOut(strike)
        p.setFont(f)
        p.setPen(fg)
        p.drawText(QPointF(col * self._cell_w, y + self._ascent), "".join(text))

    # ---------------------------------------------------------------- scrolling
    def _sync_scrollbar(self) -> None:
        sb = self.verticalScrollBar()
        h = self._history_len()
        sb.blockSignals(True)
        sb.setRange(0, h)
        sb.setPageStep(self.rows)
        sb.setSingleStep(1)
        sb.setValue(h - self._offset)
        sb.blockSignals(False)

    def _on_scrollbar(self, v: int) -> None:
        self._offset = max(0, self._history_len() - v)
        self.viewport().update()

    def scroll_lines(self, n: int) -> None:
        """Positive scrolls up into the history, negative back towards the live screen."""
        self._offset = max(0, min(self._history_len(), self._offset + n))
        self._sync_scrollbar()
        self.viewport().update()

    def wheelEvent(self, e: QWheelEvent) -> None:  # noqa: N802
        steps = e.angleDelta().y() / 120.0
        self.scroll_lines(int(round(steps * 3)))
        e.accept()

    # ---------------------------------------------------------------- selection
    def _cell_at(self, pos: QPoint) -> Tuple[int, int]:
        col = max(0, min(self.cols - 1, int(pos.x() // self._cell_w)))
        row = max(0, min(self.rows - 1, int(pos.y() // self._cell_h)))
        return self._first_row() + row, col

    def _norm_sel(self) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        if self._sel is None or self._sel[0] == self._sel[1]:
            return None
        a, b = self._sel
        return (a, b) if a <= b else (b, a)

    def selected_text(self) -> str:
        sel = self._norm_sel()
        if sel is None:
            return ""
        (r0, c0), (r1, c1) = sel
        out = []
        for r in range(r0, r1 + 1):
            line = self._line(r)
            start = c0 if r == r0 else 0
            end = c1 if r == r1 else self.cols - 1
            out.append("".join(line[c].data for c in range(start, end + 1)).rstrip())
        return "\n".join(out)

    def clear_selection(self) -> None:
        self._sel = None
        self.viewport().update()

    def copy(self) -> None:
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)

    def paste(self) -> None:
        text = QGuiApplication.clipboard().text()
        if text:
            self.send(text.replace("\r\n", "\r").replace("\n", "\r").encode("utf-8"))

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self.setFocus()
        if e.button() == Qt.MouseButton.LeftButton:
            cell = self._cell_at(e.position().toPoint())
            self._sel = (cell, cell)
            self._selecting = True
            self.viewport().update()
        elif e.button() == Qt.MouseButton.MiddleButton:
            self.paste()
        elif e.button() == Qt.MouseButton.RightButton:
            show_menu_at(
                [
                    {"label": "Copy", "icon": "copy", "disabled": not self.selected_text(), "on_select": self.copy},
                    {"label": "Paste", "icon": "clipboard-paste", "on_select": self.paste},
                    {"separator": True},
                    {"label": "Clear selection", "on_select": self.clear_selection},
                ],
                e.globalPosition().toPoint(),
                self,
            )

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._selecting and self._sel is not None:
            self._sel = (self._sel[0], self._cell_at(e.position().toPoint()))
            self.viewport().update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._selecting = False
            if self._sel is not None:
                self._sel = (self._sel[0], self._cell_at(e.position().toPoint()))
                self.viewport().update()

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        row, col = self._cell_at(e.position().toPoint())
        line = self._line(row)
        text = "".join(line[c].data or " " for c in range(self.cols))
        if col >= len(text) or text[col].isspace():
            return
        a = col
        while a > 0 and not text[a - 1].isspace():
            a -= 1
        b = col
        while b + 1 < len(text) and not text[b + 1].isspace():
            b += 1
        self._sel = ((row, a), (row, b))
        self.viewport().update()

    # ---------------------------------------------------------------- keys and focus
    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        key, mods = e.key(), e.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(mods & Qt.KeyboardModifier.AltModifier)
        # The app's panel and session shortcuts go through instead of to the shell.
        if alt and not ctrl and Qt.Key.Key_1 <= key <= Qt.Key.Key_6:
            e.ignore()
            return
        if ctrl and alt and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            e.ignore()
            return
        if ctrl and shift and key == Qt.Key.Key_C:
            self.copy()
            return
        if (ctrl and shift and key == Qt.Key.Key_V) or (shift and key == Qt.Key.Key_Insert):
            self.paste()
            return
        if ctrl and key == Qt.Key.Key_C and self.selected_text():
            self.copy()
            self.clear_selection()
            return
        if shift and key == Qt.Key.Key_PageUp:
            self.scroll_lines(self.rows - 1)
            return
        if shift and key == Qt.Key.Key_PageDown:
            self.scroll_lines(-(self.rows - 1))
            return
        data = key_to_bytes(e, DECCKM in self.screen.mode)
        if data is None:
            super().keyPressEvent(e)
            return
        self.scroll_lines(-self._offset)
        self.send(data)
        e.accept()

    def event(self, e: QEvent) -> bool:
        # Tab must reach the shell rather than move focus.
        if e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.keyPressEvent(e)
            return True
        return super().event(e)

    def focusInEvent(self, e) -> None:  # noqa: N802
        super().focusInEvent(e)
        self._cursor_on = True
        if not store.reduced_motion:
            self._blink.start()
        self.viewport().update()

    def focusOutEvent(self, e) -> None:  # noqa: N802
        super().focusOutEvent(e)
        self._blink.stop()
        self._cursor_on = True
        self.viewport().update()

    def _toggle_cursor(self) -> None:
        self._cursor_on = not self._cursor_on
        self.viewport().update()


# ---------------------------------------------------------------- the panel


@dataclass
class TermRef:
    id: str
    title: str
    exited: bool = False


_per_session: Dict[str, List[TermRef]] = {}
_active_by_session: Dict[str, str] = {}


class TTab(QWidget):
    """One terminal tab in the toolbar: the title in the mono face and × to close."""

    activated = Signal(str)
    closed = Signal(str)

    def __init__(self, ref: TermRef, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ref = ref
        self.on = False
        self.text = ref.title
        self._hover = False
        self._x_hover = False
        self.setMouseTracking(True)
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(ref.title)
        self._font = theme.font_mono(12)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_state(self, on: bool, text: str) -> None:
        self.on, self.text = on, text
        self.setAccessibleName(text)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(9 + int(QFontMetricsF(self._font).horizontalAdvance(self.text)) + 4 + 18 + 4, 26)

    def _x_rect(self) -> QRect:
        return QRect(self.width() - 22, 4, 18, 18)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.ref.exited:
            p.setOpacity(0.55)
        if self.on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor(t.fill_2))
            p.drawRoundedRect(self.rect(), theme.R2, theme.R2)
        p.setFont(self._font)
        p.setPen(theme.qcolor(t.text if (self.on or self._hover) else t.text_3))
        p.drawText(QRect(9, 0, self.width() - 9 - 22, 26), Qt.AlignmentFlag.AlignVCenter, self.text)
        xr = self._x_rect()
        if self._x_hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor(t.fill_2))
            p.drawRoundedRect(xr, 4, 4)
        p.drawPixmap(xr.left() + 3, xr.top() + 3, icons.pixmap("x", t.text if self._x_hover else t.text_3, 11))

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = self._x_hover = False
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        xh = self._x_rect().contains(e.position().toPoint())
        if xh != self._x_hover:
            self._x_hover = xh
            self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.MiddleButton or (
            e.button() == Qt.MouseButton.LeftButton and self._x_rect().contains(e.position().toPoint())
        ):
            self.closed.emit(self.ref.id)
        elif e.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.ref.id)


class TerminalPanel(QWidget):
    """The Terminal tab of the side panel: the terminals opened from one session."""

    def __init__(self, session_id: str, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.session_id = session_id
        self.cwd = cwd
        self._views: Dict[str, TerminalView] = {}
        self._tabs: Dict[str, TTab] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.bar = Row(4, (12, 6, 10, 6))
        self.bar.setMinimumHeight(42)
        self.add_btn = IconButton("plus", "New terminal", 14)
        self.add_btn.clicked.connect(self.create)
        self.bar.add(self.add_btn)
        self.bar.stretch()
        root.addWidget(self.bar)
        root.addWidget(Hairline())
        self.host = QWidget()
        self.host.setObjectName("chrome")
        self.host.setAutoFillBackground(True)
        theme.tint(self.host, bg="chrome")
        self.host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hl = QVBoxLayout(self.host)
        hl.setContentsMargins(12, 8, 4, 4)
        self.stack = QStackedWidget()
        hl.addWidget(self.stack)
        root.addWidget(self.host, 1)
        self.empty = self._empty_state()
        root.addWidget(self.empty, 1)
        self.empty.hide()
        self._off_data = rpc.on("term.data", self._on_data)
        self._off_exit = rpc.on("term.exit", self._on_exit)
        self.destroyed.connect(lambda *_a: (self._off_data(), self._off_exit()))
        bind(store.info_changed, self, lambda _i: self.render())
        self.render()

    @staticmethod
    def _empty_state() -> QWidget:
        """One block at the top of the tab, like the other tabs' empty states: the icon, the sentence,
        and what to do about it."""
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(
            EmptyState(
                "square-terminal",
                "The terminal needs python3 (or util-linux `script`) on this machine.",
                "Install one and open the tab again; the rest of Pilcrow works without it.",
            )
        )
        lay.addStretch(1)
        return host

    # ---------------------------------------------------------------- state
    @property
    def terms(self) -> List[TermRef]:
        return _per_session.setdefault(self.session_id, [])

    @property
    def active(self) -> Optional[str]:
        return _active_by_session.get(self.session_id)

    def _pty_ok(self) -> bool:
        info = store.info
        return not (info and (info.get("terminal") or {}).get("pty") is False)

    def set_session(self, session: Json) -> None:
        self.session_id = str(session.get("id") or self.session_id)
        self.cwd = str(session.get("cwd") or session.get("project") or self.cwd)
        self.render()

    def view(self, tid: str) -> Optional[TerminalView]:
        return self._views.get(tid)

    # ---------------------------------------------------------------- terminals
    def create(self) -> None:
        sid, cwd = self.session_id, self.cwd

        def ok(r: Any) -> None:
            r = r or {}
            tid = str(r.get("id") or "")
            if not tid:
                return
            _per_session.setdefault(sid, []).append(TermRef(tid, basename(cwd) or cwd))
            _active_by_session[sid] = tid
            self.render()
            v = self._views.get(tid)
            if v is not None:
                v.setFocus()

        store.call("term.open", {"cwd": cwd, "cols": 100, "rows": 30}, quiet=True).then(ok).catch(
            lambda e: store.toast(err_text(e), "error")
        )

    def close_term(self, tid: str) -> None:
        store.call("term.close", {"id": tid}, quiet=True).catch(lambda _e: None)
        lst = [t for t in self.terms if t.id != tid]
        _per_session[self.session_id] = lst
        if lst:
            _active_by_session[self.session_id] = lst[-1].id
        else:
            _active_by_session.pop(self.session_id, None)
        v = self._views.pop(tid, None)
        if v is not None:
            self.stack.removeWidget(v)
            v.deleteLater()
        self.render()

    def _activate(self, tid: str) -> None:
        _active_by_session[self.session_id] = tid
        self.render()
        v = self._views.get(tid)
        if v is not None:
            v.setFocus()

    def _ensure_view(self, ref: TermRef) -> TerminalView:
        v = self._views.get(ref.id)
        if v is not None:
            return v
        v = TerminalView(100, 30)
        tid = ref.id
        v.on_output = lambda data, tid=tid: store.call(
            "term.input", {"id": tid, "data": data.decode("utf-8", "replace")}, quiet=True
        ).catch(lambda _e: None)
        v.resized.connect(
            lambda cols, rows, tid=tid: store.call(
                "term.resize", {"id": tid, "cols": cols, "rows": rows}, quiet=True
            ).catch(lambda _e: None)
        )
        self._views[tid] = v
        self.stack.addWidget(v)
        store.call("term.snapshot", {"id": tid}, quiet=True).then(
            lambda s: s and s.get("buffer") and v.feed(str(s["buffer"]))
        ).catch(lambda _e: None)
        return v

    def _on_data(self, d: Any) -> None:
        if isinstance(d, dict):
            v = self._views.get(str(d.get("id")))
            if v is not None:
                v.feed(str(d.get("data") or ""))

    def _on_exit(self, d: Any) -> None:
        if not isinstance(d, dict):
            return
        tid = str(d.get("id"))
        code = d.get("code")
        v = self._views.get(tid)
        if v is not None:
            v.feed(f"\r\n\x1b[2m[process exited{f' with code {code}' if code is not None else ''}]\x1b[0m\r\n")
        for lst in _per_session.values():
            for t in lst:
                if t.id == tid:
                    t.exited = True
        self.render()

    # ---------------------------------------------------------------- render
    def render(self) -> None:
        pty = self._pty_ok()
        self.empty.setVisible(not pty)
        self.host.setVisible(pty)
        self.bar.setVisible(pty)
        terms = self.terms
        ids = [t.id for t in terms]
        for tid in list(self._tabs):
            if tid not in ids:
                w = self._tabs.pop(tid)
                self.bar.box.removeWidget(w)
                w.deleteLater()
        for i, ref in enumerate(terms):
            w = self._tabs.get(ref.id)
            if w is None:
                w = TTab(ref)
                w.activated.connect(self._activate)
                w.closed.connect(self.close_term)
                self._tabs[ref.id] = w
                self.bar.box.insertWidget(i, w)
            w.set_state(
                ref.id == self.active,
                f"{ref.title}{f' {i + 1}' if len(terms) > 1 else ''}{' (exited)' if ref.exited else ''}",
            )
            self._ensure_view(ref)
        act = self.active
        if act and act in self._views:
            self.stack.setCurrentWidget(self._views[act])

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if not self.terms and self._pty_ok():
            self.create()
        act = self.active
        if act and act in self._views:
            self._views[act].setFocus()


if __name__ == "__main__":  # pragma: no cover - development window
    import sys

    from PySide6.QtWidgets import QApplication

    from ... import fonts

    app = QApplication(sys.argv)
    fonts.load()
    theme.theme.apply(True)
    store.init()
    rpc.connect_to(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5300/")
    panel = TerminalPanel("dev", sys.argv[2] if len(sys.argv) > 2 else ".")
    panel.setObjectName("panel")
    panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    panel.resize(760, 520)
    panel.show()
    sys.exit(app.exec())
