"""One tool call: a quiet head (status, verb, target, meta) over a body that opens by default when it
is worth reading and on a click otherwise; the permission prompt under it while Claude waits; an
agent's own steps nested inside. AskUserQuestion and ExitPlanMode are cards of their own (islands)."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from ... import theme
from ...lib.fmt import duration
from ...lib.tools_meta import ToolMeta, file_path_of, is_file_tool, str_, tool_meta
from ...lib.transcript_model import Item, ToolItem
from ...lib.workflow import workflow_counts
from ...store import store
from ...widgets.transcript.ctx import ToolCtx
from ..paint import Measurer, PaintState, rounded
from ..pixels import vline
from ..scene import Block, Group, Target
from .base import chevron, paint_head_tint
from .bodies import ToolBodyBlock
from .island import IslandBlock

Json = Dict[str, Any]
EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
SPECIAL = ("AskUserQuestion", "ExitPlanMode")
HEAD_H, HEAD_H_WAIT = 28, 32
ChildBuilder = Callable[[List[Item], ToolCtx], Group]


def open_by_default(item: ToolItem, waiting: bool) -> bool:
    if waiting or item.status == "error":
        return True
    if item.name in EDIT_TOOLS:
        return item.status in ("streaming", "running")
    return False


def elapsed_ms(item: ToolItem) -> float:
    if item.end_ts and item.start_ts:
        return item.end_ts - item.start_ts
    return item.elapsed * 1000 if item.elapsed else 0


def diff_stats(item: ToolItem) -> Optional[Dict[str, int]]:
    s = item.structured if isinstance(item.structured, dict) else None
    add = dele = 0
    patch = s.get("structuredPatch") if s else None
    if isinstance(patch, list) and patch:
        for h in patch:
            for ln in (h.get("lines") or []) if isinstance(h, dict) else []:
                if str(ln)[:1] == "+":
                    add += 1
                elif str(ln)[:1] == "-":
                    dele += 1
        return {"add": add, "del": dele}
    i = item.input or {}
    if item.name == "Edit" and "old_string" in i and item.input_done:
        from ...widgets.transcript.bodies.code import compute_hunks

        for h in compute_hunks(str(i.get("old_string") or ""), str(i.get("new_string") or "")):
            for ln in h.lines:
                if ln.t == "+":
                    add += 1
                elif ln.t == "-":
                    dele += 1
        return {"add": add, "del": dele}
    if item.name == "Write" and isinstance(i.get("content"), str) and (not s or s.get("type") == "create"):
        return {"add": len(i["content"].split("\n")), "del": 0}
    return None


def is_special(item: ToolItem, perm: Optional[Json]) -> bool:
    if item.name not in SPECIAL:
        return False
    return perm is not None or item.status != "streaming"


class ToolBlock(Group):
    """The card: head, body, (agent children), (reports), (permission)."""

    KIND = "tool"
    CHILD_INDENT = 28

    def __init__(
        self, item: ToolItem, ctx: ToolCtx, perm: Optional[Json], child_builder: Optional[ChildBuilder] = None
    ) -> None:
        super().__init__(f"tool:{item.id}", head_h=HEAD_H, pad_top=4, pad_bottom=10, open_=False)
        self.item = item
        self.ctx = ctx
        self.perm = perm
        self.child_builder = child_builder
        self.user_open: Optional[bool] = None
        self.meta: ToolMeta = tool_meta(item.name, item.input, ctx.cwd)
        self.file: Optional[str] = None
        self.body = ToolBodyBlock(item, ctx, "body")
        self.reports: Optional[ToolBodyBlock] = None
        self.children_group: Optional[Group] = None
        self.perm_island: Optional[IslandBlock] = None
        self._loaded_items: Optional[List[Item]] = None
        self._meta_rects: Dict[str, QRect] = {}
        self.shaped = True
        self.append(self.body)
        self.sync(item, ctx, perm, first=True)

    # ---- state
    @property
    def awaiting(self) -> bool:
        return self.perm is not None

    @property
    def wants_open(self) -> bool:
        if self.user_open is not None:
            return self.user_open
        it = self.item
        plan_file = it.name == "Write" and "/.claude/plans/" in str_(it.input, "file_path").replace("\\", "/")
        verbose = self.ctx.view == "verbose" and it.status not in ("running", "streaming")
        return (open_by_default(it, self.awaiting) or verbose) and not plan_file

    def mt(self) -> int:
        return 10 if self.awaiting else 12

    def mb(self) -> int:
        return 14 if self.awaiting else 12

    def sync(self, item: ToolItem, ctx: ToolCtx, perm: Optional[Json], first: bool = False) -> List[Block]:
        """Bring the card in line with the item; returns the blocks whose content changed."""
        perm_changed = (perm or {}).get("requestId") != (self.perm or {}).get("requestId")
        rev_changed = item.rev != self.rev
        ctx_changed = ctx is not self.ctx
        self.item, self.ctx, self.perm = item, ctx, perm
        self.rev = item.rev
        self.meta = tool_meta(item.name, item.input, ctx.cwd)
        self.file = file_path_of(item.name, item.input) if is_file_tool(item.name) else None
        changed: List[Block] = []
        if not (rev_changed or perm_changed or ctx_changed or first):
            return changed
        self.sig = (item.rev, item.status, (perm or {}).get("requestId"), ctx.view, self.open)
        # chrome
        self.set_chrome(
            head_h=HEAD_H_WAIT if self.awaiting else HEAD_H, pad_top=4, pad_bottom=0 if self.awaiting else 10
        )
        # body
        if self.body.sync(item, ctx):
            changed.append(self.body)
        # agent children
        kids = item.children or self._loaded_items or []
        if kids and self.child_builder is not None:
            if self.children_group is None:
                self.children_group = AgentChildren(self.child_builder, kids, ctx.with_depth(ctx.depth + 1))
                self.insert(1, self.children_group)
                changed.append(self.children_group)
            else:
                changed.extend(self.children_group.sync(kids, ctx.with_depth(ctx.depth + 1)))
        elif self.children_group is not None:
            self.remove(self.children_group.index)
            self.children_group = None
        if item.name in ("Agent", "Task"):
            if self.reports is None:
                self.reports = ToolBodyBlock(item, ctx, "reports")
                self.reports.state["children"] = kids
                self.insert(1 + (1 if self.children_group is not None else 0), self.reports)
                changed.append(self.reports)
            else:
                self.reports.state["children"] = kids
                if self.reports.sync(item, ctx):
                    changed.append(self.reports)
        # the permission prompt
        if self.awaiting and self.perm_island is None:
            self.perm_island = PermissionIsland(perm, ctx, item)
            self.append(self.perm_island)
            changed.append(self.perm_island)
        elif self.awaiting and perm_changed and self.perm_island is not None:
            self.perm_island.update(perm, ctx, item)
            changed.append(self.perm_island)
        elif not self.awaiting and self.perm_island is not None:
            self.remove(self.perm_island.index)
            self.perm_island.drop_widget()
            self.perm_island = None
        # open by default while proposed, waiting or failed; closed again once done, unless the person chose
        want = self.wants_open
        if want != self.open:
            self.open = want
            self.set_reveal(1.0 if want else 0.0)
        return changed

    def set_loaded_children(self, items: List[Item]) -> None:
        self._loaded_items = items
        self.sync(self.item, self.ctx, self.perm, first=True)

    # ---- heights
    def measure(self, width: int, m: Measurer) -> int:
        self.warm = True
        return self.h

    def estimate(self, width: int, m: Measurer) -> int:
        return self.h

    # ---- paint
    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        t = st.t
        if self.awaiting:
            rounded(p, QRect(x - 10, y, w + 20, self.h), QColor(t.raised), QColor(t.hair_2), 12)
        head_h = self.head_h
        hover = st.hover is not None and st.hover.kind == "head"
        if hover:
            paint_head_tint(p, QRect(x - 6, y, w + 6, head_h), st)
        it = self.item
        meta = self.meta
        # status column, 18 wide
        icon_y = y + (head_h - 14) // 2
        self._paint_status(p, x + 2, icon_y, st)
        # verb · target
        cx = x + 28
        fv = st.m.font(theme.FS_UI)
        if meta.verb:
            st.m.draw_text(p, cx, y + (head_h - st.m.line_h(fv)) / 2, meta.verb, fv, QColor(t.text_3))
            cx += st.m.text_w(meta.verb, fv) + 7
        struck = it.status in ("denied", "interrupted")
        ft = st.m.font(12.5 if meta.mono else theme.FS_UI, 400, meta.mono)
        if struck:
            from PySide6.QtGui import QFont

            ft = QFont(ft)
            ft.setStrikeOut(True)
        meta_w = self._meta_width(st)
        chev_w = 13 + 7
        open_w = (13 + 7) if self.file else 0
        avail = max(40, (x + w) - cx - meta_w - chev_w - open_w - 8)
        shown = st.m.elide(meta.target, ft, int(avail))
        st.m.draw_text(p, cx, y + (head_h - st.m.line_h(ft)) / 2, shown, ft, QColor(t.text_3 if struck else t.text))
        cx += st.m.text_w(shown, ft) + 7
        # the chevron is always there, quiet; brighter on hover or when open
        chevron(
            p,
            int(cx),
            y + (head_h - 13) // 2,
            13,
            self.reveal,
            QColor(t.text_3),
            st,
            1.0 if (hover or self.open) else 0.45,
        )
        cx += chev_w
        if self.file:
            on = st.hover is not None and st.hover.kind == "button" and st.hover.id == "open-file"
            p.setOpacity(1.0 if (hover or on) else 0.0 if st.caching else 0.0)
            if hover or on:
                if on:
                    rounded(
                        p,
                        QRect(int(cx) - 4, y + (head_h - 22) // 2, 22, 22),
                        theme.blend(t.fill_2, t.canvas),
                        None,
                        theme.R2,
                    )
                p.drawPixmap(
                    int(cx), y + (head_h - 13) // 2, st.m.icon("arrow-up-right", QColor(t.text if on else t.text_3), 13)
                )
            p.setOpacity(1.0)
            self._meta_rects["open-file"] = QRect(int(cx) - x - 4, (head_h - 22) // 2, 22, 22)
        # meta, right-aligned
        self._paint_meta(p, x + w, y, head_h, st)
        # the rule down the left of an open body (the agent children get their own rail)
        _ = x

    def _paint_status(self, p: QPainter, x: int, y: int, st: PaintState) -> None:
        t = st.t
        it = self.item
        if self.awaiting:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.color("accent_wash"))
            p.drawEllipse(QRectF(x + 0.5, y + 0.5, 13, 13))
            p.setBrush(t.color("accent"))
            p.drawEllipse(QRectF(x + 3.5, y + 3.5, 7, 7))
            return
        stt = it.status
        if stt in ("streaming", "running"):
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(t.text_3), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            ang = int((st.now % 1.0) * 360)
            p.drawArc(QRectF(x + 1.5, y + 1.5, 11, 11), -ang * 16, 270 * 16)
            return
        name, color = {
            "error": ("x", t.err),
            "denied": ("ban", t.err),
            "interrupted": ("circle-stop", t.text_3),
            "background": ("circle-dot", t.info),
        }.get(stt, (self.meta.icon_name, t.text_2))
        p.drawPixmap(x, y, st.m.icon(name, QColor(color), 14))

    def _meta_parts(self, st: PaintState) -> List[Tuple[str, str, str]]:
        """(kind, text, tone): badges and figures at the right of the head."""
        it = self.item
        t = st.t
        out: List[Tuple[str, str, str]] = []
        stt = it.status
        if stt == "background":
            out.append(("badge", "background", "info"))
        if stt == "denied":
            out.append(("badge", "declined", "err"))
        if stt == "error":
            out.append(("text", "Failed", t.err))
        if stt == "interrupted":
            out.append(("badge", "stopped", ""))
        if it.name in ("Workflow", "RunWorkflow"):
            rt = store.rt.get(self.ctx.session_id)
            tasks = [x for x in ((rt.get("tasks") if rt else None) or []) if isinstance(x, dict)]
            task = next((x for x in tasks if x.get("toolUseId") == it.id), None)
            counts = workflow_counts(((task or {}).get("workflow") or {}).get("agents") or [])
            if task and counts.total:
                out.append(("micro", f"{counts.done}/{counts.total} agents", t.text_3))
        stats = None if stt == "error" else diff_stats(it)
        has_stats = bool(stats and (stats["add"] > 0 or stats["del"] > 0))
        if stats and has_stats:
            if stats["add"] > 0:
                out.append(("mono", f"+{stats['add']}", t.ok))
            if stats["del"] > 0:
                out.append(("mono", f"−{stats['del']}", t.err))
        ms = elapsed_ms(it)
        if stt in ("running", "streaming") and it.start_ts:
            ms = time.time() * 1000 - it.start_ts
        if ms > 1500 and not has_stats:
            out.append(("micro", duration(ms), t.text_3))
        return out

    def _meta_width(self, st: PaintState) -> int:
        w = 0
        for kind, text, _tone in self._meta_parts(st):
            if kind == "badge":
                w += int(st.m.text_w(text, st.m.font(11, 600))) + 14 + 8
            elif kind == "mono":
                w += int(st.m.text_w(text, st.m.font(11.5, 400, True))) + 5
            else:
                w += int(st.m.text_w(text, st.m.font(theme.FS_MICRO, 600 if text == "Failed" else 400))) + 8
        return w

    def _paint_meta(self, p: QPainter, right: int, y: int, head_h: int, st: PaintState) -> None:
        x = right
        for kind, text, tone in reversed(self._meta_parts(st)):
            if kind == "badge":
                f = st.m.font(11, 600)
                bw = int(st.m.text_w(text, f)) + 14
                x -= bw
                t = st.t
                fg, bg = {"err": (t.err, t.err_wash), "info": (t.info, t.info_wash)}.get(tone, (t.text_2, t.fill_2))
                rounded(p, QRect(x, y + (head_h - 19) // 2, bw, 19), theme.blend(bg, t.canvas), None, 9)
                st.m.draw_text(p, x + 7, y + (head_h - st.m.line_h(f)) / 2, text, f, QColor(fg))
                x -= 8
            elif kind == "mono":
                f = st.m.font(11.5, 400, True)
                tw = st.m.text_w(text, f)
                x -= tw
                st.m.draw_text(p, x, y + (head_h - st.m.line_h(f)) / 2, text, f, QColor(tone))
                x -= 5
            else:
                f = st.m.font(theme.FS_MICRO, 600 if text == "Failed" else 400)
                tw = st.m.text_w(text, f)
                x -= tw
                st.m.draw_text(p, x, y + (head_h - st.m.line_h(f)) / 2, text, f, QColor(tone))
                x -= 8

    # ---- interaction
    def hit(self, x: int, y: int) -> Optional[Target]:
        if y >= self.head_h:
            return None
        r = self._meta_rects.get("open-file")
        if self.file and r is not None and r.contains(x, y):
            return Target("button", "open-file", (r.x(), r.y(), r.width(), r.height()))
        return Target("head", "tool", (0, 0, self.h_width or 0, self.head_h))

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor

    def tooltip_for(self, target: Target) -> str:
        if target.kind == "button" and target.id == "open-file":
            return "Open in Files"
        return self.meta.target if len(self.meta.target) > 60 else ""

    def activate(self, target: Target, view: Any) -> None:
        if target.kind == "button" and target.id == "open-file" and self.file:
            store.open_file(self.file)
            return
        if target.kind == "head":
            self.user_open = not self.open
            view.toggle_group(self, self.user_open)

    def text(self) -> str:
        return f"{self.meta.verb} {self.meta.target}".strip()

    def live(self) -> bool:
        return self.item.status in ("running", "streaming") or self.awaiting

    def tick(self, now: float) -> Optional[Tuple[int, int, int, int]]:
        return (0, 0, 100000, self.head_h) if self.item.status in ("running", "streaming") else None

    @property
    def static(self) -> bool:
        return not self.live()


class AgentChildren(Group):
    """The sub-agent's own steps, behind a rail."""

    KIND = "agent-children"
    CHILD_INDENT = 15

    def __init__(self, builder: ChildBuilder, items: List[Item], ctx: ToolCtx) -> None:
        super().__init__(f"agent-children:{id(self)}", head_h=0, pad_top=0, pad_bottom=0, open_=True)
        self.shaped = True
        self.builder = builder
        self.inner = builder(items, ctx)
        self.append(self.inner)

    def sync(self, items: List[Item], ctx: ToolCtx) -> List[Block]:
        fn = getattr(self.inner, "sync", None)
        return fn(items, ctx) if fn is not None else []

    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        vline(p, x + 1, y, self.h, st.t.color("hair_2"))


