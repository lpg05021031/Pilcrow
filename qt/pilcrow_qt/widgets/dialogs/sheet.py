"""The dialog shell (a port of web/src/components/ui/Dialog.tsx).

A :class:`Sheet` is a frameless QDialog laid over the window as a child widget: it fills the window
with the scrim and floats the ``#sheet`` material (styled by ``theme.stylesheet``) near the top,
centred, at the width the caller asks for. It is bound to one key of ``store.dialogs``: Escape, the
scrim and the close button set that key back to its "off" value, and the store routing then
dismisses the widget; closing through the store (a falsy value) dismisses it directly.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ... import icons, theme
from ...store import store
from ..ui.bits import IconButton, hairline
from ..ui.signals import bind
from .parts import Backdrop, Text, sheet_css

SHEET_TOP = 0.09  # the sheet sits 9% of the way down, like the web's `padding: 9vh`
SHEET_MAX_H = 860


class _BodyScroll(QScrollArea):
    """A scroll area whose size hint is its content's, so the sheet can be as tall as it needs."""

    def sizeHint(self) -> QSize:  # noqa: N802
        w = self.widget()
        if w is None:
            return super().sizeHint()
        return w.sizeHint()


def host_of(window: QWidget) -> QWidget:
    """Where overlays live: the main window's central widget (so they cover pages, not the frame)."""
    if isinstance(window, QMainWindow) and window.centralWidget() is not None:
        return window.centralWidget()
    return window


