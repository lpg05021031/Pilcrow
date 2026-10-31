"""How a turn's work folds: the work fold (one line that opens to chapters or steps), chapters (what
Claude said along the way as headings, the steps under them), runs of steps with quiet reads folded
into "Explored", and the flat list of rows a stretch of plain items becomes.

The pure helpers (what is quiet, how chapters are cut, the summary words) are the ones in
widgets/transcript/folds.py; this module gives them groups and blocks instead of widgets.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter

from ... import theme
from ...lib.fmt import duration
from ...lib.folds import (
    Chapter,
    ExploreSeg,
    ItemGroup,
    ToolSeg,
    chapters_of,
    explore_label,
    group_items,
    is_quiet,
    live_label,
    tally,
    work_summary,
)
from ...lib.folds import (
    StepsGroup as StepsSpec,
)
from ...lib.transcript_model import Item, ToolItem
from ...lib.units import Unit, WorkUnit
from ...store import store
from ...widgets.transcript.ctx import ToolCtx
from ..paint import Measurer, PaintState, doc_height, draw_doc, rounded
from ..pixels import vline
from ..scene import Block, Group, Target
from .base import chevron, paint_head_tint
from .misc import (
    CommandOutputBlock,
    DividerBlock,
    NoticeBlock,
    ResultBlock,
    compact_label,
    draw_mark,
    notice_mode,
    result_notice,
)
from .prose import TextGroup
from .thinking import ThinkingBlock
from .tool import SpecialIsland, ToolBlock, is_special
from .user import UserBlock

Json = Dict[str, Any]
BUSY = ("running", "starting", "requires_action")


def _perm(ctx: ToolCtx, item: Item) -> Optional[Json]:
    return ctx.pending.get(item.id) if item.kind == "tool" else None


# ---------------------------------------------------------------- rows


def make_row(item: Item, ctx: ToolCtx, in_work: bool, sid: str) -> Block:
    k = item.kind
    if k == "user":
        return UserBlock(item, sid, ctx.cwd)
    if k == "text":
        return TextGroup(item, ctx.cwd, quiet=in_work)
    if k == "thinking":
        return ThinkingBlock(item, ctx.view)
    if k == "tool":
        perm = _perm(ctx, item)
        if is_special(item, perm):
            return SpecialIsland(item, ctx, perm)
        return ToolBlock(item, ctx, perm, lambda items, c: Flat(items, c, sid, in_work=True))
    if k == "notice":
        if notice_mode(item) == "divider":
            return DividerBlock(f"notice:{item.id}", item.text)
        return NoticeBlock(item)
    if k == "compact":
        return DividerBlock(f"compact:{item.id}", compact_label(item))
    if k == "command-output":
        return CommandOutputBlock(item, ctx.cwd)
    if k == "result":
        if item.ok:
            return ResultBlock(item, sid)
        return NoticeBlock(result_notice(item))
    b = Block(f"unknown:{item.id}")
    return b


def row_key(item: Item, ctx: ToolCtx) -> str:
    """The key a row for this item has (so a reconcile can tell a changed kind)."""
    k = item.kind
    if k == "tool":
        return f"special:{item.id}" if is_special(item, _perm(ctx, item)) else f"tool:{item.id}"
    if k == "notice":
        return f"notice:{item.id}"
    if k == "result":
        return f"result:{item.id}" if item.ok else f"notice:{item.id}"
    return f"{k}:{item.id}"


def sync_row(b: Block, item: Item, ctx: ToolCtx) -> List[Block]:
    """Bring a row's block in line with its item; returns the blocks that changed."""
    if b.rev == item.rev and not b.live():
        # the item is as it was: rows that read only the item (and the context they keep) stand
        if isinstance(b, (TextGroup, NoticeBlock, CommandOutputBlock, ResultBlock)):
            return []
        if isinstance(b, ToolBlock) and b.ctx is ctx:
            return []
        if isinstance(b, ThinkingBlock) and b.view_mode == ctx.view:
            return []
    if isinstance(b, UserBlock):
        return [b] if b.sync(item) else []
    if isinstance(b, TextGroup):
        return b.sync(item)
    if isinstance(b, ThinkingBlock):
        return [b] if b.sync(item, ctx.view) else []
    if isinstance(b, ToolBlock):
        return b.sync(item, ctx, _perm(ctx, item))
    if isinstance(b, SpecialIsland):
        return b.sync(item, ctx, _perm(ctx, item))
    if isinstance(b, NoticeBlock):
        src = result_notice(item) if item.kind == "result" else item
        return [b] if b.sync(src) else []
    if isinstance(b, CommandOutputBlock):
        return [b] if b.sync(item) else []
    if isinstance(b, ResultBlock):
        return [b] if b.sync(item) else []
    if isinstance(b, DividerBlock):
        label = compact_label(item) if item.kind == "compact" else item.text
        if label != b.label:
            b.label = label
            return [b]
    return []


