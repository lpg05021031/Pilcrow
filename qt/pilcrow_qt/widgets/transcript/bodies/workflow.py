"""Workflow runs (a port of tools/WorkflowBody.tsx).

In the conversation a workflow is one quiet tool row; its body (`WorkflowInline`) is a sentence, the
run drawn as phases with their agents (`WorkflowDetail`, shared with the Tasks panel), the script and
the result. `WorkflowMeta` is the row's right-hand text. The run comes from the session's task list in
``store.rt[sid].st["tasks"]`` and refreshes on ``store.rt_changed``.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QLabel, QWidget

from .... import icons, theme
from ....lib.fmt import duration, short_model, tokens, truncate
from ....lib.tools_meta import str_
from ....lib.transcript_model import ToolItem
from ....lib.workflow import WorkflowMeta as ScriptMeta
from ....lib.workflow import agent_state, parse_workflow_meta, workflow_columns, workflow_counts
from ....store import store
from ...ui import bits
from ...ui.collapse import Collapse
from ...ui.signals import bind
from ..ctx import ToolCtx
from .code import CodeView
from .common import ElideLabel, IconLabel, Json, Row, Surface, Toggle, clear_layout, faint, restyle, tool_label, vbox
from .generic import markdown_label

STATE_LABEL = {
    "queued": "Queued",
    "running": "Running",
    "done": "Done",
    "cached": "Reused",
    "error": "Failed",
    "blocked": "Blocked",
}
STATE_ICON = {"done": "check", "cached": "rotate-ccw", "error": "x", "blocked": "ban"}
STATUS_LABEL = {"completed": "Completed", "failed": "Failed", "stopped": "Stopped", "running": "Running"}


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


def _now_ms() -> float:
    return time.time() * 1000


class WorkflowMeta(QLabel):
    """`.tool-time` for a workflow row: how far the run is, while it runs and after."""

    def __init__(self, session_id: str, tool_use_id: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.session_id, self.tool_use_id = session_id, tool_use_id
        self.setProperty("tone", "faint")
        self.setProperty("size", "micro")
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
    def __init__(self, item: ToolItem, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.item, self.ctx = item, ctx
        self.script = str_(item.input, "script")
        self.meta: ScriptMeta = parse_workflow_meta(self.script)
        lay = vbox(self, spacing=6)
        self.description = self.meta.description or str_(item.input, "description")
        if self.description:
            lay.addWidget(bits.label(self.description, tone="muted", size="small", wrap=True))
        row = Row(spacing=10)
        self.summary = faint("")
        row.add(self.summary, 1)
        open_btn = bits.button(
            "Open in Tasks", size="sm", icon="panel-right", on_click=lambda: store.set_ui(panel=True, panel_tab="tasks")
        )
        open_btn.setAccessibleName("Open in Tasks")
        row.add(open_btn)
        lay.addWidget(row)
        self.detail_host = QWidget()
        self.detail_lay = vbox(self.detail_host)
        lay.addWidget(self.detail_host)
        self.detail: Optional[WorkflowDetail] = None
        if self.script:
            lay.addWidget(Toggle("Script", lambda: CodeView(self.script, lang="javascript", max_lines=40)))
        if item.result and item.result.text:
            text = truncate(item.result.text, 30000)
            lay.addWidget(
                Toggle(
                    "Error" if item.result.is_error else "Result", lambda: markdown_label(text, ctx.cwd, compact=True)
                )
            )
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
        if task is None:
            if self.detail is not None:
                clear_layout(self.detail_lay)
                self.detail = None
            return
        if self.detail is not None and self.detail.task_id == task.get("id"):
            self.detail.set_task(task, session_tasks(self.ctx.session_id))
            return
        clear_layout(self.detail_lay)
        self.detail = WorkflowDetail(task, self.ctx.session_id, session_tasks(self.ctx.session_id), script=self.script)
        self.detail_lay.addWidget(self.detail)


# ---------------------------------------------------------------- the run


class StateMark(QWidget):
    """`.wf-mark`: a dot while queued, a spinner while running, a tinted glyph after."""

    def __init__(self, st: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.st = st
        self.setFixedSize(14, 16)
        self.setAccessibleName(STATE_LABEL.get(st, st))
        lay = vbox(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if st == "running":
            lay.addWidget(bits.Spinner(12))
        elif st in STATE_ICON:
            lay.addWidget(IconLabel(STATE_ICON[st], "ok" if st in ("done", "cached") else "err", 12))
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        if self.st != "queued":
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(theme.tokens().color("text_4"), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(4.5, 5.5, 5, 5))


class ProgressBar(QWidget):
    """`.wf-bar`: a two-pixel line, done in ok, running in accent, failed in err."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.done = self.running = self.error = 0.0
        self.setFixedHeight(2)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_fractions(self, done: float, running: float, error: float) -> None:
        self.done, self.running, self.error = done, running, error
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        w = self.width()
        p.fillRect(self.rect(), t.color("fill_2"))
        x = 0.0
        for frac, tok in ((self.done, "ok"), (self.running, "accent"), (self.error, "err")):
            if frac > 0:
                p.fillRect(QRectF(x, 0, w * frac, 2), t.color(tok))
                x += w * frac


