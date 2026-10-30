"""Find in the conversation (a port of web/src/components/FindBar.tsx) for the canvas.

The search runs over the item models (what the person said, what Claude wrote, the targets of its
steps), so a row that is cold still counts. Stepping to a match opens the fold around it, brings
the block into view and lights the words; the list refreshes as new output arrives; Enter / Shift+Enter
step, Escape closes.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget

from .. import icons, theme
from ..store import store
from ..widgets.ui.bits import IconButton
from ..widgets.ui.signals import bind

_last_query = ""
MAX_MATCHES = 3000


class FindBar(QWidget):
    def __init__(
        self, transcript, on_close: Optional[Callable[[], None]] = None, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.transcript = transcript
        self._on_close = on_close or (lambda: store.set_find_open(False))
        self.matches: List[Tuple[int, str, int]] = []  # (unit index, item id, offset)
        self.index = 0
        self.setAccessibleName("Find in conversation")
        self.setFixedHeight(38)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(11, 0, 5, 0)
        lay.setSpacing(2)
        self.ico = QLabel()
        self.ico.setFixedSize(14, 14)
        lay.addWidget(self.ico)
        lay.addSpacing(6)
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
        lay.addSpacing(6)
        lay.addWidget(self.count)
        lay.addSpacing(6)
        self.prev_btn = IconButton("chevron-up", "Previous match (Shift+Enter)", 14)
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn = IconButton("chevron-down", "Next match (Enter)", 14)
        self.next_btn.clicked.connect(lambda: self.step(1))
        self.close_btn = IconButton("x", "Close (Esc)", 14)
        self.close_btn.clicked.connect(self.close_bar)
        for b in (self.prev_btn, self.next_btn, self.close_btn):
            b.setFixedSize(24, 24)
            lay.addWidget(b)
        self._refresh = QTimer(self)
        self._refresh.setSingleShot(True)
        self._refresh.setInterval(250)
        self._refresh.timeout.connect(lambda: self.search(True))
        bind(store.rt_changed, self, lambda sid: sid == transcript.sid and self._refresh.start())
        bind(theme.theme.changed, self, lambda _t: self._paint())
        transcript.find_bar = self
        self._paint()
        if not store.find_open:
            store.set_find_open(True)
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
        self.index = min(self.index, max(0, len(found) - 1)) if keep_index else self._nearest()
        self._fill_count()
        self._paint_current(not keep_index)

    def _nearest(self) -> int:
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
        needle = self.field.text().strip()
        if not self.matches or not needle:
            self.transcript.set_needle("")
            return
        _unit, item_id, _off = self.matches[self.index]
        k = sum(1 for u, i, o in self.matches[: self.index] if i == item_id)
        if reveal:
            self.transcript.reveal_item(item_id)
        self.transcript.set_needle(needle, item_id, k)

    # ---- keys
    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if obj is self.field and e.type() == QEvent.Type.KeyPress:
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.step(-1 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
                return True
            if e.key() == Qt.Key.Key_Escape:
                self.close_bar()
                return True
        return super().eventFilter(obj, e)

    def close_bar(self) -> None:
        self._refresh.stop()
        if self.transcript.find_bar is self:
            self.transcript.find_bar = None
        self._on_close()

    # ---- looks
    def _paint(self) -> None:
        t = theme.tokens()
        self.ico.setPixmap(icons.pixmap("search", t.text_3, 14))
        self.setObjectName("findbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bg = "#252528" if t.dark else t.raised
        self.setStyleSheet(
            f"#findbar {{ background: {bg}; border: 1px solid {theme.blend(t.hair_2, bg).name()}; border-radius: 11px; }} "
            f"QLineEdit {{ font-size: {int(theme.FS_UI + 0.5)}px; color: {t.text}; background: transparent; border: none; padding: 0; "
            f"selection-background-color: {theme.blend(t.selection, bg).name()}; }}"
        )
        self._paint_count()

    def _paint_count(self) -> None:
        t = theme.tokens()
        color = t.err if self.count.property("none") else t.text_3
        self.count.setStyleSheet(f"QLabel {{ font-size: {int(theme.FS_SMALL + 0.5)}px; color: {color}; }}")
