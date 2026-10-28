"""Find in the conversation (a port of web/src/components/FindBar.tsx).

The search runs over the item models rather than the widgets (what the person said, what Claude
wrote, the targets of its steps), so a row in a page that is not drawn yet still counts. Stepping
to a match draws its page, opens the fold around it, brings the row into view and washes it in the
accent. The list refreshes as new output arrives; Enter / Shift+Enter step, Escape closes.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget

from ... import theme
from ...store import store
from ..ui.bits import IconButton
from ..ui.signals import bind
from .items import icon_label, pt, set_icon, style_label
from .view import Transcript

_last_query = ""
MAX_MATCHES = 3000


class Highlight(QWidget):
    """A wash over the row of the current match, mouse-transparent."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.tokens().color("accent_wash"))
        p.drawRoundedRect(self.rect(), theme.R2, theme.R2)


class FindBar(QWidget):
    def __init__(
        self, transcript: Transcript, on_close: Optional[Callable[[], None]] = None, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.transcript = transcript
        self._on_close = on_close or (lambda: store.set_find_open(False))
        self.matches: List[Tuple[int, str, int]] = []  # (unit index, item id, offset)
        self.index = 0
        self._row: Optional[QWidget] = None
        self.setAccessibleName("Find in conversation")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 6, 6)
        lay.setSpacing(6)
        self.ico = icon_label("search", theme.tokens().text_3, 14)
        lay.addWidget(self.ico)
        self.field = QLineEdit()
        self.field.setProperty("quiet", "true")
        self.field.setPlaceholderText("Find in conversation")
        self.field.setClearButtonEnabled(False)
        self.field.setAccessibleName("Find in conversation")
        self.field.setText(_last_query)
        self.field.textChanged.connect(lambda _t: self.search(False))
        self.field.installEventFilter(self)
        lay.addWidget(self.field, 1)
        self.count = QLabel()
        lay.addWidget(self.count)
        self.prev_btn = IconButton("chevron-up", "Previous match (Shift+Enter)", 14)
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn = IconButton("chevron-down", "Next match (Enter)", 14)
        self.next_btn.clicked.connect(lambda: self.step(1))
        self.close_btn = IconButton("x", "Close (Esc)", 14)
        self.close_btn.clicked.connect(self.close_bar)
        for b in (self.prev_btn, self.next_btn, self.close_btn):
            lay.addWidget(b)
        self.highlight = Highlight(transcript.widget())
        self._refresh = QTimer(self)
        self._refresh.setSingleShot(True)
        self._refresh.setInterval(250)
        self._refresh.timeout.connect(lambda: self.search(True))
        bind(store.rt_changed, self, lambda sid: sid == transcript.sid and self._refresh.start())
        bind(theme.theme.changed, self, lambda _t: self._paint())
        transcript.find_bar = self
        self._paint()
        if not store.find_open:
            store.set_find_open(True)  # draws every page, so every row can be found
        self.search(False)
        self.field.selectAll()
        self.field.setFocus()

    def setFocus(self) -> None:  # type: ignore[override]
        self.field.selectAll()
        self.field.setFocus()

    # ---- searching
    def query(self) -> str:
        return self.field.text()

    def search(self, keep_index: bool) -> None:
        global _last_query
        _last_query = self.field.text()
        needle = self.field.text().strip().lower()
        found: List[Tuple[int, str, int]] = []
        if needle:
            for unit, item_id, text in self.transcript.find_entries():
                t = text.lower()
                i = t.find(needle)
                while i >= 0 and len(found) < MAX_MATCHES:
                    found.append((unit, item_id, i))
                    i = t.find(needle, i + len(needle))
                if len(found) >= MAX_MATCHES:
                    break
        self.matches = found
        if keep_index:
            nxt = min(self.index, max(0, len(found) - 1))
        else:
            nxt = self._nearest()
        self.index = nxt
        self._fill_count()
        self._paint_current(not keep_index)

    def _nearest(self) -> int:
        """Start from the first match at or below the top of the visible part of the conversation."""
        if not self.matches:
            return 0
        top = self.transcript.first_visible_unit()
        for i, (unit, _id, _o) in enumerate(self.matches):
            if unit >= top:
                return i
        return len(self.matches) - 1

    def step(self, d: int) -> None:
        n = len(self.matches)
        if not n:
            return
        self.index = (self.index + d + n) % n
        self._fill_count()
        self._paint_current(True)

    def _fill_count(self) -> None:
        q = self.field.text().strip()
        n = len(self.matches)
        self.count.setText("" if not q else f"{self.index + 1} of {n}" if n else "No matches")
        self.count.setProperty("none", bool(q) and not n)
        self.prev_btn.setEnabled(n > 0)
        self.next_btn.setEnabled(n > 0)
        self._paint_count()

    def _paint_current(self, reveal: bool) -> None:
        if not self.matches:
            self.highlight.hide()
            self._row = None
            return
        _unit, item_id, _off = self.matches[self.index]
        row = self.transcript.reveal_item(item_id)
        if row is None:
            self.highlight.hide()
            return
        self._row = row
        self._place_highlight()
        if reveal:
            # a row in a page far off screen has no layout yet: place it after the layout has run
            QTimer.singleShot(0, lambda: (self._place_highlight(), self.transcript.scroll_to_row(row)))
            self.transcript.scroll_to_row(row)

    def _place_highlight(self) -> None:
        row = self._row
        if row is None:
            self.highlight.hide()
            return
        pos = row.mapTo(self.transcript.widget(), QPoint(0, 0))
        self.highlight.setGeometry(pos.x() - 6, pos.y() - 2, row.width() + 12, row.height() + 4)
        self.highlight.show()
        self.highlight.raise_()

    # ---- keys
    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent

        if obj is self.field and e.type() == QEvent.Type.KeyPress:
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.step(-1 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
                return True
            if e.key() == Qt.Key.Key_Escape:
                self.close_bar()
                return True
        return super().eventFilter(obj, e)

    def close_bar(self) -> None:
        self.highlight.hide()
        self.highlight.deleteLater()
        if self.transcript.find_bar is self:
            self.transcript.find_bar = None
        self._on_close()

    # ---- looks
    def _paint(self) -> None:
        t = theme.tokens()
        set_icon(self.ico, "search", t.text_3, 14)
        self.setObjectName("findbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"#findbar {{ background: {t.chrome}; border-bottom: 1px solid {theme.blend(t.hair, t.chrome).name()}; }} QLineEdit {{ font-size: {pt(theme.FS_UI)}; }}"
        )
        self._paint_count()

    def _paint_count(self) -> None:
        t = theme.tokens()
        style_label(self.count, theme.FS_SMALL, t.err if self.count.property("none") else t.text_3)
