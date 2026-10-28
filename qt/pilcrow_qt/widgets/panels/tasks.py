"""The Tasks tab (a port of web/src/components/panels/TasksPanel.tsx and the run card in
tools/WorkflowBody.tsx): Claude's to-do list, the workflows it is running with their phases and
agents, the background commands and agents running now, and the ones that finished."""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from ... import icons, theme
from ...lib.fmt import duration, plural, short_model, time_ago, tokens
from ...lib.workflow import WorkflowCounts, agent_state, parse_workflow_meta, workflow_columns, workflow_counts
from ...store import store
from ..ui.bits import Clickable, Spinner, button, label
from ..ui.collapse import Collapse
from ..ui.signals import bind
from .right_panel import Refresher, Section, empty_text

Json = Dict[str, Any]
STATUS_LABEL = {"completed": "Completed", "failed": "Failed", "stopped": "Stopped", "running": "Running"}
STATE_LABEL = {
    "queued": "Queued",
    "running": "Running",
    "done": "Done",
    "cached": "Reused",
    "error": "Failed",
    "blocked": "Blocked",
}
STATE_ICON = {"done": "check", "cached": "rotate-ccw", "error": "x", "blocked": "ban"}


def _clock(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def _str(d: Optional[Json], key: str) -> str:
    v = (d or {}).get(key)
    return v if isinstance(v, str) else ""


def reconstruct_todos(sid: str) -> Optional[List[Json]]:
    """The latest plan: TaskCreate/TaskUpdate tasks when newer than the last TodoWrite, else its todos."""
    m = store.models.get(sid)
    if m is None:
        return None
    todo = m.latest_tool("TodoWrite")
    tasks: Dict[str, Json] = {}
    order = 0
    last_task_ts = 0.0
    for t in m.tools.values():
        s = t.structured if isinstance(t.structured, dict) else {}
        if t.name == "TaskCreate" and t.status == "done":
            task = s.get("task") if isinstance(s.get("task"), dict) else {}
            tid = task.get("id", s.get("id"))
            if tid is None:
                mt = re.search(r"#?(\d+)", (t.result.text if t.result else "") or "")
                tid = mt.group(1) if mt else None
            if tid is None:
                order += 1
                tid = order
            tid = str(tid)
            tasks[tid] = {
                "id": tid,
                "content": _str(t.input, "subject"),
                "status": "pending",
                "activeForm": _str(t.input, "activeForm") or None,
            }
            last_task_ts = max(last_task_ts, t.start_ts)
        elif t.name == "TaskUpdate" and t.status == "done":
            tid = str((t.input or {}).get("taskId"))
            cur = tasks.get(tid)
            if not cur:
                continue
            if (t.input or {}).get("status") == "deleted":
                del tasks[tid]
            else:
                cur["status"] = _str(t.input, "status") or cur["status"]
                cur["content"] = _str(t.input, "subject") or cur["content"]
                cur["activeForm"] = _str(t.input, "activeForm") or cur["activeForm"]
            last_task_ts = max(last_task_ts, t.start_ts)
    if tasks and (todo is None or last_task_ts > todo.start_ts):
        return list(tasks.values())
    todos = (todo.input or {}).get("todos") if todo else None
    return todos if isinstance(todos, list) else None


def _in_workflow(t: Json, workflows: List[Json]) -> bool:
    """Sub-agents a workflow spawned are drawn inside the workflow; everything else lists on its own."""
    if t.get("type") != "local_agent" or not t.get("ownedBySubagent"):
        return False
    st = t.get("startedAt")
    for w in workflows:
        ws, we = w.get("startedAt"), w.get("endedAt")
        if ws and st and st >= ws and (not we or st <= we + 2000):
            return True
    return False


# ---------------------------------------------------------------- painted bits


class Bar(QWidget):
    """A thin progress bar: ``set_parts([(fraction, token_name), ...])`` paints left to right."""

    def __init__(self, height: int = 4, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._parts: List[tuple] = []
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_parts(self, parts: List[tuple]) -> None:
        self._parts = parts
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.height() / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.blend(t.fill_2, t.chrome))
        p.drawRoundedRect(QRectF(self.rect()), r, r)
        x = 0.0
        for frac, tone in self._parts:
            w = max(0.0, min(1.0, frac)) * self.width()
            if w <= 0:
                continue
            p.setBrush(theme.qcolor(getattr(t, tone)))
            p.drawRoundedRect(QRectF(x, 0, w, self.height()), r, r)
            x += w


class TodoBox(QWidget):
    """The 15px circle before a to-do: empty, a dot while in progress, a tick when done."""

    def __init__(self, status: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.status = status
        self.setFixedSize(15, 15)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if self.status == "completed":
            p.setBrush(theme.blend(t.fill_3, t.chrome))
            p.drawEllipse(QRectF(0, 0, 15, 15))
            p.drawPixmap(2, 2, icons.pixmap("check", t.text_2, 11))
            return
        p.setPen(QPen(theme.blend(t.text_2 if self.status == "in_progress" else t.hair_3, t.chrome), 1.5))
        p.drawEllipse(QRectF(1, 1, 13, 13))
        if self.status == "in_progress":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.text))
            p.drawEllipse(QRectF(5, 5, 5, 5))