def reconcile(
    group: Group,
    keys: List[str],
    make: Callable[[int], Block],
    update: Callable[[Block, int], List[Block]],
    settled: int = 0,
) -> List[Block]:
    """Make a group's children match ``keys`` in order: the common prefix is kept and updated (the
    first ``settled`` of them without even asking), the rest is rebuilt from the first difference.
    Returns the blocks whose content changed. The cost is the part after ``settled``: a flush with a
    long settled history behind it touches the tail only."""
    changed: List[Block] = []
    kids = group.children
    n_have, n_keys = len(kids), len(keys)
    n = min(n_have, n_keys)
    keep = min(settled, n)
    if keep and kids[keep - 1].key != keys[keep - 1]:
        keep = 0  # the contract did not hold (the history itself changed): compare everything
    while keep < n and kids[keep].key == keys[keep]:
        keep += 1
    if keep < n_have:
        group.truncate(keep)
    for i in range(min(settled, keep), keep):
        changed.extend(update(kids[i], i))
    for i in range(keep, n_keys):
        b = make(i)
        group.append(b)
        changed.append(b)
    return changed


# ---------------------------------------------------------------- Flat


class Flat(Group):
    """A stretch of plain items, or the inside of a fold or a chapter: rows, runs of tools as Steps."""

    KIND = "flat"

    def __init__(
        self, items: List[Item], ctx: ToolCtx, sid: str, in_work: bool = False, key: str = "", live: bool = False
    ) -> None:
        super().__init__(key or f"flat:{id(self)}", head_h=0, open_=True)
        self.shaped = True
        self.items: List[Item] = []
        self.ctx = ctx
        self.sid = sid
        self.in_work = in_work
        self.live_run = live
        self.sync(items, ctx, live)

    def sync(self, items: List[Item], ctx: ToolCtx, live: bool = False) -> List[Block]:
        self.items = items
        self.ctx = ctx
        self.live_run = live
        groups = group_items(items)
        keys = [g.key if isinstance(g, StepsSpec) else row_key(g.item, ctx) for g in groups]

        def make(i: int) -> Block:
            g = groups[i]
            if isinstance(g, StepsSpec):
                return Steps(g.items, ctx, self.sid, g.key, live)
            return make_row(g.item, ctx, self.in_work, self.sid)

        def update(b: Block, i: int) -> List[Block]:
            g = groups[i]
            if isinstance(g, StepsSpec):
                return b.sync(g.items, ctx, live) if isinstance(b, Steps) else []
            return sync_row(b, g.item, ctx)

        return reconcile(self, keys, make, update)

    def text(self) -> str:
        return ""


# ---------------------------------------------------------------- steps and Explored


class MoreBlock(Block):
    """``N earlier steps`` (and the turn's summary): the line that shows the hidden steps."""

    KIND = "more"
    MT = 0
    MB = 0

    def __init__(self, key: str, label: str, on_click: Callable[[], None]) -> None:
        super().__init__(key)
        self.label = label
        self.on_click = on_click

    def estimate(self, width: int, m: Measurer) -> int:
        return 28

    measure = estimate

    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        hover = st.hover is not None
        f = st.m.font(theme.FS_UI)
        color = QColor(st.t.text if hover else st.t.text_3)
        p.drawPixmap(x, y + 7, st.m.icon("layers", color, 14))
        st.m.draw_text(p, x + 20, y + (28 - st.m.line_h(f)) / 2, st.m.elide(self.label, f, w - 24), f, color)

    def hit(self, x: int, y: int) -> Optional[Target]:
        return Target("button", "more", (0, 0, self.h_width or 0, 28))

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor

    def activate(self, target: Target, view: Any) -> None:
        self.on_click()

    def text(self) -> str:
        return self.label


