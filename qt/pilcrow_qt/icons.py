"""Lucide icons as QIcons/QPixmaps, tinted on demand.

The SVGs in assets/icons are stroke-based with ``currentColor`` (scripts/extract-icons.mjs writes
them from lucide-react). ``icon("square-terminal", color="#9c9c9c", size=14)`` renders one at the
device pixel ratio and caches it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICON_DIR = Path(__file__).parent / "assets" / "icons"
STROKE = 2.0


@lru_cache(maxsize=512)
def _svg(name: str, color: str, stroke: float) -> bytes:
    path = ICON_DIR / f"{name}.svg"
    if not path.exists():
        # A missing icon draws nothing rather than crashing; the name is the clue in the log.
        print(f"[icons] no icon named {name!r}")
        return b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"/>'
    src = path.read_text(encoding="utf-8")
    src = src.replace("currentColor", color)
    if stroke != STROKE:
        src = src.replace('stroke-width="2"', f'stroke-width="{stroke}"', 1)
    return src.encode("utf-8")


def pixmap(name: str, color: str | QColor = "#9c9c9c", size: int = 16, stroke: float = STROKE) -> QPixmap:
    """A tinted icon pixmap, crisp on HiDPI."""
    if isinstance(color, QColor):
        color = color.name(QColor.NameFormat.HexArgb) if color.alpha() < 255 else color.name()
    return _pixmap(name, color, size, stroke)


@lru_cache(maxsize=1024)
def _pixmap(name: str, color: str, size: int, stroke: float) -> QPixmap:
    app = QGuiApplication.instance()
    dpr = app.devicePixelRatio() if app is not None else 1.0
    px = int(round(size * dpr))
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(_svg(name, color, stroke)))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, px, px))
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(
    name: str, color: str | QColor = "#9c9c9c", size: int = 16, stroke: float = STROKE, hover: Optional[str] = None
) -> QIcon:
    """A QIcon for buttons and actions. ``hover`` tints the Active mode (Qt uses it for hovered buttons)."""
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size, stroke), QIcon.Mode.Normal)
    if hover:
        ic.addPixmap(pixmap(name, hover, size, stroke), QIcon.Mode.Active)
    return ic


def size(n: int) -> QSize:
    return QSize(n, n)


def available() -> list[str]:
    return sorted(p.stem for p in ICON_DIR.glob("*.svg"))