class PermissionIsland(IslandBlock):
    """The permission prompt as a real widget under the tool row."""

    def __init__(self, perm: Json, ctx: ToolCtx, item: ToolItem) -> None:
        self.perm, self.ctx, self.item = perm, ctx, item
        super().__init__(f"perm:{item.id}", self._make)

    def _make(self):
        from ...widgets.transcript.permission import PermissionCard

        return PermissionCard(self.perm, self.ctx, self.item, stale=False)

    def update(self, perm: Json, ctx: ToolCtx, item: ToolItem) -> None:
        self.perm, self.ctx, self.item = perm, ctx, item
        self.drop_widget()
        self.warm = False


class SpecialIsland(IslandBlock):
    """AskUserQuestion / ExitPlanMode: the form while Claude waits, the record after."""

    MT = 12
    MB = 12

    def __init__(self, item: ToolItem, ctx: ToolCtx, perm: Optional[Json]) -> None:
        self.item, self.ctx, self.perm = item, ctx, perm
        self.sig = None
        super().__init__(f"special:{item.id}", self._make)
        self.rev = item.rev

    def _make(self):
        from ...widgets.transcript.bodies.ask import AskBody
        from ...widgets.transcript.bodies.plan import PlanBody

        if self.item.name == "AskUserQuestion":
            return AskBody(self.item, self.ctx, self.perm)
        return PlanBody(self.item, self.ctx, self.perm)

    def sync(self, item: ToolItem, ctx: ToolCtx, perm: Optional[Json]) -> List[Block]:
        sig = (item.rev, item.status, (perm or {}).get("requestId"))
        changed = sig != self.sig
        self.item, self.ctx, self.perm = item, ctx, perm
        self.rev = item.rev
        if changed:
            self.sig = sig
            self.drop_widget()
            self.warm = False
            return [self]
        return []

    def text(self) -> str:
        m = tool_meta(self.item.name, self.item.input, self.ctx.cwd)
        return f"{m.verb} {m.target}".strip()
