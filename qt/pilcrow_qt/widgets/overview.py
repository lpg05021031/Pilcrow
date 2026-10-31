"""The control room (a port of web/src/components/Overview.tsx).

Every session that is working or waiting, at a glance, with the things that need you answerable
right here. Four tiles say the state of the fleet; under them, what needs you first, then the work
in flight, then what finished today and what it cost. Cards update live from the store's runtime
state plus ``fleet.state`` polled every 20 s; nothing here needs a transcript loaded.
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import icons, theme
from ..lib.fmt import basename, cost, duration, plural, rel_path, time_ago
from ..lib.tools_meta import str_
from ..lib.workflow import workflow_counts
from ..store import Runtime, store
from .dialogs.parts import Badge, Card, ElideLabel, Kbd, LayoutButton, Text, Well, half_hairline, page_column
from .ui.bits import Box, ContextRing, IconButton, Mark, Spinner, WorkingMark, button, paint_box
from .ui.signals import bind

Json = Dict[str, Any]
FLEET_EVERY_MS = 20_000
STALE_MS = 45_000


def now_ms() -> float:
    return time.time() * 1000


# ---------------------------------------------------------------- permission helpers
# These live in widgets/transcript/permission.py (another part of the port); the control room
# imports them lazily so it runs on its own while that module is still landing.


def typing_elsewhere() -> bool:
    """Number keys answer a prompt unless the person is typing somewhere. An empty composer counts as
    not typing: it has focus by default, and a lone "1" there is never a message."""
    fw = QApplication.focusWidget()
    try:
        from .transcript.permission import typing_elsewhere as impl  # type: ignore[import-not-found]

        try:
            return bool(impl(fw))
        except TypeError:
            return bool(impl())
    except Exception:  # noqa: BLE001 — not there yet, or a different shape: use the local rule
        pass
    if fw is None:
        return False
    is_composer = fw.metaObject().className() == "ComposerEdit" or fw.objectName() == "ComposerEdit"
    if is_composer:
        text = fw.toPlainText() if hasattr(fw, "toPlainText") else fw.text() if hasattr(fw, "text") else ""
        return bool(text)
    return isinstance(fw, (QLineEdit, QTextEdit, QPlainTextEdit))


def suggestion_label(s: Json, cwd: str = "") -> str:
    """A short label for a permission suggestion (what "Always allow" would add)."""
    try:
        from .transcript.permission import suggestion_label as impl  # type: ignore[import-not-found]

        return str(impl(s, cwd))
    except Exception:  # noqa: BLE001
        pass
    dest = {
        "session": "for this session",
        "localSettings": "in this project (just you)",
        "projectSettings": "in this project",
        "userSettings": "everywhere",
    }.get(str(s.get("destination") or ""), "")
    if s.get("type") == "addRules" and s.get("behavior") == "allow":
        rules = s.get("rules") or []
        tools = {str(r.get("toolName")) for r in rules}
        if len(tools) == 1 and (tools & {"Bash", "PowerShell"}):
            return f"{f'Always allow these {len(rules)} commands' if len(rules) > 1 else 'Always allow this command'} {dest}".strip()
        if len(tools) == 1:
            return f"Always allow {next(iter(tools))} {dest}".strip()
        return f"Always allow {len(rules)} rules {dest}".strip()
    if s.get("type") == "setMode":
        return (
            f"Allow all edits {dest}" if s.get("mode") == "acceptEdits" else f"Switch to {s.get('mode')} {dest}"
        ).strip()
    if s.get("type") == "addDirectories":
        return (
            f"Allow access to {', '.join(rel_path(str(d), cwd) for d in (s.get('directories') or []))} {dest}".strip()
        )
    return f"{s.get('type')} {dest}".strip()


def perm_summary(p: Json, cwd: str) -> Tuple[str, bool, str]:
    """What a request is about, in one line: (text, mono?, note)."""
    i = p.get("input") or {}
    tool = p.get("toolName")
    if tool in ("Bash", "PowerShell"):
        return str_(i, "command"), True, str_(i, "description")
    if tool in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        return rel_path(str_(i, "file_path") or str_(i, "notebook_path"), cwd), True, ""
    if tool == "WebFetch":
        return str_(i, "url"), True, ""
    if tool == "AskUserQuestion":
        qs = i.get("questions") if isinstance(i, dict) else None
        q = qs[0].get("question") if isinstance(qs, list) and qs and isinstance(qs[0], dict) else None
        return str(q or "Claude has a question"), False, ""
    if tool == "ExitPlanMode":
        return "A plan is ready for review", False, ""
    return str(p.get("description") or p.get("title") or ""), False, ""


def activity_line(st: Json) -> Tuple[str, str, bool]:
    """(icon, text, quiet) for the line under a working card's title."""
    a = st.get("activity")
    if not a:
        return (
            "hourglass",
            "Starting Claude Code…" if st.get("status") == "starting" else "Waiting for the model…",
            True,
        )
    if a.get("kind") == "tool":
        return "wrench", f"{a.get('tool')} · {a.get('label')}", False
    if a.get("kind") == "text":
        return "message-square-quote", str(a.get("label") or ""), False
    return "brain", "Thinking…", True


def running_workflow(tasks: Any) -> Optional[Json]:
    for t in tasks or []:
        if (
            isinstance(t, dict)
            and t.get("type") == "local_workflow"
            and t.get("status") == "running"
            and not t.get("ambient")
        ):
            return t
    return None


def short_model(m: Any) -> str:
    s = str(m or "")
    if s.startswith("claude-"):
        s = s[len("claude-") :]
    if len(s) > 9 and s[-9] == "-" and s[-8:].isdigit():
        s = s[:-9]
    return s


# ---------------------------------------------------------------- pieces


