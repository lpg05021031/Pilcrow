"""A title bar of our own, for a frameless window.

On Linux the window manager's bar is a grey strip that does not know the theme; the WebKit shell
drew its own (a GTK HeaderBar) and the Windows host too. This one is the same idea in Qt:
the title, the window buttons, dragging by the bar, double-click to maximise, and resizing from
the window's edges through the platform (``startSystemMove`` / ``startSystemResize``, which work on
X11 and Wayland alike).

It is one surface with the sidebar: the same chrome, no line under it, no title (the session's name is
in the sheet's own header), the window buttons as quiet as the sidebar's own icons. Its top corners
are rounded while the window is not maximised: the window is translucent (``RoundedFrame``) and the
bar and the ground paint the chrome inside that shape.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QMouseEvent, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QWidget

from ... import theme
from ..ui.signals import bind
from .bits import IconButton

HEIGHT = 38
EDGE = 6  # px from the window edge where a press starts a resize (8 at a corner, which is harder to hit)
CORNER = 8
RADIUS = 10  # the window's top corners


def rounded_top(w: float, h: float, r: float) -> QPainterPath:
    """A rectangle with its two top corners rounded by ``r``."""
    p = QPainterPath()
    p.moveTo(0, h)
    p.lineTo(0, r)
    p.quadTo(0, 0, r, 0)
    p.lineTo(w - r, 0)
    p.quadTo(w, 0, w, r)
    p.lineTo(w, h)
    p.closeSubpath()
    return p


def corner_radius(win: QWidget) -> int:
    """The top corners' radius: none while maximised or full screen (the window meets the screen's edges)."""
    return 0 if (win.isMaximized() or win.isFullScreen()) else RADIUS


class TitleBar(QWidget):
    def __init__(self, window: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._win = window
        self.setObjectName("titlebar")
        self.setFixedHeight(HEIGHT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 8, 0)
        self._lay = lay
        lay.setSpacing(9)
        lay.addStretch(1)
        self.min_btn = IconButton("minus", "Minimise", 14, "text_3", box=26, width=32, over="chrome")
        self.max_btn = IconButton("square", "Maximise", 12, "text_3", box=26, width=32, over="chrome")
        self.close_btn = IconButton("x", "Close", 15, "text_3", box=26, width=32, over="chrome")
        self.min_btn.clicked.connect(window.showMinimized)
        self.max_btn.clicked.connect(self.toggle_max)
        self.close_btn.clicked.connect(window.close)
        for b in (self.min_btn, self.max_btn, self.close_btn):
            lay.addWidget(b)
        # painted here, in the window's rounded shape, not filled square by the palette
        self.setAutoFillBackground(False)
        bind(theme.theme.changed, self, lambda _t: self.update())
        window.installEventFilter(self)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillPath(rounded_top(self.width(), self.height(), corner_radius(self._win)), QColor(theme.tokens().chrome))

    def toggle_max(self) -> None:
        if self._win.isMaximized():
            self._win.showNormal()
        else:
            self._win.showMaximized()

    # ---- dragging
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.childAt(e.position().toPoint()) is None:
            wh = self._win.windowHandle()
            if wh is not None and not self._win.isMaximized():
                wh.startSystemMove()
                return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggle_max()

    # ---- the window's state
    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if obj is self._win and e.type() == QEvent.Type.WindowStateChange:
            self.max_btn.set_icon_name("copy" if self._win.isMaximized() else "square")
            self.max_btn.setToolTip("Restore" if self._win.isMaximized() else "Maximise")
            self.update()  # the corners square off when maximised and round again after
        return False


class EdgeResizer(QObject):
    """Resizing a frameless window from its edges and corners, through the platform.

    It watches the native window (``QWindow``), not the widget: every pointer move reaches the native
    window before Qt hands it to the widget under the pointer, whereas a widget sees a move only when
    no child under the pointer takes it (a list, the conversation, the sidebar's rows all do). So the
    edge is known wherever the pointer is. Near an edge the cursor is an override, which no child's
    own cursor can replace; a press there starts the platform's resize and goes no further."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._win = window
        self._shape: Optional[Qt.CursorShape] = None  # the override in force, if any
        self._handle = None
        window.installEventFilter(self)
        self._attach()

    def _attach(self) -> None:
        wh = self._win.windowHandle()
        if wh is not None and wh is not self._handle:
            wh.installEventFilter(self)
            self._handle = wh

    def _edges(self, pos: QPointF) -> Qt.Edges:
        w, h = self._win.width(), self._win.height()
        x, y = pos.x(), pos.y()
        near_l, near_r = x < CORNER, x >= w - CORNER
        near_t, near_b = y < CORNER, y >= h - CORNER
        edges = Qt.Edges()
        # at a corner the zone is a little larger, along both edges
        if x < EDGE or (near_l and (near_t or near_b)):
            edges |= Qt.Edge.LeftEdge
        if x >= w - EDGE or (near_r and (near_t or near_b)):
            edges |= Qt.Edge.RightEdge
        if y < EDGE or (near_t and (near_l or near_r)):
            edges |= Qt.Edge.TopEdge
        if y >= h - EDGE or (near_b and (near_l or near_r)):
            edges |= Qt.Edge.BottomEdge
        return edges

    def _set_cursor(self, edges: Qt.Edges) -> None:
        shape = _cursor_for(edges) if edges else None
        if shape == self._shape:
            return
        if shape is None:
            QGuiApplication.restoreOverrideCursor()
        elif self._shape is None:
            QGuiApplication.setOverrideCursor(QCursor(shape))
        else:
            QGuiApplication.changeOverrideCursor(QCursor(shape))
        self._shape = shape

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        t = e.type()
        if obj is self._win:
            if t in (QEvent.Type.Show, QEvent.Type.WinIdChange):
                self._attach()
            elif t == QEvent.Type.WindowStateChange and (self._win.isMaximized() or self._win.isFullScreen()):
                self._set_cursor(Qt.Edges())
            return False
        if obj is not self._handle:
            return False
        if self._win.isMaximized() or self._win.isFullScreen():
            return False
        if t == QEvent.Type.MouseMove:
            if e.buttons() == Qt.MouseButton.NoButton or self._shape is not None:
                self._set_cursor(self._edges(e.position()))
        elif t == QEvent.Type.MouseButtonPress and e.button() == Qt.MouseButton.LeftButton:
            edges = self._edges(e.position())
            if edges and self._handle.startSystemResize(edges):
                return True
        elif t == QEvent.Type.MouseButtonRelease:
            self._set_cursor(self._edges(e.position()))
        elif t == QEvent.Type.Leave:
            self._set_cursor(Qt.Edges())
        return False


def _cursor_for(edges: Qt.Edges) -> Qt.CursorShape:
    left, right = bool(edges & Qt.Edge.LeftEdge), bool(edges & Qt.Edge.RightEdge)
    t, b = bool(edges & Qt.Edge.TopEdge), bool(edges & Qt.Edge.BottomEdge)
    if (left and t) or (right and b):
        return Qt.CursorShape.SizeFDiagCursor
    if (right and t) or (left and b):
        return Qt.CursorShape.SizeBDiagCursor
    if left or right:
        return Qt.CursorShape.SizeHorCursor
    if t or b:
        return Qt.CursorShape.SizeVerCursor
    return Qt.CursorShape.ArrowCursor
