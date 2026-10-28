"""One tool call in the conversation (a port of web/src/components/tools/ToolCard.tsx).

A quiet one-line head (icon, verb, target, status, elapsed) over a body that opens by default for the
tools that are worth reading (edits while proposed, errors, anything waiting on the person) and on a
click otherwise. AskUserQuestion and ExitPlanMode are drawn as their own cards without a head.
``refresh(rev)`` re-renders only when the item's revision, the pending request or the context changed,
and keeps the open/closed state the person chose.
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QWidget

from ... import theme
from ...lib.fmt import duration
from ...lib.tools_meta import ToolMeta, file_path_of, is_file_tool, str_, tool_meta
from ...lib.transcript_model import ToolItem
from ...store import store
from ..ui import bits
from ..ui.collapse import Collapse
from ..ui.signals import bind
from .bodies.agent import AgentBody
from .bodies.ask import AskBody
from .bodies.bash import BashBody
from .bodies.code import compute_hunks
from .bodies.common import Badge, ElideLabel, IconLabel, Json, Row, Section, clear_layout, restyle, result_text, vbox
from .bodies.files import EditBody, NotebookBody, ReadBody, WriteBody
from .bodies.generic import GenericBody, WebBody
from .bodies.plan import PlanBody
from .bodies.search import SearchBody
from .bodies.todo import TaskBody, TodoBody
from .bodies.workflow import WorkflowInline, WorkflowMeta
from .ctx import ToolCtx
from .permission import PermissionCard

EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")


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


def open_by_default(item: ToolItem, waiting: bool) -> bool:
    """Finished file edits fold to one line; they open while being proposed, while waiting for approval, or on error."""
    if waiting or item.status == "error":
        return True
    if item.name in EDIT_TOOLS:
        return item.status in ("streaming", "running")
    return False


def elapsed_ms(item: ToolItem) -> float:
    if item.end_ts and item.start_ts:
        return item.end_ts - item.start_ts
    return item.elapsed * 1000 if item.elapsed else 0


def render_body(item: ToolItem, ctx: ToolCtx) -> QWidget:
    name = item.name
    i = item.input or {}
    if name in ("Bash", "PowerShell"):
        return BashBody(item, ctx)
    if name == "Read":
        return ReadBody(item, ctx)
    if name in ("Edit", "MultiEdit"):
        return EditBody(item, ctx)
    if name == "Write":
        return WriteBody(item, ctx)
    if name == "NotebookEdit":
        return NotebookBody(item, ctx)
    if name in ("Grep", "Glob"):
        return SearchBody(item, ctx)
    if name == "TodoWrite":
        return TodoBody(i.get("todos"))
    if name in ("TaskCreate", "TaskUpdate", "TaskList", "TaskGet"):
        return TaskBody(item, ctx)
    if name in ("Agent", "Task"):
        return AgentBody(item, ctx)
    if name in ("WebFetch", "WebSearch"):
        return WebBody(item, ctx)
    if name == "ToolSearch":
        return result_text(item) or Section()
    if name in ("Workflow", "RunWorkflow"):
        return WorkflowInline(item, ctx)
    return GenericBody(item, ctx)


class WaitDot(QWidget):
    """`.wait-dot`: the accent dot that says a row is waiting for the person."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.setAccessibleName("Waiting for you")
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.color("accent_wash"))
        p.drawEllipse(QRectF(0.5, 0.5, 13, 13))
        p.setBrush(t.color("accent"))
        p.drawEllipse(QRectF(3.5, 3.5, 7, 7))


def status_widget(item: ToolItem, waiting: bool, meta: ToolMeta) -> QWidget:
    if waiting:
        return WaitDot()
    st = item.status
    if st in ("streaming", "running"):
        return bits.Spinner(14)
    if st == "error":
        return IconLabel("x", "err", 14)
    if st == "denied":
        return IconLabel("ban", "err", 14)
    if st == "interrupted":
        return IconLabel("circle-stop", "text_3", 14)
    if st == "background":
        return IconLabel("circle-dot", "info", 14)
    return IconLabel(meta.icon_name, "text_2", 14)


