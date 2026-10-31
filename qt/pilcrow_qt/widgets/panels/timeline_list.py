"""The timeline's list on the canvas engine: one block per step (the rail, the title, what it was,
when, +add −del), the "Now" row at the end, and the picked step's detail as an island under its row.
Three hundred steps are three hundred small blocks, estimated at once and measured as they show;
picking a step changes one island and the rows' tints, nothing is rebuilt.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ... import theme
from ...canvas import nodes as nd
from ...canvas.blocks.base import NodeBlock
from ...canvas.blocks.fold import reconcile
from ...canvas.blocks.island import IslandBlock
from ...canvas.paint import Measurer, PaintState, rounded
from ...canvas.scene import Block, Scene, Target
from ...canvas.view import CanvasView
from ...lib.fmt import plural

Json = Dict[str, Any]
ROW_PAD = (4, 5, 6, 5)
RAIL_W = 18


def _clock_s(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def step_title(s: Json) -> str:
    kind = s.get("kind")
    if kind == "message":
        return str(s.get("label") or "Message sent")
    if kind == "start":
        return str(s.get("label") or "Session started")
    if kind == "external":
        return "Changed outside the conversation"
    if kind == "restore":
        return str(s.get("label") or "Restored")
    return str(s.get("label") or s.get("tool") or "Step")


def step_sub(s: Json) -> str:
    kind = s.get("kind")
    if kind == "tool":
        return str(s.get("tool") or "")
    if kind == "external":
        return plural(len(s.get("files") or []), "file") + " changed between turns"
    if kind == "message":
        return "You"
    if kind == "start":
        return "Claude Code"
    return "Timeline"


def num(kind: str, n: int) -> nd.Text:
    """``.num.add`` / ``.num.del``: a signed count in mono, ok or err."""
    sign = "+" if kind == "add" else "−"
    return nd.Text(f"{sign}{n}", theme.FS_MICRO, 500, "ok" if kind == "add" else "err", mono=True, wrap=False)


class StepBlock(NodeBlock):
    """One step (``.tl-row``). Selected: the accent dot, the tint, and the jump arrow shown."""

    KIND = "tl-step"
    MT = 0
    MB = 0

    def __init__(self, step: Json, last: bool, on_pick: Callable[[int], None]) -> None:
        self.step = step
        self.n = int(step.get("n") or 0)
        self.last = last
        self.selected = False
        self.on_pick = on_pick
        self.title = step_title(step)
        super().__init__(f"tl:{self.n}", self._build, (0, 0, 0, 0), est_h=44)
        self.on("row:pick", lambda _t, _v: self.on_pick(self.n))
        self.on("jump", self._jump)

    def sync(self, step: Json, last: bool) -> bool:
        changed = step != self.step or last != self.last
        self.step, self.last = step, last
        self.title = step_title(step)
        if changed:
            self.rebuild()
        return changed

    def set_selected(self, on: bool) -> bool:
        if on == self.selected:
            return False
        self.selected = on
        return True

    def _build(self, m: Measurer) -> nd.Node:
        s = self.step
        is_msg = s.get("kind") == "message"
        title = nd.Text(
            f"“{self.title}”" if is_msg else self.title,
            theme.FS_SMALL,
            400,
            "text_2" if is_msg else "text",
            wrap=False,
            italic=is_msg,
        )
        sub = nd.hstack(
            nd.Text(step_sub(s), theme.FS_MICRO, 400, "text_4", wrap=False),
            nd.Text(_clock_s(float(s.get("at") or 0)), theme.FS_MICRO, 400, "text_4", wrap=False),
            spacing=8,
        )
        main = nd.vstack(title, sub, spacing=1)
        parts: List[Optional[nd.Node]] = [nd.Painted(self._paint_dot, RAIL_W, 24, "", "mark"), main]
        if s.get("changed"):
            parts.append(nd.hstack(num("add", int(s.get("add") or 0)), num("del", int(s.get("del") or 0)), spacing=5))
        else:
            parts.append(nd.Text("—", theme.FS_MICRO, 400, "text_3", wrap=False))
        if s.get("toolUseId") or s.get("uuid"):
            parts.append(nd.Button("", "jump", icon="arrow-up-right", kind="ghost", size="xs", icon_px=13))
        return nd.hstack(*parts, spacing=6, stretch=[1], pad=ROW_PAD, valign="top")

    def _jump(self, _t: Target, _view: Any) -> None:
        from ...store import store

        store.toast("That step is not in the conversation view.", "info")

    def _paint_dot(self, p: QPainter, r: QRect, st: PaintState) -> None:
        """The mark on the rail (``.tl-dot``): hollow = nothing changed, filled = files changed, a square
        = you spoke, amber = changed outside the conversation, a ring = restored; the chosen one is the
        accent."""
        t = st.t
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = r.x() + 9.0, r.y() + 6 + 3.5
        if not self.last:
            # the rail runs down to the next row (the block's full height, plus the row padding)
            p.fillRect(
                QRectF(cx - 0.5, cy + 4, 1, self.h - cy + r.y() - ROW_PAD[1] + 2), theme.blend(t.hair_2, t.canvas)
            )
        kind = self.step.get("kind")
        p.setPen(Qt.PenStyle.NoPen)
        if self.selected:
            halo = QColor(t.accent)
            halo.setAlphaF(0.22)
            p.setBrush(halo)
            rr = 3.5 * 1.3 + 3
            p.drawEllipse(QPointF(cx, cy), rr, rr)
            p.setBrush(QColor(t.accent))
            p.drawEllipse(QPointF(cx, cy), 3.5 * 1.3, 3.5 * 1.3)
            return
        if kind in ("message", "start"):
            p.setBrush(QColor(t.text_2))
            p.drawRoundedRect(QRectF(cx - 4, cy - 4, 8, 8), 2, 2)
        elif kind == "external":
            p.setBrush(QColor(t.warn))
            p.drawEllipse(QPointF(cx, cy), 3.5, 3.5)
        elif kind == "restore":
            p.setPen(QPen(QColor(t.text_2), 2))
            p.setBrush(theme.qcolor(t.raised))
            p.drawEllipse(QPointF(cx, cy), 2.5, 2.5)
        elif self.step.get("changed"):
            p.setBrush(QColor(t.text_3))
            p.drawEllipse(QPointF(cx, cy), 3.5, 3.5)
        else:
            p.setPen(QPen(theme.blend(t.hair_3, t.canvas), 1.5))
            p.setBrush(theme.qcolor(t.raised))
            p.drawEllipse(QPointF(cx, cy), 2.75, 2.75)

    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        hover = st.hover is not None
        if hover or self.selected:
            extra = theme.R2 if self.selected else 0  # square-bottomed over the detail
            rounded(p, QRect(x, y, w, self.h + extra), theme.blend(st.t.fill_1, st.t.canvas), None, theme.R2)
        # the jump arrow shows on hover or while picked (its room is always there)
        if self.root is not None:
            for node in self.root.children:
                if isinstance(node, nd.Button) and node.tid == "jump":
                    node.hidden = not (hover or self.selected)
        super().paint(p, x, y, w, st)

    def hit(self, x: int, y: int) -> Optional[Target]:
        t = super().hit(x, y)
        if t is not None and t.kind == "button":
            return t
        return Target("row", "pick", (0, 0, self.h_width or 0, self.h))

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor

    def text(self) -> str:
        return self.title

    @property
    def static(self) -> bool:
        return True


class NowBlock(NodeBlock):
    """``.tl-now``: the present, chosen when no step is."""

    KIND = "tl-now"

    def __init__(self, on_pick: Callable[[], None]) -> None:
        self.chosen = True
        self.busy = False
        super().__init__("tl:now", self._build, (0, 0, 0, 0), est_h=46)
        self.on_pick = on_pick
        self.on("row:now", lambda _t, _v: self.on_pick())

    def set_state(self, on: bool, busy: bool) -> bool:
        changed = (on, busy) != (self.chosen, self.busy)
        self.chosen, self.busy = on, busy
        if changed:
            self.rebuild()
        return changed

    def _build(self, m: Measurer) -> nd.Node:
        sub = "Claude is working — new steps appear as they happen" if self.busy else "The folder as it is right now"
        return nd.hstack(
            nd.Painted(self._paint_mark, RAIL_W, 24, "", "mark"),
            nd.vstack(
                nd.Text("Now", theme.FS_SMALL, 400, "text", wrap=False),
                nd.Text(sub, theme.FS_MICRO, 400, "text_3", wrap=True),
                spacing=1,
            ),
            spacing=6,
            stretch=[1],
            pad=ROW_PAD,
            valign="top",
        )

    def _paint_mark(self, p: QPainter, r: QRect, st: PaintState) -> None:
        from ...canvas.blocks.misc import draw_mark

        draw_mark(p, QRectF(r.x() + 2, r.y() + 4, 14, 14), st.t, 1.0, st.t.text if self.chosen else st.t.text_2)

    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        if st.hover is not None or self.chosen:
            rounded(p, QRect(x, y, w, self.h), theme.blend(st.t.fill_1, st.t.canvas), None, theme.R2)
        super().paint(p, x, y, w, st)

    def hit(self, x: int, y: int) -> Optional[Target]:
        return Target("row", "now", (0, 0, self.h_width or 0, self.h))

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor

    def text(self) -> str:
        return "Now"


class DetailIsland(IslandBlock):
    """The picked step's detail (its diff, file by file): a real widget under the row."""

    MB = 6

    def __init__(self, step: Json, sid: str, cwd: str) -> None:
        self.step, self.sid, self.cwd = step, sid, cwd
        super().__init__(f"tl-detail:{int(step.get('n') or 0)}", self._make)
        self.indent = 22

    def _make(self) -> QWidget:
        from .history import StepDetail

        return StepDetail(self.step, self.sid, self.cwd)


