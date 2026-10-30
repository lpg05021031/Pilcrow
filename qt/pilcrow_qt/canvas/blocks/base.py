"""Shared block machinery: a block drawn from a node tree, head painting, hover tints, time."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter

from ... import theme
from ..nodes import Node
from ..paint import Measurer, PaintState, rounded
from ..scene import GRADE_EXACT, Block, Target

Handler = Callable[[Target, Any], None]


class NodeBlock(Block):
    """A block whose content is a node tree built lazily by ``builder(m)``. ``pad`` is left, top,
    right, bottom; ``handlers`` maps a target id (or ``kind:id``) to what a click does."""

    KIND = "node"
    __slots__ = ("builder", "root", "pad", "handlers", "est_h", "_sig_fn")

    def __init__(
        self,
        key: str,
        builder: Callable[[Measurer], Node],
        pad: Tuple[int, int, int, int] = (0, 0, 0, 0),
        est_h: int = 44,
    ) -> None:
        super().__init__(key)
        self.builder = builder
        self.root: Optional[Node] = None
        self.pad = pad
        self.handlers: Dict[str, Handler] = {}
        self.est_h = est_h

    def on(self, tid: str, fn: Handler) -> NodeBlock:
        self.handlers[tid] = fn
        return self

    def rebuild(self) -> None:
        """Drop the tree so the next measure builds it again (after the item changed)."""
        if self.root is not None:
            self.root.cool()
        self.root = None
        self.warm = False

    # ---- heights
    def estimate(self, width: int, m: Measurer) -> int:
        return self.est_h + self.pad[1] + self.pad[3]

    def measure(self, width: int, m: Measurer) -> int:
        if self.root is None:
            self.root = self.builder(m)
        pl, t, r, b = self.pad
        h = self.root.layout(pl + self.indent, t, max(1, width - pl - r - self.indent), m)
        self.warm = True
        return (h + t + b) if h > 0 else 0

    def cool(self) -> None:
        if self.root is not None:
            self.root.cool()
            self.root = None
        self.warm = False

    # ---- paint and hits
    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        if self.root is None:
            return
        self.root.paint(p, x, y, st)

    def hit(self, x: int, y: int) -> Optional[Target]:
        return self.root.hit(x, y) if self.root is not None else None

    def targets(self) -> List[Target]:
        return list(self.root.targets()) if self.root is not None else []

    def activate(self, target: Target, view: Any) -> None:
        fn = self.handlers.get(f"{target.kind}:{target.id}") or self.handlers.get(target.id)
        if fn is not None:
            fn(target, view)

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor

    def text(self) -> str:
        return self.root.text() if self.root is not None else ""

    def live(self) -> bool:
        return self.root is not None and self.root.live()

    def tick(self, now: float) -> Optional[Tuple[int, int, int, int]]:
        return (0, 0, 100000, self.h) if self.live() else None


# ---------------------------------------------------------------- heads


def paint_head_tint(p: QPainter, rect: QRect, st: PaintState, strength: str = "fill_1") -> None:
    """The soft tint under a hovered head (``.tool-head:hover``)."""
    rounded(p, rect, theme.blend(getattr(st.t, strength), st.t.canvas), None, theme.R2)


def chevron(
    p: QPainter, x: int, y: int, size: int, open_: float, color: QColor, st: PaintState, alpha: float = 1.0
) -> None:
    """A chevron turned by ``open_`` (0 → right, 1 → down); drawn at ``alpha``."""
    pm_r = st.m.icon("chevron-right", color, size)
    pm_d = st.m.icon("chevron-down", color, size)
    if alpha < 1.0:
        p.setOpacity(alpha)
    if open_ <= 0.0:
        p.drawPixmap(x, y, pm_r)
    elif open_ >= 1.0:
        p.drawPixmap(x, y, pm_d)
    else:
        p.save()
        p.translate(x + size / 2, y + size / 2)
        p.rotate(90.0 * open_)
        p.drawPixmap(int(-size / 2), int(-size / 2), pm_r)
        p.restore()
    p.setOpacity(1.0)


def elapsed_text(ms: float) -> str:
    from ...lib.fmt import duration

    return duration(ms)


def now_ms() -> float:
    return time.time() * 1000


_ = (QRectF, GRADE_EXACT)
