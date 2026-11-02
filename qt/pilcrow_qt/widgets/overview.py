"""The control room (a port of web/src/components/Overview.tsx).

Every session that is working or waiting, at a glance, with the things that need you answerable
right here. Four tiles say the state of the fleet; under them, what needs you first, then the work
in flight, then what finished today and what it cost. Cards update live from the store's runtime
state plus ``fleet.state`` polled every 20 s; nothing here needs a transcript loaded.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import icons, theme
from ..lib.fmt import basename, cost, duration, now_ms, plural, rel_path, time_ago
from ..lib.tools_meta import str_
from ..lib.workflow import running_workflow, workflow_counts
from ..store import Runtime, store
from .dialogs.parts import Badge, Card, ElideLabel, Kbd, LayoutButton, Text, half_hairline
from .transcript.permission import suggestion_label, typing_elsewhere
from .ui.bits import Box, ContextRing, IconButton, Mark, Spinner, WorkingMark, button
from .ui.signals import bind

Json = Dict[str, Any]
FLEET_EVERY_MS = 20_000
STALE_MS = 45_000


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


def short_model(m: Any) -> str:
    s = str(m or "")
    if s.startswith("claude-"):
        s = s[len("claude-") :]
    if len(s) > 9 and s[-9] == "-" and s[-8:].isdigit():
        s = s[:-9]
    return s


# ---------------------------------------------------------------- pieces


class Tile(LayoutButton):
    """One figure about the fleet in the stat strip: a quiet label over the figure. Clicking it scrolls to
    the matching section. Always one of four across, whatever the width (no reflow into a grid)."""

    def __init__(self, icon: str, label: str, on_click: Callable[[], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._tone = ""
        self._live = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(label)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setFixedHeight(58)
        self.clicked.connect(on_click)
        self.set_look(hover="fill_1/raised", radius=10)
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 9, 10, 9)
        col.setSpacing(2)
        krow = QHBoxLayout()
        krow.setContentsMargins(0, 0, 0, 0)
        krow.setSpacing(6)
        self.mark = WorkingMark(11)
        self.mark.hide()
        krow.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignVCenter)
        self.key = Text(label, theme.FS_MICRO, 400, "text_3", wrap=False)
        self.key.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        krow.addWidget(self.key, 1)
        col.addLayout(krow)
        vrow = QHBoxLayout()
        vrow.setContentsMargins(0, 0, 0, 0)
        vrow.setSpacing(6)
        self.value = Text("0", theme.FS_TITLE, 600, "text", 1.25, wrap=False, letter_spacing=-0.01)
        vrow.addWidget(self.value)
        self.sub = Text("", theme.FS_MICRO, 400, "text_4", wrap=False)
        self.sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        vrow.addWidget(self.sub, 1, Qt.AlignmentFlag.AlignBottom)
        col.addLayout(vrow)
        self._label = label

    def set_value(self, text: str, tone: str = "", sub: str = "", live: bool = False) -> None:
        if (text, tone, sub, live) == (self.value.text(), self._tone, self.sub.text(), self._live):
            return  # refresh runs on every runtime change: touch nothing that did not change
        self.value.setText(text)
        self._tone, self._live = tone, live
        self.value.set_color("accent_text" if tone == "accent" else "text")
        self.sub.setText(sub)
        self.mark.setVisible(live)


class StatStrip(Card):
    """``.ov-stats``: the four figures in one card, split by hairlines, four across at every width."""

    def __init__(self, tiles: List[Tile], parent: Optional[QWidget] = None) -> None:
        super().__init__(12, parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(0)
        for i, tl in enumerate(tiles):
            if i:
                lay.addWidget(_VRule())
            lay.addWidget(tl, 1)


class _VRule(QWidget):
    """A hairline between two figures, inset from the card's top and bottom."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedWidth(1)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def paintEvent(self, _e: Any) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.fillRect(0, 12, 1, max(0, self.height() - 24), theme.blend(t.hair, t.raised))