class Tile(LayoutButton):
    """One figure about the fleet (``.ov-tile``). Clicking it scrolls to the matching section."""

    def __init__(self, icon: str, label: str, on_click: Callable[[], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._icon = icon
        self._tone = ""
        self._live = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(label)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(68)
        self.clicked.connect(on_click)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(13, 0, 13, 0)
        lay.setSpacing(12)
        self.ico_box = Box(radius=10)
        self.ico_box.setFixedSize(34, 34)
        il = QHBoxLayout(self.ico_box)
        il.setContentsMargins(0, 0, 0, 0)
        self.ico = QLabel()
        self.ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        il.addWidget(self.ico, 0, Qt.AlignmentFlag.AlignCenter)
        self.mark = WorkingMark(18)
        self.mark.hide()
        il.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.ico_box)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        self.value = Text("0", theme.FS_DISPLAY, 600, "text", 1.1, wrap=False, letter_spacing=-0.02)
        col.addWidget(self.value)
        krow = QHBoxLayout()
        krow.setContentsMargins(0, 0, 0, 0)
        krow.setSpacing(0)
        self.key = Text(label, theme.FS_MICRO, 400, "text_3", wrap=False)
        self.key.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        krow.addWidget(self.key)
        self.sub = Text("", theme.FS_MICRO, 400, "text_4", wrap=False)
        krow.addWidget(self.sub, 1)
        col.addLayout(krow)
        lay.addLayout(col, 1)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._label = label
        self._sub = ""
        self._paint()

    def set_value(self, text: str, tone: str = "", sub: str = "", live: bool = False) -> None:
        self.value.setText(text)
        self._tone, self._sub, self._live = tone, sub, live
        self._paint()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        line = theme.blend(t.hair_3 if self.underMouse() else t.hair_2, t.canvas)
        paint_box(p, QRectF(self.rect()), QColor(t.raised), line, 14)

    def _paint(self) -> None:
        t = theme.tokens()
        accent = self._tone == "accent"
        self.ico_box.set_look(bg="accent_wash/raised" if accent else "fill_1/raised")
        self.ico.setPixmap(
            icons.pixmap(self._icon, t.accent_text if accent else (t.text if self._live else t.text_2), 16)
        )
        self.ico.setVisible(not self._live)
        self.mark.setVisible(self._live)
        self.value.set_color("accent_text" if accent else "text")
        self.key.setText(self._label)
        self.sub.setText(f" · {self._sub}" if self._sub else "")


class SectionHead(QWidget):
    """``.ov-h``: an optional glyph, the title, a count, and a quiet line at the right end."""

    def __init__(self, title: str, glyph: bool = False, end_text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.glyph = Box(bg="accent", line="accent_wash/canvas", radius=6, line_w=2)
        self.glyph.setFixedSize(12, 12)
        self.glyph.setVisible(glyph)
        lay.addWidget(self.glyph)
        self.title = Text(title, theme.FS_H, 600, "text", wrap=False, letter_spacing=-0.01)
        lay.addWidget(self.title)
        self.count = Badge(0)
        lay.addWidget(self.count)
        lay.addStretch(1)
        self.end = Text(end_text, theme.FS_SMALL, 400, "text_3", wrap=False)
        lay.addWidget(self.end)
        self._glyph_on = glyph


class EmptyNote(Box):
    """``.ov-empty``: a quiet filled box with an icon and a sentence."""

    def __init__(self, text: str, icon: str = "", mark: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(bg="fill_1/canvas", radius=12, parent=parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(12)
        if mark:
            lay.addWidget(Mark(18), 0, Qt.AlignmentFlag.AlignVCenter)
        elif icon:
            ico = QLabel()
            ico.setPixmap(icons.pixmap(icon, theme.tokens().text_4, 16))
            lay.addWidget(ico, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = Text(text, theme.FS_SMALL, 400, "text_3", 1.5)
        lay.addWidget(self.text, 1)


class CardHead(QWidget):
    """Title and the project · branch · model line (``.ovc-head``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.title = ElideLabel()
        self.title.setFont(theme.font_ui(theme.FS_UI, 560))
        theme.tint(self.title, fg="text")
        self.proj = ElideLabel()
        self.proj.setFont(theme.font_ui(theme.FS_MICRO))
        theme.tint(self.proj, fg="text_3")
        lay.addWidget(self.title)
        lay.addWidget(self.proj)

    def update_from(self, s: Json, st: Json) -> None:
        self.title.setText(str(s.get("title") or ""))
        proj = basename(str(s.get("project") or s.get("cwd") or ""))
        wt = s.get("worktree") or {}
        branch = wt.get("branch") if isinstance(wt, dict) and wt.get("branch") else s.get("gitBranch")
        if branch:
            proj += f" · {branch}"
        if st.get("activeModel"):
            proj += f" · {short_model(st.get('activeModel'))}"
        self.proj.setText(proj)


class WorkflowBar(QWidget):
    """``.wf-bar``: done, running and failed shares of a workflow as a thin bar."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(3)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.done = self.running = self.error = 0.0

    def set_shares(self, done: float, running: float, error: float) -> None:
        self.done, self.running, self.error = done, running, error
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        w = self.width()
        p.setBrush(theme.blend(t.fill_2, t.raised))
        p.drawRoundedRect(QRectF(0, 0, w, 3), 1.5, 1.5)
        x = 0.0
        for share, color in ((self.done, t.text_2), (self.running, t.accent), (self.error, t.err)):
            if share > 0:
                p.setBrush(QColor(color))
                p.drawRoundedRect(QRectF(x, 0, w * share, 3), 1.5, 1.5)
                x += w * share


def _micro(text: str = "") -> QLabel:
    lb = QLabel(text)
    theme.tint(lb, fg="text_3")
    lb.setProperty("fs", "micro")
    return lb


class WorkingCard(Card):
    """A session at work (``.ovc.busy``): what it is doing, the workflow if one runs, elapsed, cost, context."""

    def __init__(self, sid: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(14, parent)
        self.sid = sid
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Open the session")
        self._st: Json = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(15, 13, 15, 12)
        lay.setSpacing(9)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.head = CardHead()
        row.addWidget(self.head, 1)
        self.live = WorkingMark(15)
        self.live.setToolTip("Working")
        row.addWidget(self.live, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(row)
        act = QHBoxLayout()
        act.setSpacing(8)
        self.act_ico = QLabel()
        act.addWidget(self.act_ico)
        self.act = ElideLabel()
        self.act.setFont(theme.font_ui(theme.FS_SMALL))
        act.addWidget(self.act, 1)
        lay.addLayout(act)
        self.wf_row = QWidget()
        wl = QHBoxLayout(self.wf_row)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(7)
        self.wf_ico = QLabel()
        wl.addWidget(self.wf_ico)
        self.wf_name = ElideLabel()
        self.wf_name.setProperty("fs", "micro")
        wl.addWidget(self.wf_name)
        self.wf_bar = WorkflowBar()
        wl.addWidget(self.wf_bar, 1)
        self.wf_n = _micro()
        wl.addWidget(self.wf_n)
        self.wf_row.hide()
        lay.addWidget(self.wf_row)
        foot = QHBoxLayout()
        foot.setSpacing(12)
        self.elapsed = _micro()
        self.elapsed.setToolTip("This turn so far")
        foot.addWidget(self.elapsed)
        self.agents_ico = QLabel()
        self.agents_ico.setToolTip("Sub-agents running")
        foot.addWidget(self.agents_ico)
        self.agents = _micro()
        self.agents.setToolTip("Sub-agents running")
        foot.addWidget(self.agents)
        self.cost = _micro()
        self.cost.setToolTip("Cost so far")
        foot.addWidget(self.cost)
        foot.addStretch(1)
        self.quiet = _micro()
        foot.addWidget(self.quiet)
        self.ring = ContextRing(14)
        foot.addWidget(self.ring)
        lay.addLayout(foot)
        self._quiet_act = True
        bind(theme.theme.changed, self, lambda _t: self._paint_text())
        self._paint_text()

    def _paint_text(self) -> None:
        t = theme.tokens()
        theme.tint(self.act, fg="text_3" if self._quiet_act else "text_2")
        self.wf_ico.setPixmap(icons.pixmap("workflow", t.text_3, 12))
        self.agents_ico.setPixmap(icons.pixmap("bot", t.text_3, 12))

    def update_from(self, s: Json, st: Json, now: float, hot: bool) -> None:
        self._st = st
        self.head.update_from(s, st)
        ico, text, quiet = activity_line(st)
        stale = bool(st.get("lastActivity")) and now - float(st.get("lastActivity") or 0) > STALE_MS
        self._quiet_act = quiet
        t = theme.tokens()
        self.act_ico.setPixmap(icons.pixmap(ico, t.text_3, 13))
        self.act.setText(text)
        theme.tint(self.act, fg="text_3" if quiet else "text_2")
        self.live.setToolTip("Quiet for a while" if stale else "Working")
        wf = running_workflow(st.get("tasks"))
        if wf:
            c = workflow_counts(((wf.get("workflow") or {}).get("agents")) or [])
            total = max(1, c.total)
            self.wf_name.setText(str(wf.get("workflowName") or "Workflow"))
            self.wf_bar.set_shares(c.done / total, c.running / total, c.error / total)
            self.wf_n.setText(f"{c.done}/{c.total}")
        self.wf_row.setVisible(bool(wf))
        agents = sum(
            1
            for x in st.get("tasks") or []
            if isinstance(x, dict) and x.get("status") == "running" and x.get("type") == "local_agent"
        )
        self.agents.setText(str(agents) if agents else "")
        self.agents.setVisible(agents > 0)
        self.agents_ico.setVisible(agents > 0)
        u = st.get("usage") or {}
        c_usd = float(u.get("costUsd") or 0)
        self.cost.setText(cost(c_usd) + ("  above median" if hot else "") if c_usd > 0 else "")
        self.cost.setToolTip("Well above the median session today" if hot else "Cost so far")
        self.cost.setVisible(c_usd > 0)
        theme.tint(self.cost, fg="accent_text" if hot else "text_3")
        cw = int(u.get("contextWindow") or 0)
        self.ring.set_values(int(u.get("contextTokens") or 0), cw)
        self.ring.setVisible(cw > 0)
        self.ring.setToolTip(f"Context {round(int(u.get('contextTokens') or 0) / cw * 100) if cw else 0}% used")
        self.tick(now)

    def tick(self, now: float) -> None:
        st = self._st
        started = st.get("turnStartedAt")
        elapsed = now - float(started) if started else 0
        self.elapsed.setText(duration(elapsed) if elapsed > 1000 else "")
        self.elapsed.setVisible(elapsed > 1000)
        last = st.get("lastActivity")
        stale = bool(last) and now - float(last) > STALE_MS
        self.quiet.setText(f"quiet for {duration(now - float(last))}" if stale else "")
        self.quiet.setVisible(stale)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            store.open_session(self.sid)


class KeyButton(LayoutButton):
    """A small button with a key cap at its end (``.btn.sm`` + ``.key``)."""

    def __init__(self, text: str, kind: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text)
        self.setAutoDefault(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 0, 9, 0)
        lay.setSpacing(6)
        self.label = QLabel(text)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.label)
        self.kbd = Kbd("")
        self.kbd.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.kbd.hide()
        lay.addWidget(self.kbd)
        self.setFixedHeight(26)
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self.setProperty("kind", kind)
        self.label.setFont(theme.font_ui(theme.FS_SMALL, 600 if kind == "primary" else 400))
        theme.tint(self.label, fg="accent_ink" if kind == "primary" else "text_2" if kind == "ghost" else "text")
        if kind == "primary":
            self.set_look(bg="accent", hover="accent_2", radius=theme.R2)
        elif kind == "ghost":
            self.set_look(hover="fill_2/canvas", radius=theme.R2)
        else:
            self.set_look(bg="fill_2/canvas", hover="fill_3/canvas", radius=theme.R2)

    def set_key(self, n: Optional[int]) -> None:
        self.kbd.setText(str(n) if n else "")
        self.kbd.setVisible(bool(n))


class NeedsCard(Card):
    """A session waiting on you (``.ovc.needs``): the request in a well, and the answer buttons."""

    def __init__(self, sid: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(14, parent)
        self.sid = sid
        self.set_tone("needs")
        self._perm: Optional[Json] = None
        self._first = False
        self._busy = False
        self._decided = ""
        self._allow_always = False
        self._cwd = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(19, 13, 15, 12)
        lay.setSpacing(9)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.head = CardHead()
        row.addWidget(self.head, 1)
        self.wait = QLabel()
        row.addWidget(self.wait, 0, Qt.AlignmentFlag.AlignTop)
        self.open_btn = IconButton("arrow-up-right", "Open the session", 14)
        self.open_btn.clicked.connect(lambda: store.open_session(self.sid))
        row.addWidget(self.open_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(row)
        self.well = Well(8)
        wl = QHBoxLayout(self.well)
        wl.setContentsMargins(10, 8, 10, 8)
        wl.setSpacing(8)
        self.tool = QLabel()
        wl.addWidget(self.tool, 0, Qt.AlignmentFlag.AlignTop)
        self.what = QLabel()
        self.what.setWordWrap(True)
        self.what.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        wl.addWidget(self.what, 1)
        lay.addWidget(self.well)
        self.note = _micro()
        self.note.setWordWrap(True)
        self.note.hide()
        lay.addWidget(self.note)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.allow_btn = KeyButton("Allow", "primary")
        self.allow_btn.clicked.connect(self.allow)
        actions.addWidget(self.allow_btn)
        self.always_btn = KeyButton("Always allow")
        self.always_btn.clicked.connect(self.always)
        actions.addWidget(self.always_btn)
        self.deny_btn = KeyButton("Deny", "ghost")
        self.deny_btn.clicked.connect(self.deny)
        actions.addWidget(self.deny_btn)
        self.open_primary = button("Open to respond", kind="primary", size="sm")
        self.open_primary.clicked.connect(lambda: store.open_session(self.sid))
        actions.addWidget(self.open_primary)
        self.more = _micro()
        actions.addWidget(self.more)
        self.waiting_note = _micro("Waiting for your input.")
        actions.addWidget(self.waiting_note)
        self.open_plain = button("Open", size="sm")
        self.open_plain.clicked.connect(lambda: store.open_session(self.sid))
        actions.addWidget(self.open_plain)
        self.decided_ico = QLabel()
        self.decided_ico.hide()
        actions.addWidget(self.decided_ico)
        self.decided_lb = QLabel()
        actions.addWidget(self.decided_lb)
        actions.addStretch(1)
        lay.addLayout(actions)
        bind(theme.theme.changed, self, lambda _t: self._paint_extra())
        self._paint_extra()

    def _paint_extra(self) -> None:
        t = theme.tokens()
        self.wait.setFont(theme.font_ui(theme.FS_MICRO))
        self.wait.setContentsMargins(0, 2, 0, 0)
        theme.tint(self.wait, fg="accent_text")
        self.tool.setFont(theme.font_ui(theme.FS_SMALL, 600))
        theme.tint(self.tool, fg="text_2")
        self.decided_lb.setFont(theme.font_ui(theme.FS_SMALL))
        theme.tint(self.decided_lb, fg="ok")
        self.decided_ico.setPixmap(icons.pixmap("circle-check", t.ok, 14))

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        # the rail: a 3px accent (ok once answered) down the left edge
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.ok if self._decided else t.accent))
        p.drawRoundedRect(QRectF(0, 12, 3, self.height() - 24), 1.5, 1.5)

    @property
    def perm(self) -> Optional[Json]:
        return self._perm

    def update_from(self, s: Json, st: Json, first: bool, now: float) -> None:
        self._first = first
        self._cwd = str(s.get("cwd") or "")
        pending = st.get("pending") or []
        perm = pending[0] if pending else None
        self._perm = perm
        self.head.update_from(s, st)
        interactive = bool(perm and perm.get("interactive"))
        self._allow_always = bool(perm and not perm.get("suppressAlwaysAllowRule") and perm.get("suggestions"))
        if perm:
            text, mono, note = perm_summary(perm, self._cwd)
            self.tool.setText(str(perm.get("displayName") or perm.get("toolName") or ""))
            self.what.setText(text)
            self.what.setFont(theme.font_mono(12) if mono else theme.font_ui(theme.FS_SMALL))
            theme.tint(self.what, fg="text")
            self.note.setText(note)
            self.note.setVisible(bool(note))
            more = len(pending) - 1
            self.more.setText(f"+{more} more waiting" if more > 0 else "")
            self.more.setVisible(more > 0 and not interactive and not self._decided)
            if self._allow_always:
                self.always_btn.setToolTip(suggestion_label(perm["suggestions"][0], self._cwd))
            default_no = bool(perm.get("defaultToNo"))
            self.allow_btn.set_kind("" if default_no else "primary")
            self.deny_btn.set_kind("primary" if default_no else "ghost")
            keys = first and not interactive
            self.allow_btn.set_key(1 if keys and not default_no else None)
            self.always_btn.set_key(2 if keys else None)
            self.deny_btn.set_key((3 if self._allow_always else 2) if keys else None)
            tool = perm.get("toolName")
            self.open_primary.setText(
                "Answer the question"
                if tool == "AskUserQuestion"
                else "Review the plan"
                if tool == "ExitPlanMode"
                else "Open to respond"
            )
        self.well.setVisible(bool(perm))
        self.note.setVisible(bool(perm and self.note.text()))
        decided = bool(self._decided)
        self.allow_btn.setVisible(bool(perm) and not interactive and not decided)
        self.always_btn.setVisible(bool(perm) and not interactive and not decided and self._allow_always)
        self.deny_btn.setVisible(bool(perm) and not interactive and not decided)
        self.open_primary.setVisible(bool(perm) and interactive and not decided)
        self.decided_lb.setVisible(decided)
        self.decided_ico.setVisible(decided)
        self.waiting_note.setVisible(not perm)
        self.open_plain.setVisible(not perm)
        for b in (self.allow_btn, self.always_btn, self.deny_btn):
            b.setEnabled(not self._busy)
        self.tick(now)

    def tick(self, now: float) -> None:
        perm = self._perm
        self.wait.setText(f"waiting {duration(now - float(perm.get('at') or now))}" if perm else "")

    # ---- answering
    def decide(self, decision: Json) -> None:
        perm = self._perm
        if not perm or self._busy:
            return
        self._busy = True
        for b in (self.allow_btn, self.always_btn, self.deny_btn):
            b.setEnabled(False)

        def ok(_r: Any) -> None:
            self._decided = "allow" if decision.get("behavior") == "allow" else "deny"
            self.decided_lb.setText("Allowed — Claude is continuing" if self._decided == "allow" else "Declined")
            self.set_tone("decided")
            for b in (self.allow_btn, self.always_btn, self.deny_btn, self.more):
                b.hide()
            self.decided_ico.show()
            self.decided_lb.show()
            self.update()
            QTimer.singleShot(900, self._reset)

        def bad(_e: Any) -> None:
            self._busy = False
            for b in (self.allow_btn, self.always_btn, self.deny_btn):
                b.setEnabled(True)

        store.respond(self.sid, str(perm.get("requestId") or ""), decision).then(ok).catch(bad)

    def _reset(self) -> None:
        self._decided = ""
        self._busy = False
        self.set_tone("needs")
        self.update()

    def allow(self) -> None:
        self.decide({"behavior": "allow", "decisionClassification": "user_temporary"})

    def always(self) -> None:
        if self._perm:
            self.decide(
                {
                    "behavior": "allow",
                    "updatedPermissions": self._perm.get("suggestions") or [],
                    "decisionClassification": "user_permanent",
                }
            )

    def deny(self) -> None:
        self.decide(
            {"behavior": "deny", "message": "The user declined this action.", "decisionClassification": "user_reject"}
        )

    def handle_key(self, key: str) -> bool:
        """1/2/3 on the first waiting card; returns whether the key meant something."""
        perm = self._perm
        if not self._first or not perm or perm.get("interactive"):
            return False
        if key == "1" and not perm.get("defaultToNo"):
            self.allow()
        elif key == "2" and self._allow_always:
            self.always()
        elif key == ("3" if self._allow_always else "2"):
            self.deny()
        else:
            return False
        return True


class FinishedRow(LayoutButton):
    """A session that finished today (``.ovr``): mark, title and the last reply, cost and when."""

    def __init__(self, sid: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = sid
        self._unread = False
        self._focused = False
        self._s: Json = {}
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Open the session")
        self.setAutoDefault(False)
        self.clicked.connect(lambda: store.open_session(self.sid))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 9, 10, 9)
        lay.setSpacing(10)
        self.mark = QLabel()
        self.mark.setFixedWidth(18)
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.mark)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        self.title = Text("", theme.FS_SMALL, 400, "text", wrap=False)
        self.sub = Text("", theme.FS_MICRO, 400, "text_3", wrap=False)
        col.addWidget(self.title)
        col.addWidget(self.sub)
        lay.addLayout(col, 1)
        self.cost = Text("", theme.FS_MICRO, 400, "text_4", wrap=False)
        lay.addWidget(self.cost)
        self.when = Text("", theme.FS_MICRO, 400, "text_4", wrap=False)
        lay.addWidget(self.when)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def set_focused(self, on: bool) -> None:
        if on != self._focused:
            self._focused = on
            self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.set_look(hover="fill_1/raised", on="fill_1/raised", radius=8)
        self.look_active = self._focused
        self.title.set_font(weight=600 if self._unread else 400)
        s = self._s
        if s.get("status") == "error":
            self.mark.setPixmap(icons.pixmap("circle-alert", t.err, 14))
        elif self._unread:
            self.mark.setPixmap(_dot(t.text_2, 6))
        else:
            self.mark.setPixmap(icons.pixmap("circle-check", t.text_4, 14))

    def update_from(self, s: Json, st: Json, unread: bool, now: float) -> None:
        self._s, self._unread = s, unread
        self.title.setText(str(s.get("title") or ""))
        reply = (st.get("lastReply") or {}).get("text") if isinstance(st.get("lastReply"), dict) else None
        self.sub.setText(str(reply) if reply else basename(str(s.get("project") or s.get("cwd") or "")))
        c = float((st.get("usage") or {}).get("costUsd") or 0)
        self.cost.setText(cost(c) if c else "")
        self.cost.setVisible(bool(c))
        self.tick(now)
        self._paint()

    def tick(self, now: float) -> None:
        self.when.setText(time_ago(self._s.get("lastModified"), now))


def _dot(color: str, size: int):
    from PySide6.QtGui import QPixmap

    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(0, 0, size, size))
    p.end()
    return pm


class SpendBars(QWidget):
    """Twelve five-minute bars for the last hour; the current one in Claude's colour (``.spend-bars``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.buckets: List[float] = [0.0] * 12

    def set_buckets(self, b: List[float]) -> None:
        self.buckets = list(b)
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        sx, sy = self.width() / 120.0, self.height() / 30.0
        mx = max(max(self.buckets), 0.0001)
        n = len(self.buckets)
        for i, v in enumerate(self.buckets):
            h = max(1.5, v / mx * 26)
            color = t.accent if i == n - 1 else t.text_3 if v > 0 else theme.blend(t.fill_2, t.raised).name()
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF((i * 10 + 1) * sx, (28 - h) * sy, 8 * sx, h * sy), 1.5, 1.5)
        p.setBrush(theme.blend(t.hair_2, t.raised))
        p.drawRect(QRectF(0, 29.2 * sy, 120 * sx, 0.8 * sy))


class SpendRow(LayoutButton):
    """One of the sessions that cost the most today: title, a bar against the top one, the amount."""

    def __init__(
        self, sid: str, title: str, share: float, amount: float, first: bool, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.sid = sid
        self.share = share
        self.first = first
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Open the session")
        self.setAccessibleName("Open the session")
        self.setAutoDefault(False)
        self.clicked.connect(lambda: store.open_session(self.sid))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(10)
        self.title = Text(title, theme.FS_MICRO, 400, "text_2", wrap=False)
        lay.addWidget(self.title, 1)
        self.bar = QWidget()
        self.bar.setFixedSize(88, 4)
        self.bar.paintEvent = self._paint_bar  # type: ignore[method-assign]
        lay.addWidget(self.bar)
        self.amount = Text(cost(amount), theme.FS_MICRO, 400, "text_2", wrap=False)
        lay.addWidget(self.amount)

    def _paint_bar(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self.bar)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.blend(t.fill_2, t.raised))
        p.drawRoundedRect(QRectF(0, 0, 88, 4), 2, 2)
        p.setBrush(QColor(t.accent if self.first else t.text_4))
        p.drawRoundedRect(QRectF(0, 0, 88 * max(0.0, min(1.0, self.share)), 4), 2, 2)


class Spend(Card):
    """Spend in the last hour, five-minute bars, and the sessions that cost the most today (``.spend``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(12, parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(14)
        hour = QVBoxLayout()
        hour.setSpacing(8)
        top = QHBoxLayout()
        self.hour_lb = Text("Last hour", theme.FS_MICRO, 400, "text_3", wrap=False)
        top.addWidget(self.hour_lb)
        top.addStretch(1)
        self.hour_v = Text(cost(0), theme.FS_SMALL, 600, "text", wrap=False)
        top.addWidget(self.hour_v)
        hour.addLayout(top)
        self.bars = SpendBars()
        hour.addWidget(self.bars)
        axis = QHBoxLayout()
        self.ax0 = Text("60 min ago", 10, 400, "text_4", wrap=False)
        self.ax1 = Text("now", 10, 400, "text_4", wrap=False)
        axis.addWidget(self.ax0)
        axis.addStretch(1)
        axis.addWidget(self.ax1)
        hour.addLayout(axis)
        lay.addLayout(hour)
        self.top_host = QWidget()
        self.top_lay = QVBoxLayout(self.top_host)
        self.top_lay.setContentsMargins(0, 0, 0, 0)
        self.top_lay.setSpacing(6)
        lay.addWidget(self.top_host)
        self.none = QLabel("No spend recorded today.")
        theme.tint(self.none, fg="text_3")
        self.none.hide()
        lay.addWidget(self.none)
        self.rows: List[SpendRow] = []
        bind(theme.theme.changed, self, lambda _t: self._paint_text())
        self._paint_text()

    def _paint_text(self) -> None:
        self.update()

    def update_from(self, fleet: Json, by_id: Dict[str, Json], now: float) -> None:
        n, w = 12, 5 * 60e3
        start = now - n * w
        buckets = [0.0] * n
        for e in fleet.get("spend") or []:
            i = int(math.floor((float(e.get("at") or 0) - start) / w))
            if 0 <= i < n:
                buckets[i] += float(e.get("delta") or 0)
        self.bars.set_buckets(buckets)
        self.hour_v.setText(cost(sum(buckets)))
        top = list(((fleet.get("today") or {}).get("perSession")) or [])[:5]
        top_max = float(top[0].get("cost") or 1) if top else 1.0
        while self.top_lay.count():
            w_ = self.top_lay.takeAt(0).widget()
            if w_ is not None:
                w_.deleteLater()
        self.rows = []
        for i, p in enumerate(top):
            sid = str(p.get("id"))
            title = str((by_id.get(sid) or {}).get("title") or p.get("title") or "")
            row = SpendRow(
                sid, title, float(p.get("cost") or 0) / top_max if top_max else 0, float(p.get("cost") or 0), i == 0
            )
            self.rows.append(row)
            self.top_lay.addWidget(row)
        self.top_host.setVisible(bool(top))
        self.none.setVisible(not top)


# ---------------------------------------------------------------- the page


class _KeyWatch(QObject):
    """An app-wide event filter owned by the page: it is removed with its owner."""

    def __init__(self, page: Overview) -> None:
        super().__init__(page)
        self._page = page
        self._on = False

    def watch(self, on: bool) -> None:
        app = QApplication.instance()
        if app is None or on == self._on:
            return
        self._on = on
        if on:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)

    def eventFilter(self, _obj: QObject, e: QEvent) -> bool:  # noqa: N802
        try:
            return self._page.key_event(e)
        except RuntimeError:  # the page is gone; the filter is on its way out with it
            return False


class Overview(QWidget):
    fleet_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.fleet: Optional[Json] = None
        self._focus = -1  # keyboard focus; none until the keys are used
        self.ordered: List[str] = []
        self.needs_cards: Dict[str, NeedsCard] = {}
        self.working_cards: Dict[str, WorkingCard] = {}
        self.finished_rows: List[FinishedRow] = []
        self._laid_cols = 0
        self._laid_tiles = 0
        self._laid_two = 0
        self.now = now_ms()
        self._alive = {"on": True}
        self.destroyed.connect(lambda: self._alive.__setitem__("on", False))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        head = QWidget()
        head.setFixedHeight(theme.HEADER_H)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(20, 0, 20, 0)
        hl.setSpacing(14)
        self.side_btn = IconButton("panel-left-open", "Show sidebar (Ctrl+B)", 16)
        self.side_btn.clicked.connect(lambda: store.set_ui(sidebar=True))
        hl.addWidget(self.side_btn)
        self.title = QLabel("Control room")
        self.title.setProperty("fs", "title")
        hl.addWidget(self.title)
        self.summary = QLabel()
        theme.tint(self.summary, fg="text_3")
        self.summary.setProperty("fs", "small")
        hl.addWidget(self.summary)
        hl.addStretch(1)
        root.addWidget(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        hh = QHBoxLayout(host)
        hh.setContentsMargins(0, 0, 0, 0)
        hh.addStretch(1)
        self.body = QWidget()
        self.body.setObjectName("ov-body")
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(20, 18, 20, 48)
        bl.setSpacing(28)
        hh.addWidget(self.body)
        hh.addStretch(1)
        self.scroll.setWidget(host)
        self.scroll.viewport().installEventFilter(self)
        root.addWidget(self.scroll, 1)

        # the four tiles
        stats = QWidget()
        sl = QGridLayout(stats)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(10)
        self.stats_grid = sl
        self.tile_working = Tile("activity", "Working", lambda: self.jump(self._first_section()))
        self.tile_needs = Tile("hourglass", "Needs you", lambda: self.jump(self._first_section()))
        self.tile_finished = Tile("circle-check", "Finished today", lambda: self.jump(self.finished_sec))
        self.tile_spent = Tile("circle-dollar-sign", "Spent today", lambda: self.jump(self.spend_sec))
        self.tiles = [self.tile_working, self.tile_needs, self.tile_finished, self.tile_spent]
        for i, tl in enumerate(self.tiles):
            sl.addWidget(tl, 0, i)
        bl.addWidget(stats)

        # needs you
        self.needs_sec = QWidget()
        nl = QVBoxLayout(self.needs_sec)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(10)
        self.needs_head = SectionHead("Needs you", glyph=True, end_text="Press 1, 2 or 3 to answer the first")
        nl.addWidget(self.needs_head)
        self.needs_lay = QVBoxLayout()
        self.needs_lay.setSpacing(10)
        nl.addLayout(self.needs_lay)
        self.needs_sec.hide()
        bl.addWidget(self.needs_sec)

        # working
        self.working_sec = QWidget()
        wl = QVBoxLayout(self.working_sec)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(10)
        self.working_head = SectionHead("Working")
        wl.addWidget(self.working_head)
        self.working_grid_host = QWidget()
        self.working_grid = QGridLayout(self.working_grid_host)
        self.working_grid.setContentsMargins(0, 0, 0, 0)
        self.working_grid.setSpacing(10)
        wl.addWidget(self.working_grid_host)
        self.working_sec.hide()  # shown while something works: the tile and the header say when nothing does
        bl.addWidget(self.working_sec)

        # finished today | spend (one column under 760px of content, like the web)
        two = QWidget()
        tl2 = QGridLayout(two)
        tl2.setContentsMargins(0, 0, 0, 0)
        tl2.setSpacing(28)
        self.two_grid = tl2
        self.finished_sec = QWidget()
        fl = QVBoxLayout(self.finished_sec)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(10)
        self.finished_head = SectionHead("Finished today")
        fl.addWidget(self.finished_head)
        self.finished_list = Card(12)
        self.finished_lay = QVBoxLayout(self.finished_list)
        self.finished_lay.setContentsMargins(4, 4, 4, 4)
        self.finished_lay.setSpacing(0)
        fl.addWidget(self.finished_list)
        self.finished_empty = EmptyNote("No sessions have finished today.", icon="circle-check")
        fl.addWidget(self.finished_empty)
        self.finished_sec.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tl2.addWidget(self.finished_sec, 0, 0)
        self.spend_sec = QWidget()
        spl = QVBoxLayout(self.spend_sec)
        spl.setContentsMargins(0, 0, 0, 0)
        spl.setSpacing(10)
        self.spend_head = SectionHead("Spend")
        self.spend_head.count.hide()
        spl.addWidget(self.spend_head)
        self.spend = Spend()
        self.spend.hide()
        spl.addWidget(self.spend)
        self.spend_spinner = Spinner(14)
        spl.addWidget(self.spend_spinner)
        self.spend_sec.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tl2.addWidget(self.spend_sec, 1, 0)
        bl.addWidget(two)
        bl.addStretch(1)
        self._place_two(1)

        # live state: the store for sessions and runtimes, fleet.state for the rest, a clock for elapsed times
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick)
        self._fleet_timer = QTimer(self)
        self._fleet_timer.setInterval(FLEET_EVERY_MS)
        self._fleet_timer.timeout.connect(self.load_fleet)
        bind(store.sessions_changed, self, lambda _s: self.refresh())
        bind(store.rt_changed, self, lambda _sid: self.refresh())
        bind(store.unread_changed, self, lambda _u: self.refresh())
        bind(store.ui_changed, self, lambda _u: self.side_btn.setVisible(not store.ui.sidebar))
        bind(store.connected_changed, self, lambda on: on and self.load_fleet())
        self.side_btn.setVisible(not store.ui.sidebar)
        # The keys (j/k, Enter, 1/2/3) are watched app-wide through a child object: it goes when the
        # page goes, so no filter ever points at a widget that has been torn down.
        self._keys = _KeyWatch(self)
        self.refresh()
        self.load_fleet()

    # ---- lifecycle
    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._tick_timer.start()
        self._fleet_timer.start()
        self._keys.watch(True)
        self.load_fleet()

    def hideEvent(self, e) -> None:  # noqa: N802
        self._tick_timer.stop()
        self._fleet_timer.stop()
        self._keys.watch(False)
        super().hideEvent(e)

    def _fit_body(self) -> None:
        vw = self.scroll.viewport().width()
        self.body.setFixedWidth(max(320, min(page_column(vw), vw)))
        content = self.body.width() - 40
        tiles = 2 if content <= 640 else 4
        if tiles != self._laid_tiles:
            self._laid_tiles = tiles
            while self.stats_grid.count():
                self.stats_grid.takeAt(0)
            for i, tl in enumerate(self.tiles):
                self.stats_grid.addWidget(tl, i // tiles, i % tiles)
        self._place_two(1 if content <= 760 else 2)
        if self._cols() != self._laid_cols and self.working_cards:
            self.refresh()

    def _place_two(self, cols: int) -> None:
        """``.ov-two``: finished | spend side by side (3fr / minmax(260px, 2fr)), or stacked under 760px."""
        if cols == self._laid_two:
            return
        self._laid_two = cols
        g = self.two_grid
        while g.count():
            g.takeAt(0)
        if cols == 2:
            g.addWidget(self.finished_sec, 0, 0)
            g.addWidget(self.spend_sec, 0, 1)
            g.setColumnStretch(0, 3)
            g.setColumnStretch(1, 2)
            self.spend_sec.setMinimumWidth(260)
        else:
            g.addWidget(self.finished_sec, 0, 0)
            g.addWidget(self.spend_sec, 1, 0)
            g.setColumnStretch(0, 1)
            g.setColumnStretch(1, 0)
            self.spend_sec.setMinimumWidth(0)

    def _cols(self) -> int:
        """``.ov-grid``: repeat(auto-fill, minmax(290px, 1fr)) with a 10px gap."""
        return max(1, (self.body.width() - 40 + 10) // 300)

    # ---- data
    def load_fleet(self) -> None:
        if not store.connected:
            return

        alive = self._alive

        def got(f: Any) -> None:
            # The answer can land after the page is gone (a test tearing down, a window closing).
            if not alive["on"] or not isinstance(f, dict):
                return
            self.fleet = f
            for sid, st in (f.get("live") or {}).items():
                if isinstance(st, dict):
                    r = store.rt.setdefault(sid, Runtime())
                    r.st = {**r.st, **st}
            self.fleet_changed.emit(f)
            self.refresh()

        store.call("fleet.state", {}, quiet=True).then(got).catch(lambda _e: None)

    def state_of(self, sid: str) -> Json:
        r = store.rt.get(sid)
        if r is not None and r.st:
            return r.st
        return ((self.fleet or {}).get("live") or {}).get(sid) or {}

    def lists(self) -> Tuple[List[Json], List[Json], List[Json]]:
        sessions = store.sessions
        needs = [
            s
            for s in sessions
            if len(self.state_of(str(s.get("id"))).get("pending") or []) > 0 or s.get("status") == "requires_action"
        ]
        working = [s for s in sessions if s not in needs and s.get("status") in ("running", "starting")]
        day_start = (
            datetime.fromtimestamp(self.now / 1000).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            * 1000
        )
        finished = [
            s
            for s in sessions
            if s not in needs
            and s not in working
            and not s.get("archived")
            and float(s.get("lastModified") or 0) >= day_start
        ]
        finished.sort(
            key=lambda s: (-int(bool(store.unread.get(str(s.get("id"))))), -float(s.get("lastModified") or 0))
        )
        return needs, working, finished[:12]

    def refresh(self) -> None:
        self.now = now_ms()
        needs, working, finished = self.lists()
        self.ordered = [str(s.get("id")) for s in needs + working + finished]
        focus_id = self.ordered[self._focus] if 0 <= self._focus < len(self.ordered) else None
        fleet = self.fleet or {}
        today = fleet.get("today") or {}
        per = [p for p in (today.get("perSession") or []) if isinstance(p, dict)]
        xs = sorted(float(p.get("cost") or 0) for p in per)
        median = xs[len(xs) // 2] if xs else 0.0
        by_id = {str(s.get("id")): s for s in store.sessions}

        self.summary.setText(
            (f"{len(working)} working" if working else "Nothing running")
            + (f" · {len(needs)} waiting for you" if needs else "")
        )
        total = float(today.get("cost") or 0)
        self.tile_working.set_value(str(len(working)), "live" if working else "", live=bool(working))
        self.tile_needs.set_value(str(len(needs)), "accent" if needs else "")
        self.tile_finished.set_value(str(len(finished)))
        self.tile_spent.set_value(
            cost(total), sub=plural(int(today.get("sessions") or 0), "session") if self.fleet else ""
        )

        # needs you
        self.needs_head.count.set_count(len(needs))
        keep = {str(s.get("id")) for s in needs}
        for sid in list(self.needs_cards):
            if sid not in keep:
                self.needs_cards.pop(sid).deleteLater()
        while self.needs_lay.count():
            self.needs_lay.takeAt(0)
        for i, s in enumerate(needs):
            sid = str(s.get("id"))
            card = self.needs_cards.get(sid)
            if card is None:
                card = self.needs_cards[sid] = NeedsCard(sid)
            card.update_from(s, self.state_of(sid), i == 0, self.now)
            card.set_focused(sid == focus_id)
            self.needs_lay.addWidget(card)
        self.needs_sec.setVisible(bool(needs))

        # working
        self.working_head.count.set_count(len(working))
        keep = {str(s.get("id")) for s in working}
        for sid in list(self.working_cards):
            if sid not in keep:
                self.working_cards.pop(sid).deleteLater()
        while self.working_grid.count():
            self.working_grid.takeAt(0)
        cols = self._laid_cols = self._cols()
        for i, s in enumerate(working):
            sid = str(s.get("id"))
            card = self.working_cards.get(sid)
            if card is None:
                card = self.working_cards[sid] = WorkingCard(sid)
            mine = next((float(p.get("cost") or 0) for p in per if str(p.get("id")) == sid), 0.0)
            hot = median > 0 and mine > median * 3 and len(per) >= 4
            card.update_from(s, self.state_of(sid), self.now, hot)
            card.set_focused(sid == focus_id)
            self.working_grid.addWidget(card, i // cols, i % cols)
        self.working_sec.setVisible(bool(working))

        # finished today
        self.finished_head.count.set_count(len(finished))
        while self.finished_lay.count():
            w = self.finished_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.finished_rows = []
        for i, s in enumerate(finished):
            sid = str(s.get("id"))
            if i:
                self.finished_lay.addWidget(half_hairline("raised"))
            row = FinishedRow(sid)
            row.update_from(s, self.state_of(sid), bool(store.unread.get(sid)), self.now)
            row.set_focused(sid == focus_id)
            self.finished_rows.append(row)
            self.finished_lay.addWidget(row)
        self.finished_list.setVisible(bool(finished))
        self.finished_empty.setVisible(not finished)

        # spend
        if self.fleet:
            self.spend_head.end.setText(f"{cost(total)} today")
            self.spend.update_from(self.fleet, by_id, self.now)
        self.spend.setVisible(bool(self.fleet))
        self.spend_spinner.setVisible(not self.fleet)

    def _tick(self) -> None:
        self.now = now_ms()
        for c in self.needs_cards.values():
            c.tick(self.now)
        for w in self.working_cards.values():
            w.tick(self.now)
        for r in self.finished_rows:
            r.tick(self.now)

    # ---- keyboard: j/k or arrows move between cards; Enter opens; the first waiting card takes 1/2/3
    def _first_section(self) -> QWidget:
        """Where a tile with nothing live behind it leads: the first section on screen."""
        for sec in (self.needs_sec, self.working_sec, self.finished_sec):
            if sec.isVisible():
                return sec
        return self.finished_sec

    def jump(self, section: QWidget) -> None:
        self.scroll.verticalScrollBar().setValue(section.mapTo(self.scroll.widget(), section.rect().topLeft()).y() - 8)

    def card_for(self, sid: str) -> Optional[QWidget]:
        return (
            self.needs_cards.get(sid)
            or self.working_cards.get(sid)
            or next((r for r in self.finished_rows if r.sid == sid), None)
        )

    def set_focus_index(self, i: int) -> None:
        self._focus = i
        focus_id = self.ordered[i] if 0 <= i < len(self.ordered) else None
        for sid, c in self.needs_cards.items():
            c.set_focused(sid == focus_id)
        for sid, w in self.working_cards.items():
            w.set_focused(sid == focus_id)
        for r in self.finished_rows:
            r.set_focused(r.sid == focus_id)
        card = self.card_for(focus_id) if focus_id else None
        if card is not None:
            self.scroll.ensureWidgetVisible(card, 0, 8)

    @property
    def focus_index(self) -> int:
        return self._focus

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        if obj is self.scroll.viewport() and e.type() == QEvent.Type.Resize:
            self._fit_body()
        return False

    def key_event(self, e: QEvent) -> bool:
        """A key press anywhere in the app while the page is up (``window.addEventListener('keydown')``)."""
        t = e.type()
        if t not in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
            return False
        if not self.isVisible() or store.page != "overview" or any(store.dialogs.values()) or store.lightbox:
            return False
        if e.modifiers() & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier
        ):
            return False
        if typing_elsewhere():
            return False
        key, text = e.key(), e.text()
        if t == QEvent.Type.ShortcutOverride:
            if (key == Qt.Key.Key_Escape and self._focus >= 0) or (
                key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._focus >= 0
            ):
                e.accept()
                return True
            return False
        return self.handle_key(key, text)

    def handle_key(self, key: int, text: str) -> bool:
        n = len(self.ordered)
        if text == "j" or key == Qt.Key.Key_Down:
            self.set_focus_index(min(n - 1, self._focus + 1))
        elif key == Qt.Key.Key_Escape and self._focus >= 0:
            self.set_focus_index(-1)
        elif text == "k" or key == Qt.Key.Key_Up:
            self.set_focus_index(max(0, self._focus - 1))
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and 0 <= self._focus < n:
            store.open_session(self.ordered[self._focus])
        elif text in ("1", "2", "3") and self.needs_cards:
            first = next((self.needs_cards[sid] for sid in self.ordered if sid in self.needs_cards), None)
            return bool(first is not None and first.handle_key(text))
        else:
            return False
        return True
