"""The rows between the words: notices, dividers, compactions, command output, the turn's footer,
the working line, the loading skeleton."""

from __future__ import annotations

import html
import random
import re
import time
from typing import Any, Callable, Optional, Tuple

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter

from ... import theme
from ...lib.fmt import cost, duration, tokens
from ...lib.transcript_model import CommandOutputItem, CompactItem, NoticeItem, ResultItem
from ...store import store
from .. import nodes as nd
from ..paint import Measurer, PaintState, rounded
from ..scene import Block, Target
from .base import NodeBlock

VERBS = [
    "Reading",
    "Drafting",
    "Composing",
    "Outlining",
    "Revising",
    "Proofing",
    "Collating",
    "Annotating",
    "Indexing",
    "Marking up",
    "Typesetting",
    "Kerning",
]


# ---------------------------------------------------------------- dividers


class DividerBlock(Block):
    """A centred line of micro text with hairlines either side."""

    KIND = "divider"
    __slots__ = ("label",)

    def __init__(self, key: str, label: str) -> None:
        super().__init__(key)
        self.label = label

    def estimate(self, width: int, m: Measurer) -> int:
        return 18 + 16 + 18

    def measure(self, width: int, m: Measurer) -> int:
        self.warm = True
        return 18 + 16 + 18

    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        f = st.m.font(theme.FS_MICRO)
        tw = st.m.text_w(self.label, f)
        cy = y + 18 + 8
        hair = theme.blend(st.t.hair, st.t.canvas)
        left_w = int((w - tw - 24) / 2)
        p.fillRect(QRect(x, cy, max(0, left_w), 1), hair)
        p.fillRect(QRect(x + w - left_w, cy, max(0, left_w), 1), hair)
        st.m.draw_text(p, x + (w - tw) / 2, y + 18 + (16 - st.m.line_h(f)) / 2, self.label, f, QColor(st.t.text_3))

    def text(self) -> str:
        return self.label


def compact_label(it: CompactItem) -> str:
    text = "Conversation compacted" + ("" if it.trigger == "manual" else " automatically")
    if it.pre_tokens:
        text += f" · {tokens(it.pre_tokens)} → {tokens(it.post_tokens) if it.post_tokens else 'summary'}"
    return text


# ---------------------------------------------------------------- notices


def notice_mode(it: NoticeItem) -> str:
    if it.tag in ("divider", "interrupt"):
        return "divider"
    if it.tag in ("task", "retry-ok", "cli-info"):
        return str(it.tag)
    return "notice"


class NoticeBlock(NodeBlock):
    KIND = "notice"
    __slots__ = ("item", "open")

    def __init__(self, item: NoticeItem) -> None:
        self.item = item
        self.open = False
        mode = notice_mode(item)
        pad = (0, 10, 0, 10) if mode in ("notice", "task") else (0, 6, 0, 6)
        super().__init__(f"notice:{item.id}", self._build, pad, est_h=40)
        self.on("more", self._toggle)
        self.sync(item)

    def sync(self, item: NoticeItem) -> bool:
        changed = item.rev != self.rev
        self.item = item
        self.rev = item.rev
        self.sig = (item.rev, self.open)
        if changed:
            self.rebuild()
        return changed

    def _toggle(self, _t: Target, view: Any) -> None:
        self.open = not self.open
        self.sig = (self.item.rev, self.open)
        self.rebuild()
        view.remeasure(self)

    def _build(self, m: Measurer) -> nd.Node:
        it = self.item
        mode = notice_mode(it)
        t = m.tokens
        if mode == "cli-info":
            first = re.split(r"(?<=\.)\s", it.text)[0]
            body = (
                nd.Markdown(it.text, compact=True, quiet=True)
                if self.open
                else nd.Text(first, theme.FS_SMALL, 400, "text_3", wrap=True, line_h=19)
            )
            more = (
                nd.Button("Less" if self.open else "More", "more", kind="link") if len(it.text) > len(first) else None
            )
            return nd.hstack(
                nd.Icon("info", "text_3", 12), nd.vstack(body, more, spacing=2), spacing=8, stretch=[1], valign="top"
            )
        if mode in ("task", "retry-ok"):
            warn = it.level == "warning"
            icon = ("circle-alert" if warn else "circle-check") if mode == "task" else "info"
            color = ("accent_text" if warn else "ok") if mode == "task" else "text_3"
            return nd.hstack(
                nd.Icon(icon, color, 12),
                nd.Text(it.text, theme.FS_SMALL, 400, "text_3", wrap=True, line_h=19),
                spacing=8,
                stretch=[1],
                valign="top",
            )
        # a notice on a wash
        bg = t.err_wash if it.level == "error" else t.accent_wash if it.level == "warning" else t.fill_1
        icon = "circle-alert" if it.level == "error" else "triangle-alert" if it.level == "warning" else "info"
        color = "err" if it.level == "error" else "accent_text" if it.level == "warning" else "text_3"
        count = f' <span style="color:{t.text_3}">×{it.count}</span>' if it.count and it.count > 1 else ""
        text = nd.Text(
            f'<p style="margin:0;line-height:20px">{html.escape(it.text)}{count}</p>',
            theme.FS_UI,
            400,
            "text",
            wrap=True,
            html=True,
        )
        col = nd.vstack(text, spacing=2)
        if it.detail:
            if self.open:
                col.add(
                    nd.Surface(
                        nd.Text(
                            f'<div style="white-space:pre-wrap;line-height:19px">{html.escape(it.detail)}</div>',
                            12,
                            400,
                            "text_2",
                            mono=True,
                            html=True,
                        ),
                        "inset",
                        None,
                        theme.R2,
                        (10, 8, 10, 8),
                    )
                )
            col.add(nd.Button("Hide details" if self.open else "Details", "more", kind="link"))
        inner = nd.hstack(nd.Icon(icon, color, 14), col, spacing=10, stretch=[1], valign="top")
        # the wash is painted by a Surface with a blended colour name: resolve here
        surf = nd.Surface(inner, None, None, theme.R3, (14, 10, 14, 10))
        return _Washed(surf, theme.blend(bg, t.canvas))

    def text(self) -> str:
        return self.item.text


