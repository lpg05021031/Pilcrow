"""Rules on the device pixel grid.

A one-pixel rule painted through a fractional device pixel ratio (1.25 or 1.5, common laptop
settings) covers a row and a half of device pixels and comes out as two rows at two greys. The
helpers here put a rule on whole device pixels wherever the painter's transform (the view's offset,
a cached pixmap's ratio) lands it: ``floor(ratio)`` pixels thick, so one at 1× and 1.5× and two at
2×, which is what a browser draws for ``1px`` at those ratios. A bordered rectangle (a well, a card)
has its edges snapped the same way and takes a pen of that thickness, so its straight sides are
crisp; its corners stay anti-aliased.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainter, QTransform

_SIMPLE = (
    QTransform.TransformationType.TxNone,
    QTransform.TransformationType.TxTranslate,
    QTransform.TransformationType.TxScale,
)  # a rotated or sheared painter is left alone


def _grid(p: QPainter) -> Optional[Tuple[QTransform, QTransform, float]]:
    """(logical → device, device → logical, the scale) for a painter whose transform is a scale and
    an offset; None when it is anything else."""
    tr = p.deviceTransform()
    if tr.type() not in _SIMPLE:
        return None
    inv, ok = tr.inverted()
    if not ok:
        return None
    return tr, inv, tr.m22()


def _edge(v: float) -> int:
    """The nearest device pixel edge."""
    return int(math.floor(v + 0.5))


def thickness(p: QPainter) -> int:
    """How many device pixels a hairline is on this painter's device."""
    return max(1, int(p.deviceTransform().m22() + 1e-6))


def pen_width(p: QPainter) -> float:
    """A hairline's thickness in the painter's logical pixels (what a QPen takes)."""
    g = _grid(p)
    return 1.0 if g is None else thickness(p) / g[2]


def snap(p: QPainter, r: QRectF) -> QRectF:
    """``r`` with every edge on the device grid (at least one device pixel wide and high), in the
    painter's logical coordinates."""
    g = _grid(p)
    if g is None:
        return QRectF(r)
    tr, inv, _s = g
    d = tr.mapRect(QRectF(r))
    x0, y0 = _edge(d.left()), _edge(d.top())
    x1, y1 = max(x0 + 1, _edge(d.right())), max(y0 + 1, _edge(d.bottom()))
    return inv.mapRect(QRectF(x0, y0, x1 - x0, y1 - y0))


def hline(p: QPainter, x: float, y: float, w: float, color: Any) -> None:
    """A horizontal hairline from (x, y) spanning ``w``, on whole device rows."""
    if w <= 0:
        return
    g = _grid(p)
    if g is None:
        p.fillRect(QRectF(x, y, w, 1), color)
        return
    tr, inv, s = g
    a = tr.map(QPointF(x, y))
    x0, y0 = _edge(a.x()), _edge(a.y())
    x1 = max(x0 + 1, _edge(a.x() + w * tr.m11()))
    p.fillRect(inv.mapRect(QRectF(x0, y0, x1 - x0, max(1, int(s + 1e-6)))), color)


def vline(p: QPainter, x: float, y: float, h: float, color: Any) -> None:
    """A vertical hairline from (x, y) spanning ``h``, on whole device columns."""
    if h <= 0:
        return
    g = _grid(p)
    if g is None:
        p.fillRect(QRectF(x, y, 1, h), color)
        return
    tr, inv, _s = g
    a = tr.map(QPointF(x, y))
    x0, y0 = _edge(a.x()), _edge(a.y())
    y1 = max(y0 + 1, _edge(a.y() + h * tr.m22()))
    p.fillRect(inv.mapRect(QRectF(x0, y0, max(1, int(tr.m11() + 1e-6)), y1 - y0)), color)