class AgentRow(QWidget):
    """`.wfa`: one agent of the run; opens to its prompt and result."""

    def __init__(self, a: Json, now: float, subs: List[Json], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.a = a
        st = agent_state(a)
        self.st = st
        lay = vbox(self, spacing=0)
        self.head = QWidget()
        self.head.setCursor(Qt.CursorShape.PointingHandCursor)
        self.head.setAccessibleName(str(a.get("label") or f"agent {int(a.get('index', 0)) + 1}"))
        hl = Row(spacing=7, margins=(5, 4, 6, 4))
        hb = vbox(self.head)
        hb.addWidget(hl)
        hl.lay.addWidget(StateMark(st), 0, Qt.AlignmentFlag.AlignTop)
        main = QWidget()
        ml = vbox(main, spacing=1)
        top = Row(spacing=5)
        label = ElideLabel(
            str(a.get("label") or f"agent {int(a.get('index', 0)) + 1}"),
            color="text_3" if st == "queued" else "text",
            size=theme.FS_SMALL,
        )
        top.add(label, 1)
        attempt = a.get("attempt")
        if isinstance(attempt, (int, float)) and attempt > 1:
            chip = bits.label(f"×{int(attempt)}", size="micro")
            chip.setToolTip(f"Retried: {a['lastAttemptReason']}" if a.get("lastAttemptReason") else "Retried")
            restyle(
                chip,
                lambda t: (
                    f"QLabel {{ font-size: 10px; padding: 0 4px; border-radius: 4px; "
                    f"background: {theme.blend(t.fill_2, t.canvas).name()}; color: {t.text_3}; }}"
                ),
            )
            top.add(chip)
        if a.get("isolation") == "worktree":
            top.add(IconLabel("git-branch", "text_4", 10))
        elif a.get("isolation") == "remote":
            top.add(IconLabel("cloud", "text_4", 10))
        end = Row(spacing=7)
        if a.get("model"):
            end.add(faint(short_model(str(a["model"])), size="micro"))
        self.ran_label = faint("", size="micro")
        end.add(self.ran_label)
        top.add(end)
        ml.addWidget(top)
        what = self._what(st)
        if what:
            detail = ElideLabel(what, color="err" if st in ("error", "blocked") else "text_3", size=theme.FS_MICRO)
            ml.addWidget(detail)
        hl.add(main, 1)
        lay.addWidget(self.head)
        self.body = Collapse(self._build_body, False)
        lay.addWidget(self.body)
        if subs:
            sw = QWidget()
            sl = vbox(sw, margins=(11, 1, 0, 3), spacing=1)
            for t in subs:
                sl.addWidget(SubAgentRow(t, now, elbow=True))
            lay.addWidget(sw)
        bits.Clickable(self.head).clicked.connect(self.body.toggle)
        self.tick(now)

    def _what(self, st: str) -> str:
        a = self.a
        if st == "running":
            return str(
                a.get("lastToolSummary") or (f"Using {a['lastToolName']}" if a.get("lastToolName") else "Thinking…")
            )
        if st in ("error", "blocked"):
            return str(a.get("error") or "").split("\n")[0]
        if st == "queued":
            return "Waiting for a slot"
        return str(a.get("resultPreview") or "").split("\n")[0]

    def tick(self, now: float) -> None:
        a = self.a
        started = a.get("startedAt")
        if not started:
            self.ran_label.setVisible(False)
            return
        end = now if self.st == "running" else (a.get("lastProgressAt") or a.get("updatedAt") or now)
        ran = end - started
        self.ran_label.setText(duration(ran))
        self.ran_label.setVisible(ran > 1500)

    def _build_body(self) -> QWidget:
        a = self.a
        body = QWidget()
        bl = vbox(body, margins=(26, 2, 6, 8), spacing=8)
        if a.get("agentType"):
            kv = Row(spacing=5)
            kv.add(IconLabel("bot", "text_2", 11))
            kv.add(bits.label(str(a["agentType"]), tone="muted", size="small"))
            if a.get("agentId"):
                kv.add(bits.label(f"· {str(a['agentId'])[:8]}", tone="faint", size="small", mono=True))
            kv.stretch()
            bl.addWidget(kv)
        if a.get("promptPreview"):
            bl.addWidget(tool_label("Prompt"))
            bl.addWidget(bits.label(str(a["promptPreview"]), tone="muted", size="small", wrap=True))
        if a.get("resultPreview") or a.get("error"):
            bl.addWidget(tool_label("Error" if a.get("error") else "Result"))
            lb = bits.label(
                str(a.get("error") or a.get("resultPreview")),
                tone="err" if a.get("error") else "muted",
                size="small",
                wrap=True,
            )
            bl.addWidget(lb)
        return body


class SubAgentRow(QWidget):
    """`.wfs`: a sub-agent an agent spawned, off an elbow from the agent's mark."""

    def __init__(self, t: Json, now: float, elbow: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        st = "running" if t.get("status") == "running" else "done" if t.get("status") == "completed" else "error"
        row = Row(spacing=4, margins=(0, 2, 6, 2))
        lay = vbox(self)
        lay.addWidget(row)
        if elbow:
            row.add(_Elbow())
        row.add(StateMark(st))
        label = ElideLabel(
            str(t.get("description") or t.get("subagentType") or "sub-agent"),
            color="text" if st == "running" else "text_2",
            size=theme.FS_MICRO,
        )
        label.setToolTip(str(t.get("description") or ""))
        row.add(label, 1)
        end = Row(spacing=7, margins=(8, 0, 0, 0))
        if t.get("subagentType") and t.get("subagentType") != "general-purpose":
            end.add(faint(str(t["subagentType"]), size="micro"))
        usage = t.get("usage")
        if isinstance(usage, dict) and usage.get("total_tokens"):
            end.add(faint(tokens(usage["total_tokens"]), size="micro"))
        started = t.get("startedAt")
        ran = ((t.get("endedAt") or now) - started) if started else 0
        if ran > 1500:
            end.add(faint(duration(ran), size="micro"))
        row.add(end)


class _Elbow(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(10, 20)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        c = theme.tokens().color("hair_2")
        p.fillRect(QRectF(1, 0, 1, 10), c)
        p.fillRect(QRectF(1, 9, 7, 1), c)


class PhaseNode(QWidget):
    """`.wfd-node`: the numbered circle on the rail; a check when done, a cross when failed."""

    def __init__(self, index: int, state: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.index, self.state = index, state
        self.setFixedSize(18, 18)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.75, 0.75, 16.5, 16.5)
        if self.state in ("done", "error"):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.color("ok" if self.state == "done" else "err"))
            p.drawEllipse(r)
            p.drawPixmap(3, 3, icons.pixmap("check" if self.state == "done" else "x", t.accent_ink, 11))
            return
        p.setPen(QPen(t.color("accent_text" if self.state == "running" else "hair_3"), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(r)
        p.setFont(theme.font_ui(10))
        p.setPen(t.color("accent_text" if self.state == "running" else "text_3"))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self.index + 1))


def phase_state(agents: List[Json], i: int, active_idx: int, live: bool, total: int) -> str:
    states = [agent_state(a) for a in agents]
    if not agents:
        if active_idx != -1 and i < active_idx:
            return "done"
        if not live and total > 0 and active_idx == -1:
            return "done"
        return "pending"
    if all(s in ("done", "cached") for s in states):
        return "done"
    if any(s == "running" for s in states):
        return "running"
    if any(s in ("error", "blocked") for s in states) and not any(s == "queued" for s in states):
        return "error"
    if any(s == "queued" for s in states) and active_idx == i:
        return "running"
    return "pending"


class WorkflowDetail(QWidget):
    """The whole run: phases down the page, each with its agents and the sub-agents they spawned."""

    def __init__(
        self,
        task: Json,
        session_id: str,
        all_tasks: List[Json],
        script: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.task, self.session_id, self.all_tasks = task, session_id, all_tasks
        self.task_id = task.get("id")
        self.script = script
        self.collapsed = task.get("status") != "running"
        self.busy = False
        self.show_log = False
        self._agent_rows: List[AgentRow] = []
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self.lay = vbox(self, spacing=0)
        self.surface = Surface("raised", "hair_2", 10, margins=(0, 0, 0, 0), spacing=0)
        self.lay.addWidget(self.surface)
        self._build()

    def set_task(self, task: Json, all_tasks: List[Json]) -> None:
        self.task, self.all_tasks = task, all_tasks
        self._build()

    def _script_meta(self) -> ScriptMeta:
        if self.script is None and self.task.get("toolUseId"):
            m = store.models.get(self.session_id)
            t = m.tools.get(str(self.task["toolUseId"])) if m else None
            self.script = str_(t.input if t else None, "script")
        return parse_workflow_meta(self.script)

    def _build(self) -> None:
        clear_layout(self.surface.lay)
        self._agent_rows = []
        task = self.task
        meta = self._script_meta()
        cols = workflow_columns(task, meta)
        wf = task.get("workflow") or {}
        counts = workflow_counts(wf.get("agents") or [])
        live = task.get("status") == "running"
        now = _now_ms()
        elapsed = ((task.get("endedAt") or now) - task["startedAt"]) if task.get("startedAt") else 0
        failed, stopped = task.get("status") == "failed", task.get("status") == "stopped"
        logs = [x for x in wf.get("logs") or [] if isinstance(x, dict)]

        head = QWidget()
        head.setCursor(Qt.CursorShape.PointingHandCursor)
        head.setAccessibleName(str(task.get("workflowName") or task.get("description") or "Workflow"))
        hr = Row(spacing=9, margins=(11, 9, 10, 9))
        vbox(head).addWidget(hr)
        hr.add(IconLabel("workflow", "err" if stopped or failed else "accent_text" if live else "ok", 14))
        title = QWidget()
        tl = vbox(title, spacing=1)
        name = ElideLabel(
            str(task.get("workflowName") or task.get("description") or "Workflow"),
            color="text",
            size=theme.FS_UI,
            weight=500,
        )
        tl.addWidget(name)
        sub = (
            f"{counts.done} of {counts.total} agents done"
            if counts.total
            else ("Starting" if live else STATUS_LABEL.get(str(task.get("status")), str(task.get("status"))))
        )
        if counts.running:
            sub += f" · {counts.running} running"
        if counts.error:
            sub += f" · {counts.error} failed"
        if elapsed > 0:
            sub += f" · {duration(elapsed)}"
        tl.addWidget(faint(sub, size="micro"))
        hr.add(title, 1)
        end = Row(spacing=4)
        if live:
            end.add(bits.Spinner(12))
        else:
            end.add(IconLabel("x" if failed or stopped else "check", "err" if failed or stopped else "ok", 13))
        self.chev = IconLabel("chevron-right" if self.collapsed else "chevron-down", "text_3", 13)
        end.add(self.chev)
        hr.add(end)
        bits.Clickable(head).clicked.connect(self._toggle)
        self.surface.add(head)
        if counts.total > 0:
            bar = ProgressBar()
            bar.set_fractions(counts.done / counts.total, counts.running / counts.total, counts.error / counts.total)
            bar.setContentsMargins(0, 0, 0, 0)
            wrap = QWidget()
            vbox(wrap, margins=(11, 0, 11, 9 if self.collapsed else 0)).addWidget(bar)
            self.surface.add(wrap)

        # Sub-agents the workflow's agents spawned. Claude Code does not say which agent launched them, so
        # each one hangs off the agent that was running when it started.
        subs = [
            t
            for t in self.all_tasks
            if t.get("type") == "local_agent"
            and t.get("ownedBySubagent")
            and t.get("startedAt")
            and task.get("startedAt")
            and t["startedAt"] >= task["startedAt"]
            and (not task.get("endedAt") or t["startedAt"] <= task["endedAt"] + 2000)
        ]
        by_agent: Dict[int, List[Json]] = {}
        agents = [a for a in wf.get("agents") or [] if a]
        for t in subs:
            owners = [
                a
                for a in agents
                if a.get("startedAt")
                and t["startedAt"] >= a["startedAt"]
                and (
                    agent_state(a) == "running"
                    or not a.get("lastProgressAt")
                    or t["startedAt"] <= a["lastProgressAt"] + 2000
                )
            ]
            owners.sort(key=lambda a: a.get("startedAt") or 0, reverse=True)
            key = owners[0]["index"] if owners else -1
            by_agent.setdefault(key, []).append(t)
        active_idx = next(
            (i for i, c in enumerate(cols) if any(agent_state(a) in ("running", "queued") for a in c.agents)), -1
        )

        self.body = Collapse(
            lambda: self._build_body(cols, counts, live, active_idx, by_agent, logs, now), not self.collapsed
        )
        self.surface.add(self.body)
        if live:
            self._timer.start()
        else:
            self._timer.stop()

    def _build_body(
        self, cols, counts, live: bool, active_idx: int, by_agent: Dict[int, List[Json]], logs: List[Json], now: float
    ) -> QWidget:
        body = QWidget()
        bl = vbox(body, margins=(11, 10, 8, 4), spacing=0)
        if not cols:
            bl.addWidget(faint("Waiting for the first agent…" if live else "No agents were reported for this run."))
        for i, c in enumerate(cols):
            state = phase_state(c.agents, i, active_idx, live, counts.total)
            phase = QWidget()
            pl = Row(spacing=9, margins=(0, 0, 0, 4 if i == len(cols) - 1 else 10))
            vbox(phase).addWidget(pl)
            pl.lay.addWidget(PhaseNode(int(c.phase.get("index", i)), state), 0, Qt.AlignmentFlag.AlignTop)
            col = QWidget()
            cl = vbox(col, spacing=4)
            ph = Row(spacing=6)
            title = ElideLabel(
                str(c.phase.get("title") or ""),
                color="text_3" if state == "pending" else "text",
                size=theme.FS_SMALL,
                weight=500 if state != "pending" else 400,
            )
            ph.add(title, 1)
            kind = c.phase.get("kind")
            if kind and kind != "sequential":
                k = bits.label(str(kind), size="micro")
                restyle(
                    k,
                    lambda t: (
                        f"QLabel {{ font-size: 10px; padding: 0 5px; border-radius: 7px; "
                        f"background: {theme.blend(t.fill_2, t.canvas).name()}; color: {t.text_3}; }}"
                    ),
                )
                ph.add(k)
            if state not in ("done", "error"):
                states = [agent_state(a) for a in c.agents]
                bad = len([s for s in states if s in ("error", "blocked")])
                text = (
                    (f"running · {bad} failed" if bad else "running")
                    if state == "running"
                    else ("queued" if i == active_idx else "pending")
                )
                sl = bits.label(text, size="micro")
                restyle(
                    sl,
                    lambda t, st=state: (
                        f"QLabel {{ font-size: {theme.FS_MICRO}px; "
                        f"color: {t.accent_text if st == 'running' else t.text_4}; }}"
                    ),
                )
                ph.add(sl)
            cl.addWidget(ph)
            for a in c.agents:
                row = AgentRow(a, now, by_agent.get(int(a.get("index", -2)), []))
                self._agent_rows.append(row)
                cl.addWidget(row)
            if not c.agents:
                g = faint("No agents" if state == "done" else "Not started yet", size="micro")
                g.setContentsMargins(6, 3, 6, 3)
                cl.addWidget(g)
            pl.add(col, 1)
            bl.addWidget(phase)
        orphans = by_agent.get(-1, [])
        if orphans:
            ow = QWidget()
            ol = vbox(ow, margins=(27, 6, 0, 6), spacing=2)
            ol.addWidget(tool_label("Sub-agents"))
            for t in orphans:
                ol.addWidget(SubAgentRow(t, now, elbow=False))
            bl.addWidget(ow)
        foot = Row(spacing=4, margins=(0, 6, 0, 6))
        self.log_body: Optional[Collapse] = None
        if logs:

            def log_lines() -> QWidget:
                lw = QWidget()
                ll = vbox(lw, margins=(2, 0, 0, 8), spacing=2)
                for x in logs:
                    line = Row(spacing=10)
                    at = x.get("at")
                    stamp = (
                        datetime.fromtimestamp(at / 1000).strftime("%H:%M:%S") if isinstance(at, (int, float)) else ""
                    )
                    ts = faint(stamp, size="micro")
                    ts.setFixedWidth(54)
                    line.add(ts)
                    line.add(bits.label(str(x.get("message") or ""), tone="muted", size="micro", wrap=True), 1)
                    ll.addWidget(line)
                return lw

            self.log_toggle = Toggle(f"Log · {len(logs)}", log_lines)
            foot.add(self.log_toggle)
        foot.stretch()
        usage = self.task.get("usage")
        if isinstance(usage, dict) and usage.get("total_tokens"):
            foot.add(faint(f"{tokens(usage['total_tokens'])} tokens", size="micro"))
        if live:
            self.stop_btn = bits.button(
                "Stop", kind="ghost", size="sm", icon="circle-stop", on_click=self.stop, tooltip="Stop this workflow"
            )
            self.stop_btn.setAccessibleName("Stop this workflow")
            restyle(self.stop_btn, lambda t: f"QPushButton {{ color: {t.err}; }}")
            foot.add(self.stop_btn)
        bl.addWidget(foot)
        return body

    def _toggle(self) -> None:
        self.collapsed = not self.collapsed
        self.body.set_open(not self.collapsed)
        self.chev.set("chevron-right" if self.collapsed else "chevron-down")

    def _tick(self) -> None:
        now = _now_ms()
        for r in self._agent_rows:
            r.tick(now)

    def stop(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.stop_btn.setEnabled(False)

        def done(_r: Any) -> None:
            store.toast(f"Stopping {self.task.get('workflowName') or 'the workflow'}", "info")
            self.busy = False

        def failed(_e: Any) -> None:
            self.busy = False
            self.stop_btn.setEnabled(True)

        store.call("session.stopTask", {"id": self.session_id, "taskId": self.task.get("id")}).then(done).catch(failed)