class _Washed(nd.Node):
    """A surface painted on a given colour (the notice washes are blends, not tokens)."""

    __slots__ = ("child", "color")

    def __init__(self, child: nd.Node, color: QColor) -> None:
        super().__init__()
        self.child, self.color = child, color

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w = x, y, w
        self.h = self.child.layout(x, y, w, m)
        return self.h

    def shift(self, dx: int, dy: int) -> None:
        super().shift(dx, dy)
        self.child.shift(dx, dy)

    def paint(self, p: QPainter, ox: int, oy: int, st: PaintState) -> None:
        rounded(p, QRect(ox + self.x, oy + self.y, self.w, self.h), self.color, None, theme.R3)
        self.child.paint(p, ox, oy, st)

    def hit(self, x: int, y: int):
        return self.child.hit(x, y)

    def targets(self):
        return self.child.targets()

    def text(self) -> str:
        return self.child.text()

    def cool(self) -> None:
        self.child.cool()


# ---------------------------------------------------------------- command output


class CommandOutputBlock(NodeBlock):
    KIND = "command-output"
    __slots__ = ("item", "cwd")

    def __init__(self, item: CommandOutputItem, cwd: str) -> None:
        self.item = item
        self.cwd = cwd
        super().__init__(f"cmd:{item.id}", self._build, (0, 10, 0, 10), est_h=80)
        self.sync(item)

    def sync(self, item: CommandOutputItem) -> bool:
        changed = item.rev != self.rev
        self.item = item
        self.rev = item.rev
        self.sig = (item.rev,)
        if changed:
            self.rebuild()
        return changed

    def _build(self, m: Measurer) -> nd.Node:
        head = nd.hstack(
            nd.Icon("square-terminal", "text_3", 12),
            nd.Text("Command output", theme.FS_MICRO, 600, "text_3", wrap=False),
            spacing=6,
        )
        body = nd.Markdown(self.item.text, compact=True, cwd=self.cwd)
        inner = nd.vstack(head, body, spacing=6)
        return _Washed(
            nd.Surface(inner, None, None, theme.R3, (14, 10, 14, 10)), theme.blend(m.tokens.fill_1, m.tokens.canvas)
        )

    def text(self) -> str:
        return self.item.text


# ---------------------------------------------------------------- the turn's footer


