"""Shared block machinery: a block drawn from a node tree, head painting, hover tints, time."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter

from ... import theme
from ..nodes import Node
from ..paint import Measurer, PaintState, rounded
from ..scene import Block, Target

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
        """Drop the tree so the next measure builds it again (after the item changed). The generation
        moves on, so a pixmap cached of the old picture is not blitted again; its rectangle is noted,
        so the commit repaints it even when its height stays the same."""
        if self.root is not None:
            self.root.cool()
        self.root = None
        self.warm = False
        self.gen += 1
        if self.scene is not None:
            self.scene.note_block(self)  # its own rectangle; a height change notes the rest itself

    # ---- heights
    def estimate(self, width: int, m: Measurer) -> int:
        return self.est_h + self.pad[1] + self.pad[3]

    def measure(self, width: int, m: Measurer) -> int:
        if self.root is None:
            self.root = self.builder(m)
        pl, t, r, b = self.pad
        h = self.root.layout(pl, t, max(1, width - pl - r), m)
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
        """The live nodes' rectangle (a spinner), not the block."""
        return self.root.live_rect() if self.root is not None else None


# ---------------------------------------------------------------- heads


NEEDS_R = 12


def paint_needs_frame(p: QPainter, rect: QRect, st: PaintState) -> None:
    """The card of a step that waits on the person (a permission, a question, a plan): raised, with
    the accent ring 1.5 px wide — the one signal, shared with the composer's border and the pill."""
    rounded(p, rect, QColor(st.t.raised), QColor(st.t.accent_ring), NEEDS_R, width=1.5)


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


#: what a head shows at its right, in reading order: (kind, text, colour). Kinds: ``badge`` (a
#: tinted pill; the colour is ``err``, ``info`` or empty), ``mono`` (a diffstat figure), ``strong``
#: (a word in 600, "Failed"), anything else a micro figure (a duration, a count)
MetaPart = Tuple[str, str, Any]


def _meta_font(kind: str, st: PaintState) -> QFont:
    if kind == "badge":
        return st.m.font(11, 600)
    if kind == "mono":
        return st.m.font(11.5, 400, True)
    return st.m.font(theme.FS_MICRO, 600 if kind == "strong" else 400)


def meta_width(st: PaintState, parts: List[MetaPart]) -> int:
    """The room the figures at the right of a head take (``paint_meta``)."""
    w = 0
    for kind, text, _tone in parts:
        tw = int(st.m.text_w(text, _meta_font(kind, st)))
        w += tw + (14 + 8 if kind == "badge" else 5 if kind == "mono" else 8)
    return w


def paint_meta(p: QPainter, right: int, y: int, head_h: int, st: PaintState, parts: List[MetaPart]) -> None:
    """The figures at the right of a head, right-aligned at ``right`` and centred on its line."""
    t = st.t
    x = right
    for kind, text, tone in reversed(parts):
        f = _meta_font(kind, st)
        ty = y + (head_h - st.m.line_h(f)) / 2
        if kind == "badge":
            bw = int(st.m.text_w(text, f)) + 14
            x -= bw
            fg, bg = {"err": (t.err, t.err_wash), "info": (t.info, t.info_wash)}.get(tone, (t.text_2, t.fill_2))
            rounded(p, QRect(x, y + (head_h - 19) // 2, bw, 19), theme.blend(bg, t.canvas), None, 9)
            st.m.draw_text(p, x + 7, ty, text, f, QColor(fg))
            x -= 8
            continue
        x -= st.m.text_w(text, f)
        st.m.draw_text(p, x, ty, text, f, QColor(tone))
        x -= 5 if kind == "mono" else 8
