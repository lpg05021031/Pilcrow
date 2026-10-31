"""The Tasks tab (a port of web/src/components/panels/TasksPanel.tsx and the run card in
tools/WorkflowBody.tsx): Claude's to-do list, the workflows it is running with their phases and
agents, the background commands and agents running now, and the ones that finished.

Every measurement is the one in panels.css / transcript.css (``.todo``, ``.task-row``, ``.wfd`` and
friends); each widget's docstring names its rule."""

from __future__ import annotations

import html
import math
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from ... import icons, theme
from ...canvas.pixels import hline, vline
from ...lib.fmt import duration, plural, short_model, time_ago, tokens
from ...lib.workflow import WorkflowCounts, agent_state, parse_workflow_meta, workflow_columns, workflow_counts
from ...store import store
from ..ui.bits import Clickable, Pill, Spinner
from ..ui.collapse import Collapse
from ..ui.signals import bind
from .right_panel import Btn, Card, EmptyState, Refresher, Section, elide, lbl

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
    """A thin progress bar (``.progress`` 4px, ``.wf-bar`` 2px): ``set_parts([(fraction, token), …])``."""

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
        p.setBrush(theme.blend(t.fill_2, t.canvas))
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
    """The 15px circle before a to-do (``.todo-box``): a hairline ring, a breathing dot while in
    progress, a tick on a filled disc when done."""

    def __init__(self, status: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.status = status
        self.setFixedSize(15, 15)
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self.update)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def showEvent(self, e) -> None:  # noqa: N802
        if self.status == "in_progress" and not store.reduced_motion:
            self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if self.status == "completed":
            p.setBrush(theme.blend(t.fill_3, t.canvas))
            p.drawEllipse(QRectF(0, 0, 15, 15))
            p.drawPixmap(2, 2, icons.pixmap("check", t.text_2, 11))
            return
        p.setPen(QPen(theme.blend(t.text_2 if self.status == "in_progress" else t.hair_3, t.canvas), 1.5))
        p.drawEllipse(QRectF(0.75, 0.75, 13.5, 13.5))
        if self.status == "in_progress":
            p.setPen(Qt.PenStyle.NoPen)
            dot = QColor(t.text)
            if self._timer.isActive():  # @keyframes breathe: 1.4s ease-in-out between 0.45 and 1
                phase = (time.monotonic() - self._t0) % 1.4 / 1.4
                dot.setAlphaF(0.45 + 0.55 * (0.5 + 0.5 * math.cos(phase * 2 * math.pi)))
            p.setBrush(dot)
            p.drawEllipse(QPointF(7.5, 7.5), 2.5, 2.5)


class TintSpinner(Spinner):
    """The shared spinner in a token's colour (``.wf-mark.running`` is the accent)."""

    def __init__(self, size: int = 12, tone: str = "text_3", parent: Optional[QWidget] = None) -> None:
        super().__init__(size, parent)
        self._tone = tone

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(getattr(t, self._tone)), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        ang = int(((time.monotonic() - self._t0) % 1.0) * 360)
        p.drawArc(QRectF(1.5, 1.5, self._size - 3, self._size - 3), -ang * 16, 270 * 16)