class Explore(Group):
    """One line for a run of quiet steps; opens to the cards."""

    KIND = "explore"
    CHILD_INDENT = 28
    HEAD = 28

    def __init__(self, items: List[ToolItem], ctx: ToolCtx, sid: str, key: str) -> None:
        super().__init__(key, head_h=self.HEAD, pad_top=0, pad_bottom=4, open_=False)
        self.shaped = True
        self.items = items
        self.ctx = ctx
        self.sid = sid
        self.label = explore_label(items)
        self.sync(items, ctx)

    def sync(self, items: List[ToolItem], ctx: ToolCtx) -> List[Block]:
        self.items = items
        self.ctx = ctx
        self.label = explore_label(items)
        self.sig = tuple(t.id for t in items)
        keys = [f"tool:{t.id}" for t in items]
        return reconcile(
            self,
            keys,
            lambda i: ToolBlock(items[i], ctx, None, lambda its, c: Flat(its, c, self.sid, in_work=True)),
            lambda b, i: b.sync(items[i], ctx, None) if isinstance(b, ToolBlock) else [],
        )

    def measure(self, width: int, m: Measurer) -> int:
        self.warm = True
        return self.h

    def estimate(self, width: int, m: Measurer) -> int:
        return self.h

    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        t = st.t
        hover = st.hover is not None and st.hover.kind == "head"
        if hover:
            paint_head_tint(p, QRect(x - 6, y, w + 6, self.HEAD), st)
        p.drawPixmap(x + 2, y + (self.HEAD - 14) // 2, st.m.icon("telescope", QColor(t.text_3), 14))
        f = st.m.font(theme.FS_UI)
        st.m.draw_text(p, x + 28, y + (self.HEAD - st.m.line_h(f)) / 2, self.label, f, QColor(t.text))
        cx = x + 28 + st.m.text_w(self.label, f) + 7
        chevron(
            p,
            int(cx),
            y + (self.HEAD - 13) // 2,
            13,
            self.reveal,
            QColor(t.text_3),
            st,
            1.0 if (hover or self.open) else 0.45,
        )

    def hit(self, x: int, y: int) -> Optional[Target]:
        return Target("head", "explore", (0, 0, self.h_width or 0, self.HEAD)) if y < self.HEAD else None

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor

    def activate(self, target: Target, view: Any) -> None:
        view.toggle_group(self, not self.open)

    def text(self) -> str:
        return self.label


class Steps(Group):
    """A run of tool calls: cards, quiet finished runs folded into Explored, and past ten segments the
    earlier ones behind an "N earlier steps" line."""

    KIND = "steps"
    MT = 6
    MB = 6
    COLLAPSE_AT = 10

    def __init__(self, items: List[ToolItem], ctx: ToolCtx, sid: str, key: str, live: bool = False) -> None:
        super().__init__(key, head_h=0, open_=True)
        self.shaped = True
        self.items = items
        self.ctx = ctx
        self.sid = sid
        self.live_run = live
        self.expanded = False
        self.sync(items, ctx, live)

    def _segments(self) -> list:
        out: list = []
        run: List[ToolItem] = []
        live = self.live_run

        def flush(tail: bool = False) -> None:
            nonlocal run
            if len(run) >= 2 and not (tail and live):
                out.append(ExploreSeg("x-" + run[0].id, run))
            else:
                out.extend(ToolSeg(t) for t in run)
            run = []

        for t in self.items:
            if self.ctx.view != "verbose" and is_quiet(t, self.ctx):
                run.append(t)
            else:
                flush()
                out.append(ToolSeg(t))
        flush(True)
        return out

    def sync(self, items: List[ToolItem], ctx: ToolCtx, live: bool = False) -> List[Block]:
        self.items = items
        self.ctx = ctx
        self.live_run = live
        segs = self._segments()
        hidden = len(segs) - 6 if (not self.expanded and len(segs) > self.COLLAPSE_AT) else 0
        visible = segs[hidden:]
        keys: List[str] = []
        if hidden:
            keys.append(f"{self.key}:more")
        for s in visible:
            keys.append(s.key if isinstance(s, ExploreSeg) else row_key(s.item, ctx))
        summary = next((t.summary for t in items if t.summary), None)
        label = f"{summary[:80] + ' · ' if summary else ''}{hidden} earlier steps"

        def make(i: int) -> Block:
            if hidden and i == 0:
                return MoreBlock(f"{self.key}:more", label, self._expand)
            s = visible[i - (1 if hidden else 0)]
            if isinstance(s, ExploreSeg):
                return Explore(s.items, ctx, self.sid, s.key)
            return make_row(s.item, ctx, True, self.sid)

        def update(b: Block, i: int) -> List[Block]:
            if hidden and i == 0:
                if isinstance(b, MoreBlock) and b.label != label:
                    b.label = label
                    return [b]
                return []
            s = visible[i - (1 if hidden else 0)]
            if isinstance(s, ExploreSeg):
                return b.sync(s.items, ctx) if isinstance(b, Explore) else []
            return sync_row(b, s.item, ctx)

        self._expand_ref = None
        return reconcile(self, keys, make, update)

    def _expand(self) -> None:
        self.expanded = True
        if self.scene is not None:
            self.sync(self.items, self.ctx, self.live_run)
            self.scene.note_block(self)

    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        if len(self.children) > 2:
            vline(p, x + 8, y + 14, self.h - 28, theme.blend(st.t.hair_2, st.t.canvas))


# ---------------------------------------------------------------- chapters


class ChapterGroup(Group):
    """A heading (what Claude said) with its steps under it, a rail down the side."""

    KIND = "chapter"
    CHILD_INDENT = 24

    def __init__(self, ch: Chapter, ctx: ToolCtx, sid: str, last: bool, state: Dict[str, bool]) -> None:
        super().__init__(ch.key, head_h=24, pad_top=2, pad_bottom=2 if last else 12, open_=False)
        self.shaped = True
        self.ch = ch
        self.ctx = ctx
        self.sid = sid
        self.last = last
        self.state = state  # remembered toggles, shared by the fold
        self.title_doc = None
        self._title_src = ""
        self._title_rev = -1
        self.tally_text = ""
        self.tally_failed = 0
        self.flat = Flat(self._steps(ch), ctx, sid, in_work=True, key=f"{ch.key}:flat", live=self.live)
        self.append(self.flat)
        self.sync(ch, ctx, last)
        self.set_open(self.wants_open)

    @staticmethod
    def _steps(ch: Chapter) -> List[Item]:
        return ([ch.thought] if ch.thought is not None else []) + list(ch.steps)

    @property
    def waiting(self) -> bool:
        return any(it.kind == "tool" and it.id in self.ctx.pending for it in self.ch.steps)

    @property
    def live(self) -> bool:
        ch = self.ch
        return any(
            (it.kind == "tool" and it.status in ("running", "streaming")) or (it.kind == "thinking" and it.streaming)
            for it in ch.steps
        ) or bool(ch.title and ch.title.streaming)

    @property
    def wants_open(self) -> bool:
        if self.ch.key in self.state:
            return self.state[self.ch.key]
        return self.ctx.view == "verbose" or self.waiting or self.live

    def sync(self, ch: Chapter, ctx: ToolCtx, last: bool) -> List[Block]:
        self.ch, self.ctx, self.last = ch, ctx, last
        t = tally(ch.steps)
        self.tally_text = t.text + (f" · {duration(t.ms)}" if t.ms > 1500 else "")
        self.tally_failed = t.failed
        self.sig = (ch.title.rev if ch.title else None, self.tally_text, self.live, last)
        self.set_chrome(pad_bottom=2 if last else 12)
        changed = self.flat.sync(self._steps(ch), ctx, self.live)
        if ch.title is not None and ch.title.rev != self._title_rev:
            self._title_rev = ch.title.rev
            self.title_doc = None
            changed.append(self)
        want = self.wants_open
        if want != self.open and self.ch.key not in self.state:
            self.open = want
            self.set_reveal(1.0 if want else 0.0)
        return changed

    def measure(self, width: int, m: Measurer) -> int:
        self.warm = True
        avail = max(60, width - 24 - 120)
        if self.ch.title is not None:
            src = self.ch.title.text
            if self.title_doc is None or self._title_src != src or int(self.title_doc.textWidth()) != avail:
                self.title_doc = m.markdown_doc(
                    src, avail, compact=True, quiet=True, streaming=bool(self.ch.title.streaming), cwd=self.ctx.cwd
                )
                self._title_src = src
            self.set_chrome(head_h=max(24, doc_height(self.title_doc) + 4))
        else:
            self.set_chrome(head_h=24)
        return self.h

    def estimate(self, width: int, m: Measurer) -> int:
        return self.h

    def cool(self) -> None:
        self.title_doc = None
        self.warm = False

    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        t = st.t
        hover = st.hover is not None and st.hover.kind == "head"
        # the rail: a dot, and a line down to the next chapter
        if not self.last:
            vline(p, x + 7, y + 18, self.h - 18 + 12, theme.blend(t.hair_2, t.canvas))
        c = QColor(t.err if self.tally_failed else t.accent if self.live else t.text_4)
        if self.live:
            phase = (st.now % 1.4) / 1.4
            c.setAlphaF(0.55 + 0.45 * (0.5 - 0.5 * math.cos(phase * 2 * math.pi)))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QRectF(x + 3.5, y + 9, 7, 7))
        hx = x + 24
        if hover:
            paint_head_tint(p, QRect(hx - 6, y, w - 24 + 6, self.head_h), st)
        tally_f = st.m.font(theme.FS_MICRO)
        tw = st.m.text_w(self.tally_text, tally_f) if self.ch.steps else 0
        if self.ch.title is not None and self.title_doc is not None:
            clip = QRect(hx, y + 2, w - 24 - int(tw) - 40, self.head_h - 2)
            if st.clip is not None:
                clip = clip.intersected(st.clip)
            draw_doc(p, self.title_doc, hx, y + 2, clip, QColor(t.text if (hover or self.open) else t.text_2))
        else:
            f = st.m.font(theme.FS_UI)
            label = "Steps" if any(it.kind == "tool" for it in self.ch.steps) else "Thinking"
            st.m.draw_text(p, hx, y + (24 - st.m.line_h(f)) / 2, label, f, QColor(t.text_3))
        if self.ch.steps:
            right = x + w
            chevron(p, right - 12, y + 6, 12, self.reveal, QColor(t.text_4), st, 1.0 if (hover or self.open) else 0.6)
            tx = right - 12 - 12 - tw
            st.m.draw_text(p, tx, y + 4, self.tally_text, tally_f, QColor(t.text_4))
            if self.tally_failed:
                pass  # the count is in the text; failure is read from the rail's red dot

    def hit(self, x: int, y: int) -> Optional[Target]:
        if y < self.head_h:
            return Target("head", "chapter", (24, 0, (self.h_width or 0) - 24, self.head_h))
        return None

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor

    def activate(self, target: Target, view: Any) -> None:
        self.state[self.ch.key] = not self.open
        view.toggle_group(self, not self.open)

    def text(self) -> str:
        return self.ch.title.text if self.ch.title is not None else ""

    def live_(self) -> bool:
        return self.live

    def tick(self, now: float) -> Optional[Tuple[int, int, int, int]]:
        return (0, 0, 24, self.head_h) if self.live else None

    @property
    def static(self) -> bool:
        return not self.live