class SectionHead(QWidget):
    """``.ov-h``: an optional glyph, the title, a count, and a quiet line at the right end."""

    def __init__(self, title: str, glyph: bool = False, end_text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.glyph = Box(bg="accent", line="accent_wash/canvas", radius=6, line_w=2)
        self.glyph.setFixedSize(12, 12)
        lay.addWidget(self.glyph)
        self.glyph.setVisible(glyph)
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


class QuietLine(QWidget):
    """An empty section in the control room: one quiet line with its icon, no box around it."""

    def __init__(self, text: str, icon: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 0, 2)
        lay.setSpacing(8)
        self._icon = icon
        self.ico = QLabel()
        lay.addWidget(self.ico, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = Text(text, theme.FS_SMALL, 400, "text_3", wrap=False)
        lay.addWidget(self.text, 1)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        self.ico.setPixmap(icons.pixmap(self._icon, theme.tokens().text_4, 14))


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
        # a helper says whose it is (HELPERS.md §6.4)
        helper = st.get("helper") if isinstance(st.get("helper"), dict) else s.get("helper")
        if isinstance(helper, dict) and helper.get("leadId"):
            proj = f"helper of {helper.get('leadTitle') or 'a lead session'} · {proj}"
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

    def paintEvent(self, _e) -> None:
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


class WorkingRow(LayoutButton):
    """A session at work, one row of the Working list (``.ovr.busy``): the turning mark; the title with
    the turn's figures at the right; what it is doing now; where it works, and how long it has been quiet;
    the workflow's progress when one runs. One column at every width: nothing reflows on a resize."""

    def __init__(self, sid: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = sid
        self._st: Json = {}
        self._focused = False
        self._fig = ("", "")  # (cost and agents, elapsed): the figures line is set when either changes
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Open the session")
        self.setAutoDefault(False)
        self.clicked.connect(lambda: store.open_session(self.sid))
        self.set_look(hover="fill_1/raised", on="fill_1/raised", radius=8)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 12, 10)
        lay.setSpacing(10)
        self.live = WorkingMark(14)
        self.live.setFixedSize(18, 18)
        self.live.setToolTip("Working")
        lay.addWidget(self.live, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.title = ElideLabel()
        self.title.setFont(theme.font_ui(theme.FS_UI, 560))
        theme.tint(self.title, fg="text")
        top.addWidget(self.title, 1)
        self.figures = _micro()
        self.figures.setToolTip("This turn so far · cost so far")
        top.addWidget(self.figures)
        self.ring = ContextRing(13)
        top.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(top)
        act = QHBoxLayout()
        act.setSpacing(7)
        self.act_ico = QLabel()
        self.act_ico.setFixedWidth(13)
        act.addWidget(self.act_ico)
        self.act = ElideLabel()
        self.act.setFont(theme.font_ui(theme.FS_SMALL))
        act.addWidget(self.act, 1)
        col.addLayout(act)
        where = QHBoxLayout()
        where.setSpacing(10)
        self.proj = ElideLabel()
        self.proj.setFont(theme.font_ui(theme.FS_MICRO))
        theme.tint(self.proj, fg="text_3")
        where.addWidget(self.proj, 1)
        self.quiet = _micro()
        self.quiet.setToolTip("No output for a while")
        where.addWidget(self.quiet)
        col.addLayout(where)
        self.wf_row = QWidget()
        wl = QHBoxLayout(self.wf_row)
        wl.setContentsMargins(0, 3, 0, 0)
        wl.setSpacing(7)
        self.wf_ico = QLabel()
        wl.addWidget(self.wf_ico)
        self.wf_name = ElideLabel()
        self.wf_name.setProperty("fs", "micro")
        self.wf_name.setMaximumWidth(220)
        wl.addWidget(self.wf_name)
        self.wf_bar = WorkflowBar()
        wl.addWidget(self.wf_bar, 1)
        self.wf_n = _micro()
        wl.addWidget(self.wf_n)
        self.wf_row.hide()
        col.addWidget(self.wf_row)
        lay.addLayout(col, 1)
        self.head = CardHead()  # the title and where-line text, shared with the waiting cards
        self.head.hide()
        self._quiet_act = True
        bind(theme.theme.changed, self, lambda _t: self._paint_text())
        self._paint_text()

    def set_focused(self, on: bool) -> None:
        if on != self._focused:
            self._focused = on
            self.look_active = on
            self.update()

    def _paint_text(self) -> None:
        t = theme.tokens()
        theme.tint(self.act, fg="text_3" if self._quiet_act else "text_2")
        self.wf_ico.setPixmap(icons.pixmap("workflow", t.text_3, 12))

    def update_from(self, s: Json, st: Json, now: float, hot: bool) -> None:
        self._st = st
        self.head.update_from(s, st)
        if self.title.text() != self.head.title.text():
            self.title.setText(self.head.title.text())
        if self.proj.text() != self.head.proj.text():
            self.proj.setText(self.head.proj.text())
        ico, text, quiet = activity_line(st)
        if (ico, text, quiet) != getattr(self, "_act_was", None):
            self._act_was = (ico, text, quiet)
            self._quiet_act = quiet
            self.act_ico.setPixmap(icons.pixmap(ico, theme.tokens().text_3, 13))
            self.act.setText(text)
            theme.tint(self.act, fg="text_3" if quiet else "text_2")
        wf = running_workflow(st.get("tasks"), ambient=False)
        if wf:
            c = workflow_counts(((wf.get("workflow") or {}).get("agents")) or [])
            total = max(1, c.total)
            self.wf_name.setText(str(wf.get("workflowName") or "Workflow"))
            self.wf_bar.set_shares(c.done / total, c.running / total, c.error / total)
            self.wf_n.setText(f"{c.done}/{c.total}")
        if self.wf_row.isVisibleTo(self) != bool(wf):
            self.wf_row.setVisible(bool(wf))
        agents = sum(
            1
            for x in st.get("tasks") or []
            if isinstance(x, dict) and x.get("status") == "running" and x.get("type") == "local_agent"
        )
        u = st.get("usage") or {}
        c_usd = float(u.get("costUsd") or 0)
        parts = []
        if c_usd > 0:
            parts.append(cost(c_usd) + (" · above median" if hot else ""))
        if agents:
            parts.append(plural(agents, "agent"))
        self._fig = (" · ".join(parts), self._fig[1])
        theme.tint(self.figures, fg="accent_text" if hot else "text_3")
        self.figures.setToolTip("Well above the median session today" if hot else "This turn so far · cost so far")
        cw = int(u.get("contextWindow") or 0)
        self.ring.set_values(int(u.get("contextTokens") or 0), cw)
        self.ring.setVisible(cw > 0)
        self.ring.setToolTip(f"Context {round(int(u.get('contextTokens') or 0) / cw * 100) if cw else 0}% used")
        self.tick(now)

    def tick(self, now: float) -> None:
        st = self._st
        started = st.get("turnStartedAt")
        elapsed = now - float(started) if started else 0
        self._fig = (self._fig[0], duration(elapsed) if elapsed > 1000 else "")
        fig = " · ".join(x for x in (self._fig[1], self._fig[0]) if x)
        if self.figures.text() != fig:
            self.figures.setText(fig)
        last = st.get("lastActivity")
        stale = bool(last) and now - float(last) > STALE_MS
        q = f"quiet for {duration(now - float(last))}" if stale else ""
        if self.quiet.text() != q:
            self.quiet.setText(q)
        self.live.setToolTip("Quiet for a while" if stale else "Working")


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

    def set_key(self, n: Any) -> None:
        """The cap: a number (the control room's keys) or a letter (the board's); None hides it."""
        self.kbd.setText(str(n) if n else "")
        self.kbd.setVisible(bool(n))


class NeedsCard(QFrame):
    """A session waiting on you, one row of the Needs-you list: who is asking and since when, what they
    ask on a line of its own, and the answers. The same quiet grammar as the Working list: no coloured
    rail or wash, no key caps; the one strong colour is the default answer. (The 1/2/3 keys still answer
    the first row; each button says its key in its tooltip.)"""

    def __init__(self, sid: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = sid
        self._perm: Optional[Json] = None
        self._first = False
        self._busy = False
        self._decided = ""
        self._focused = False
        self._allow_always = False
        self._cwd = ""
        self._job: str = ""  # the dispatch job this session runs for, if any
        self._helper: Optional[Json] = None  # the lead this session helps, if any
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 11, 12, 12)
        lay.setSpacing(10)
        self.mark = QLabel()
        self.mark.setFixedSize(18, 18)
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.head = CardHead()
        row.addWidget(self.head, 1)
        self.wait = QLabel()
        row.addWidget(self.wait, 0, Qt.AlignmentFlag.AlignTop)
        self.open_btn = IconButton("arrow-up-right", "Open the session", 14)
        self.open_btn.clicked.connect(lambda: store.open_session(self.sid))
        row.addWidget(self.open_btn, 0, Qt.AlignmentFlag.AlignTop)
        col.addLayout(row)
        col.addSpacing(5)
        # what is asked: the tool's name, quiet, and the command or path as it is
        self.well = QWidget()
        wl = QHBoxLayout(self.well)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(8)
        self.tool = QLabel()
        wl.addWidget(self.tool, 0, Qt.AlignmentFlag.AlignTop)
        self.what = QLabel()
        self.what.setWordWrap(True)
        self.what.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        wl.addWidget(self.what, 1)
        col.addWidget(self.well)
        self.note = _micro()
        self.note.setWordWrap(True)
        self.note.hide()
        col.addWidget(self.note)
        col.addSpacing(6)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.allow_btn = KeyButton("Allow", "primary")
        self.allow_btn.clicked.connect(self.allow)
        actions.addWidget(self.allow_btn)
        self.always_btn = KeyButton("Always allow")
        self.always_btn.clicked.connect(self.always)
        actions.addWidget(self.always_btn)
        self.all_btn = KeyButton("Allow for all")
        self.all_btn.setToolTip("Answer every run of this job that asks the same thing, now and later")
        self.all_btn.clicked.connect(self.allow_all)
        self.all_btn.hide()
        actions.addWidget(self.all_btn)
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
        col.addLayout(actions)
        lay.addLayout(col, 1)
        bind(theme.theme.changed, self, lambda _t: self._paint_extra())
        self._paint_extra()

    def set_focused(self, on: bool) -> None:
        if on != self._focused:
            self._focused = on
            self.update()

    def set_tone(self, _tone: str) -> None:
        """The answered state shows in the words at the foot, not in a tint over the row."""
        self.update()

    def _paint_extra(self) -> None:
        t = theme.tokens()
        self.wait.setFont(theme.font_ui(theme.FS_MICRO))
        self.wait.setContentsMargins(0, 2, 0, 0)
        theme.tint(self.wait, fg="text_3")
        self.tool.setFont(theme.font_ui(theme.FS_SMALL))
        theme.tint(self.tool, fg="text_3")
        self.decided_lb.setFont(theme.font_ui(theme.FS_SMALL))
        theme.tint(self.decided_lb, fg="text_2")
        self.decided_ico.setPixmap(icons.pixmap("circle-check", t.ok, 14))
        self.mark.setPixmap(icons.pixmap("hourglass", t.text_2, 14))

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self._focused:
            t = theme.tokens()
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.blend(t.fill_1, t.raised))
            p.drawRoundedRect(QRectF(self.rect()), 8, 8)

    @property
    def perm(self) -> Optional[Json]:
        return self._perm

    def update_from(self, s: Json, st: Json, first: bool, now: float) -> None:
        self._first = first
        self._cwd = str(s.get("cwd") or "")
        d = s.get("dispatch")
        self._job = str(d.get("jobId") or "") if isinstance(d, dict) else ""
        h = s.get("helper")
        self._helper = h if isinstance(h, dict) and h.get("leadId") else None
        self.all_btn.setText("Allow for all helpers" if self._helper else "Allow for all")
        self.all_btn.setToolTip(
            "Answer every helper of this lead that asks the same thing, now and later"
            if self._helper
            else "Answer every run of this job that asks the same thing, now and later"
        )
        pending = st.get("pending") or []
        perm = pending[0] if pending else None
        self._perm = perm
        self.head.update_from(s, st)
        interactive = bool(perm and perm.get("interactive"))
        self._allow_always = bool(perm and not perm.get("suppressAlwaysAllowRule") and perm.get("suggestions"))
        if perm:
            text, mono, note = perm_summary(perm, self._cwd)
            name = str(perm.get("displayName") or perm.get("toolName") or "")
            self.tool.setText({"AskUserQuestion": "Question", "ExitPlanMode": "Plan"}.get(name, name))
            self.what.setText(text)
            self.what.setFont(theme.font_mono(12) if mono else theme.font_ui(theme.FS_UI))
            theme.tint(self.what, fg="text")
            self.note.setText(note)
            self.note.setVisible(bool(note))
            more = len(pending) - 1
            self.more.setText(f"+{more} more waiting" if more > 0 else "")
            self.more.setVisible(more > 0 and not interactive and not self._decided)
            default_no = bool(perm.get("defaultToNo"))
            self.allow_btn.set_kind("" if default_no else "primary")
            self.deny_btn.set_kind("primary" if default_no else "ghost")
            keys = first and not interactive
            self.allow_btn.setToolTip("Allow · 1" if keys and not default_no else "Allow this once")
            self.deny_btn.setToolTip(f"Deny · {3 if self._allow_always else 2}" if keys else "Deny")
            if self._allow_always:
                tip = suggestion_label(perm["suggestions"][0], self._cwd)
                self.always_btn.setToolTip(f"{tip} · 2" if keys else tip)
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
        self.all_btn.setVisible(bool(perm) and not interactive and not decided and bool(self._job or self._helper))
        self.deny_btn.setVisible(bool(perm) and not interactive and not decided)
        self.open_primary.setVisible(bool(perm) and interactive and not decided)
        self.decided_lb.setVisible(decided)
        self.decided_ico.setVisible(decided)
        self.waiting_note.setVisible(not perm)
        self.open_plain.setVisible(not perm)
        for b in (self.allow_btn, self.always_btn, self.all_btn, self.deny_btn):
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

    def allow_all(self) -> None:
        """The dispatch job answers this request for every repository that asks it, now and later."""
        perm = self._perm
        if not perm or not (self._job or self._helper) or self._busy:
            return
        self._busy = True
        for b in (self.allow_btn, self.always_btn, self.all_btn, self.deny_btn):
            b.setEnabled(False)

        def ok(_r: Any) -> None:
            self._decided = "allow"
            self.decided_lb.setText(
                "Allowed for every helper of the lead" if self._helper else "Allowed for every repository of the job"
            )
            self.set_tone("decided")
            for b in (self.allow_btn, self.always_btn, self.all_btn, self.deny_btn, self.more):
                b.hide()
            self.decided_ico.show()
            self.decided_lb.show()
            self.update()
            QTimer.singleShot(900, self._reset)

        def bad(_e: Any) -> None:
            self._busy = False
            for b in (self.allow_btn, self.always_btn, self.all_btn, self.deny_btn):
                b.setEnabled(True)

        rid = str(perm.get("requestId") or "")
        call = (
            store.call("helpers.allow", {"leadId": str(self._helper.get("leadId")), "requestId": rid})
            if self._helper
            else store.call("dispatch.allow", {"id": self._job, "requestId": rid})
        )
        call.then(ok).catch(bad)

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
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.buckets: List[float] = [0.0] * 12

    def set_buckets(self, b: List[float]) -> None:
        self.buckets = list(b)
        self.update()

    def paintEvent(self, _e) -> None:
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


class ShareBar(QWidget):
    """A session's share of the top one's spend: an 88 × 4 track and its fill, the first in the accent."""

    def __init__(self, share: float, first: bool) -> None:
        super().__init__()
        self.share, self.first = share, first
        self.setFixedSize(88, 4)

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.blend(t.fill_2, t.raised))
        p.drawRoundedRect(QRectF(0, 0, 88, 4), 2, 2)
        p.setBrush(QColor(t.accent if self.first else t.text_4))
        p.drawRoundedRect(QRectF(0, 0, 88 * max(0.0, min(1.0, self.share)), 4), 2, 2)


class SpendRow(LayoutButton):
    """One of the sessions that cost the most today: title, a bar against the top one, the amount."""

    def __init__(
        self, sid: str, title: str, share: float, amount: float, first: bool, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.sid = sid
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
        self.bar = ShareBar(share, first)
        lay.addWidget(self.bar)
        self.amount = Text(cost(amount), theme.FS_MICRO, 400, "text_2", wrap=False)
        lay.addWidget(self.amount)


class Spend(Card):
    """Spend in the last hour, five-minute bars, and the sessions that cost the most today (``.spend``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(12, parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)
        hour = QVBoxLayout()
        hour.setSpacing(6)
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


def _relay(lay: QVBoxLayout, rows: List[QWidget]) -> None:
    """Put ``rows`` in ``lay`` in this order with a half hairline between each two; the rows are kept
    (only the hairlines are made anew), so a list that changes order costs no widget churn."""
    while lay.count():
        w = lay.takeAt(0).widget()
        if w is not None and w not in rows:
            w.deleteLater()  # a hairline
    for i, r in enumerate(rows):
        if i:
            lay.addWidget(half_hairline("raised"))
        lay.addWidget(r)
        r.show()


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

    def eventFilter(self, _obj: QObject, e: QEvent) -> bool:
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
        self.working_cards: Dict[str, WorkingRow] = {}
        self.finished_rows: List[FinishedRow] = []
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

        # the four figures, in one strip
        self.tile_working = Tile("activity", "Working", lambda: self.jump(self._first_section()))
        self.tile_needs = Tile("hourglass", "Needs you", lambda: self.jump(self._first_section()))
        self.tile_finished = Tile("circle-check", "Finished today", lambda: self.jump(self.finished_sec))
        self.tile_spent = Tile("circle-dollar-sign", "Spent today", lambda: self.jump(self.spend_sec))
        self.tiles = [self.tile_working, self.tile_needs, self.tile_finished, self.tile_spent]
        self.stats = StatStrip(self.tiles)
        bl.addWidget(self.stats)

        # needs you
        self.needs_sec = QWidget()
        nl = QVBoxLayout(self.needs_sec)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(10)
        self.needs_head = SectionHead("Needs you")
        nl.addWidget(self.needs_head)
        self.needs_list = Card(12)
        self.needs_lay = QVBoxLayout(self.needs_list)
        self.needs_lay.setContentsMargins(4, 4, 4, 4)
        self.needs_lay.setSpacing(0)
        nl.addWidget(self.needs_list)
        self._needs_order: List[str] = []
        self.needs_sec.hide()
        bl.addWidget(self.needs_sec)

        # working
        self.working_sec = QWidget()
        wl = QVBoxLayout(self.working_sec)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(10)
        self.working_head = SectionHead("Working")
        wl.addWidget(self.working_head)
        self.working_list = Card(12)
        self.working_lay = QVBoxLayout(self.working_list)
        self.working_lay.setContentsMargins(4, 4, 4, 4)
        self.working_lay.setSpacing(0)
        wl.addWidget(self.working_list)
        self._working_order: List[str] = []
        self.working_sec.hide()  # shown while something works: the tile and the header say when nothing does
        bl.addWidget(self.working_sec)

        # finished today, then spend
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
        self.finished_empty = QuietLine("Nothing has finished today.", "circle-check")
        fl.addWidget(self.finished_empty)
        self._finished_by: Dict[str, FinishedRow] = {}
        self._finished_order: List[str] = []
        bl.addWidget(self.finished_sec)
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
        bl.addWidget(self.spend_sec)
        bl.addStretch(1)

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
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._tick_timer.start()
        self._fleet_timer.start()
        self._keys.watch(True)
        self.load_fleet()

    def hideEvent(self, e) -> None:
        self._tick_timer.stop()
        self._fleet_timer.stop()
        self._keys.watch(False)
        super().hideEvent(e)

    def _fit_body(self) -> None:
        """The conversation's column width, centred; everything inside is one column, so a resize only
        changes how much of each line shows."""
        vw = self.scroll.viewport().width()
        self.body.setFixedWidth(max(320, min(theme.column_width(vw), vw)))

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
        for i, s in enumerate(needs):
            sid = str(s.get("id"))
            card = self.needs_cards.get(sid)
            if card is None:
                card = self.needs_cards[sid] = NeedsCard(sid)
            card.update_from(s, self.state_of(sid), i == 0, self.now)
            card.set_focused(sid == focus_id)
        order = [str(s.get("id")) for s in needs]
        if order != self._needs_order:
            self._needs_order = order
            _relay(self.needs_lay, [self.needs_cards[sid] for sid in order])
        self.needs_sec.setVisible(bool(needs))

        # working
        self.working_head.count.set_count(len(working))
        keep = {str(s.get("id")) for s in working}
        for sid in list(self.working_cards):
            if sid not in keep:
                self.working_cards.pop(sid).deleteLater()
        order = [str(s.get("id")) for s in working]
        for s in working:
            sid = str(s.get("id"))
            row = self.working_cards.get(sid)
            if row is None:
                row = self.working_cards[sid] = WorkingRow(sid)
            mine = next((float(p.get("cost") or 0) for p in per if str(p.get("id")) == sid), 0.0)
            hot = median > 0 and mine > median * 3 and len(per) >= 4
            row.update_from(s, self.state_of(sid), self.now, hot)
            row.set_focused(sid == focus_id)
        if order != self._working_order:
            self._working_order = order
            _relay(self.working_lay, [self.working_cards[sid] for sid in order])
        self.working_sec.setVisible(bool(working))

        # finished today: the rows are kept by session and laid out again only when the list changes
        self.finished_head.count.set_count(len(finished))
        keep = {str(s.get("id")) for s in finished}
        for sid in list(self._finished_by):
            if sid not in keep:
                self._finished_by.pop(sid).deleteLater()
        order = [str(s.get("id")) for s in finished]
        for s in finished:
            sid = str(s.get("id"))
            row = self._finished_by.get(sid)
            if row is None:
                row = self._finished_by[sid] = FinishedRow(sid)
            row.update_from(s, self.state_of(sid), bool(store.unread.get(sid)), self.now)
            row.set_focused(sid == focus_id)
        if order != self._finished_order:
            self._finished_order = order
            _relay(self.finished_lay, [self._finished_by[sid] for sid in order])
        self.finished_rows = [self._finished_by[sid] for sid in order]
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

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
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
        if typing_elsewhere(QApplication.focusWidget()):
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