class StateMark(QWidget):
    """An agent's state in a 14×16 box (``.wf-mark``): a spinner, a hollow dot while queued, or the
    state's icon in its colour."""

    def __init__(self, st: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.st = st
        self.setFixedSize(14, 16)
        self.setAccessibleName(STATE_LABEL.get(st, st))
        if st == "running":
            lay = QHBoxLayout(self)
            lay.setContentsMargins(1, 2, 1, 2)
            lay.addWidget(TintSpinner(12, "accent_text"))
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.st == "queued":
            p.setPen(QPen(QColor(t.text_4), 1.5))
            p.drawEllipse(QRectF(4.75, 5.75, 4.5, 4.5))
        elif self.st in STATE_ICON:
            color = t.ok if self.st in ("done", "cached") else t.err
            p.drawPixmap(1, 2, icons.pixmap(STATE_ICON[self.st], color, 12))


class HoverRow(QWidget):
    """A row that lights up on hover or while open (``.wfa-row``, ``.wfd-head``)."""

    def __init__(self, radius: int = theme.R1, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._hover = False
        self.open = False
        self.radius = radius
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_open(self, open_: bool) -> None:
        self.open = open_
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        if self._hover or self.open:
            t = theme.tokens()
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.blend(t.fill_1, t.raised))
            p.drawRoundedRect(QRectF(self.rect()), self.radius, self.radius)


class Chip(Pill):
    """A tiny pill: ``.wf-chip`` (×2) and ``.wf-phase-kind`` (parallel)."""

    def __init__(self, text: str, round_: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, bg="fill_2/raised", radius=7 if round_ else 4, parent=parent)
        pad = 5 if round_ else 4
        self.setContentsMargins(pad, 0, pad, 0)
        self.setFont(theme.font_ui(10))
        theme.tint(self, fg="text_3")


# ---------------------------------------------------------------- rows


def _todo_row(todo: Json) -> QWidget:
    """``.todo``: the box and the text, 10px apart, 3px above and below, 13px with 1.5 line height."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 3, 0, 3)
    lay.setSpacing(10)
    status = str(todo.get("status") or "pending")
    box = TodoBox(status)
    boxw = QWidget()
    bl = QVBoxLayout(boxw)
    bl.setContentsMargins(0, 2, 0, 0)
    bl.addWidget(box)
    bl.addStretch(1)
    lay.addWidget(boxw, 0, Qt.AlignmentFlag.AlignTop)
    text = (todo.get("activeForm") or todo.get("content")) if status == "in_progress" else todo.get("content")
    tone = "faint" if status == "completed" else ("" if status == "in_progress" else "muted")
    lb = lbl(
        str(text or ""), theme.FS_UI, 500 if status == "in_progress" else 400, tone=tone, wrap=True, line_height=1.5
    )
    if status == "completed":
        lb.set_html(f"<s>{html.escape(str(text or ''))}</s>", str(text or ""))
    lay.addWidget(lb, 1)
    return w


class TaskRow(Card):
    """A background command or agent: running (``.task-row``, a card with Stop) or finished
    (``.task-row.done``, a quiet line)."""

    def __init__(self, t: Json, sid: str, done: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__("canvas", "hair", theme.R3, parent)
        self.task = t
        self._done = done
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2 if done else 12, 4 if done else 9, 2 if done else 10, 4 if done else 9)
        lay.setSpacing(10)
        self.ico = QLabel()
        self.ico.setFixedSize(13, 13)
        lay.addWidget(self.ico)
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(2)
        self.title = elide(
            lbl(
                str(t.get("description") or t.get("subagentType") or t.get("type") or ""),
                theme.FS_UI,
                tone="muted" if done else "",
            )
        )
        ml.addWidget(self.title)
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
            self.sub = elide(lbl(" · ".join(parts), 11.5, tone="faint"))
        else:
            sl.addWidget(Spinner(10))
            self.sub = elide(lbl(self._live_text(), 11.5, tone="faint"))
        sl.addWidget(self.sub, 1)
        ml.addWidget(sub)
        lay.addWidget(main, 1)
        if not done:
            stop = Btn("Stop", icon="circle-stop", kind="danger-text", size="sm", tooltip="Stop this task")
            stop.clicked.connect(lambda: store.call("session.stopTask", {"id": sid, "taskId": t.get("id")}))
            lay.addWidget(stop)
        self._paint_icon()
        bind(theme.theme.changed, self, lambda _t: self._paint_icon())

    def update(self, t: Json) -> None:
        """The same task, a step further: the title and the live line follow without a rebuild."""
        if t == self.task:
            return
        self.task = t
        self.title.setText(str(t.get("description") or t.get("subagentType") or t.get("type") or ""))
        if self._done:
            parts = [str(t.get("status") or "")]
            if t.get("endedAt"):
                parts.append(time_ago(float(t["endedAt"])))
            if t.get("summary"):
                parts.append(str(t["summary"])[:120])
            self.sub.setText(" · ".join(parts))
        else:
            self.sub.setText(self._live_text())
        self._paint_icon()

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

    def _paint_icon(self) -> None:
        tk = theme.tokens()
        t = self.task
        if self._done:
            ok = t.get("status") == "completed"
            self.ico.setPixmap(icons.pixmap("check" if ok else "x", tk.ok if ok else tk.err, 13))
        else:
            self.ico.setPixmap(icons.pixmap("square-terminal" if t.get("type") == "local_bash" else "bot", tk.text, 13))

    def paintEvent(self, e) -> None:  # noqa: N802
        if not self._done:
            super().paintEvent(e)


class AgentRow(QWidget):
    """One agent of a workflow (``.wfa``): its state, label, model and time; opens to its prompt and
    result; the sub-agents it spawned hang under it with an elbow."""

    def __init__(self, a: Json, now: float, subs: List[Json], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        st = agent_state(a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.row = HoverRow(theme.R1)
        rl = QHBoxLayout(self.row)
        rl.setContentsMargins(5, 4, 6, 4)
        rl.setSpacing(7)
        rl.addWidget(StateMark(st), 0, Qt.AlignmentFlag.AlignTop)
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(5)
        self.label = lbl(
            str(a.get("label") or f"agent {int(a.get('index') or 0) + 1}"),
            theme.FS_SMALL,
            tone="faint" if st == "queued" else "",
        )
        self.label.setFixedHeight(16)
        top.addWidget(self.label)
        attempt = int(a.get("attempt") or 0)
        if attempt > 1:
            chip = Chip(f"×{attempt}")
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
        top.addStretch(1)
        if end_parts:
            top.addWidget(lbl("  ".join(end_parts), theme.FS_MICRO, color="text_4"))
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
            ml.addWidget(elide(lbl(str(what), theme.FS_MICRO, tone="err" if st in ("error", "blocked") else "faint")))
        rl.addWidget(main, 1)
        lay.addWidget(self.row)
        self.body = Collapse(lambda: self._build_body(a), False)
        lay.addWidget(self.body)
        Clickable(self.row).clicked.connect(self._toggle)
        if subs:
            subw = QWidget()
            sl = QVBoxLayout(subw)
            sl.setContentsMargins(11, 1, 0, 3)
            sl.setSpacing(1)
            for i, t in enumerate(subs):
                sl.addWidget(SubAgentRow(t, now, elbow=True, last=i == len(subs) - 1))
            lay.addWidget(subw)

    def _toggle(self) -> None:
        self.body.toggle()
        self.row.set_open(self.body.is_open)

    def _build_body(self, a: Json) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(26, 2, 6, 8)
        lay.setSpacing(8)
        if a.get("agentType"):
            kv = QWidget()
            kl = QHBoxLayout(kv)
            kl.setContentsMargins(0, 0, 0, 0)
            kl.setSpacing(5)
            ico = QLabel()
            ico.setPixmap(icons.pixmap("bot", theme.tokens().text_2, 11))
            kl.addWidget(ico)
            kl.addWidget(lbl(str(a["agentType"]), theme.FS_SMALL, tone="muted"))
            if a.get("agentId"):
                kl.addWidget(lbl(f" · {str(a['agentId'])[:8]}", theme.FS_SMALL, tone="faint", mono=True))
            kl.addStretch(1)
            lay.addWidget(kv)
        for title, text, err in (
            ("Prompt", a.get("promptPreview"), False),
            ("Error" if a.get("error") else "Result", a.get("error") or a.get("resultPreview"), bool(a.get("error"))),
        ):
            if not text:
                continue
            block = QWidget()
            bl = QVBoxLayout(block)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(3)
            bl.addWidget(lbl(title, theme.FS_MICRO, 600, tone="faint"))
            bl.addWidget(lbl(str(text), theme.FS_SMALL, tone="err" if err else "muted", wrap=True, line_height=1.5))
            lay.addWidget(block)
        return w


class SubAgentRow(QWidget):
    """A sub-agent an agent spawned (``.wfs``): an elbow off the agent's mark, the state, label, usage."""

    def __init__(self, t: Json, now: float, elbow: bool, last: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._elbow, self._last = elbow, last
        st = "running" if t.get("status") == "running" else "done" if t.get("status") == "completed" else "error"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14 if elbow else 0, 2, 6, 2)
        lay.setSpacing(4)
        self.setMinimumHeight(20)
        lay.addWidget(StateMark(st))
        lb = elide(
            lbl(
                str(t.get("description") or t.get("subagentType") or "sub-agent"),
                theme.FS_MICRO,
                tone="" if st == "running" else "muted",
            )
        )
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
            end = lbl("  ".join(parts), theme.FS_MICRO, color="text_4")
            end.setContentsMargins(8, 0, 0, 0)
            lay.addWidget(end)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        if not self._elbow:
            return
        t = theme.tokens()
        p = QPainter(self)
        c = theme.blend(t.hair_3, t.raised)
        # the elbow: a stem down from the agent's mark, and a short arm into this row's mark
        vline(p, 1, 0, self.height() if not self._last else 10, c)
        hline(p, 1, 9, 7, c)


class PhaseRow(QWidget):
    """One phase of the run (``.wfd-phase``): the numbered node on the rail, the title, the agents."""

    def __init__(
        self,
        c: Any,
        index: int,
        state: str,
        states: List[str],
        active_idx: int,
        last: bool,
        agents: List[QWidget],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.state, self.last = state, last
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 4 if last else 10)
        lay.setSpacing(9)
        self.node = PhaseNode(int(c.phase.get("index") or 0) + 1, state)
        nodew = QWidget()
        nl = QVBoxLayout(nodew)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.addWidget(self.node)
        nl.addStretch(1)
        nodew.setFixedWidth(18)
        lay.addWidget(nodew)
        col = QWidget()
        cl = QVBoxLayout(col)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        head = QWidget()
        head.setMinimumHeight(18)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)
        title = lbl(
            str(c.phase.get("title") or ""),
            theme.FS_SMALL,
            500 if state == "pending" else 550,
            tone="faint" if state == "pending" else "",
        )
        hl.addWidget(title)
        kind = c.phase.get("kind")
        if kind and kind != "sequential":
            hl.addWidget(Chip(str(kind), round_=True))
        hl.addStretch(1)
        if state not in ("done", "error"):
            failed_n = sum(1 for s in states if s in ("error", "blocked"))
            if state == "running":
                st_text = f"running · {failed_n} failed" if failed_n else "running"
            else:
                st_text = "queued" if index == active_idx else "pending"
            hl.addWidget(
                lbl(
                    st_text,
                    theme.FS_MICRO,
                    tone="accent" if state == "running" else "",
                    color="" if state == "running" else "text_4",
                )
            )
        cl.addWidget(head)
        agents_w = QWidget()
        al = QVBoxLayout(agents_w)
        al.setContentsMargins(0, 4, 0, 0)
        al.setSpacing(1)
        for w in agents:
            al.addWidget(w)
        cl.addWidget(agents_w)
        lay.addWidget(col, 1)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        if self.last:
            return
        t = theme.tokens()
        p = QPainter(self)
        line = theme.blend(t.hair_2, t.raised)
        if self.state == "done":  # color-mix(in srgb, var(--ok) 55%, var(--hair-2))
            ok = QColor(t.ok)
            line = QColor(
                int(ok.red() * 0.55 + line.red() * 0.45),
                int(ok.green() * 0.55 + line.green() * 0.45),
                int(ok.blue() * 0.55 + line.blue() * 0.45),
            )
        vline(p, 9, 20, self.height() - 20 + 1, line)