class ResultBlock(Block):
    """✱ duration · tokens · cost; the detail and "Fork from here" show while the pointer is on the row."""

    KIND = "result"
    __slots__ = ("item", "sid", "_fork_rect")

    def __init__(self, item: ResultItem, sid: str) -> None:
        super().__init__(f"result:{item.id}")
        self.item = item
        self.sid = sid
        self._fork_rect = QRect()
        self.sync(item)

    def sync(self, item: ResultItem) -> bool:
        changed = item.rev != self.rev
        self.item = item
        self.rev = item.rev
        self.sig = (item.rev,)
        return changed

    def estimate(self, width: int, m: Measurer) -> int:
        return 8 + 20 + 20

    def measure(self, width: int, m: Measurer) -> int:
        self.warm = True
        return 8 + 20 + 20

    def _detail(self) -> str:
        it = self.item
        parts = []
        if it.output_tokens:
            parts.append(f"{tokens(it.output_tokens)} tokens")
        if it.cost_usd:
            parts.append(cost(it.cost_usd))
        return " · ".join(parts)

    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        it = self.item
        t = st.t
        f = st.m.font(theme.FS_MICRO)
        cy = y + 8
        fm = st.m.font(12)
        st.m.draw_text(p, x, cy + (20 - st.m.line_h(fm)) / 2, "✱", fm, QColor(t.accent))
        cx = x + 14
        dur = duration(it.duration_ms)
        st.m.draw_text(p, cx, cy + (20 - st.m.line_h(f)) / 2, dur, f, QColor(t.text_3))
        cx += st.m.text_w(dur, f) + 8
        hovered = st.hover is not None
        if hovered:
            d = self._detail()
            if d:
                st.m.draw_text(p, cx, cy + (20 - st.m.line_h(f)) / 2, d, f, QColor(t.text_3))
                cx += st.m.text_w(d, f) + 8
            if it.chain_uuid:
                on = st.hover.kind == "button"
                color = QColor(t.text if on else t.text_3)
                p.drawPixmap(int(cx), cy + 4, st.m.icon("git-fork", color, 12))
                label = "Fork from here"
                st.m.draw_text(p, cx + 16, cy + (20 - st.m.line_h(f)) / 2, label, f, color)
                lw = st.m.text_w(label, f)
                self._fork_rect = QRect(int(cx - x), 8, int(16 + lw), 20)

    def hit(self, x: int, y: int) -> Optional[Target]:
        if self.item.chain_uuid and self._fork_rect.contains(x, y):
            return Target(
                "button",
                "fork",
                (self._fork_rect.x(), self._fork_rect.y(), self._fork_rect.width(), self._fork_rect.height()),
            )
        return Target("row", "result", (0, 0, self.h_width or 0, self.h))

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor if target.kind == "button" else None

    def tooltip_for(self, target: Target) -> str:
        if target.kind == "button":
            return "Start a new session that continues from this point"
        it = self.item
        tip = duration(it.duration_ms)
        if it.output_tokens:
            tip += f" · {tokens(it.output_tokens)} tokens out"
        if it.input_tokens:
            tip += f" · {tokens(it.input_tokens)} in"
        if it.cost_usd:
            tip += f" · {cost(it.cost_usd)}"
        return tip

    def activate(self, target: Target, view: Any) -> None:
        if target.kind == "button":
            store.fork_session(self.sid, self.item.chain_uuid)

    def text(self) -> str:
        return ""


def result_notice(it: ResultItem) -> NoticeItem:
    text = (
        "Stopped: reached the turn limit"
        if it.subtype == "error_max_turns"
        else "Stopped: reached the budget limit"
        if it.subtype == "error_max_budget_usd"
        else it.text or "The turn ended with an error"
    )
    return NoticeItem(
        id=it.id, level="error", text=text, detail="\n".join(it.errors) if it.errors else None, rev=it.rev
    )


# ---------------------------------------------------------------- the working line