class StateMark(QWidget):
    """An agent's state in 14px: a spinner, a hollow dot while queued, or the state's icon."""

    def __init__(self, st: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.st = st
        self.setFixedSize(14, 16)
        self.setAccessibleName(STATE_LABEL.get(st, st))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if st == "running":
            lay.addWidget(Spinner(12))
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.st == "queued":
            p.setPen(QPen(theme.blend(t.hair_3, t.chrome), 1.5))
            p.drawEllipse(QRectF(3, 4, 8, 8))
        elif self.st in STATE_ICON:
            color = t.ok if self.st in ("done", "cached") else t.err if self.st == "error" else t.warn
            p.drawPixmap(1, 2, icons.pixmap(STATE_ICON[self.st], color, 12))


# ---------------------------------------------------------------- rows


def _todo_row(todo: Json) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 3, 0, 3)
    lay.setSpacing(10)
    status = str(todo.get("status") or "pending")
    lay.addWidget(TodoBox(status), 0, Qt.AlignmentFlag.AlignTop)
    text = (todo.get("activeForm") or todo.get("content")) if status == "in_progress" else todo.get("content")
    lb = label(
        str(text or ""),
        tone="faint" if status == "completed" else ("" if status == "in_progress" else "muted"),
        wrap=True,
    )
    if status == "completed":
        f = lb.font()
        f.setStrikeOut(True)
        lb.setFont(f)
    elif status == "in_progress":
        lb.setFont(theme.font_ui(theme.FS_UI, QFont.Weight.Medium))
    lay.addWidget(lb, 1)
    return w


