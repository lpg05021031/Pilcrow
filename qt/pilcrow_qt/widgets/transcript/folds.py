"""How a turn's work is folded (the Flat/Steps/Explore/WorkFold/Chapters parts of Transcript.tsx).

Whatever Claude says stays on the page; each stretch of work between paragraphs folds to one line
(``WorkFold``) that opens to chapters: what Claude said along the way as headings, the steps under
them. Inside, runs of tool calls are ``Steps``; quiet read-only steps that finished cleanly fold
into one "Explored" line. Every container here reconciles against what it drew last: rows are
appended as items arrive, a row with a moved ``rev`` is refreshed, and only a changed shape (a
chapter gaining a heading, a run folding into "Explored") rebuilds from that point on.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Union

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ... import icons, theme
from ...lib.fmt import basename, duration, truncate
from ...lib.tools_meta import str_
from ...lib.transcript_model import Item, TextItem, ThinkingItem, ToolItem
from ...lib.units import Unit, WorkUnit
from ...store import store
from ..ui.bits import WorkingMark
from ..ui.collapse import Collapse
from ..ui.signals import bind
from .ctx import ToolCtx
from .items import (
    BUSY,
    AssistantText,
    CommandOutput,
    Compact,
    Head,
    MarkdownView,
    Notice,
    ResultFooter,
    Row,
    Thinking,
    UserMessage,
    icon_label,
    pt,
    row_margins,
    set_icon,
    style_label,
)


def faded_pixmap(name: str, color: str, size: int, alpha: float):
    """A tinted icon drawn at an opacity (the chevrons of transcript.css sit at 0.7 until hovered)."""
    pm = icons.pixmap(name, color, size)
    if alpha >= 1.0:
        return pm
    out = QPixmap(pm.size())
    out.setDevicePixelRatio(pm.devicePixelRatio())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setOpacity(alpha)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


# Which folds you opened in the conversation on screen. Opening a conversation starts clean.
turn_open: Dict[str, bool] = {}
chapter_open: Dict[str, bool] = {}


def forget_folds() -> None:
    turn_open.clear()
    chapter_open.clear()


# ---------------------------------------------------------------- tool cards (another module; a placeholder until it lands)


def make_tool_card(
    item: ToolItem, ctx: ToolCtx, perm: Optional[dict] = None, parent: Optional[QWidget] = None
) -> QWidget:
    """``ToolCard(item, ctx, perm)`` from tool_card.py, or a one-line stand-in while that module is being written."""
    try:
        from .tool_card import ToolCard  # type: ignore[import-not-found]
    except ImportError:
        return FallbackToolCard(item, ctx, perm, parent)
    return ToolCard(item, ctx, perm, parent)


def make_permission_card(
    perm: dict, ctx: ToolCtx, item: Optional[ToolItem] = None, stale: bool = False, parent: Optional[QWidget] = None
) -> QWidget:
    try:
        from .permission import PermissionCard  # type: ignore[import-not-found]
    except ImportError:
        return FallbackPermission(perm, ctx, parent)
    return PermissionCard(perm, ctx, item, stale, parent)


class FallbackToolCard(Row):
    """Icon, verb and target on one line: enough to see the conversation take shape without the real cards."""

    def __init__(
        self, item: ToolItem, ctx: ToolCtx, perm: Optional[dict] = None, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.item = item
        self.ctx = ctx
        self.perm = perm
        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)
        self.setMinimumHeight(28)
        self.ico = icon_label("wrench", theme.tokens().text_3, 14)
        hl.addWidget(self.ico)
        self.verb = QLabel()
        hl.addWidget(self.verb)
        self.target = QLabel()
        hl.addWidget(self.target, 1)
        self.refresh(item.rev)
        bind(theme.theme.changed, self, lambda _t: self._draw())

    def _draw(self) -> None:
        from ...lib.tools_meta import tool_meta

        t = theme.tokens()
        m = tool_meta(self.item.name, self.item.input, self.ctx.cwd)
        set_icon(self.ico, m.icon_name, t.text_3, 14)
        self.verb.setText(m.verb)
        self.verb.setVisible(bool(m.verb))
        self.target.setText(m.target)
        style_label(self.verb, theme.FS_UI, t.text_3)
        dim = self.item.status in ("denied", "interrupted")
        style_label(
            self.target,
            theme.MONO_SIZE if m.mono else theme.FS_UI,
            t.text_3 if dim else t.text,
            extra=f"font-family: '{theme._MONO_FAMILY or 'monospace'}';" if m.mono else "",
        )

    def find_text(self) -> str:
        from ...lib.tools_meta import tool_meta

        return tool_meta(self.item.name, self.item.input, self.ctx.cwd).target


class FallbackPermission(Row):
    def __init__(self, perm: dict, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        hl = QHBoxLayout(self)
        hl.setContentsMargins(14, 10, 14, 10)
        self.label = QLabel(f"Claude wants to use {perm.get('displayName') or perm.get('toolName') or 'a tool'}")
        hl.addWidget(self.label)


def refresh_card(w: QWidget, item: ToolItem, ctx: ToolCtx) -> None:
    """``refresh(rev)`` is the contract; the real card also takes the context and the request."""
    fn = getattr(w, "refresh", None)
    if fn is None:
        return
    try:
        fn(item.rev, ctx, ctx.pending.get(item.id))
    except TypeError:
        fn(item.rev)


# ---------------------------------------------------------------- pure helpers (the functions at the top of Transcript.tsx)

SPECIAL = ("AskUserQuestion", "ExitPlanMode")
QUIET = {
    "Read",
    "Grep",
    "Glob",
    "LS",
    "WebSearch",
    "WebFetch",
    "ToolSearch",
    "BashOutput",
    "TaskList",
    "TaskGet",
    "NotebookRead",
}


def tool_word(t: ToolItem) -> str:
    n = t.name
    if n in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        return "edit"
    if n in ("Read", "NotebookRead"):
        return "read"
    if n in ("Grep", "Glob", "LS", "WebSearch", "WebFetch", "ToolSearch"):
        return "search"
    if n in ("Bash", "PowerShell", "BashOutput"):
        return "command"
    if n in ("Task", "Agent", "Workflow", "RunWorkflow"):
        return "agent"
    if n.startswith("mcp__"):
        return "mcp"
    return "other"


def live_label(work: List[Item]) -> Optional[str]:
    """What Claude is doing right now, for the fold line of a turn that is still running."""
    for it in reversed(work):
        if it.kind == "tool" and it.status in ("running", "streaming"):
            file = str_(it.input, "file_path") or str_(it.input, "notebook_path")
            w = tool_word(it)
            if w == "edit":
                return f"Editing {basename(file)}" if file else "Editing a file"
            if w == "read":
                return f"Reading {basename(file)}" if file else "Reading a file"
            if w == "search":
                return "Searching"
            if w == "command":
                d = it.input.get("description") if isinstance(it.input, dict) else None
                return truncate(str(d), 64) if d else "Running a command"
            if w == "agent":
                return "Running an agent"
            if w == "mcp":
                return "Using a connector"
            return f"Using {it.name}"
        if it.kind == "thinking" and it.streaming:
            return "Thinking"
    return None


@dataclass
class Summary:
    text: str
    failed: int
    steps: int


def work_summary(work: List[Item]) -> Summary:
    tools = [it for it in work if it.kind == "tool"]
    files: Set[str] = set()
    reads = searches = commands = agents = mcp = other = failed = 0
    for t in tools:
        if t.status in ("error", "denied"):
            failed += 1
        w = tool_word(t)
        if w == "edit":
            files.add(str_(t.input, "file_path") or str_(t.input, "notebook_path") or t.id)
        elif w == "read":
            reads += 1
        elif w == "search":
            searches += 1
        elif w == "command":
            commands += 1
        elif w == "agent":
            agents += 1
        elif w == "mcp":
            mcp += 1
        else:
            other += 1
    for it in work:
        if it.kind == "notice" and it.level == "error":
            failed += 1
    parts: List[str] = []
    if files:
        parts.append(f"edited {'a file' if len(files) == 1 else f'{len(files)} files'}")
    if commands:
        parts.append(f"ran {'a command' if commands == 1 else f'{commands} commands'}")
    if reads:
        parts.append(f"read {'a file' if reads == 1 else f'{reads} files'}")
    if searches:
        parts.append(f"searched {'once' if searches == 1 else f'{searches} times'}")
    if agents:
        parts.append("ran an agent" if agents == 1 else f"ran {agents} agents")
    if mcp:
        parts.append(f"used {'a connector' if mcp == 1 else f'{mcp} connector tools'}")
    if other:
        parts.append(f"{other} other {'step' if other == 1 else 'steps'}")
    if not parts:
        parts.append("thought it through" if any(it.kind == "thinking" for it in work) else "worked")
    text = ", ".join(parts)
    return Summary(text[:1].upper() + text[1:], failed, len(tools))


@dataclass
class Chapter:
    key: str
    title: Optional[TextItem]
    thought: Optional[ThinkingItem]
    steps: List[Item] = field(default_factory=list)


def chapters_of(items: List[Item]) -> List[Chapter]:
    """Narration becomes a heading with its steps under it; a thought that led straight into the
    narration is tucked onto the same heading."""
    out: List[Chapter] = []
    cur = Chapter("c0", None, None, [])
    pending: Optional[ThinkingItem] = None

    def push() -> None:
        if cur.title or cur.thought or cur.steps:
            out.append(cur)

    for it in items:
        if it.kind == "thinking":
            if pending is not None:
                cur.steps.append(pending)
            pending = it
            continue
        if it.kind == "text":
            push()
            cur = Chapter("c-" + it.id, it, pending, [])
            pending = None
            continue
        if pending is not None:
            cur.steps.append(pending)
            pending = None
        cur.steps.append(it)
    if pending is not None:
        cur.steps.append(pending)
    push()
    return out


@dataclass
class Tally:
    text: str
    failed: int
    ms: float
    n: int


def tally(steps: List[Item]) -> Tally:
    tools = [it for it in steps if it.kind == "tool"]
    counts = {"edit": 0, "command": 0, "read": 0, "search": 0, "agent": 0, "mcp": 0, "other": 0}
    failed = 0
    for t in tools:
        if t.status in ("error", "denied"):
            failed += 1
        counts[tool_word(t)] += 1
    parts: List[str] = []
    for key, one, many in (
        ("edit", "edit", "edits"),
        ("command", "command", "commands"),
        ("read", "read", "reads"),
        ("search", "search", "searches"),
        ("agent", "agent", "agents"),
        ("mcp", "connector call", "connector calls"),
    ):
        n = counts[key]
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    if counts["other"]:
        parts.append(f"{counts['other']} other")
    first = tools[0].start_ts if tools else 0
    last = tools[-1] if tools else None
    ms = ((last.end_ts or last.start_ts) - first) if first and last else 0
    return Tally(" · ".join(parts), failed, ms, len(tools))


def is_quiet(t: ToolItem, ctx: ToolCtx) -> bool:
    if t.id in ctx.pending or t.status != "done":
        return False
    bg = isinstance(t.input, dict) and bool(t.input.get("run_in_background"))
    return t.name in QUIET or (t.name in ("Bash", "PowerShell") and not (t.result and t.result.is_error) and not bg)


def explore_label(items: List[ToolItem]) -> str:
    def n(names: tuple) -> int:
        return sum(1 for t in items if t.name in names)

    parts: List[str] = []
    cmds, reads = n(("Bash", "PowerShell", "BashOutput")), n(("Read", "NotebookRead"))
    searches, web, tasks = (
        n(("Grep", "Glob", "LS", "ToolSearch")),
        n(("WebSearch", "WebFetch")),
        n(("TaskList", "TaskGet")),
    )
    if cmds:
        parts.append("ran a command" if cmds == 1 else f"ran {cmds} commands")
    if reads:
        parts.append("read a file" if reads == 1 else f"read {reads} files")
    if searches:
        parts.append("searched once" if searches == 1 else f"searched {searches} times")
    if web:
        parts.append("checked the web" if web == 1 else f"checked {web} web pages")
    if tasks:
        parts.append("checked tasks")
    text = ", ".join(parts)
    return text[:1].upper() + text[1:]


@dataclass
class StepsGroup:
    key: str
    items: List[ToolItem]


@dataclass
class ItemGroup:
    key: str
    item: Item


Group = Union[StepsGroup, ItemGroup]


def group_items(items: List[Item]) -> List[Group]:
    out: List[Group] = []
    run: List[ToolItem] = []

    def flush() -> None:
        nonlocal run
        if run:
            out.append(StepsGroup("s-" + run[0].id, run))
        run = []

    for it in items:
        if it.kind == "tool" and it.name not in SPECIAL:
            run.append(it)
        else:
            flush()
            out.append(ItemGroup(it.id, it))
    flush()
    return out


def unit_sig(u: Unit, ctx: ToolCtx) -> str:
    """What a unit looks like: its items and their revisions (and whether a permission sits on one)."""
    items = u.work if isinstance(u, WorkUnit) else u.items
    sig = ("W!" if u.live else "W") if isinstance(u, WorkUnit) else "P"
    for it in items:
        sig += f"|{it.id}:{it.rev}"
        if it.kind == "tool" and it.id in ctx.pending:
            sig += "+" + str(ctx.pending[it.id].get("requestId"))
    return sig


# ---------------------------------------------------------------- the stacked list


class Stack(QWidget):
    """Rows stacked with block-margin collapsing: the gap between two rows is the larger of the
    lower one's TOP and the upper one's BOTTOM (what the browser does with ``margin``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.rows: List[QWidget] = []
        self.keys: List[str] = []

    def append(self, key: str, w: QWidget) -> None:
        gap = 0
        if self.rows:
            gap = max(row_margins(self.rows[-1])[1], row_margins(w)[0])
        if self.rows:
            self.lay.addSpacing(gap)
        self.lay.addWidget(w)
        self.rows.append(w)
        self.keys.append(key)

    def truncate(self, n: int) -> None:
        """Drop everything from row ``n`` on."""
        while len(self.rows) > n:
            w = self.rows.pop()
            self.keys.pop()
            self.lay.removeWidget(w)
            w.setParent(None)
            w.deleteLater()
            if self.rows:
                item = self.lay.takeAt(self.lay.count() - 1)  # the spacer before it
                del item

    def first_margin(self) -> int:
        return row_margins(self.rows[0])[0] if self.rows else 0

    def last_margin(self) -> int:
        return row_margins(self.rows[-1])[1] if self.rows else 0


# ---------------------------------------------------------------- steps: runs of tool cards, quiet ones as "Explored"


@dataclass
class ToolSeg:
    item: ToolItem


@dataclass
class ExploreSeg:
    key: str
    items: List[ToolItem]


Seg = Union[ToolSeg, ExploreSeg]


class Explore(Row):
    """One line for a run of quiet steps; opens to the cards."""

    def __init__(self, items: List[ToolItem], ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.items = items
        self.ctx = ctx
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = Head()
        self.head.setMinimumHeight(28)
        self.head.setAccessibleName("Explored")
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)
        self.ico_host = QWidget()  # .tool-ico: an 18px column
        self.ico_host.setFixedSize(18, 20)
        self.ico = icon_label("telescope", theme.tokens().text_3, 14, self.ico_host)
        self.ico.move(2, 3)
        hl.addWidget(self.ico_host)
        self.label = QLabel(explore_label(items))
        hl.addWidget(self.label)
        hl.addSpacing(-3)  # .tool-line gap 7px, chevron margin-left -3px
        self.chev = icon_label("chevron-right", theme.tokens().text_3, 13)
        hl.addWidget(self.chev)
        hl.addStretch(1)
        self.head.clicked.connect(self.toggle)
        self.head.on_hover = lambda _on: self._paint()  # type: ignore[method-assign]
        lay.addWidget(self.head)
        self.body = Collapse(self._build, False)
        lay.addWidget(self.body)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _build(self) -> QWidget:
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(28, 0, 0, 4)
        vl.setSpacing(0)
        for t in self.items:
            vl.addWidget(make_tool_card(t, self.ctx))
        return w

    def toggle(self) -> None:
        self.body.set_open(not self.body.is_open)
        self._paint()

    def set_items(self, items: List[ToolItem], ctx: ToolCtx) -> None:
        self.ctx = ctx
        if [t.id for t in items] != [t.id for t in self.items]:
            self.items = items
            self.label.setText(explore_label(items))
            if self.body.body() is not None:
                old = self.body.body()
                self.body._lay.removeWidget(old)  # noqa: SLF001 - rebuild the lazily built body
                old.deleteLater()
                self.body._body = None  # noqa: SLF001
                if self.body.is_open:
                    self.body._ensure_body()  # noqa: SLF001
        else:
            self.items = items
            body = self.body.body()
            if body is not None:
                for i, t in enumerate(items):
                    w = body.layout().itemAt(i).widget()
                    if w is not None:
                        refresh_card(w, t, ctx)

    def _paint(self) -> None:
        t = theme.tokens()
        set_icon(self.ico, "telescope", t.text_3, 14)
        set_icon(self.chev, "chevron-down" if self.body.is_open else "chevron-right", t.text_3, 13)
        self.chev.setVisible(self.body.is_open or self.head.hover)  # .tool-chev shows on hover or open
        style_label(self.label, theme.FS_UI, t.text)  # .steps .tool-target
        self.head.setStyleSheet(
            f"Head {{ background: {theme.blend(t.fill_1, t.canvas).name() if self.head.hover else 'transparent'}; border-radius: {theme.R2}px; }}"
        )
        self.head.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def find_text(self) -> str:
        return self.label.text()

    def cards(self) -> List[QWidget]:
        body = self.body.body()
        if body is None:
            return []
        return [body.layout().itemAt(i).widget() for i in range(body.layout().count())]


class Steps(Row):
    """A run of tool calls: cards, with quiet finished runs folded into "Explored" and, past ten
    segments, the earlier ones behind an "N earlier steps" line."""

    TOP, BOTTOM = 6, 6
    COLLAPSE_AT = 10

    def __init__(self, items: List[ToolItem], ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.items = items
        self.ctx = ctx
        self.expanded = False
        self._sig = ""
        self.cards: Dict[str, QWidget] = {}
        self.explores: Dict[str, Explore] = {}
        self.segs: List[Seg] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.lay = lay
        self.more = QPushButton()
        self.more.setProperty("kind", "ghost")
        self.more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more.setVisible(False)
        self.more.clicked.connect(self._expand)
        lay.addWidget(self.more, 0, Qt.AlignmentFlag.AlignLeft)
        self.set_items(items, ctx)
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _live(self) -> bool:
        r = store.rt.get(self.ctx.session_id)
        return bool(r and r.status in BUSY)

    def _compute(self) -> List[Seg]:
        out: List[Seg] = []
        run: List[ToolItem] = []
        live = self._live()

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

    def set_items(self, items: List[ToolItem], ctx: ToolCtx) -> None:
        self.items = items
        self.ctx = ctx
        segs = self._compute()
        hidden = len(segs) - 6 if (not self.expanded and len(segs) > self.COLLAPSE_AT) else 0
        visible = segs[hidden:]
        sig = "|".join(
            (
                f"x:{','.join(t.id for t in s.items)}"
                if isinstance(s, ExploreSeg)
                else f"t:{s.item.id}:{self._perm_id(s.item)}"
            )
            for s in visible
        )
        if sig != self._sig:
            self._sig = sig
            self._relayout(visible)
        else:
            for s in visible:
                if isinstance(s, ExploreSeg):
                    self.explores[s.key].set_items(s.items, ctx)
                else:
                    refresh_card(self.cards[s.item.id], s.item, ctx)
        self.segs = segs
        summary = next((t.summary for t in items if t.summary), None)
        self.more.setText(f"{truncate(summary, 80) + ' · ' if summary else ''}{hidden} earlier steps")
        self.more.setVisible(hidden > 0)
        self._paint()

    def _perm_id(self, t: ToolItem) -> str:
        p = self.ctx.pending.get(t.id)
        return str(p.get("requestId")) if p else ""

    def _relayout(self, visible: List[Seg]) -> None:
        # take every widget out, then put the ones still wanted back in order; the rest go
        while self.lay.count() > 1:
            item = self.lay.takeAt(1)
            w = item.widget()
            if w is not None:
                w.setParent(None)
        wanted_cards = {s.item.id for s in visible if isinstance(s, ToolSeg)}
        wanted_x = {s.key for s in visible if isinstance(s, ExploreSeg)}
        for k in list(self.cards):
            if k not in wanted_cards or getattr(self.cards[k], "_perm_id", "") != self._perm_id(self.cards[k].item):
                self.cards.pop(k).deleteLater()
        for k in list(self.explores):
            if k not in wanted_x:
                self.explores.pop(k).deleteLater()
        for s in visible:
            if isinstance(s, ExploreSeg):
                x = self.explores.get(s.key)
                if x is None:
                    x = self.explores[s.key] = Explore(s.items, self.ctx)
                else:
                    x.set_items(s.items, self.ctx)
                self.lay.addWidget(x)
            else:
                c = self.cards.get(s.item.id)
                if c is None:
                    c = self.cards[s.item.id] = make_tool_card(s.item, self.ctx, self.ctx.pending.get(s.item.id))
                    c._perm_id = self._perm_id(s.item)  # type: ignore[attr-defined]
                else:
                    refresh_card(c, s.item, self.ctx)
                self.lay.addWidget(c)

    def _expand(self) -> None:
        self.expanded = True
        self._sig = ""
        self.set_items(self.items, self.ctx)

    def _paint(self) -> None:
        t = theme.tokens()
        self.more.setIcon(icons.icon("layers", t.text_3, 14, hover=t.text))
        self.more.setStyleSheet(
            f"QPushButton {{ font-size: {pt(theme.FS_UI)}; color: {t.text_3}; padding: 4px 6px 4px 0; text-align: left; }} QPushButton:hover {{ color: {t.text}; background: transparent; }}"
        )

    def paintEvent(self, e) -> None:  # noqa: N802
        # the hairline rail that ties the icons of one run of steps together
        if self.lay.count() > 2:
            p = QPainter(self)
            p.fillRect(8, 14, 1, max(0, self.height() - 28), theme.blend(theme.tokens().hair_2, theme.tokens().canvas))
        super().paintEvent(e)

    def row_for(self, item_id: str) -> Optional[QWidget]:
        if item_id in self.cards:
            return self.cards[item_id]
        for x in self.explores.values():
            if any(t.id == item_id for t in x.items):
                return x
        return None

    def find_rows(self) -> List[QWidget]:
        out: List[QWidget] = []
        for i in range(1, self.lay.count()):
            w = self.lay.itemAt(i).widget()
            if w is not None:
                out.append(w)
        return out


# ---------------------------------------------------------------- Flat: a slice of the conversation


class Flat(Stack):
    """The items of one slice of the conversation, runs of tool calls drawn as Steps."""

    def __init__(
        self, items: List[Item], ctx: ToolCtx, in_work: bool = False, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.in_work = in_work
        self.items: List[Item] = []
        self.set_items(items, ctx)

    def set_items(self, items: List[Item], ctx: ToolCtx) -> None:
        self.ctx = ctx
        self.items = items
        groups = group_items(items)
        keys = [g.key for g in groups]
        keep = 0
        for keep, (a, b) in enumerate(zip(keys, self.keys)):  # noqa: B007
            if a != b:
                break
        else:
            keep = min(len(keys), len(self.keys))
        if keep < len(self.keys):
            self.truncate(keep)
        for i, g in enumerate(groups):
            if i < keep:
                w = self.rows[i]
                if isinstance(g, StepsGroup):
                    w.set_items(g.items, ctx)  # type: ignore[union-attr]
                elif g.item.kind == "tool" and getattr(w, "_perm_id", "") != _perm_id(ctx, g.item):
                    # a permission arrived on (or left) a special tool: the card takes a new form
                    self.truncate(i)
                    self._append_rest(groups[i:])
                    break
                else:
                    self._refresh_row(w, g.item)
            else:
                self._append_rest(groups[i:])
                break

    def _refresh_row(self, w: QWidget, item: Item) -> None:
        if item.kind == "tool":
            refresh_card(w, item, self.ctx)
        elif hasattr(w, "refresh"):
            w.refresh(item.rev)
        if isinstance(w, UserMessage):
            w.sync_state()

    def _append_rest(self, groups: List[Group]) -> None:
        for g in groups:
            if isinstance(g, StepsGroup):
                self.append(g.key, Steps(g.items, self.ctx))
            else:
                self.append(g.key, make_row(g.item, self.ctx, self.in_work))

    def row_for(self, item_id: str) -> Optional[QWidget]:
        for w in self.rows:
            if isinstance(w, Steps):
                r = w.row_for(item_id)
                if r is not None:
                    return r
            elif getattr(getattr(w, "item", None), "id", None) == item_id:
                return w
        return None


def _perm_id(ctx: ToolCtx, item: Item) -> str:
    p = ctx.pending.get(item.id) if item.kind == "tool" else None
    return str(p.get("requestId")) if p else ""


def make_row(item: Item, ctx: ToolCtx, in_work: bool = False) -> QWidget:
    k = item.kind
    if k == "user":
        return UserMessage(item, ctx)
    if k == "text":
        return AssistantText(item, ctx, quiet=in_work)
    if k == "thinking":
        return Thinking(item, ctx.view)
    if k == "tool":
        w = make_tool_card(item, ctx, ctx.pending.get(item.id))
        w._perm_id = _perm_id(ctx, item)  # type: ignore[attr-defined]
        if not hasattr(w, "TOP"):
            w.TOP, w.BOTTOM = (10, 14) if item.id in ctx.pending else (12, 12)  # type: ignore[attr-defined]
        return w
    if k == "notice":
        return Notice(item)
    if k == "compact":
        return Compact(item)
    if k == "command-output":
        return CommandOutput(item, ctx)
    if k == "result":
        return ResultFooter(item, ctx)
    return QWidget()


# ---------------------------------------------------------------- chapters


class ChapterView(QWidget):
    def __init__(self, ch: Chapter, ctx: ToolCtx, last: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ch = ch
        self.ctx = ctx
        self.last = last
        self._open: Optional[bool] = chapter_open.get(ch.key)
        self._title_rev = -1
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 2 if last else 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(0)
        grid.setColumnMinimumWidth(0, 14)
        grid.setColumnStretch(1, 1)
        self.rail = Rail(last)
        grid.addWidget(self.rail, 0, 0, 3, 1)
        self.head = Head()
        self.head.setMinimumHeight(24)
        self.head.setAccessibleName("Chapter")
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(12)
        self.title_md: Optional[MarkdownView] = None
        self.title_lb = QLabel()
        if ch.title is not None:
            self.title_md = MarkdownView(ctx.cwd, compact=True, quiet=True)
            hl.addWidget(self.title_md, 1)
            self.title_lb.setVisible(False)
        else:
            hl.addWidget(self.title_lb, 1)
        self.tally_lb = QLabel()
        hl.addWidget(self.tally_lb, 0, Qt.AlignmentFlag.AlignTop)
        self.chev = icon_label("chevron-right", theme.tokens().text_4, 12)
        hl.addWidget(self.chev, 0, Qt.AlignmentFlag.AlignTop)
        self.head.clicked.connect(self.toggle)
        self.head.on_hover = lambda _on: self._paint()  # type: ignore[method-assign]
        grid.addWidget(self.head, 0, 1)
        self.thought: Optional[Thinking] = None
        self.thought_host = QWidget()
        tl = QVBoxLayout(self.thought_host)
        tl.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(self.thought_host, 1, 1)
        self.steps_box = Collapse(self._build_steps, self.is_open)
        grid.addWidget(self.steps_box, 2, 1)
        self.update_chapter(ch, ctx, last)
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _build_steps(self) -> QWidget:
        host = QWidget()
        vl = QVBoxLayout(host)
        vl.setContentsMargins(0, 2, 0, 0)
        self.flat = Flat(self.ch.steps, self.ctx, in_work=True)
        vl.addWidget(self.flat)
        return host

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
    def is_open(self) -> bool:
        if self._open is not None:
            return self._open
        return self.ctx.view == "verbose" or self.waiting or self.live

    def toggle(self) -> None:
        self._open = not self.is_open
        chapter_open[self.ch.key] = self._open
        self.steps_box.set_open(self._open)
        self._paint()

    def update_chapter(self, ch: Chapter, ctx: ToolCtx, last: bool) -> None:
        self.ch, self.ctx, self.last = ch, ctx, last
        self.rail.set_last(last)
        self.layout().setContentsMargins(0, 0, 0, 2 if last else 12)
        if ch.title is not None and self.title_md is not None and ch.title.rev != self._title_rev:
            self._title_rev = ch.title.rev
            self.title_md.set_text(ch.title.text, bool(ch.title.streaming))
        if ch.title is None:
            self.title_lb.setText("Steps" if any(it.kind == "tool" for it in ch.steps) else "Thinking")
        t = tally(ch.steps)
        txt = t.text
        if t.failed:
            txt += f'<span style="color:{theme.tokens().err}"> · {t.failed} failed</span>'
        if t.ms > 1500:
            txt += f" · {duration(t.ms)}"
        self.tally_lb.setText(txt)
        self.tally_lb.setVisible(bool(ch.steps))
        self.chev.setVisible(bool(ch.steps))
        if ch.thought is not None:
            if self.thought is None or self.thought.item is not ch.thought:
                if self.thought is not None:
                    self.thought.deleteLater()
                self.thought = Thinking(ch.thought, ctx.view)
                self.thought_host.layout().addWidget(self.thought)
            else:
                self.thought.refresh(ch.thought.rev)
        elif self.thought is not None:
            self.thought.deleteLater()
            self.thought = None
        self.thought_host.setVisible(ch.thought is not None)
        self.steps_box.set_open(self.is_open)
        if self.steps_box.body() is not None:
            self.flat.set_items(ch.steps, ctx)
        self.rail.set_live(self.live, t.failed > 0)
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        lit = self.is_open or self.head.hover
        if self.title_md is not None:
            self.title_md.setStyleSheet(
                f"MarkdownView {{ font-size: {pt(theme.FS_UI)}; color: {t.text if lit else t.text_2}; background: transparent; border: none; padding: 0; }}"
            )
        style_label(self.title_lb, theme.FS_UI, t.text_3, extra="line-height: 155%;")
        style_label(self.tally_lb, theme.FS_MICRO, t.text_4, extra="padding-top: 4px;")
        set_icon(self.chev, "chevron-down" if self.is_open else "chevron-right", t.text_4, 12)

    def row_for(self, item_id: str) -> Optional[QWidget]:
        if self.ch.title is not None and self.ch.title.id == item_id:
            return self.head
        if self.ch.thought is not None and self.ch.thought.id == item_id and self.thought is not None:
            return self.thought
        if self.steps_box.body() is not None:
            return self.flat.row_for(item_id)
        return None

    def find_rows(self) -> List[QWidget]:
        out: List[QWidget] = [self.head]
        if self.thought is not None:
            out.append(self.thought)
        if self.steps_box.body() is not None:
            out.extend(self.flat.rows)
        return out


class Rail(QWidget):
    """The dot and line down the side of a chapter."""

    def __init__(self, last: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.last = last
        self.live = False
        self.failed = False
        self.setFixedWidth(14)
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self.update)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_last(self, last: bool) -> None:
        self.last = last
        self.update()

    def set_live(self, live: bool, failed: bool) -> None:
        self.live, self.failed = live, failed
        if live and not store.reduced_motion and not self._timer.isActive():
            self._timer.start()
        elif not live:
            self._timer.stop()
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.last:
            p.fillRect(7, 18, 1, self.height() - 18 + 12, theme.blend(t.hair_2, t.canvas))
        c = QColor(t.err if self.failed else t.accent if self.live else t.text_4)
        if self.live and self._timer.isActive():
            import math

            phase = (time.monotonic() - self._t0) % 1.4 / 1.4
            c.setAlphaF(0.55 + 0.45 * (0.5 - 0.5 * math.cos(phase * 2 * math.pi)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(3.5, 9, 7, 7)


class Chapters(QWidget):
    def __init__(self, items: List[Item], ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 2, 0, 2)
        self.lay.setSpacing(0)
        self.views: List[ChapterView] = []
        self.ctx = ctx
        self.set_items(items, ctx)

    def set_items(self, items: List[Item], ctx: ToolCtx) -> None:
        self.ctx = ctx
        chapters = chapters_of(items)
        keep = 0
        for keep, (ch, v) in enumerate(zip(chapters, self.views)):  # noqa: B007
            if ch.key != v.ch.key:
                break
        else:
            keep = min(len(chapters), len(self.views))
        while len(self.views) > keep:
            v = self.views.pop()
            self.lay.removeWidget(v)
            v.deleteLater()
        n = len(chapters)
        for i, ch in enumerate(chapters):
            if i < len(self.views):
                self.views[i].update_chapter(ch, ctx, i == n - 1)
            else:
                v = ChapterView(ch, ctx, i == n - 1)
                self.views.append(v)
                self.lay.addWidget(v)

    def row_for(self, item_id: str) -> Optional[QWidget]:
        for v in self.views:
            r = v.row_for(item_id)
            if r is not None:
                return r
        return None

    def find_rows(self) -> List[QWidget]:
        out: List[QWidget] = []
        for v in self.views:
            out.extend(v.find_rows())
        return out


# ---------------------------------------------------------------- the work fold


class WorkFold(Row):
    """One line for a stretch of work; opens to its chapters. Live while the turn runs: the mark
    breathes and the line says what Claude is doing."""

    TOP, BOTTOM = 10, 14

    def __init__(self, unit: WorkUnit, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.unit = unit
        self.ctx = ctx
        self.key = unit.key
        self._mode = ""  # 'chapters' | 'flat'
        self._inner: Optional[QWidget] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = Head()
        self.head.setMinimumHeight(30)
        self.head.setAccessibleName("Work")
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        self.mark = WorkingMark(16)
        self.mark.setFixedSize(20, 16)  # .work-mark: 16px wide, margin-right 4px
        hl.addWidget(self.mark)
        self.sum = QLabel()
        self.sum.setSizePolicy(self.sum.sizePolicy().horizontalPolicy(), self.sum.sizePolicy().verticalPolicy())
        hl.addWidget(self.sum)
        self.fail = QLabel()
        hl.addWidget(self.fail)
        self.chev = icon_label("chevron-right", theme.tokens().text_3, 14)
        hl.addWidget(self.chev)
        hl.addStretch(1)
        self.n = QLabel()
        hl.addWidget(self.n)
        self.head.clicked.connect(self.toggle)
        self.head.on_hover = lambda _on: self._paint()  # type: ignore[method-assign]
        lay.addWidget(self.head)
        self.body = Collapse(self._build_body, False)
        lay.addWidget(self.body)
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._fill_n)
        self.summary = work_summary(unit.work)
        self.update_unit(unit, ctx)
        bind(theme.theme.changed, self, lambda _t: self._paint())

    # ---- state
    @property
    def waiting(self) -> bool:
        return any(it.kind == "tool" and it.id in self.ctx.pending for it in self.unit.work)

    @property
    def is_open(self) -> bool:
        default = self.ctx.view in ("thinking", "verbose")
        return turn_open.get(self.key, default) or self.waiting

    def toggle(self) -> None:
        now = not (turn_open.get(self.key, self.ctx.view in ("thinking", "verbose")))
        turn_open[self.key] = now
        self._sync_open()
        self._paint()

    def open(self, animate: bool = True) -> None:
        """Open the fold; find and item reveals pass ``animate=False`` so the rows land at their final
        place before the view scrolls to them."""
        turn_open[self.key] = True
        self._sync_open(animate)

    def _sync_open(self, animate: bool = True) -> None:
        self.body.set_open(self.is_open, animate)
        self._fill_n()

    def _build_body(self) -> QWidget:
        host = QWidget()
        host.setObjectName("work-body")
        host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        vl = QVBoxLayout(host)
        vl.setContentsMargins(14, 2, 0, 4)
        vl.setSpacing(0)
        self._body_lay = vl
        self._mode = ""
        self._sync_body()
        self._paint()
        return host

    def _sync_body(self) -> None:
        chapters = chapters_of(self.unit.work)
        mode = "flat" if (len(chapters) == 1 and chapters[0].title is None) else "chapters"
        if mode != self._mode:
            if self._inner is not None:
                self._body_lay.removeWidget(self._inner)
                self._inner.deleteLater()
            self._mode = mode
            self._inner = (
                Flat(self.unit.work, self.ctx, in_work=True) if mode == "flat" else Chapters(self.unit.work, self.ctx)
            )
            self._body_lay.addWidget(self._inner)
        else:
            self._inner.set_items(self.unit.work, self.ctx)  # type: ignore[union-attr]

    def update_unit(self, unit: WorkUnit, ctx: ToolCtx) -> None:
        self.unit, self.ctx = unit, ctx
        self.summary = work_summary(unit.work)
        doing = live_label(unit.work) if unit.live else None
        self.sum.setText(f"{doing}…" if doing else self.summary.text)
        self.fail.setText(f"· {self.summary.failed} failed" if self.summary.failed else "")
        self.fail.setVisible(self.summary.failed > 0)
        self.mark.setVisible(unit.live)
        if unit.live and not self._tick.isActive():
            self._tick.start()
        elif not unit.live:
            self._tick.stop()
        if self.body.body() is not None:
            self._sync_body()
        self._sync_open()
        self._paint()

    def _elapsed(self) -> float:
        if not self.unit.live:
            return 0
        r = store.rt.get(self.ctx.session_id)
        started = (r.st.get("turnStartedAt") if r else None) or 0
        first = self.unit.work[0] if self.unit.work else None
        t0 = started or (getattr(first, "ts", None) or getattr(first, "start_ts", 0) or 0)
        return time.time() * 1000 - t0 if t0 else 0

    def _fill_n(self) -> None:
        s = self.summary
        elapsed = self._elapsed()
        show = (not self.is_open or self.unit.live) and (s.steps > 0 or elapsed > 1000)
        text = f"{s.steps} {'step' if s.steps == 1 else 'steps'}" if s.steps > 0 else ""
        if elapsed > 1000:
            text += f"{' · ' if text else ''}{duration(elapsed)}"
        self.n.setText(text)
        self.n.setVisible(show)

    def _paint(self) -> None:
        t = theme.tokens()
        live = self.unit.live
        color = t.text_2 if self.head.hover else t.text_3
        style_label(self.sum, theme.FS_UI, color, 500 if live else 400)
        style_label(self.fail, theme.FS_UI, t.err)
        style_label(self.n, theme.FS_MICRO, t.text_4)
        # .work-head .tool-chev: opacity 0.7, 1 on hover
        self.chev.setPixmap(
            faded_pixmap(
                "chevron-down" if self.is_open else "chevron-right",
                t.text_2 if self.head.hover else t.text_3,
                14,
                1.0 if self.head.hover else 0.7,
            )
        )
        body = self.body.body()
        if body is not None:
            body.setStyleSheet(
                f"#work-body {{ border-left: 1px solid {theme.blend(t.hair_2, t.canvas).name()}; margin-top: 4px; }}"
            )

    def find_text(self) -> str:
        return self.sum.text()

    def row_for(self, item_id: str) -> Optional[QWidget]:
        if self._inner is None:
            return None
        return self._inner.row_for(item_id)  # type: ignore[union-attr]

    def find_rows(self) -> List[QWidget]:
        out: List[QWidget] = [self]
        if self._inner is not None:
            out.extend(self._inner.find_rows() if isinstance(self._inner, Chapters) else self._inner.rows)
        return out


# ---------------------------------------------------------------- one box of the conversation


class UnitBox(QWidget):
    """A unit of the conversation: a stretch of what was said (Flat) or one fold of work (WorkFold).
    A pause to think with no steps is drawn flat: a fold around it would be ceremony."""

    def __init__(self, unit: Unit, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.key = unit.key
        self.sig = ""
        self.unit = unit
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.inner: Optional[QWidget] = None
        self._mode = ""
        self.update_unit(unit, ctx, unit_sig(unit, ctx))

    def update_unit(self, unit: Unit, ctx: ToolCtx, sig: str) -> None:
        if sig == self.sig:
            return
        self.sig = sig
        self.unit = unit
        mode = "fold" if isinstance(unit, WorkUnit) and any(it.kind == "tool" for it in unit.work) else "flat"
        if mode != self._mode:
            if self.inner is not None:
                self.lay.removeWidget(self.inner)
                self.inner.deleteLater()
            self._mode = mode
            self.inner = (
                WorkFold(unit, ctx)
                if mode == "fold"
                else Flat(unit.work if isinstance(unit, WorkUnit) else unit.items, ctx)
            )
            self.lay.addWidget(self.inner)
        elif mode == "fold":
            self.inner.update_unit(unit, ctx)  # type: ignore[union-attr]
        else:
            self.inner.set_items(unit.work if isinstance(unit, WorkUnit) else unit.items, ctx)  # type: ignore[union-attr]

    def margins(self) -> tuple:
        if isinstance(self.inner, Flat):
            return self.inner.first_margin(), self.inner.last_margin()
        return WorkFold.TOP, WorkFold.BOTTOM

    def items(self) -> List[Item]:
        return self.unit.work if isinstance(self.unit, WorkUnit) else self.unit.items

    def row_for(self, item_id: str) -> Optional[QWidget]:
        if self.inner is None:
            return None
        return self.inner.row_for(item_id)  # type: ignore[union-attr]

    def fold(self) -> Optional[WorkFold]:
        return self.inner if isinstance(self.inner, WorkFold) else None
