"""The send button (``.send``): the composer's and the welcome page's, one round button."""

from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QPushButton, QWidget

from ... import icons, theme
from ...canvas.clock import Tween
from .bits import Spinner, rounded_path
from .signals import bind


class SendButton(QPushButton):
    """``.send``: one round button. Empty = quiet outline, typed = orange arrow (it arrives on the
    bouncy spring, and launches when a message goes), working = stop square (the glyphs morph on the
    snappy spring, the colours cross over in 180ms). ``verb`` names it ("Send" in the composer, "Start"
    on the welcome page); ``busy`` turns a spinner in the arrow's place while a session is made."""

    def __init__(self, verb: str = "Send", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.verb = verb
        self.state = "idle"
        self.spinner = Spinner(14, parent=self)
        self.spinner.move(9, 9)
        self.spinner.hide()
        self._hover = False
        self._pressed = False
        self.setFixedSize(32, 33)  # one pixel under the circle for its drop shadow
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QPushButton { border: none; background: transparent; padding: 0; }")
        self._bg_from = QColor(0, 0, 0, 0)
        self._bg_to = QColor(0, 0, 0, 0)
        self._ink_from = QColor(0, 0, 0, 0)
        self._ink_to = QColor(0, 0, 0, 0)
        self.bg_t = Tween(lambda _v: self.update(), ms=theme.T_MED, curve=theme.ease)
        self.stop_t = Tween(lambda _v: self.update(), ms=theme.SPRING_SNAPPY_T, curve=theme.spring_snappy)
        self.ready_t = Tween(lambda _v: self.update(), ms=theme.SPRING_BOUNCY_T, curve=theme.spring_bouncy)
        self.ready_t.jump(1.0)
        self.launch_t = Tween(lambda _v: self.update(), ms=460, curve=theme.ease)
        bind(theme.theme.changed, self, lambda _t: self._retarget(jump=True))
        self.set_state(False, False, True)
        self._retarget(jump=True)

    def set_state(self, ready: bool, stop: bool = False, send_with_enter: bool = True, busy: bool = False) -> None:
        prev = self.state
        self.state = "stop" if stop else "ready" if ready else "idle"
        tip = "Stop (Esc)" if stop else f"{self.verb} (Enter)" if send_with_enter else f"{self.verb} (Ctrl+Enter)"
        spin = busy != self.spinner.isVisibleTo(self)
        self.spinner.setVisible(busy)
        if self.state == prev and tip == self.toolTip() and not spin:
            return
        self.setEnabled(ready or stop)
        self.setToolTip(tip)
        self.setAccessibleName("Stop" if stop else self.verb)
        if self.state != prev:
            if self.state == "ready":
                self.ready_t.jump(0.0)
                self.ready_t.go(1.0)
            self.stop_t.go(1.0 if self.state == "stop" else 0.0)
            self._retarget()
        self.update()

    def sizeHint(self):
        return QSize(32, 33)

    def minimumSizeHint(self):
        return QSize(32, 33)

    def launch(self) -> None:
        """The arrow flies up and comes back from below: a message went out."""
        self.launch_t.jump(0.0)
        self.launch_t.go(1.0)

    def _targets(self) -> Tuple[QColor, QColor]:
        t = theme.tokens()
        if self.state == "stop":
            return QColor(t.text), QColor(t.canvas)
        if self.state == "ready":
            return QColor(t.accent_2 if self._hover else t.accent), QColor(t.accent_ink)
        return QColor(0, 0, 0, 0), QColor(t.text_3)

    def _retarget(self, jump: bool = False) -> None:
        bg, ink = self._targets()
        cur_bg = theme.lerp(self._bg_from, self._bg_to, self.bg_t.value)
        cur_ink = theme.lerp(self._ink_from, self._ink_to, self.bg_t.value)
        self._bg_from, self._bg_to = cur_bg, bg
        self._ink_from, self._ink_to = cur_ink, ink
        self.bg_t.jump(0.0)
        if jump:
            self.bg_t.jump(1.0)
        else:
            self.bg_t.go(1.0)

    def enterEvent(self, e) -> None:
        super().enterEvent(e)
        self._hover = True
        self._retarget()

    def leaveEvent(self, e) -> None:
        super().leaveEvent(e)
        self._hover = False
        self._retarget()

    def mousePressEvent(self, e) -> None:
        self._pressed = True
        self.update()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(e)

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bg = theme.lerp(self._bg_from, self._bg_to, self.bg_t.value)
        ink = theme.lerp(self._ink_from, self._ink_to, self.bg_t.value)
        scale = 1.0
        if self.state == "ready":
            scale = 0.82 + 0.18 * self.ready_t.value
        if self._pressed and self.isEnabled():
            scale *= 0.9
        p.translate(16, 16)
        p.scale(scale, scale)
        p.translate(-16, -16)
        circle = QRectF(0, 0, 32, 32)
        ready_a = bg.alphaF() if self.state == "ready" else 0.0
        if self.state == "ready" and ready_a > 0:
            # box-shadow: 0 1px 2px rgba(0,0,0,.25)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, int(64 * ready_a)))
            p.drawEllipse(circle.translated(0, 1))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawEllipse(circle)
        if self.state == "ready" and ready_a > 0:
            # inset 0 1px 0 rgba(255,255,255,.2)
            p.setClipPath(rounded_path(circle, 16))
            p.fillRect(QRectF(0, 0, 32, 1), QColor(255, 255, 255, int(51 * ready_a)))
            p.setClipping(False)
        if self.state == "idle" or (self.state == "ready" and self.bg_t.value < 1):
            # box-shadow: inset 0 0 0 1px var(--hair-2), fading as the fill arrives
            ring = theme.qcolor(t.hair_2)
            if self.state == "ready":
                ring.setAlphaF(ring.alphaF() * (1 - self.bg_t.value))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(ring, 1))
            p.drawEllipse(circle.adjusted(0.5, 0.5, -0.5, -0.5))
        # the glyphs: arrow ↔ stop square
        s = self.stop_t.value
        arrow_a = 0.0 if self.spinner.isVisible() else 1 - s
        if arrow_a > 0.01:
            pm = icons.pixmap("arrow-up", ink, 16, stroke=2.25)
            p.save()
            lt = self.launch_t.value
            dy, sc, op = 0.0, 1.0, 1.0
            if self.launch_t.running or 0 < lt < 1:
                if lt < 0.38:
                    k = lt / 0.38
                    dy, sc, op = -16 * k, 1 - 0.1 * k, 1 - k
                elif lt < 0.42:
                    dy, sc, op = 12.0, 0.9, 0.0
                else:
                    k = (lt - 0.42) / 0.58
                    dy, sc, op = 12 * (1 - k), 0.9 + 0.1 * k, k
            p.setOpacity(arrow_a * op)
            p.translate(16, 16 + dy + 4 * s)
            k2 = (0.5 + 0.5 * arrow_a) * sc
            p.scale(k2, k2)
            p.drawPixmap(QPointF(-8, -8), pm)
            p.restore()
        if s > 0.01:
            p.save()
            p.setOpacity(s)
            p.translate(16, 16)
            k3 = 0.5 + 0.5 * s
            p.scale(k3, k3)
            p.setPen(QPen(ink, 0.92))
            p.setBrush(ink)
            p.drawRoundedRect(QRectF(-4.125, -4.125, 8.25, 8.25), 0.92, 0.92)
            p.restore()