class TaskRow(QWidget):
    """A background command or agent: running (a card with Stop) or finished (a quiet line)."""

    def __init__(self, t: Json, sid: str, done: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.task = t
        self.setObjectName("taskRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4 if done else 12, 4 if done else 9, 2 if done else 10, 4 if done else 9)
        lay.setSpacing(10)
        tk = theme.tokens()
        ico = QLabel()
        if done:
            ok = t.get("status") == "completed"
            ico.setPixmap(icons.pixmap("check" if ok else "x", tk.ok if ok else tk.err, 13))
        else:
            ico.setPixmap(icons.pixmap("square-terminal" if t.get("type") == "local_bash" else "bot", tk.text_2, 13))
        lay.addWidget(ico)
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(2)
        title = label(
            str(t.get("description") or t.get("subagentType") or t.get("type") or ""), tone="muted" if done else ""
        )
        ml.addWidget(title)
        sub = QWidget()
        sl = QHBoxLayout(sub)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(4)
        if done:
            parts = [str(t.get("status") or "")]
            if t.get("endedAt"):
                parts.append(time_ago(float(t["endedAt"])))
            if t.get("summary"):
                parts.append(str(t["summary"])[:120])
            self.sub = label(" · ".join(parts), tone="faint", size="micro")
        else:
            sl.addWidget(Spinner(10))
            self.sub = label(self._live_text(), tone="faint", size="micro")
        sl.addWidget(self.sub, 1)
        ml.addWidget(sub)
        lay.addWidget(main, 1)
        if not done:
            stop = button("Stop", kind="ghost", size="sm", icon="circle-stop", tooltip="Stop this task")
            stop.setText("Stop")
            stop.clicked.connect(lambda: store.call("session.stopTask", {"id": sid, "taskId": t.get("id")}))
            lay.addWidget(stop)
        self._done = done
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _live_text(self) -> str:
        t = self.task
        parts = [str(t.get("progress") or (f"using {t['lastTool']}" if t.get("lastTool") else "working"))]
        usage = t.get("usage") or {}
        if usage:
            parts.append(
                f"{plural(int(usage.get('tool_uses') or 0), 'tool use')} · {tokens(int(usage.get('total_tokens') or 0))} tokens"
            )
        if t.get("startedAt"):
            parts.append(duration(time.time() * 1000 - float(t["startedAt"])))
        return " · ".join(parts)

    def tick(self) -> None:
        if not self._done:
            self.sub.setText(self._live_text())

    def _paint(self) -> None:
        t = theme.tokens()
        if self._done:
            self.setStyleSheet("")
        else:
            self.setStyleSheet(
                f"#taskRow {{ background: {t.canvas}; border-radius: {theme.R3}px; border: 1px solid {theme.blend(t.hair, t.canvas).name()}; }}"
            )


class AgentRow(QWidget):
    """One agent of a workflow: its state, label, model and time; opens to its prompt and result."""

    def __init__(self, a: Json, now: float, subs: List[Json], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        st = agent_state(a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(5, 4, 6, 4)
        rl.setSpacing(7)
        rl.addWidget(StateMark(st), 0, Qt.AlignmentFlag.AlignTop)
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(5)
        top.addWidget(
            label(
                str(a.get("label") or f"agent {int(a.get('index') or 0) + 1}"),
                tone="faint" if st == "queued" else "",
                size="small",
            ),
            1,
        )
        attempt = int(a.get("attempt") or 0)
        if attempt > 1:
            chip = label(f"×{attempt}", tone="faint", size="micro")
            chip.setToolTip(f"Retried: {a['lastAttemptReason']}" if a.get("lastAttemptReason") else "Retried")
            top.addWidget(chip)
        tk = theme.tokens()
        if a.get("isolation") in ("worktree", "remote"):
            iso = QLabel()
            iso.setPixmap(icons.pixmap("git-branch" if a["isolation"] == "worktree" else "cloud", tk.text_4, 10))
            top.addWidget(iso)
        ran = 0.0
        if a.get("startedAt"):
            end = now if st == "running" else float(a.get("lastProgressAt") or a.get("updatedAt") or now)
            ran = end - float(a["startedAt"])
        end_parts = []
        if a.get("model"):
            end_parts.append(short_model(str(a["model"])))
        if ran > 1500:
            end_parts.append(duration(ran))
        if end_parts:
            top.addWidget(label("  ".join(end_parts), tone="faint", size="micro"))
        ml.addLayout(top)
        if st == "running":
            what = a.get("lastToolSummary") or (f"Using {a['lastToolName']}" if a.get("lastToolName") else "Thinking…")
        elif st in ("error", "blocked"):
            what = str(a.get("error") or "").split("\n")[0]
        elif st == "queued":
            what = "Waiting for a slot"
        else:
            what = str(a.get("resultPreview") or "").split("\n")[0]
        if what:
            ml.addWidget(label(str(what), tone="faint", size="micro"))
        rl.addWidget(main, 1)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(row)
        self.body = Collapse(lambda: self._build_body(a), False)
        lay.addWidget(self.body)
        Clickable(row).clicked.connect(self.body.toggle)
        for t in subs:
            lay.addWidget(_sub_agent_row(t, now))

    def _build_body(self, a: Json) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(26, 2, 6, 6)
        lay.setSpacing(6)
        if a.get("agentType"):
            text = str(a["agentType"]) + (f" · {str(a['agentId'])[:8]}" if a.get("agentId") else "")
            lay.addWidget(label(text, tone="faint", size="micro"))
        if a.get("promptPreview"):
            lay.addWidget(label("Prompt", tone="faint", size="micro"))
            lay.addWidget(label(str(a["promptPreview"]), size="small", mono=True, wrap=True))
        if a.get("resultPreview") or a.get("error"):
            lay.addWidget(label("Error" if a.get("error") else "Result", tone="faint", size="micro"))
            lay.addWidget(
                label(
                    str(a.get("error") or a.get("resultPreview") or ""),
                    tone="err" if a.get("error") else "",
                    size="small",
                    mono=True,
                    wrap=True,
                )
            )
        return w


def _sub_agent_row(t: Json, now: float) -> QWidget:
    st = "running" if t.get("status") == "running" else "done" if t.get("status") == "completed" else "error"
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(22, 2, 6, 2)
    lay.setSpacing(6)
    lay.addWidget(StateMark(st))
    lb = label(str(t.get("description") or t.get("subagentType") or "sub-agent"), size="small")
    lb.setToolTip(str(t.get("description") or ""))
    lay.addWidget(lb, 1)
    parts = []
    if t.get("subagentType") and t["subagentType"] != "general-purpose":
        parts.append(str(t["subagentType"]))
    if t.get("usage"):
        parts.append(tokens(int((t["usage"] or {}).get("total_tokens") or 0)))
    ran = (float(t.get("endedAt") or now) - float(t["startedAt"])) if t.get("startedAt") else 0
    if ran > 1500:
        parts.append(duration(ran))
    if parts:
        lay.addWidget(label("  ".join(parts), tone="faint", size="micro"))
    return w


class WorkflowDetail(QWidget):
    """The whole run: phases down the card, each with its agents and the sub-agents they spawned."""

    def __init__(self, task: Json, sid: str, all_tasks: List[Json], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.task = task
        self.sid = sid
        self.setObjectName("wfd")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        m = store.models.get(sid)
        tool = m.tools.get(str(task.get("toolUseId") or "")) if m else None
        script = _str(tool.input if tool else None, "script")
        meta = parse_workflow_meta(script)
        self.cols = workflow_columns(task, meta)
        self.counts: WorkflowCounts = workflow_counts((task.get("workflow") or {}).get("agents") or [])
        live = task.get("status") == "running"
        now = time.time() * 1000
        elapsed = (float(task.get("endedAt") or now) - float(task["startedAt"])) if task.get("startedAt") else 0
        failed, stopped = task.get("status") == "failed", task.get("status") == "stopped"
        t = theme.tokens()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(11, 9, 10, 9)
        hl.setSpacing(9)
        ico = QLabel()
        ico.setPixmap(icons.pixmap("workflow", t.err if (stopped or failed) else t.accent_text if live else t.ok, 14))
        hl.addWidget(ico, 0, Qt.AlignmentFlag.AlignTop)
        title = QWidget()
        tl = QVBoxLayout(title)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(1)
        self.name = label(str(task.get("workflowName") or task.get("description") or "Workflow"))
        self.name.setFont(theme.font_ui(theme.FS_BODY, QFont.Weight.DemiBold))
        tl.addWidget(self.name)
        c = self.counts
        sub = (
            f"{c.done} of {c.total} agents done"
            if c.total
            else ("Starting" if live else STATUS_LABEL.get(str(task.get("status")), str(task.get("status"))))
        )
        if c.running:
            sub += f" · {c.running} running"
        if c.error:
            sub += f" · {c.error} failed"
        if elapsed > 0:
            sub += f" · {duration(elapsed)}"
        self.sub = label(sub, tone="faint", size="micro")
        tl.addWidget(self.sub)
        hl.addWidget(title, 1)
        end = QLabel()
        if live:
            hl.addWidget(Spinner(12))
        else:
            end.setPixmap(
                icons.pixmap("x" if (failed or stopped) else "check", t.err if (failed or stopped) else t.ok, 13)
            )
            hl.addWidget(end)
        self.chev = QLabel()
        hl.addWidget(self.chev)
        head.setCursor(Qt.CursorShape.PointingHandCursor)
        head.setAccessibleName(self.name.text())
        lay.addWidget(head)
        if c.total:
            bar = Bar(2)
            bar.set_parts([(c.done / c.total, "ok"), (c.running / c.total, "accent"), (c.error / c.total, "err")])
            bw = QWidget()
            bl = QHBoxLayout(bw)
            bl.setContentsMargins(14, 0, 14, 0)
            bl.addWidget(bar)
            lay.addWidget(bw)
        self.body = Collapse(lambda: self._build_body(all_tasks, live, now), live)
        lay.addWidget(self.body)
        Clickable(head).clicked.connect(self._toggle)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _toggle(self) -> None:
        self.body.toggle()
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.setStyleSheet(
            f"#wfd {{ background: {t.canvas}; border-radius: 10px; border: 1px solid {theme.blend(t.hair, t.canvas).name()}; }}"
        )
        self.chev.setPixmap(icons.pixmap("chevron-down" if self.body.is_open else "chevron-right", t.text_3, 13))

    def _build_body(self, all_tasks: List[Json], live: bool, now: float) -> QWidget:
        task = self.task
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(11, 6, 10, 10)
        lay.setSpacing(6)
        agents = [a for a in ((task.get("workflow") or {}).get("agents") or []) if a]
        subs = [x for x in all_tasks if _in_workflow(x, [task])]
        by_agent: Dict[int, List[Json]] = {}
        for s in subs:
            owners = [
                a
                for a in agents
                if a.get("startedAt")
                and s["startedAt"] >= a["startedAt"]
                and (
                    agent_state(a) == "running"
                    or not a.get("lastProgressAt")
                    or s["startedAt"] <= a["lastProgressAt"] + 2000
                )
            ]
            owners.sort(key=lambda a: -(a.get("startedAt") or 0))
            key = int(owners[0]["index"]) if owners else -1
            by_agent.setdefault(key, []).append(s)
        cols = self.cols
        if not cols:
            lay.addWidget(
                label(
                    "Waiting for the first agent…" if live else "No agents were reported for this run.",
                    tone="faint",
                    size="small",
                )
            )
        active_idx = next(
            (i for i, c in enumerate(cols) if any(agent_state(a) in ("running", "queued") for a in c.agents)), -1
        )
        t = theme.tokens()
        for i, c in enumerate(cols):
            states = [agent_state(a) for a in c.agents]
            if not c.agents:
                state = (
                    "done"
                    if (active_idx != -1 and i < active_idx)
                    or (not live and self.counts.total > 0 and active_idx == -1)
                    else "pending"
                )
            elif all(s in ("done", "cached") for s in states):
                state = "done"
            elif "running" in states:
                state = "running"
            elif any(s in ("error", "blocked") for s in states) and "queued" not in states:
                state = "error"
            elif "queued" in states and active_idx == i:
                state = "running"
            else:
                state = "pending"
            phase = QWidget()
            pl = QHBoxLayout(phase)
            pl.setContentsMargins(0, 0, 0, 10 if i < len(cols) - 1 else 4)
            pl.setSpacing(9)
            node = QLabel()
            node.setFixedSize(18, 18)
            node.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if state == "done":
                node.setPixmap(icons.pixmap("check", t.accent_ink, 11))
                node.setStyleSheet(f"background: {t.ok}; border-radius: 9px;")
            elif state == "error":
                node.setPixmap(icons.pixmap("x", t.accent_ink, 11))
                node.setStyleSheet(f"background: {t.err}; border-radius: 9px;")
            else:
                node.setText(str(int(c.phase.get("index") or 0) + 1))
                ring = t.accent_text if state == "running" else theme.blend(t.hair_3, t.raised).name()
                node.setStyleSheet(
                    f"background: {t.raised}; border-radius: 9px; border: 1.5px solid {ring}; color: {t.accent_text if state == 'running' else t.text_3}; font-size: 10px; font-weight: 600;"
                )
            pl.addWidget(node, 0, Qt.AlignmentFlag.AlignTop)
            col = QWidget()
            cl = QVBoxLayout(col)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(4)
            head = QHBoxLayout()
            head.setSpacing(6)
            title = label(str(c.phase.get("title") or ""), tone="faint" if state == "pending" else "", size="small")
            title.setFont(
                theme.font_ui(theme.FS_SMALL, QFont.Weight.Medium if state == "pending" else QFont.Weight.DemiBold)
            )
            head.addWidget(title)
            kind = c.phase.get("kind")
            if kind and kind != "sequential":
                k = label(str(kind), tone="faint", size="micro")
                k.setStyleSheet(
                    f"background: {theme.blend(t.fill_2, t.canvas).name()}; border-radius: 999px; padding: 0 5px; font-size: 10px; color: {t.text_3};"
                )
                head.addWidget(k)
            head.addStretch(1)
            if state not in ("done", "error"):
                failed_n = sum(1 for s in states if s in ("error", "blocked"))
                if state == "running":
                    st_text = f"running · {failed_n} failed" if failed_n else "running"
                else:
                    st_text = "queued" if i == active_idx else "pending"
                st_lb = label(st_text, tone="accent" if state == "running" else "faint", size="micro")
                head.addWidget(st_lb)
            cl.addLayout(head)
            for a in c.agents:
                cl.addWidget(AgentRow(a, now, by_agent.get(int(a.get("index") or 0), [])))
            if not c.agents:
                cl.addWidget(label("No agents" if state == "done" else "Not started yet", tone="faint", size="micro"))
            pl.addWidget(col, 1)
            lay.addWidget(phase)
        orphans = by_agent.get(-1) or []
        if orphans:
            lay.addWidget(label("Sub-agents", tone="faint", size="micro"))
            for s in orphans:
                lay.addWidget(_sub_agent_row(s, now))
        foot = QHBoxLayout()
        foot.setSpacing(8)
        logs = (task.get("workflow") or {}).get("logs") or []
        log_body: Optional[Collapse] = None
        if logs:
            log_btn = button(f"Log · {len(logs)}", kind="ghost", size="sm")
            foot.addWidget(log_btn)
            log_body = Collapse(lambda: self._build_log(logs), False)
            log_btn.clicked.connect(log_body.toggle)
        usage = task.get("usage") or {}
        if int(usage.get("total_tokens") or 0) > 0:
            foot.addWidget(label(f"{tokens(int(usage['total_tokens']))} tokens", tone="faint", size="micro"))
        foot.addStretch(1)
        if live:
            stop = button("Stop", kind="ghost", size="sm", icon="circle-stop", tooltip="Stop this workflow")
            stop.setText("Stop")
            stop.clicked.connect(self._stop)
            foot.addWidget(stop)
        lay.addLayout(foot)
        if log_body is not None:
            lay.addWidget(log_body)
        return w

    def _build_log(self, logs: List[Json]) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(2)
        for entry in logs:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(label(_clock(float(entry.get("at") or 0)), tone="faint", size="micro"))
            row.addWidget(label(str(entry.get("message") or ""), size="micro", wrap=True), 1)
            lay.addLayout(row)
        return w

    def _stop(self) -> None:
        name = self.task.get("workflowName") or "the workflow"
        store.call("session.stopTask", {"id": self.sid, "taskId": self.task.get("id")}).then(
            lambda _r: store.toast(f"Stopping {name}", "info")
        )


# ---------------------------------------------------------------- the panel


class TasksPanel(QWidget):
    """To-do list, workflows, running and finished background work for one session."""

    def __init__(self, session_id: str, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session_id
        self.cwd = cwd
        self._key: Any = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.host = QWidget()
        self._lay = QVBoxLayout(self.host)
        self._lay.setContentsMargins(16, 4, 16, 24)
        self._lay.setSpacing(0)
        self._lay.addStretch(1)
        self.scroll.setWidget(self.host)
        lay.addWidget(self.scroll)
        self._sections: List[QWidget] = []
        self._rows: List[TaskRow] = []
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(lambda: [r.tick() for r in self._rows])
        self._refresher = Refresher(self, session_id, self.render, fs=False)
        self.render()

    def render(self) -> None:
        rt = store.rt_of(self.sid)
        tasks: List[Json] = list(rt.get("tasks") or []) if rt else []
        todos = reconstruct_todos(self.sid)
        key = (
            rt.rev if rt else 0,
            [
                (
                    t.get("id"),
                    t.get("status"),
                    t.get("progress"),
                    t.get("lastTool"),
                    (t.get("workflow") or {}).get("updatedAt"),
                )
                for t in tasks
            ],
            todos,
        )
        if key == self._key:
            return
        self._key = key
        for s in self._sections:
            self._lay.removeWidget(s)
            s.setParent(None)
            s.deleteLater()
        self._sections = []
        self._rows = []
        workflows = [t for t in tasks if t.get("type") == "local_workflow" and not t.get("ambient")]
        own = [
            t
            for t in tasks
            if t.get("type") != "local_workflow" and not t.get("ambient") and not _in_workflow(t, workflows)
        ]
        running = [t for t in own if t.get("status") == "running"]
        finished = list(reversed([t for t in own if t.get("status") != "running"][-12:]))
        done = sum(1 for t in (todos or []) if isinstance(t, dict) and t.get("status") == "completed")

        s = Section("To-do list")
        if todos:
            s.head_add(label(f"{done} of {len(todos)} done", tone="faint", size="small"))
            bar = Bar(4)
            bar.set_parts([(done / len(todos), "text_2")])
            s.add(bar)
            lst = QWidget()
            ll = QVBoxLayout(lst)
            ll.setContentsMargins(0, 2, 0, 2)
            ll.setSpacing(2)
            for t in todos:
                if isinstance(t, dict):
                    ll.addWidget(_todo_row(t))
            s.add(lst)
        else:
            s.add(empty_text("Claude's plan for multi-step work appears here."))
        self._add_section(s)

        if workflows:
            s = Section("Workflows")
            for t in reversed(workflows):
                s.add(WorkflowDetail(t, self.sid, tasks))
            self._add_section(s)

        s = Section("Running now")
        if running:
            for t in running:
                row = TaskRow(t, self.sid, False)
                self._rows.append(row)
                s.add(row)
        else:
            s.add(empty_text("No background commands or agents running."))
        self._add_section(s)

        if finished:
            s = Section("Finished")
            for t in finished:
                s.add(TaskRow(t, self.sid, True))
            self._add_section(s)
        for i, sec in enumerate(self._sections):
            sec.set_last(i == len(self._sections) - 1)  # type: ignore[attr-defined]
        if running and not self._tick.isActive():
            self._tick.start()
        elif not running:
            self._tick.stop()

    def _add_section(self, s: Section) -> None:
        self._lay.insertWidget(self._lay.count() - 1, s)
        self._sections.append(s)

    def workflow_cards(self) -> List[WorkflowDetail]:
        return self.findChildren(WorkflowDetail)
