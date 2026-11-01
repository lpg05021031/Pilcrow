"""The queue tray above the composer (``.queue`` in Composer.tsx): the messages held while Claude works,
each one editable, removable, or sent now."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import (
    QPointF,
    QRect,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QPainter,
    QPen,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import icons, theme
from ..store import store
from .ui.bits import IconButton
from .ui.button import Btn
from .ui.signals import bind

Json = Dict[str, Any]

# ---------------------------------------------------------------- the queue tray


class QueueTray(QFrame):
    """Messages waiting for the current step to finish (``.queue``). Each can be edited, sent right away, or dropped."""

    def __init__(self, session_id: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session_id
        self.held: List[Json] = []
        self._key: List[tuple] = []
        self.editing: Optional[str] = None
        self.setObjectName("queue")
        self.setAccessibleName("Queued messages")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(6, 6, 6, 4)
        self.lay.setSpacing(0)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(8, 2, 8, 6)
        hl.setSpacing(6)
        self.head_ico = QLabel()
        hl.addWidget(self.head_ico)
        self.head_text = QLabel()
        head_font = theme.font_ui(theme.FS_MICRO, 600)
        head_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.1)
        self.head_text.setFont(head_font)
        theme.tint(self.head_text, fg="text_3")
        hl.addWidget(self.head_text, 1)
        self.lay.addWidget(head)
        self.rows = QVBoxLayout()
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(0)
        self.lay.addLayout(self.rows)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.head_ico.setPixmap(icons.pixmap("clock-3", t.accent_text, 12))
        self.update()

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(QPen(theme.qcolor(t.hair), 1))
        p.setBrush(theme.mix(t.raised, 0.7, t.canvas))  # color-mix(in srgb, var(--raised) 70%, var(--canvas))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), theme.R3, theme.R3)

    def set_held(self, held: List[Json], force: bool = False) -> None:
        """Rebuild the rows when the queue changed (a rebuild while a row is being edited would lose the typing)."""
        key = [(h.get("uuid"), h.get("text"), h.get("images"), tuple(h.get("files") or [])) for h in held]
        if not force and key == self._key:
            return
        self._key = key
        self.held = held
        n = len(held)
        self.head_text.setText(
            "Queued — Claude reads it after this step"
            if n == 1
            else f"{n} queued — Claude reads them in order after this step"
        )
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for i, h in enumerate(held):
            self.rows.addWidget(QueueItem(self, h, i + 1, editing=self.editing == h.get("uuid")))
        self.setVisible(n > 0)

    def start_edit(self, uuid: str) -> None:
        self.editing = uuid
        self.set_held(self.held, force=True)

    def save(self, h: Json, text: str) -> None:
        self.editing = None
        t = text.strip()
        if not t and not h.get("images") and not (h.get("files") or []):
            store.remove_held(self.sid, h["uuid"]).catch(lambda _e: None)
        elif t != h.get("text"):
            store.update_held(self.sid, h["uuid"], t).catch(lambda _e: None)
        else:
            self.set_held(self.held, force=True)

    def cancel_edit(self) -> None:
        self.editing = None
        self.set_held(self.held, force=True)


class QueueAttach(QWidget):
    """``.queue-attach``: a small pill with an icon and a count or file name."""

    def __init__(self, icon: str, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._icon, self._text = icon, text
        fm = QFontMetrics(theme.font_ui(11))
        self.setFixedSize(6 + 11 + 3 + fm.horizontalAdvance(text) + 6, 18)

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.qcolor(t.fill_2))
        p.drawRoundedRect(QRectF(self.rect()), 9, 9)
        p.drawPixmap(QPointF(6, 3.5), icons.pixmap(self._icon, t.text_3, 11))
        p.setFont(theme.font_ui(11))
        p.setPen(QColor(t.text_3))
        p.drawText(QRect(20, 0, self.width() - 20, 18), Qt.AlignmentFlag.AlignVCenter, self._text)


class QueueItem(QWidget):
    """``.queue-item``: number, text (click to edit), attachment pills, and the actions under the pointer."""

    def __init__(self, tray: QueueTray, h: Json, n: int, editing: bool) -> None:
        super().__init__(tray)
        self.tray, self.h = tray, h
        self._editing = editing
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 6, 5)
        lay.setSpacing(8)
        num = QLabel(str(n))
        num.setFixedWidth(18)
        num.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        num.setFont(theme.font_ui(11, 600))
        num.setContentsMargins(0, 3, 0, 0)
        theme.tint(num, fg="text_4")
        lay.addWidget(num)
        if editing:
            self.edit = _QueueEdit(str(h.get("text") or ""), lambda text: tray.save(h, text), tray.cancel_edit)
            lay.addWidget(self.edit, 1)
            QTimer.singleShot(0, self.edit.setFocus)
        else:
            text = str(h.get("text") or "")
            lines = text.split("\n")
            shown = "\n".join(lines[:3]) + ("…" if len(lines) > 3 else "")
            self.text_btn = QPushButton(shown or "(no text)")
            self.text_btn.setFlat(True)
            self.text_btn.setToolTip("Click to edit")
            self.text_btn.setAccessibleName("Edit queued message")
            self.text_btn.setCursor(Qt.CursorShape.IBeamCursor)
            self.text_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.text_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self.text_btn.setStyleSheet(
                f"QPushButton {{ border: none; text-align: left; padding: 2px 0; font-size: {theme.FS_UI}px; background: transparent; }}"
            )
            theme.tint(self.text_btn, fg="text_4" if not text else "text_2")
            self.text_btn.clicked.connect(lambda: tray.start_edit(h["uuid"]))
            lay.addWidget(self.text_btn, 1)
            if (h.get("images") or 0) > 0:
                lay.addWidget(QueueAttach("image", str(h["images"])), 0, Qt.AlignmentFlag.AlignTop)
            for f in h.get("files") or []:
                lay.addWidget(QueueAttach("file", str(f)), 0, Qt.AlignmentFlag.AlignTop)
        self.actions = QWidget()
        al = QHBoxLayout(self.actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(2)
        self.now = Btn(
            "Now",
            icon="zap",
            kind="more",
            icon_size=12,
            tooltip="Send now — interrupts the current step",
            on_click=lambda: store.send_held_now(tray.sid, h["uuid"]),
        )
        self.now.override = {"fs": 11, "fw": 600}
        self.now.setAccessibleName("Send now")
        al.addWidget(self.now)
        self.x = IconButton("x", "Remove queued message", 13)
        self.x.setFixedSize(24, 24)
        self.x.setToolTip("Remove")
        self.x.clicked.connect(lambda: store.remove_held(tray.sid, h["uuid"]))
        al.addWidget(self.x)
        # the actions keep their room and show under the pointer (opacity 0 → 1 in CSS)
        for b in (self.now, self.x):
            sp = b.sizePolicy()
            sp.setRetainSizeWhenHidden(True)
            b.setSizePolicy(sp)
            b.setVisible(editing)
        lay.addWidget(self.actions, 0, Qt.AlignmentFlag.AlignTop)
        self.setMouseTracking(True)

    def enterEvent(self, e) -> None:
        super().enterEvent(e)
        self.now.show()
        self.x.show()
        self.update()

    def leaveEvent(self, e) -> None:
        super().leaveEvent(e)
        if not self._editing:
            self.now.hide()
            self.x.hide()
        self.update()

    def paintEvent(self, _e) -> None:
        if not (self.underMouse() or self._editing):
            return
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.qcolor(t.fill_1))
        p.drawRoundedRect(QRectF(self.rect()), theme.R2, theme.R2)


class _QueueEdit(QPlainTextEdit):
    """``.queue-edit``: an inset field; Enter saves, Escape cancels, so does leaving it."""

    def __init__(self, text: str, on_save: Callable[[str], None], on_cancel: Callable[[], None]) -> None:
        super().__init__()
        self._save, self._cancel = on_save, on_cancel
        self._done = False
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.document().setDocumentMargin(0)
        self.setStyleSheet(
            f"QPlainTextEdit {{ background: palette(base); border: 1px solid palette(dark);"
            f" border-radius: {theme.R1}px; padding: 2px 6px; font-size: {theme.FS_UI}px; }}"
            " QPlainTextEdit:focus { border-color: palette(link-visited); }"
        )
        self.setPlainText(text)
        lines = min(6, max(1, text.count("\n") + 1))
        self.setFixedHeight(max(24, lines * self.fontMetrics().lineSpacing() + 8))
        self.moveCursor(QTextCursor.MoveOperation.End)
        self.setAccessibleName("Queued message")

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self._finish(self.toPlainText())
            return
        if e.key() == Qt.Key.Key_Escape:
            if not self._done:
                self._done = True
                self._cancel()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        self._finish(self.toPlainText())

    def _finish(self, text: str) -> None:
        if self._done:
            return
        self._done = True
        self._save(text)
