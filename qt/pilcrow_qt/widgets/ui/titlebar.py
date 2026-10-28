"""A title bar of our own, for a frameless window.

On Linux the window manager's bar is a grey strip that does not know the theme; the WebKit shell
drew its own (a GTK HeaderBar) and the Windows host too. This one is the same idea in Qt: the mark,
the title, the window buttons, dragging by the bar, double-click to maximise, and resizing from
the window's edges through the platform (``startSystemMove`` / ``startSystemResize``, which work on
X11 and Wayland alike).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ... import theme
from .bits import IconButton, Mark
from .signals import bind

HEIGHT = 38
EDGE = 6  # px from the window edge where a press starts a resize


class TitleBar(QWidget):
    def __init__(self, window: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._win = window
        self.setObjectName("titlebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(HEIGHT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(8)
        lay.addWidget(Mark(15))
        self.title = QLabel(window.windowTitle() or "Pilcrow")
        self.title.setObjectName("titlebar-title")
        lay.addWidget(self.title, 1)
        self.min_btn = IconButton("minus", "Minimise", 14)
        self.max_btn = IconButton("square", "Maximise", 12)
        self.close_btn = IconButton("x", "Close", 15)
        self.min_btn.clicked.connect(window.showMinimized)
        self.max_btn.clicked.connect(self.toggle_max)
        self.close_btn.clicked.connect(window.close)
        for b in (self.min_btn, self.max_btn, self.close_btn):
            b.setFixedSize(30, 26)
            lay.addWidget(b)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()
        window.installEventFilter(self)

    def _paint(self) -> None:
        t = theme.tokens()
        self.setStyleSheet(
            f"#titlebar {{ background: {t.chrome}; border-bottom: 1px solid {theme.blend(t.hair, t.chrome).name()}; }}"
            f"#titlebar-title {{ color: {t.text_2}; font-size: {theme.FS_SMALL}px; font-weight: 500; }}"
        )
        self.close_btn._color = ""
        self.close_btn._paint_icon()

    def toggle_max(self) -> None:
        if self._win.isMaximized():
            self._win.showNormal()
        else:
            self._win.showMaximized()

    # ---- dragging
    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.childAt(e.position().toPoint()) in (None, self.title):
            wh = self._win.windowHandle()
            if wh is not None and not self._win.isMaximized():
                wh.startSystemMove()
                return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggle_max()

    # ---- the window's title and state
    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        if obj is self._win:
            if e.type() == QEvent.Type.WindowTitleChange:
                self.title.setText(self._win.windowTitle())
            elif e.type() == QEvent.Type.WindowStateChange:
                self.max_btn.set_icon_name("copy" if self._win.isMaximized() else "square")
                self.max_btn.setToolTip("Restore" if self._win.isMaximized() else "Maximise")
        return False


class EdgeResizer(QObject):
    """Resizing a frameless window from its edges and corners, through the platform."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._win = window
        window.setMouseTracking(True)
        window.installEventFilter(self)

    def _edges(self, pos: QPoint) -> Qt.Edges:
        r: QRect = self._win.rect()
        edges = Qt.Edges()
        if pos.x() <= EDGE:
            edges |= Qt.Edge.LeftEdge
        if pos.x() >= r.width() - EDGE:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= EDGE:
            edges |= Qt.Edge.TopEdge
        if pos.y() >= r.height() - EDGE:
            edges |= Qt.Edge.BottomEdge
        return edges

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        if obj is not self._win or self._win.isMaximized():
            return False
        if e.type() == QEvent.Type.MouseMove:
            edges = self._edges(e.position().toPoint())
            self._win.setCursor(_cursor_for(edges))
        elif e.type() == QEvent.Type.MouseButtonPress and e.button() == Qt.MouseButton.LeftButton:
            edges = self._edges(e.position().toPoint())
            wh = self._win.windowHandle()
            if edges and wh is not None:
                wh.startSystemResize(edges)
                return True
        elif e.type() == QEvent.Type.Leave:
            self._win.unsetCursor()
        return False


def _cursor_for(edges: Qt.Edges) -> Qt.CursorShape:
    l, r = bool(edges & Qt.Edge.LeftEdge), bool(edges & Qt.Edge.RightEdge)
    t, b = bool(edges & Qt.Edge.TopEdge), bool(edges & Qt.Edge.BottomEdge)
    if (l and t) or (r and b):
        return Qt.CursorShape.SizeFDiagCursor
    if (r and t) or (l and b):
        return Qt.CursorShape.SizeBDiagCursor
    if l or r:
        return Qt.CursorShape.SizeHorCursor
    if t or b:
        return Qt.CursorShape.SizeVerCursor
    return Qt.CursorShape.ArrowCursor
