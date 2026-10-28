"""Workflow runs in the conversation (a port of WorkflowInline/WorkflowMeta in tools/WorkflowBody.tsx).

In the conversation a workflow is one quiet tool row like any other; its body (`WorkflowInline`) is
the script's description, a sentence on how far the run is, a way into the Tasks panel — where the
whole run is drawn (widgets/panels/tasks.py WorkflowDetail) — and the result behind a toggle.
`WorkflowMeta` is the row's right-hand text. The run comes from the session's task list in
``store.rt[sid].st["tasks"]`` and refreshes on ``store.rt_changed``.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtWidgets import QLabel, QWidget

from .... import theme
from ....lib.fmt import truncate
from ....lib.tools_meta import str_
from ....lib.transcript_model import ToolItem
from ....lib.workflow import WorkflowMeta as ScriptMeta
from ....lib.workflow import parse_workflow_meta, workflow_counts
from ....store import store
from ...ui import bits
from ...ui.signals import bind
from ..ctx import ToolCtx
from .common import Json, Row, Toggle, faint, restyle, vbox
from .generic import markdown_label


def session_tasks(session_id: str) -> List[Json]:
    rt = store.rt.get(session_id)
    return [t for t in (rt.get("tasks") if rt else None) or [] if isinstance(t, dict)]


def workflow_task(session_id: str, tool_use_id: str) -> Optional[Json]:
    """The run this tool started; while it has no toolUseId yet, the one local workflow that is running."""
    for t in session_tasks(session_id):
        if t.get("toolUseId") == tool_use_id or (
            t.get("type") == "local_workflow" and t.get("status") == "running" and not t.get("toolUseId")
        ):
            return t
    return None


class WorkflowMeta(QLabel):
    """`.tool-time` for a workflow row: how far the run is, while it runs and after."""

    def __init__(self, session_id: str, tool_use_id: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.session_id, self.tool_use_id = session_id, tool_use_id
        self.setProperty("tone", "faint")
        self.setProperty("fs", "micro")
        bind(store.rt_changed, self, lambda sid: sid == session_id and self.refresh())
        self.refresh()

    def refresh(self) -> None:
        task = workflow_task(self.session_id, self.tool_use_id)
        counts = workflow_counts(((task or {}).get("workflow") or {}).get("agents") or [])
        if not task or not counts.total:
            self.setVisible(False)
            return
        self.setText(f"{counts.done}/{counts.total} agents")
        failed = f" · {counts.error} failed" if counts.error else ""
        self.setToolTip(f"{counts.done} done · {counts.running} running · {counts.queued} queued{failed}")
        self.setVisible(True)


class WorkflowInline(QWidget):
    """`.wfi`: description, progress sentence with "Open in Tasks", and the result behind a toggle."""

    def __init__(self, item: ToolItem, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.item, self.ctx = item, ctx
        self.script = str_(item.input, "script")
        self.meta: ScriptMeta = parse_workflow_meta(self.script)
        lay = vbox(self, spacing=6)
        self.description = self.meta.description or str_(item.input, "description")
        if self.description:
            lay.addWidget(bits.label(self.description, tone="muted", size="small", wrap=True))  # .wfi-desc
        row = Row(spacing=10)  # .wfi-row
        self.summary = faint("")
        row.add(self.summary)
        self.open_btn = bits.button(
            "Open in Tasks", size="sm", icon="panel-right", on_click=lambda: store.set_ui(panel=True, panel_tab="tasks")
        )
        self.open_btn.setAccessibleName("Open in Tasks")
        restyle(self.open_btn, _xs_button)
        row.add(self.open_btn)
        row.stretch()
        lay.addWidget(row)
        self.result: Optional[Toggle] = None
        if item.result and item.result.text:
            text = truncate(item.result.text, 30000)
            self.result = Toggle(
                "Error" if item.result.is_error else "Result", lambda: markdown_label(text, ctx.cwd, compact=True)
            )
            lay.addWidget(self.result)
        bind(store.rt_changed, self, lambda sid: sid == ctx.session_id and self.refresh())
        self.refresh()

    def refresh(self) -> None:
        task = workflow_task(self.ctx.session_id, self.item.id)
        wf = (task or {}).get("workflow") or {}
        counts = workflow_counts(wf.get("agents") or [])
        phases = max(len(self.meta.phases), len([p for p in wf.get("phases") or [] if p]))
        parts = []
        if phases:
            parts.append(f"{phases} phase{'s' if phases > 1 else ''}")
        if counts.total:
            parts.append(f"{counts.done} of {counts.total} agents done")
        if counts.running:
            parts.append(f"{counts.running} running")
        if counts.error:
            parts.append(f"{counts.error} failed")
        self.summary.setText(" · ".join(parts))
        self.summary.setVisible(bool(parts))


def _xs_button(t: theme.Tokens) -> str:
    """`.btn.xs`: 24px tall, 9px sides, 12px text on a quiet fill."""
    return (
        f"QPushButton {{ background: {theme.blend(t.fill_2, t.canvas).name()}; border: none; border-radius: {theme.R2}px; "
        f"min-height: 24px; max-height: 24px; padding: 0 9px; font-size: {theme.FS_SMALL}px; font-weight: 500; color: {t.text}; }} "
        f"QPushButton:hover {{ background: {theme.blend(t.fill_3, t.canvas).name()}; }}"
    )