class ToolHead(QWidget):
    """`.tool-head`: icon · verb · target · chevron · (open in Files) ········ meta."""

    toggled = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._hover = False
        self._open = False
        self._file: Optional[str] = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # .tool-head: an 18px icon column, a 10px gap, the line (verb · target · chevron) and the meta
        # at the right. The web pulls the row 6px left and pads it 6px, so its content sits on the
        # text edge: here the row simply starts there.
        self.setMinimumHeight(28)
        self.row = Row(spacing=10, margins=(0, 0, 0, 0))
        vbox(self).addWidget(self.row)
        self.ico_host = QWidget()
        self.ico_host.setFixedSize(18, 20)
        self.ico_lay = vbox(self.ico_host)
        self.ico_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.row.add(self.ico_host)
        self.line = Row(spacing=7)  # .tool-line
        self.verb = bits.label("", tone="faint")
        self.line.add(self.verb)
        self.target = ElideLabel("")
        self.line.add(self.target)
        self.chev = IconLabel("chevron-right", "text_3", 13)
        self.line.add(self.chev)
        self.open_btn = bits.IconButton("arrow-up-right", "Open in Files", 13)
        self.open_btn.setFixedSize(22, 22)  # .tool-open
        self.open_btn.clicked.connect(self._open_file)
        self.line.add(self.open_btn)
        self.line.stretch()
        self.row.add(self.line, 1)
        self.meta = Row(spacing=8)
        self.row.add(self.meta)
        bind(theme.theme.changed, self, lambda _t: self.update())
        self._refresh_hover()

    def render(self, item: ToolItem, ctx: ToolCtx, perm: Optional[Json], open_: bool) -> None:
        meta = tool_meta(item.name, item.input, ctx.cwd)
        self._open = open_
        self.setAccessibleName(f"{meta.verb} {meta.target}".strip())
        clear_layout(self.ico_lay)
        self.ico_lay.addWidget(status_widget(item, perm is not None, meta))
        self.verb.setText(meta.verb)
        self.verb.setVisible(bool(meta.verb))
        struck = item.status in ("denied", "interrupted")
        self.target.set_text(meta.target, color="text_3" if struck else "text", mono=meta.mono, strike=struck)
        self._file = file_path_of(item.name, item.input) if is_file_tool(item.name) else None
        self.chev.set("chevron-down" if open_ else "chevron-right")
        clear_layout(self.meta.lay)
        st = item.status
        if st == "background":
            self.meta.add(Badge("background", "info"))
        if st == "denied":
            self.meta.add(Badge("declined", "err"))
        if st == "error":
            flag = bits.label("Failed", size="micro")
            restyle(flag, lambda t: f"QLabel {{ color: {t.err}; font-size: {theme.FS_MICRO}px; font-weight: 600; }}")
            self.meta.add(flag)
        if st == "interrupted":
            self.meta.add(Badge("stopped"))
        if item.name in ("Workflow", "RunWorkflow"):
            self.meta.add(WorkflowMeta(ctx.session_id, item.id))
        # A failed edit changed nothing, so it gets no +/− counts.
        stats = None if st == "error" else diff_stats(item)
        has_stats = bool(stats and (stats["add"] > 0 or stats["del"] > 0))
        if stats and has_stats:
            counts = Row(spacing=5)
            if stats["add"] > 0:
                a = bits.label(f"+{stats['add']}", mono=True)
                restyle(a, lambda t: f"QLabel {{ color: {t.ok}; font-size: 11.5px; }}")
                counts.add(a)
            if stats["del"] > 0:
                d = bits.label(f"−{stats['del']}", mono=True)
                restyle(d, lambda t: f"QLabel {{ color: {t.err}; font-size: 11.5px; }}")
                counts.add(d)
            self.meta.add(counts)
        ms = elapsed_ms(item)
        if ms > 1500 and not has_stats:
            self.meta.add(bits.label(duration(ms), tone="faint", size="micro"))
        self._refresh_hover()

    def _open_file(self) -> None:
        if self._file:
            store.open_file(self._file)

    def _refresh_hover(self) -> None:
        self.chev.setVisible(self._open or self._hover)
        self.open_btn.setVisible(bool(self._file) and self._hover)
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self._refresh_hover()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self._refresh_hover()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.toggled.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.toggled.emit()
            return
        super().keyPressEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        if not self._hover:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.tokens().color("fill_1"))
        p.drawRoundedRect(QRectF(self.rect()), theme.R2, theme.R2)