class WorkingBlock(Block):
    """The breathing mark, a verb from the composing room, elapsed time and thinking tokens, while a
    turn runs and no fold on the page carries the mark."""

    KIND = "working"
    __slots__ = ("sid", "verb", "start_ts", "shown", "status", "compacting", "thinking_tokens", "_t0")

    def __init__(self, sid: str) -> None:
        super().__init__("working")
        self.sid = sid
        self.verb = random.choice(VERBS)
        self.start_ts = 0.0
        self.shown = False
        self.status = ""
        self.compacting = False
        self.thinking_tokens = 0
        self._t0 = time.monotonic()

    def sync(self, start_ts: float, quiet: bool) -> bool:
        r = store.rt.get(self.sid)
        status = r.status if r else ""
        compacting = bool(r and r.st.get("compacting"))
        shown = status in ("running", "starting") and (not quiet or compacting)
        sig = (shown, status, compacting, int(start_ts))
        changed = sig != self.sig
        self.sig = sig
        self.shown, self.status, self.compacting, self.start_ts = shown, status, compacting, start_ts
        self.thinking_tokens = int(r.thinking_tokens) if r else 0
        return changed

    def estimate(self, width: int, m: Measurer) -> int:
        return 10 + 30 + 4 if self.shown else 0

    def measure(self, width: int, m: Measurer) -> int:
        self.warm = True
        return 10 + 30 + 4 if self.shown else 0

    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        if not self.shown:
            return
        t = st.t
        cy = y + 10
        draw_mark(p, QRectF(x, cy + 7, 15, 15), t, 0.55 + 0.45 * (0.5 - 0.5 * _cos((st.now - self._t0) % 1.8 / 1.8)))
        f = st.m.font(theme.FS_UI, 500)
        text = (
            "Compacting the conversation"
            if self.compacting
            else "Starting Claude Code"
            if self.status == "starting"
            else f"{self.verb}…"
        )
        st.m.draw_text(p, x + 25, cy + (30 - st.m.line_h(f)) / 2, text, f, QColor(t.text_3))
        cx = x + 25 + st.m.text_w(text, f) + 10
        elapsed = time.time() * 1000 - self.start_ts if self.start_ts else 0
        meta = duration(elapsed) if elapsed > 1000 else ""
        if self.thinking_tokens:
            meta += f"{' · ' if meta else ''}{tokens(self.thinking_tokens)} thinking tokens"
        if meta:
            f2 = st.m.font(theme.FS_SMALL)
            st.m.draw_text(p, cx, cy + (30 - st.m.line_h(f2)) / 2, meta, f2, QColor(t.text_3))

    def live(self) -> bool:
        return self.shown

    def tick(self, now: float) -> Optional[Tuple[int, int, int, int]]:
        return (0, 0, 100000, self.h) if self.shown else None

    @property
    def static(self) -> bool:
        return False


def _cos(phase: float) -> float:
    import math

    return math.cos(phase * 2 * math.pi)


def draw_mark(p: QPainter, r: QRectF, t: theme.Tokens, bowl_alpha: float = 1.0, ink: Optional[str] = None) -> None:
    """The pilcrow ¶ from the brand SVG (viewBox 4.5 4 14 16)."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = r.height() / 16.0
    x0, y0 = r.x() - 4.5 * s, r.y() - 4 * s
    bowl = QColor(t.accent)
    bowl.setAlphaF(bowl_alpha)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bowl)
    p.drawEllipse(QPointF(x0 + 10.6 * s, y0 + 9.2 * s), 4.1 * s, 4.1 * s)
    p.setBrush(QColor(ink or t.text_2))
    rx = 0.5 * s
    p.drawRoundedRect(QRectF(x0 + 10.6 * s, y0 + 5.1 * s, 6.6 * s, 1.9 * s), rx, rx)
    p.drawRoundedRect(QRectF(x0 + 12.4 * s, y0 + 5.1 * s, 1.9 * s, 13.8 * s), rx, rx)
    p.drawRoundedRect(QRectF(x0 + 15.3 * s, y0 + 5.1 * s, 1.9 * s, 13.8 * s), rx, rx)


# ---------------------------------------------------------------- the loading skeleton


class SkeletonBlock(Block):
    KIND = "skeleton"
    __slots__ = ("shown",)

    def __init__(self) -> None:
        super().__init__("skeleton")
        self.shown = True

    def estimate(self, width: int, m: Measurer) -> int:
        return 6 + 14 * 3 + 10 * 2 if self.shown else 0

    measure = estimate

    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        if not self.shown:
            return
        c = theme.blend(st.t.fill_1, st.t.canvas)
        cy = y + 6
        for short in (False, True, False):
            rounded(p, QRect(x, cy, min(w, 260) if short else w, 14), c, None, theme.R2)
            cy += 24

    @property
    def static(self) -> bool:
        return True


class GapBlock(Block):
    """Empty room (the pads between units that the web's margins made)."""

    KIND = "gap"
    __slots__ = ("gap",)

    def __init__(self, key: str, gap: int) -> None:
        super().__init__(key)
        self.gap = gap
        self.h = gap

    def estimate(self, width: int, m: Measurer) -> int:
        return self.gap

    measure = estimate


__all__ = [
    "CommandOutputBlock",
    "DividerBlock",
    "GapBlock",
    "NoticeBlock",
    "ResultBlock",
    "SkeletonBlock",
    "WorkingBlock",
    "compact_label",
    "draw_mark",
    "result_notice",
]

_ = (Callable,)