class Sheet(QDialog):
    def __init__(
        self,
        window: QWidget,
        key: str,
        off_value: Any = None,
        width: int = 560,
        title: Optional[str] = None,
        icon: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        host = parent or host_of(window)
        super().__init__(host)
        # A QDialog is a window by construction; dropping the flag makes it an ordinary child widget
        # that the window's own grab() and layout see, while keeping QDialog's key handling.
        self.setWindowFlags(Qt.WindowType.Widget)
        self.key = key
        self.off_value = off_value
        self.window_ = window
        self._host = host
        self._width = width
        self._closed = False
        self._prev_focus: Optional[QWidget] = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setStyleSheet("QDialog { background: transparent; }")

        self.frame = QFrame(self)
        self.frame.setObjectName("sheet")
        self.frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.frame.setStyleSheet(sheet_css(theme.tokens()))
        bind(theme.theme.changed, self.frame, lambda t: self.frame.setStyleSheet(sheet_css(t)))
        fl = QVBoxLayout(self.frame)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(0)

        self.head: Optional[QWidget] = None
        self.title_lb: Optional[QLabel] = None
        if title is not None:
            self.head = QWidget()
            self.head.setFixedHeight(48)
            hl = QHBoxLayout(self.head)
            hl.setContentsMargins(22, 18, 16, 6)
            hl.setSpacing(10)
            if icon:
                ico = QLabel()
                ico.setPixmap(icons.pixmap(icon, theme.tokens().text_2, 15))
                hl.addWidget(ico)
            self.title_lb = Text(title, theme.FS_H, 600, wrap=False)
            hl.addWidget(self.title_lb, 1)
            self.close_btn = IconButton("x", "Close (Esc)", 15)
            self.close_btn.setFixedSize(24, 24)
            self.close_btn.clicked.connect(self.reject)
            hl.addWidget(self.close_btn)
            fl.addWidget(self.head)

        self.scroll = _BodyScroll()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(22, 10, 22, 20)
        self.body_lay.setSpacing(18)
        self.scroll.setWidget(self.body)
        fl.addWidget(self.scroll, 1)

        self.foot_line = hairline()
        self.foot_line.hide()
        fl.addWidget(self.foot_line)
        self.foot = QWidget()
        self.foot_lay = QHBoxLayout(self.foot)
        self.foot_lay.setContentsMargins(22, 14, 18, 16)
        self.foot_lay.setSpacing(8)
        self.foot.hide()
        fl.addWidget(self.foot)

        host.installEventFilter(self)
        self.setGeometry(host.rect())
        bind(theme.theme.changed, self, lambda _t: self.update())

    # ---- building
    def add_footer(self, *widgets: QWidget, stretch_before: bool = True) -> None:
        """Put buttons in the footer; ``stretch_before`` keeps them at the right edge."""
        if stretch_before and self.foot_lay.count() == 0:
            self.foot_lay.addStretch(1)
        for w in widgets:
            self.foot_lay.addWidget(w)
        self.foot.show()
        self.foot_line.show()

    def set_body_margins(self, left: int, top: int, right: int, bottom: int, spacing: Optional[int] = None) -> None:
        self.body_lay.setContentsMargins(left, top, right, bottom)
        if spacing is not None:
            self.body_lay.setSpacing(spacing)

    # ---- showing and leaving
    def present(self) -> None:
        """Show over the window and take the keyboard."""
        self._prev_focus = QApplication.focusWidget()
        self.setGeometry(self._host.rect())
        self.backdrop = Backdrop(self._host)
        self.show()
        self.raise_()
        self.place()
        top = self.window()
        if top is not None and not top.isActiveWindow():
            top.activateWindow()
        self.focus_first()

    def place(self) -> None:
        """Size the sheet to its content (capped to the window) and centre it near the top."""
        host = self._host
        w = max(280, min(self._width, host.width() - 40))
        self.frame.setFixedWidth(w)
        lay = self.body.layout()
        inner_w = w - 2
        body_h = (
            lay.heightForWidth(inner_w)
            if lay is not None and lay.hasHeightForWidth()
            else self.body.sizeHint().height()
        )
        h = body_h + 2
        if self.head is not None:
            h += self.head.sizeHint().height()
        if self.foot.isVisibleTo(self.frame):
            h += self.foot.sizeHint().height() + 1
        h = min(h, int(host.height() * 0.88), SHEET_MAX_H, max(120, host.height() - 40))
        x = (host.width() - w) // 2
        y = min(int(host.height() * SHEET_TOP), max(0, host.height() - h))
        self.frame.setGeometry(x, y, w, h)

    def focus_first(self) -> None:
        """Like the web: the field marked autofocus, else the first field, else the primary button."""
        for w in self.frame.findChildren(QWidget):
            if w.property("autofocus") and w.isVisibleTo(self.frame):
                w.setFocus()
                return
        for cls in (QLineEdit, QPlainTextEdit):
            for w in self.frame.findChildren(cls):
                if w.isVisibleTo(self.frame) and w.isEnabled():
                    w.setFocus()
                    return
        for b in self.frame.findChildren(QPushButton):
            if b.isDefault() and b.isVisibleTo(self.frame):
                b.setFocus()
                return
        self.setFocus()

    def dismiss(self) -> None:
        """Leave without touching the store (the store already says the dialog is gone)."""
        self._closed = True
        self.hide()
        prev = self._prev_focus
        if prev is not None and not prev.isHidden():
            try:
                prev.setFocus()
            except RuntimeError:
                pass
        self.deleteLater()

    def reject(self) -> None:  # noqa: N802
        if self._closed:
            return
        self._closed = True
        store.set_dialog(self.key, self.off_value)
        self.dismiss()

    def accept(self) -> None:  # noqa: N802
        self.reject()

    def submit(self) -> None:
        """Ctrl+Enter inside the sheet; dialogs with a primary action override it."""

    # ---- events
    def event(self, e: QEvent) -> bool:
        # Take Escape and Enter away from the window's shortcuts while a sheet is up.
        if e.type() == QEvent.Type.ShortcutOverride and e.key() in (
            Qt.Key.Key_Escape,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ):
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.submit()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if not self.frame.geometry().contains(e.position().toPoint()):
            self.reject()
            return
        super().mousePressEvent(e)

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        if obj is self._host and e.type() == QEvent.Type.Resize and not self._closed:
            self.setGeometry(self._host.rect())
            self.place()
        return False

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        bd = getattr(self, "backdrop", None)
        if bd is not None:
            bd.paint(p, self)
        else:
            p.fillRect(self.rect(), theme.qcolor(theme.tokens().scrim))