class Chapters(Group):
    KIND = "chapters"

    def __init__(self, items: List[Item], ctx: ToolCtx, sid: str, state: Dict[str, bool], key: str) -> None:
        super().__init__(key, head_h=0, pad_top=2, pad_bottom=2, open_=True)
        self.shaped = True
        self.ctx = ctx
        self.sid = sid
        self.state = state
        self.sync(items, ctx)

    def sync(self, items: List[Item], ctx: ToolCtx, live: bool = False) -> List[Block]:
        self.ctx = ctx
        chapters = chapters_of(items)
        n = len(chapters)
        return reconcile(
            self,
            [ch.key for ch in chapters],
            lambda i: ChapterGroup(chapters[i], ctx, self.sid, i == n - 1, self.state),
            lambda b, i: b.sync(chapters[i], ctx, i == n - 1) if isinstance(b, ChapterGroup) else [],
        )


# ---------------------------------------------------------------- the work fold


class FoldGroup(Group):
    """One line for a stretch of work; opens to its chapters. Live while the turn runs: the mark
    breathes and the line says what Claude is doing."""

    KIND = "fold"
    MT = 10
    MB = 14
    CHILD_INDENT = 14
    HEAD = 30

    def __init__(self, unit: WorkUnit, ctx: ToolCtx, sid: str, state: Dict[str, bool]) -> None:
        super().__init__(unit.key, head_h=self.HEAD, pad_top=6, pad_bottom=4, open_=False)
        self.unit = unit
        self.ctx = ctx
        self.sid = sid
        self.state = state  # fold and chapter toggles the person made, per key
        self.summary = work_summary(unit.work)
        self.inner: Optional[Group] = None
        self._mode = ""
        self.shaped = False  # the inside is built the first time it is needed (opened, revealed, found)
        self.sync(unit, ctx)
        self.set_open(self.wants_open)

    def ensure_shape(self, scene: Any) -> None:
        if self.shaped:
            return
        self.shaped = True
        self.sync(self.unit, self.ctx)

    def content_h(self) -> int:
        if self.shaped:
            return self.fen.total()
        unit = getattr(self, "unit", None)  # not there yet while Group.__init__ runs
        if unit is None:
            return 0
        # a fair guess at the inside until it is built: a row per step, a heading per paragraph
        return sum(40 if it.kind == "text" else 28 for it in unit.work if it.kind != "thinking") + 8

    @property
    def waiting(self) -> bool:
        return any(it.kind == "tool" and it.id in self.ctx.pending for it in self.unit.work)

    @property
    def wants_open(self) -> bool:
        if self.key in self.state:
            return self.state[self.key] or self.waiting
        return self.ctx.view in ("thinking", "verbose") or self.waiting

    def sync(self, unit: WorkUnit, ctx: ToolCtx) -> List[Block]:
        self.unit, self.ctx = unit, ctx
        self.summary = work_summary(unit.work)
        doing = live_label(unit.work) if unit.live else None
        self.sig = (unit.live, self.summary.text, self.summary.failed, doing, ctx.view, len(unit.work))
        changed: List[Block] = []
        if not self.shaped:
            # nothing inside to keep in line; the head reads the unit directly
            want = self.wants_open
            if want != self.open and self.key not in self.state:
                self.open = want
                self.set_reveal(1.0 if want else 0.0)
            elif self.waiting and not self.open:
                self.open = True
                self.set_reveal(1.0)
            changed.append(self)
            return changed
        chapters = chapters_of(unit.work)
        mode = "flat" if (len(chapters) == 1 and chapters[0].title is None) else "chapters"
        if mode != self._mode or self.inner is None:
            if self.inner is not None:
                self.truncate(0)
            self._mode = mode
            self.inner = (
                Flat(unit.work, ctx, self.sid, in_work=True, key=f"{self.key}:flat", live=unit.live)
                if mode == "flat"
                else Chapters(unit.work, ctx, self.sid, self.state, f"{self.key}:chapters")
            )
            self.append(self.inner)
            changed.append(self.inner)
        else:
            changed.extend(self.inner.sync(unit.work, ctx, unit.live))  # type: ignore[union-attr]
        want = self.wants_open
        if want != self.open and self.key not in self.state:
            self.open = want
            self.set_reveal(1.0 if want else 0.0)
        elif self.waiting and not self.open:
            self.open = True
            self.set_reveal(1.0)
        changed.append(self)
        return changed

    def measure(self, width: int, m: Measurer) -> int:
        self.warm = True
        return self.h

    def estimate(self, width: int, m: Measurer) -> int:
        return self.h

    def _elapsed(self) -> float:
        if not self.unit.live:
            return 0
        r = store.rt.get(self.ctx.session_id)
        started = (r.st.get("turnStartedAt") if r else None) or 0
        first = self.unit.work[0] if self.unit.work else None
        t0 = started or (getattr(first, "ts", None) or getattr(first, "start_ts", 0) or 0)
        return time.time() * 1000 - t0 if t0 else 0

    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        t = st.t
        hover = st.hover is not None and st.hover.kind == "head"
        if hover:
            paint_head_tint(p, QRect(x - 6, y, w + 6, self.HEAD), st)
        live = self.unit.live
        cx = x
        if live:
            phase = ((st.now) % 1.8) / 1.8
            alpha = 0.55 + 0.45 * (0.5 - 0.5 * math.cos(phase * 2 * math.pi))
            draw_mark(p, QRectF(x, y + (self.HEAD - 16) / 2, 16, 16), t, alpha)
            cx += 20 + 6
        f = st.m.font(theme.FS_UI, 500 if live else 400)
        doing = live_label(self.unit.work) if live else None
        text = f"{doing}…" if doing else self.summary.text
        color = QColor(t.text_2 if hover else t.text_3)
        st.m.draw_text(p, cx, y + (self.HEAD - st.m.line_h(f)) / 2, text, f, color)
        cx += st.m.text_w(text, f) + 6
        if self.summary.failed:
            fail = f"· {self.summary.failed} failed"
            st.m.draw_text(p, cx, y + (self.HEAD - st.m.line_h(f)) / 2, fail, st.m.font(theme.FS_UI), QColor(t.err))
            cx += st.m.text_w(fail, f) + 6
        chevron(
            p,
            int(cx),
            y + (self.HEAD - 14) // 2,
            14,
            self.reveal,
            QColor(t.text_2 if hover else t.text_3),
            st,
            1.0 if hover else 0.7,
        )
        # right: N steps · elapsed
        s = self.summary
        elapsed = self._elapsed()
        if (not self.open or live) and (s.steps > 0 or elapsed > 1000):
            n = f"{s.steps} {'step' if s.steps == 1 else 'steps'}" if s.steps > 0 else ""
            if elapsed > 1000:
                n += f"{' · ' if n else ''}{duration(elapsed)}"
            fm = st.m.font(theme.FS_MICRO)
            tw = st.m.text_w(n, fm)
            st.m.draw_text(p, x + w - tw, y + (self.HEAD - st.m.line_h(fm)) / 2, n, fm, QColor(t.text_4))
        # the rule down the left of the open body
        if self.reveal > 0 and self.h > self.HEAD:
            vline(p, x, y + self.HEAD + 4, self.h - self.HEAD - 8, theme.blend(t.hair_2, t.canvas))

    def hit(self, x: int, y: int) -> Optional[Target]:
        return Target("head", "fold", (0, 0, self.h_width or 0, self.HEAD)) if y < self.HEAD else None

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor

    def activate(self, target: Target, view: Any) -> None:
        now = not self.open
        self.state[self.key] = now
        view.toggle_group(self, now)

    def open_now(self) -> None:
        self.state[self.key] = True
        self.open = True
        self.set_reveal(1.0)

    def text(self) -> str:
        return self.summary.text

    def live(self) -> bool:  # type: ignore[override]
        return bool(self.unit.live)

    def tick(self, now: float) -> Optional[Tuple[int, int, int, int]]:
        return (0, 0, 100000, self.HEAD) if self.unit.live else None

    @property
    def static(self) -> bool:
        return not self.unit.live