class ToolCard(QWidget):
    def __init__(
        self, item: ToolItem, ctx: ToolCtx, perm: Optional[Json] = None, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.item, self.ctx = item, ctx
        self._perm_arg = perm
        self._rev = item.rev
        self._open: Optional[bool] = None  # None until the person toggles; then their choice sticks
        self._last_perm: Optional[Json] = self.perm  # the request the perm card last showed (kept while stale)
        self._shown_perm: Optional[Json] = None  # the request the head was last drawn with
        self.setObjectName(f"tool-{item.id}")
        self.lay = vbox(self, spacing=0)
        bind(theme.theme.changed, self, lambda _t: self.update())
        self._build_all()

    def _build_all(self) -> None:
        clear_layout(self.lay)
        self._special = self._is_special()
        self._shown_perm = self.perm
        if self._special:
            self.lay.setContentsMargins(0, 12, 0, 12)
            self.special_host = QWidget()
            self.special_lay = vbox(self.special_host)
            self.lay.addWidget(self.special_host)
            self._render_special()
            return
        self.head = ToolHead()
        self.head.toggled.connect(self.toggle)
        self.lay.addWidget(self.head)
        self.body_host = QWidget()
        self.body_lay = vbox(self.body_host)
        self.body = Collapse(self._build_body, self.is_open)
        self.lay.addWidget(self.body)
        self.perm_host = QWidget()
        self.perm_lay = vbox(self.perm_host)
        self.perm_collapse = Collapse(self._build_perm, self.perm is not None)
        self.lay.addWidget(self.perm_collapse)
        self._apply_frame()
        self.head.render(self.item, self.ctx, self.perm, self.is_open)

    # ---- state
    @property
    def perm(self) -> Optional[Json]:
        return self._perm_arg if self._perm_arg is not None else self.ctx.pending.get(self.item.id)

    @property
    def awaiting(self) -> bool:
        return self.perm is not None

    def _is_special(self) -> bool:
        name, st = self.item.name, self.item.status
        interactive = self.perm is not None and name in ("AskUserQuestion", "ExitPlanMode")
        return interactive or (name in ("AskUserQuestion", "ExitPlanMode") and st != "streaming")

    @property
    def is_open(self) -> bool:
        if self._open is not None:
            return self._open
        item = self.item
        plan_file = item.name == "Write" and "/.claude/plans/" in str_(item.input, "file_path").replace("\\", "/")
        verbose = self.ctx.view == "verbose" and item.status not in ("running", "streaming")
        return (open_by_default(item, self.awaiting) or verbose) and not plan_file

    def toggle(self) -> None:
        self.set_open(not self.is_open)

    def set_open(self, open_: bool) -> None:
        self._open = open_
        if not self._special:
            self.body.set_open(open_)
            self.head.render(self.item, self.ctx, self.perm, open_)

    def body_widget(self) -> Optional[QWidget]:
        """The rendered body (a *Body widget), or None while the card has never been opened."""
        if self._special or self.body_lay.count() == 0:
            return None
        return self.body_lay.itemAt(0).widget()

    def diff_body(self):
        return self.body_widget()

    # ---- building
    def _build_body(self) -> QWidget:
        clear_layout(self.body_lay)
        self.body_lay.addWidget(render_body(self.item, self.ctx))
        return self.body_host

    def _build_perm(self) -> QWidget:
        self._render_perm()
        return self.perm_host

    def _render_perm(self) -> None:
        clear_layout(self.perm_lay)
        perm = self.perm
        # Keep showing the answered request while it folds away, instead of having it vanish mid-layout.
        shown = perm if perm is not None else self._last_perm
        if shown is not None:
            self.perm_card = PermissionCard(shown, self.ctx, self.item, stale=perm is None)
            self.perm_lay.addWidget(self.perm_card)

    def _render_special(self) -> None:
        clear_layout(self.special_lay)
        perm = self.perm
        if self.item.name == "AskUserQuestion":
            self.special = AskBody(self.item, self.ctx, perm)
        else:
            self.special = PlanBody(self.item, self.ctx, perm)
        self.special_lay.addWidget(self.special)

    def _apply_frame(self) -> None:
        if self.awaiting:
            self.lay.setContentsMargins(10, 4, 10, 0)
            self.body_lay.setContentsMargins(22, 0, 0, 8)
            self.head.setMinimumHeight(32)
        else:
            self.lay.setContentsMargins(0, 0, 0, 0)
            self.body_lay.setContentsMargins(28, 4, 0, 10)
            self.head.setMinimumHeight(28)
        self.update()

    def refresh(self, rev: int, ctx: Optional[ToolCtx] = None, perm: Optional[Json] = None) -> None:
        """Re-render when the item's revision, the pending request or the context changed; keep the fold.

        ``ctx`` replaces the context (a new ToolCtx arrives whenever the pending requests change);
        ``perm`` overrides the lookup in ``ctx.pending`` the way the constructor's argument does.
        """
        ctx_changed = ctx is not None and ctx is not self.ctx
        if ctx is not None:
            self.ctx = ctx
        if perm is not None or self._perm_arg is not None:
            self._perm_arg = perm
        cur = self.perm
        perm_changed = cur is not self._shown_perm
        if rev == self._rev and not ctx_changed and not perm_changed:
            return
        self._rev = rev
        if cur is not None:
            self._last_perm = cur
        if self._special != self._is_special():
            # A card changing kind (a question whose answer arrived) is rebuilt from scratch.
            self._build_all()
            return
        self._shown_perm = cur
        if self._special:
            self._render_special()
            return
        self._apply_frame()
        self.head.render(self.item, self.ctx, cur, self.is_open)
        if self.body.body() is not None:
            self._build_body()
        self.body.set_open(self.is_open)
        if perm_changed:
            if self.perm_collapse.body() is not None:
                self._render_perm()
            self.perm_collapse.set_open(cur is not None)

    def paintEvent(self, _e) -> None:  # noqa: N802
        if self._special or not self.awaiting:
            return
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(t.color("hair_2"), 1))
        p.setBrush(t.color("raised"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
