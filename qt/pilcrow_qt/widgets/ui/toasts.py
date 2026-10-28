"""Toasts (web/src/components/Overlays.tsx Toasts): short messages that sit just above the composer,
bottom right, and leave on their own; hovering keeps one."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSystemTrayIcon, QVBoxLayout, QWidget

from ... import icons, theme
from ...store import Toast, store

LEVEL_ICON = {"info": "info", "success": "circle-check", "warning": "triangle-alert", "error": "circle-alert"}


class Toasts(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(8)
        self.lay.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        self.setFixedWidth(380)
        self.hide()

    def refresh(self) -> None:
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for t in store.toasts:
            self.lay.addWidget(ToastCard(t))
        self.setVisible(bool(store.toasts))
        self.adjustSize()
        self.reposition()
        self.raise_()

    def reposition(self) -> None:
        p = self.parentWidget()
        if p is None:
            return
        bottom = getattr(p.window(), "composer_bottom_inset", lambda: 16)()
        self.adjustSize()
        self.move(QPoint(p.width() - self.width() - 18, p.height() - self.height() - bottom - 12))


class ToastCard(QWidget):
    def __init__(self, t: Toast, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.toast = t
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tk = theme.tokens()
        color = {"info": tk.text_2, "success": tk.ok, "warning": tk.warn, "error": tk.err}.get(t.level, tk.text_2)
        self.setStyleSheet(
            f"ToastCard {{ background: {theme.blend(tk.glass, tk.canvas).name()}; border: 1px solid {theme.blend(tk.hair_2, tk.raised).name()}; border-radius: {theme.R3}px; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 10, 10)
        lay.setSpacing(10)
        ico = QLabel()
        ico.setPixmap(icons.pixmap(LEVEL_ICON.get(t.level, "info"), color, 15))
        ico.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(ico)
        text = QLabel(t.text + (f"  ×{t.count}" if t.count > 1 else ""))
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(text, 1)
        if t.action:
            b = QPushButton(str(t.action.get("label", "")))
            b.setProperty("size", "sm")
            b.clicked.connect(lambda: (t.action["run"](), store.dismiss_toast(t.id)))
            lay.addWidget(b)
        x = QPushButton()
        x.setProperty("icon-only", "true")
        x.setIcon(icons.icon("x", tk.text_3, 12, hover=tk.text))
        x.setFixedSize(22, 22)
        x.clicked.connect(lambda: store.dismiss_toast(t.id))
        lay.addWidget(x, 0, Qt.AlignmentFlag.AlignTop)

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