def unit_group(unit: Unit, ctx: ToolCtx, sid: str, state: Dict[str, bool]) -> Group:
    """The group a unit becomes: a work fold when it has tools, a flat stretch otherwise."""
    if isinstance(unit, WorkUnit) and any(it.kind == "tool" for it in unit.work):
        return FoldGroup(unit, ctx, sid, state)
    items = unit.work if isinstance(unit, WorkUnit) else unit.items
    return Flat(items, ctx, sid, in_work=isinstance(unit, WorkUnit), key=unit_group_key(unit))


def sync_unit_group(g: Group, unit: Unit, ctx: ToolCtx) -> List[Block]:
    if isinstance(g, FoldGroup) and isinstance(unit, WorkUnit):
        return g.sync(unit, ctx)
    if isinstance(g, Flat):
        return g.sync(unit.work if isinstance(unit, WorkUnit) else unit.items, ctx)
    return []


def unit_group_key(unit: Unit) -> str:
    """Units keep their key; a work unit that gains its first tool changes shape (fold vs flat), so
    the shape rides in the key."""
    fold = isinstance(unit, WorkUnit) and any(it.kind == "tool" for it in unit.work)
    return unit.key if fold else unit.key + (":flat" if isinstance(unit, WorkUnit) else "")


_ = (ItemGroup, rounded)
