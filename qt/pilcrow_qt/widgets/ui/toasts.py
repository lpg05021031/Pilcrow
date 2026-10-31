"""Toasts (web/src/components/Overlays.tsx Toasts): short messages that sit just above the composer,
bottom right, and leave on their own; hovering keeps one.

Each toast is the web's ``.toast``: the glass material on 12px corners, a 15px icon in the level's
colour, 12px text, a ``×n`` pill when the same message repeats, an optional action and the close
button. They hug their text (380px at most) and stack on the right edge, 8px apart.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QSystemTrayIcon, QVBoxLayout, QWidget

from ... import icons, theme
from ...store import Toast, store
from ..dialogs.parts import Text, toast_bg
from .bits import IconButton, Pill, button, paint_box
from .signals import bind

LEVEL_ICON = {"info": "info", "success": "circle-check", "warning": "triangle-alert", "error": "circle-alert"}
MAX_W = 380


class Toasts(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(8)
        self.lay.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        self.hide()

    def refresh(self) -> None:
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for t in store.toasts:
            card = ToastCard(t)
            self.lay.addWidget(card, 0, Qt.AlignmentFlag.AlignRight)
            card.show()  # a layout shows a new child on the next event-loop turn; the height is needed now
        self.setVisible(bool(store.toasts))
        self.reposition()
        self.raise_()

    def _fit_cards(self) -> None:
        """Each card takes the width its words want (up to the column) and the height they wrap to."""
        for i in range(self.lay.count()):
            card = self.lay.itemAt(i).widget()
            if isinstance(card, ToastCard):
                card.fit(self.width())

    def reposition(self) -> None:
        p = self.parentWidget()
        if p is None:
            return
        bottom = getattr(p.window(), "composer_bottom_inset", lambda: 0)()
        self.setFixedWidth(min(MAX_W, max(120, p.width() - 36)))
        self._fit_cards()
        # The height is the column's own sum, read after the layout's cached hint is dropped (the
        # cards' sizes just changed). Reading it before the cards were shown left a rebuilt column
        # 0px tall, its toasts unseen until the window was resized.
        self.lay.invalidate()
        self.lay.activate()
        self.setFixedHeight(max(0, self.lay.sizeHint().height()))
        self.move(QPoint(p.width() - self.width() - 16, p.height() - self.height() - bottom - 12))


class ToastCard(QWidget):
    """``.toast``: icon, the text, the repeat count, an action and ×."""

    def __init__(self, t: Toast, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.toast = t
        self.setObjectName("toast")
        self.setMinimumHeight(40)
        self.setMaximumWidth(MAX_W)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(13, 9, 6, 9)
        lay.setSpacing(10)
        self.ico = QLabel()
        self.ico.setFixedSize(15, 15)
        lay.addWidget(self.ico, 0, Qt.AlignmentFlag.AlignTop)
        self.text = Text(t.text, theme.FS_SMALL, 400, "text", 1.45)
        self.text.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.text, 1)
        if t.count > 1:
            self.count = Pill(f"×{t.count}", bg=lambda tk: theme.over(theme.qcolor(tk.fill_2), toast_bg(tk)), radius=9)
            self.count.setContentsMargins(6, 0, 6, 0)
            self.count.setFont(theme.font_ui(10.5))
            theme.tint(self.count, fg="text_3")
            lay.addWidget(self.count, 0, Qt.AlignmentFlag.AlignVCenter)
        if t.action:
            b = button(str(t.action.get("label", "")), kind="ghost", size="sm")
            b.clicked.connect(lambda: (t.action["run"](), store.dismiss_toast(t.id)))
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        x = IconButton("x", "Dismiss", 13)
        x.setFixedSize(24, 24)
        x.clicked.connect(lambda: store.dismiss_toast(t.id))
        lay.addWidget(x, 0, Qt.AlignmentFlag.AlignTop)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def fit(self, column: int) -> None:
        """Size to the words: as wide as one line wants (at most the column), as tall as they wrap to."""
        lay = self.layout()
        w = min(column, lay.sizeHint().width())
        self.setFixedWidth(w)
        self.setFixedHeight(
            max(
                self.minimumHeight(), lay.totalHeightForWidth(w) if lay.hasHeightForWidth() else lay.sizeHint().height()
            )
        )

    def _paint(self) -> None:
        tk = theme.tokens()
        color = {"info": tk.text_3, "success": tk.ok, "warning": tk.accent_text, "error": tk.err}.get(
            self.toast.level, tk.text_3
        )
        self.ico.setPixmap(icons.pixmap(LEVEL_ICON.get(self.toast.level, "info"), color, 15))
        self.ico.setContentsMargins(0, 2, 0, 0)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        tk = theme.tokens()
        bg = toast_bg(tk)
        p = QPainter(self)
        paint_box(p, QRectF(self.rect()), bg, theme.over(theme.qcolor(tk.hair_2), bg), 12)

    def sizeHint(self):  # noqa: N802
        s = super().sizeHint()
        # hug the text: the layout's hint, but never wider than the column
        from PySide6.QtCore import QSize

        return QSize(min(MAX_W, s.width()), max(40, s.height()))

    def enterEvent(self, e) -> None:  # noqa: N802
        store.hold_toast(self.toast.id)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        store.release_toast(self.toast.id)
        super().leaveEvent(e)


_tray: Optional[QSystemTrayIcon] = None
_tray_cb: Optional[Callable[[], None]] = None


def system_notify(title: str, body: str, on_click: Optional[Callable[[], None]] = None) -> None:
    """A desktop notification through the tray (the portable way in Qt); silent where there is no tray."""
    global _tray, _tray_cb
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return
    if _tray is None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        _tray = QSystemTrayIcon(app.windowIcon() if app else None)
        _tray.setToolTip("Pilcrow")
        _tray.messageClicked.connect(lambda: _tray_cb and _tray_cb())
        _tray.show()
    _tray_cb = on_click
    _tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.NoIcon, 6000)