class TimelineList(CanvasView):
    """The list: steps, the present, and the picked step's detail."""

    def __init__(
        self, sid: str, cwd: str, on_pick: Callable[[Optional[int]], None], parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(Scene(), parent, column="fill")
        self.sid, self.cwd = sid, cwd
        self.gutter = 8
        self.pad_top, self.pad_bottom = 10, 12
        self.on_pick = on_pick
        self.now = NowBlock(lambda: on_pick(None))
        self.sel: Optional[int] = None
        self.steps: List[Json] = []
        self.rows: List[StepBlock] = []
        self.detail: Optional[DetailIsland] = None
        self.follow.following = False  # the list does not chase its end on its own
        self.setAccessibleName("Timeline")

    def sync(self, steps: List[Json], sel: Optional[int], busy: bool) -> None:
        """Bring the rows in line with the steps; the picked one opens its detail under it."""
        self.steps = steps
        self.sel = sel
        n = len(steps)
        keys: List[str] = []
        for s in steps:
            keys.append(f"tl:{int(s.get('n') or 0)}")
            if sel is not None and int(s.get("n") or 0) == sel:
                keys.append(f"tl-detail:{sel}")
        keys.append("tl:now")
        root = self.scene.root

        def make(i: int) -> Block:
            k = keys[i]
            if k == "tl:now":
                return self.now
            if k.startswith("tl-detail:"):
                step = next(s for s in steps if int(s.get("n") or 0) == sel)
                self.detail = DetailIsland(step, self.sid, self.cwd)
                return self.detail
            si = sum(1 for kk in keys[: i + 1] if kk.startswith("tl:") and kk != "tl:now") - 1
            return StepBlock(steps[si], si == n - 1, lambda m: self.on_pick(None if self.sel == m else m))

        def update(b: Block, i: int) -> List[Block]:
            k = keys[i]
            if isinstance(b, StepBlock):
                si = sum(1 for kk in keys[: i + 1] if kk.startswith("tl:") and kk != "tl:now") - 1
                return [b] if b.sync(steps[si], si == n - 1) else []
            if k == "tl:now":
                return [b] if self.now.set_state(sel is None, busy) else []
            return []

        self.begin()
        try:
            changed = reconcile(root, keys, make, update)
            for b in changed:
                if isinstance(b, IslandBlock):
                    self.register_island(b)
                if b.warm:
                    self._exact(b)
                else:
                    self.estimate(b)
            if self.now.set_state(sel is None, busy):
                self._exact(self.now) if self.now.warm else self.estimate(self.now)
            self.rows = [b for b in root.children if isinstance(b, StepBlock)]
            for r in self.rows:
                if r.set_selected(sel is not None and r.n == sel):
                    self.scene.note_block(r)
            if self.detail is not None and self.detail.scene is not self.scene:
                self.detail = None
        finally:
            self.end()

    def reveal(self, n: Optional[int]) -> None:
        """Bring a step's row (or the present) into view."""
        b: Optional[Block] = self.now if n is None else next((r for r in self.rows if r.n == n), None)
        if b is not None:
            self.scroll_to_block(b, 0.3)

    def detail_widget(self) -> Optional[QWidget]:
        return self.detail.widget if self.detail is not None and self.detail.alive else None