class PhaseNode(QWidget):
    """The 18px node on the rail (``.wfd-node``): a number, the accent ring while running, a tick or a cross."""

    def __init__(self, n: int, state: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.n, self.state = n, state
        self.setFixedSize(18, 18)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if self.state in ("done", "error"):
            p.setBrush(QColor(t.ok if self.state == "done" else t.err))
            p.drawEllipse(QRectF(0, 0, 18, 18))
            p.drawPixmap(3.5, 3.5, icons.pixmap("check" if self.state == "done" else "x", "#ffffff", 11))
            return
        p.setBrush(theme.qcolor(t.raised))
        p.drawEllipse(QRectF(0, 0, 18, 18))
        running = self.state == "running"
        p.setPen(QPen(QColor(t.accent_text) if running else theme.blend(t.hair_3, t.raised), 1.5 if running else 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        inset = 0.75 if running else 0.5
        p.drawEllipse(QRectF(inset, inset, 18 - 2 * inset, 18 - 2 * inset))
        p.setFont(theme.font_ui(10, 600))
        p.setPen(QColor(t.accent_text if running else t.text_3))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self.n))


class WorkflowDetail(Card):
    """The whole run (``.wfd``): phases down the card, each with its agents and the sub-agents they spawned."""

    def __init__(self, task: Json, sid: str, all_tasks: List[Json], parent: Optional[QWidget] = None) -> None:
        super().__init__("raised", "hair_2", 10, parent)
        self.task = task
        self.sid = sid
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
        self.head = HoverRow(10)
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(11, 9, 10, 9)
        hl.setSpacing(9)
        ico = QLabel()
        ico.setFixedSize(18, 14)
        ico.setPixmap(icons.pixmap("workflow", t.err if (stopped or failed) else t.accent_text if live else t.ok, 14))
        hl.addWidget(ico)
        title = QWidget()
        tl = QVBoxLayout(title)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(1)
        self.name = elide(lbl(str(task.get("workflowName") or task.get("description") or "Workflow"), theme.FS_UI, 550))
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
        self.sub = elide(lbl(sub, theme.FS_MICRO, tone="faint"))
        tl.addWidget(self.sub)
        hl.addWidget(title, 1)
        if live:
            hl.addWidget(Spinner(12))
        else:
            end = QLabel()
            end.setPixmap(
                icons.pixmap("x" if (failed or stopped) else "check", t.err if (failed or stopped) else t.ok, 13)
            )
            hl.addWidget(end)
        self.chev = QLabel()
        self.chev.setFixedSize(13, 13)
        hl.addWidget(self.chev)
        self.head.setAccessibleName(self.name.text())
        lay.addWidget(self.head)
        self.bar_wrap: Optional[QWidget] = None
        if c.total:
            bar = Bar(2)
            bar.set_parts([(c.done / c.total, "ok"), (c.running / c.total, "accent"), (c.error / c.total, "err")])
            self.bar_wrap = QWidget()
            bl = QHBoxLayout(self.bar_wrap)
            bl.setContentsMargins(11, 0, 11, 0)
            bl.addWidget(bar)
            lay.addWidget(self.bar_wrap)
        self.body = Collapse(lambda: self._build_body(all_tasks, live, now), live)
        lay.addWidget(self.body)
        Clickable(self.head).clicked.connect(self._toggle)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _toggle(self) -> None:
        self.body.toggle()
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        open_ = self.body.is_open
        self.chev.setPixmap(icons.pixmap("chevron-down" if open_ else "chevron-right", t.text_3, 13))
        if self.bar_wrap is not None:
            self.bar_wrap.layout().setContentsMargins(11, 0, 11, 0 if open_ else 9)

    def _build_body(self, all_tasks: List[Json], live: bool, now: float) -> QWidget:
        task = self.task
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(11, 10, 8, 4)
        lay.setSpacing(0)
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
            empty = lbl(
                "Waiting for the first agent…" if live else "No agents were reported for this run.",
                theme.FS_SMALL,
                tone="faint",
            )
            empty.setContentsMargins(0, 0, 0, 8)
            lay.addWidget(empty)
        active_idx = next(
            (i for i, c in enumerate(cols) if any(agent_state(a) in ("running", "queued") for a in c.agents)), -1
        )
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
            rows: List[QWidget] = [AgentRow(a, now, by_agent.get(int(a.get("index") or 0), [])) for a in c.agents]
            if not c.agents:
                ghost = lbl("No agents" if state == "done" else "Not started yet", theme.FS_MICRO, color="text_4")
                ghost.setContentsMargins(6, 3, 6, 3)
                rows.append(ghost)
            lay.addWidget(PhaseRow(c, i, state, states, active_idx, i == len(cols) - 1, rows))
        orphans = by_agent.get(-1) or []
        if orphans:
            ow = QWidget()
            ol = QVBoxLayout(ow)
            ol.setContentsMargins(27, 6, 0, 6)
            ol.setSpacing(1)
            head = lbl("Sub-agents", theme.FS_MICRO, 600, tone="faint")
            head.setContentsMargins(0, 0, 0, 2)
            ol.addWidget(head)
            for s in orphans:
                ol.addWidget(SubAgentRow(s, now, elbow=False))
            lay.addWidget(ow)
        foot = QWidget()
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(0, 6, 0, 6)
        fl.setSpacing(4)
        logs = (task.get("workflow") or {}).get("logs") or []
        log_body: Optional[Collapse] = None
        if logs:
            log_btn = Btn(f"Log · {len(logs)}", icon="chevron-right", kind="foot", icon_size=12)
            fl.addWidget(log_btn)
            log_body = Collapse(lambda: self._build_log(logs), False)

            def toggle_log() -> None:
                log_body.toggle()
                log_btn.set_icon_name("chevron-down" if log_body.is_open else "chevron-right")

            log_btn.clicked.connect(toggle_log)
        if live:
            stop = Btn(
                "Stop", icon="circle-stop", kind="danger-text", size="xs", icon_size=12, tooltip="Stop this workflow"
            )
            stop.clicked.connect(self._stop)
            fl.addWidget(stop)
        fl.addStretch(1)
        usage = task.get("usage") or {}
        if int(usage.get("total_tokens") or 0) > 0:
            fl.addWidget(lbl(f"{tokens(int(usage['total_tokens']))} tokens", theme.FS_MICRO, tone="faint"))
        lay.addWidget(foot)
        if log_body is not None:
            lay.addWidget(log_body)
        return w

    def _build_log(self, logs: List[Json]) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 0, 0, 8)
        lay.setSpacing(2)
        for entry in logs:
            row = QHBoxLayout()
            row.setSpacing(10)
            at = lbl(_clock(float(entry.get("at") or 0)), theme.FS_MICRO, tone="faint")
            at.setFixedWidth(54)
            row.addWidget(at, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(lbl(str(entry.get("message") or ""), theme.FS_MICRO, tone="muted", wrap=True), 1)
            lay.addLayout(row)
        return w

    def _stop(self) -> None:
        name = self.task.get("workflowName") or "the workflow"
        store.call("session.stopTask", {"id": self.sid, "taskId": self.task.get("id")}).then(
            lambda _r: store.toast(f"Stopping {name}", "info")
        )


# ---------------------------------------------------------------- the panel


class TasksPanel(QWidget):
    """To-do list, workflows, running and finished background work for one session (``.tasks-panel``)."""

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
        self._sections: List[Section] = []
        self._rows: List[TaskRow] = []
        self._empty_state: Optional[EmptyState] = None
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(lambda: [r.tick() for r in self._rows])
        self._refresher = Refresher(self, session_id, self.render, fs=False)
        self.render()

    def render(self) -> None:
        rt = store.rt_of(self.sid)
        tasks: List[Json] = list(rt.get("tasks") or []) if rt else []
        todos = reconstruct_todos(self.sid)
        shape = (
            [(t.get("id"), t.get("status"), (t.get("workflow") or {}).get("updatedAt")) for t in tasks],
            todos,
        )
        if shape == self._key:
            # the same tasks in the same states: the running rows follow their progress in place
            by_id = {t.get("id"): t for t in tasks}
            for row in self._rows:
                t = by_id.get(row.task.get("id"))
                if t is not None:
                    row.update(t)
            return
        self._key = shape
        for s in self._sections:
            self._lay.removeWidget(s)
            s.setParent(None)
            s.deleteLater()
        self._sections = []
        if self._empty_state is not None:
            self._lay.removeWidget(self._empty_state)
            self._empty_state.deleteLater()
            self._empty_state = None
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

        if not todos and not workflows and not running and not finished:
            self._empty_state = e = EmptyState(
                "list-checks",
                "Nothing on the list yet.",
                "Claude's plan for multi-step work, its workflows and background commands appear here.",
            )
            self._lay.insertWidget(0, e)
            self._tick.stop()
            return
        if todos:
            s = Section("To-do list")
            s.head_faint(f"{done} of {len(todos)} done")
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
            self._add_section(s)

        if workflows:
            s = Section("Workflows")
            s.body.setSpacing(8)
            for t in reversed(workflows):
                s.add(WorkflowDetail(t, self.sid, tasks))
            self._add_section(s)

        if running:
            s = Section("Running now")
            for t in running:
                row = TaskRow(t, self.sid, False)
                self._rows.append(row)
                s.add(row)
            self._add_section(s)

        if finished:
            s = Section("Finished")
            for t in finished:
                s.add(TaskRow(t, self.sid, True))
            self._add_section(s)
        for i, sec in enumerate(self._sections):
            sec.set_last(i == len(self._sections) - 1)
        if running and not self._tick.isActive():
            self._tick.start()
        elif not running:
            self._tick.stop()

    def _add_section(self, s: Section) -> None:
        self._lay.insertWidget(self._lay.count() - 1, s)
        self._sections.append(s)

    def workflow_cards(self) -> List[WorkflowDetail]:
        return self.findChildren(WorkflowDetail)
