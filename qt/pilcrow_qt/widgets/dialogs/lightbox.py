"""The lightbox (Lightbox in web/src/components/Overlays.tsx): an image, full size, over everything.
Click anywhere or press Esc to put it back. It follows ``store.lightbox`` and is installed once per
window by ``dialogs.install_overlays``."""

from __future__ import annotations

import base64
from typing import Optional
from urllib.parse import unquote

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

from ... import theme
from ...store import store
from ..ui.bits import IconButton
from ..ui.signals import bind
from .parts import Backdrop
from .sheet import host_of


def pixmap_from_src(src: str) -> QPixmap:
    """A ``data:`` URL, a ``file://`` URL or a path → pixmap (empty when it cannot be read)."""
    pm = QPixmap()
    if src.startswith("data:"):
        head, _, payload = src.partition(",")
        if ";base64" in head:
            try:
                pm.loadFromData(base64.b64decode(payload))
            except ValueError:
                pass
        else:
            pm.loadFromData(unquote(payload).encode("utf-8"))
        return pm
    if src.startswith("file:"):
        pm.load(QUrl(src).toLocalFile())
        return pm
    pm.load(src)
    return pm


def _rounded(pm: QPixmap, radius: int) -> QPixmap:
    """The pixmap with its corners cut to ``radius``."""
    out = QPixmap(pm.size())
    out.setDevicePixelRatio(pm.devicePixelRatio())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(
        QRectF(0, 0, pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()), radius, radius
    )
    p.setClipPath(path)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


class Lightbox(QWidget):
    def __init__(self, window: QWidget) -> None:
        host = host_of(window)
        super().__init__(host)
        self._host = host
        self.src: Optional[str] = None
        self._pm = QPixmap()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Image")
        self.image = QLabel(self)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.close_btn = IconButton("x", "Close (Esc)", 18, parent=self)
        self.close_btn.setAccessibleName("Close")
        self.close_btn.clicked.connect(store.close_lightbox)
        host.installEventFilter(self)
        self.hide()
        bind(store.lightbox_changed, self, self._on_change)
        bind(theme.theme.changed, self, lambda _t: self.update())
        if store.lightbox:
            self._on_change(store.lightbox)

    def _on_change(self, src: Optional[str]) -> None:
        if not src:
            self.hide()
            self.src = None
            return
        self.src = src
        self._pm = pixmap_from_src(src)
        self.setGeometry(self._host.rect())
        self.backdrop = Backdrop(self._host)
        self._layout()
        self.show()
        self.raise_()
        self.setFocus()

    def _layout(self) -> None:
        r = self.rect()
        self.close_btn.move(r.width() - self.close_btn.width() - 14, 14)
        if self._pm.isNull():
            self.image.setPixmap(QPixmap())
            self.image.setText("The image could not be shown.")
        else:
            pad = 36
            scaled = self._pm.scaled(
                max(1, r.width() - 2 * pad),
                max(1, r.height() - 2 * pad),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.image.setPixmap(_rounded(scaled, 10))
        self.image.adjustSize()
        self.image.move((r.width() - self.image.width()) // 2, (r.height() - self.image.height()) // 2)

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        if obj is self._host and e.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(self._host.rect())
            self._layout()
        return False

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.ShortcutOverride and e.key() == Qt.Key.Key_Escape:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            store.close_lightbox()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if not self.image.geometry().contains(e.position().toPoint()):
            store.close_lightbox()

    def paintEvent(self, _e) -> None:  # noqa: N802
        # .lightbox: rgba(8, 8, 8, 0.72) over blur(18px); the image on 10px corners with a 0.5px light ring
        t = theme.tokens()
        p = QPainter(self)
        bd = getattr(self, "backdrop", None)
        if bd is not None and bd.pixmap is not None:
            p.drawPixmap(0, 0, bd.pixmap)
        c = QColor(t.inset)
        c.setAlphaF(0.72)
        p.fillRect(self.rect(), c)
        if not self._pm.isNull() and self.image.pixmap() is not None:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = self.image.geometry()
            ring = QColor(t.edge_hi)
            ring.setAlphaF(0.14)
            p.setPen(ring)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(r.x() - 0.5, r.y() - 0.5, r.width() + 1, r.height() + 1), 10.5, 10.5)
