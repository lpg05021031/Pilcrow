"""Agent / Task: the sub-agent's brief, its steps and its report (a port of tools/AgentBody.tsx)."""

from __future__ import annotations

from typing import Any, List, Optional

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from .... import theme
from ....lib.fmt import duration, plural, tokens
from ....lib.tools_meta import str_
from ....lib.transcript_model import Item, ToolItem, TranscriptModel
from ....store import store
from ...ui import bits
from ...ui.signals import bind
from ..ctx import ToolCtx
from .common import Badge, Row, Section, Surface, Toggle, clear_layout, faint, vbox
from .generic import markdown_label


def text_of(content: Any) -> str:
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def children_end_with(children: List[Item], text: str) -> bool:
    last = next((c for c in reversed(children) if c.kind == "text"), None)
    return bool(last is not None and last.text.strip() == text.strip())


def render_items(items: List[Item], ctx: ToolCtx, rev: int = 0, running: bool = False) -> QWidget:
    """The nested steps as a flat list: the transcript's own row renderer when it exists, else our own."""
    try:
        from .. import view as view_mod  # the ItemList of Transcript.tsx, written alongside this module

        fn = getattr(view_mod, "render_items", None)
        if fn is not None:
            return fn(items, ctx)
    except ImportError:
        pass
    from ..tool_card import ToolCard

    sec = Section(spacing=6)
    for it in items:
        if it.kind == "tool":
            sec.add(ToolCard(it, ctx))
        elif it.kind == "text":
            sec.add(markdown_label(it.text, ctx.cwd, compact=True))
        elif it.kind == "thinking":
            secs = (it.end_ts - it.start_ts) / 1000 if it.end_ts else 0
            sec.add(
                faint(
                    f"Thought for {duration(secs * 1000)}" if secs >= 1 else "Thinking…" if it.streaming else "Thought"
                )
            )
        elif it.kind == "user":
            sec.add(bits.label(it.text, tone="muted", size="small", wrap=True))
        elif it.kind == "notice":
            sec.add(bits.label(it.text, tone="faint", size="small", wrap=True))
    return sec


class ChildrenHost(QWidget):
    """`.agent-children`: the steps indented behind a one-pixel rail."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.lay = vbox(self, margins=(15, 0, 0, 0))
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(QRectF(1, 0, 1, self.height()), theme.tokens().color("hair_2"))


class AgentBody(QWidget):
    def __init__(self, item: ToolItem, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.item, self.ctx = item, ctx
        i = item.input or {}
        s = item.structured if isinstance(item.structured, dict) else None
        self.loaded: Optional[TranscriptModel] = None
        self.agent_id: Optional[str] = (s.get("agentId") if s else None) or item.task_id
        # Usage comes in two spellings depending on where it was read from (live task events vs the transcript).
        usage = (item.task.usage if item.task else None) or (s.get("usage") if s else None)
        self.running = item.status in ("running", "background", "streaming")
        if item.task and item.task.summary and item.status != "running":
            report = ""
        elif s and not s.get("isAsync"):
            report = text_of(s.get("content")) or (item.result.text if item.result else "")
        elif not (s and s.get("isAsync")):
            report = item.result.text if item.result else ""
        else:
            report = ""
        self.report = report
        lay = vbox(self, spacing=8)

        meta = Row(spacing=8)
        if str_(i, "subagent_type"):
            meta.add(Badge(str_(i, "subagent_type")))
        if str_(i, "model"):
            meta.add(Badge(str_(i, "model")))
        if str_(i, "isolation") == "worktree":
            meta.add(Badge("worktree"))
        if isinstance(usage, dict):
            uses = usage.get("tool_uses", usage.get("toolUses", 0)) or 0
            total = usage.get("total_tokens", usage.get("totalTokens", 0)) or 0
            ms = usage.get("duration_ms")
            meta.add(
                faint(
                    f"{plural(uses, 'tool use')} · {tokens(total)} tokens" + (f" · {duration(ms)}" if ms else ""),
                    size="micro",
                )
            )
        if self.running:
            meta.add(bits.Spinner(12))
            meta.add(faint("Working in the background" if item.status == "background" else "Working", size="micro"))
        meta.stretch()
        lay.addWidget(meta)

        prompt = str_(i, "prompt")
        lay.addWidget(Toggle("Instructions", lambda: self._prompt(prompt)))

        self.children_host = ChildrenHost()
        self.children_host.setVisible(False)
        lay.addWidget(self.children_host)
        self.load_btn: Optional[bits.QPushButton] = None
        if not item.children and not self.running and self.agent_id:
            self.load_btn = bits.button(
                "Show the agent's steps", kind="ghost", size="sm", icon="download", on_click=self.load_transcript
            )
            self.load_btn.setAccessibleName("Show the agent's steps")
            lay.addWidget(self.load_btn, 0)
        self.reports_host = QWidget()
        self.reports_lay = vbox(self.reports_host, spacing=8)
        lay.addWidget(self.reports_host)
        self.render_children()

    @property
    def children(self) -> List[Item]:
        return self.item.children if self.item.children else (self.loaded.items if self.loaded else [])

    def _prompt(self, prompt: str) -> QWidget:
        s = Surface("inset", "hair", theme.R2, margins=(12, 10, 12, 10))
        s.add(bits.label(prompt, tone="muted", size="small", wrap=True))
        return s

    def render_children(self) -> None:
        children = self.children
        clear_layout(self.children_host.lay)
        if children:
            self.children_host.lay.addWidget(
                render_items(children, self.ctx.with_depth(self.ctx.depth + 1), self.item.rev, self.running)
            )
        self.children_host.setVisible(bool(children))
        clear_layout(self.reports_lay)
        summary = self.item.task.summary if self.item.task else None
        for text in (summary, self.report):
            if text and not children_end_with(children, text):
                r = Surface("fill_1", "", theme.R3, margins=(14, 10, 14, 10))
                r.add(markdown_label(text, self.ctx.cwd, compact=True))
                self.reports_lay.addWidget(r)

    def load_transcript(self) -> None:
        if not self.agent_id or self.load_btn is None:
            return
        self.load_btn.setEnabled(False)

        def ok(msgs: Any) -> None:
            m = TranscriptModel()
            m.push_all(msgs or [])
            self.loaded = m
            if self.load_btn is not None:
                self.load_btn.setVisible(False)
            self.render_children()

        store.call("session.subagent", {"id": self.ctx.session_id, "agentId": self.agent_id}).then(ok).catch(
            lambda _e: self.load_btn is not None and self.load_btn.setEnabled(True)
        )
