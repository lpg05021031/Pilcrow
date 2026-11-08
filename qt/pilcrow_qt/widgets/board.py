"""The workboard (a port of web/src/components/Board.tsx and web/src/components/board/*).

One lane for each pair of hands the work can be in — backlog, Claude, a condition, you, done — and
one card per session that is work (it has an intent) or is doing something. Schedules that will
fire sit in the Waiting lane until they do. Only the "Needs you" lane carries the accent. The panel
on the right shows the chosen card's intent, ledger and the actions its state allows; a single key
does each of them. Live state comes from the store's runtimes plus ``board.state`` polled every
20 s; nothing here needs a transcript loaded.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QDateTime, QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QDateTimeEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import icons, theme
from ..lib.board import (
    LANES,
    SCHEDULE_PREFIX,
    actions_for,
    card_line,
    card_state,
    describe_wait,
    is_schedule,
    lane_of,
    ledger_of,
    perm_summary,
    quick_waits,
    schedule_actions,
    schedule_card,
    step,
)
from ..lib.fmt import basename, cost, duration, short_model, tildify, time_ago
from ..store import Runtime, store
from .dialogs.parts import Card, ElideLabel, Kbd, Switch, Text, Well
from .dialogs.settings_ui import ModelSelect, Select, combo_value, text_area
from .transcript.permission import suggestion_label, typing_elsewhere
from .ui.bits import Box, IconButton, LayoutButton, WorkingMark, button, paint_box
from .ui.menu import show_menu
from .ui.sections import KeyButton
from .ui.signals import bind

Json = Dict[str, Any]
BOARD_EVERY_MS = 20_000
LANE_MIN_W = 224
PANEL_W = 360
CHIP = {
    "review": "To review",
    "prompt": "Asking",
    "running": "Working",
    "queued": "Queued",
    "stuck": "Stopped",
    "wait": "Waiting",
    "done": "Done",
    "backlog": "",
}
WHERE = {
    "review": "With you · to review",
    "prompt": "With you · Claude is asking",
    "running": "With Claude",
    "queued": "With Claude · queued for a slot",
    "stuck": "With Claude · stopped, needs something from you",
    "wait": "Waiting on a condition",
    "backlog": "Not started",
    "done": "Done",
}
# the card chosen last time the board was open, so coming back from a session lands on it
_remembered: Dict[str, Optional[str]] = {"id": None}


def now_ms() -> float:
    return time.time() * 1000


def _lbl(text: str = "", tone: str = "", fs: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if tone:
        lb.setProperty("tone", tone)
    if fs:
        lb.setProperty("fs", fs)
    lb.setWordWrap(wrap)
    return lb


def _dt(text: str) -> QLabel:
    """A row label of the handoff grid (``.bd-report dt``)."""
    lb = QLabel(text)
    lb.setFont(theme.font_ui(theme.FS_SMALL))
    theme.tint(lb, fg="text_4")
    return lb


def _repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


def _clear(lay) -> None:
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            _clear(it.layout())


# ---------------------------------------------------------------- small parts


class FlowLayout(QLayout):
    """Items left to right, wrapping to the next row when the width runs out (``flex-wrap: wrap``)."""

    def __init__(self, parent: Optional[QWidget] = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: List[Any] = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return self._lay(QRect(0, 0, w, 0), True)

    def setGeometry(self, r: QRect) -> None:
        super().setGeometry(r)
        self._lay(r, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _lay(self, r: QRect, test: bool) -> int:
        x, y, row_h = r.x(), r.y(), 0
        for it in self._items:
            w = it.sizeHint().width()
            h = it.sizeHint().height()
            if x + w > r.right() + 1 and row_h > 0:
                x = r.x()
                y += row_h + self._gap
                row_h = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), it.sizeHint()))
            x += w + self._gap
            row_h = max(row_h, h)
        return y + row_h - r.y()


class Chip(Box):
    """``.bd-chip``: the state in a pill on the card's first line."""

    WASH = {
        "review": "accent_wash",
        "prompt": "warn_wash",
        "running": "info_wash",
        "stuck": "err_wash",
        "done": "ok_wash",
    }
    INK = {
        "review": "accent_text",
        "prompt": "warn",
        "running": "info",
        "stuck": "err",
        "done": "ok",
        "queued": "text_3",
    }

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(bg="fill_1/raised", radius=8, parent=parent)
        self._state = ""
        self.setFixedHeight(16)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 6, 0)
        self.lb = QLabel()
        self.lb.setFont(theme.font_ui(theme.FS_MICRO, 500))
        lay.addWidget(self.lb)

    def text(self) -> str:
        return self.lb.text()

    def set_state(self, state: str, text: str) -> None:
        self._state = state
        self.lb.setText(text)
        self.setVisible(bool(text))
        self.set_look(bg=f"{self.WASH.get(state, 'fill_1')}/raised")
        theme.tint(self.lb, fg=self.INK.get(state, "text_2"))


class ActionButton(KeyButton):
    """``.btn.sm`` with a key cap at its end (``.key``): what one letter does to the card."""

    def __init__(self, text: str, key: str = "", kind: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, kind, parent)
        self.action_id = ""
        self.set_key(key)

    def set_kind(self, kind: str) -> None:
        if kind == "danger-text":
            super().set_kind("")
            self.kind = kind
            self.setProperty("kind", kind)
            theme.tint(self.label, fg="err")
            return
        super().set_kind(kind)


class Avatar(Box):
    """``.bd-av``: the model's initial in a round blue mark, or ``you`` in an accent pill."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(bg="info_wash/raised", radius=7, parent=parent)
        self.setFixedHeight(14)
        self._me = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.lb = QLabel()
        self.lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lb.setFont(theme.font_ui(9, 600))
        lay.addWidget(self.lb)

    def text(self) -> str:
        return self.lb.text()

    def set_who(self, model: str) -> None:
        self._me = not model
        self.lb.setText("you" if self._me else model[0].upper())
        self.setFixedWidth(26 if self._me else 14)
        self.set_look(bg="accent_wash/raised" if self._me else "info_wash/raised")
        theme.tint(self.lb, fg="accent_text" if self._me else "info")


# ---------------------------------------------------------------- cards


class BoardCard(Card):
    """One session as a card (``.bd-card``): the project and a state chip, the title, one line on what it
    is waiting for or doing, and a foot with who is on it and what it has cost. The stripe on the left is
    the only colour: accent for a review, amber for a prompt, blue while working, red when stopped."""

    selected = Signal(str)
    opened = Signal(str)

    def __init__(self, sid: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(theme.R2, parent)
        self.sid = sid
        self.state = ""
        self._sel = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay = QVBoxLayout(self)
        lay.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        lay.setContentsMargins(12, 9, 10, 8)
        lay.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.project = ElideLabel()
        self.project.setFont(theme.font_mono(theme.FS_MICRO))
        top.addWidget(self.project, 1)
        self.chip = Chip()
        top.addWidget(self.chip)
        lay.addLayout(top)
        self.title = Text("", theme.FS_UI, 500, "text", lh=1.35)
        lay.addWidget(self.title)
        self.line = Text("", theme.FS_SMALL, 400, "text_3", lh=1.4)
        lay.addWidget(self.line)
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.avatar = Avatar()
        foot.addWidget(self.avatar)
        self.who = ElideLabel()
        foot.addWidget(self.who, 1)
        self.working = WorkingMark(13)
        self.working.hide()
        foot.addWidget(self.working)
        self.elapsed = QLabel()
        foot.addWidget(self.elapsed)
        self.delta = QLabel()
        self.delta.setFont(theme.font_mono(theme.FS_MICRO))
        foot.addWidget(self.delta)
        self.spent = QLabel()
        foot.addWidget(self.spent)
        lay.addLayout(foot)
        self._paint_extra()

    def _paint_extra(self) -> None:
        for w in (self.project, self.who, self.elapsed, self.spent):
            theme.tint(w, fg="text_4")
            if w is not self.project:
                w.setFont(theme.font_ui(theme.FS_MICRO))
        self.update()

    def _paint(self) -> None:
        self.update()

    def set_selected(self, on: bool) -> None:
        if on != self._sel:
            self._sel = on
            self._paint()

    def paintEvent(self, e) -> None:
        t = theme.tokens()
        if getattr(self, "_sel", False):
            # the chosen card: the surface with a 1.5px accent ring (``.bd-card.sel``)
            p = QPainter(self)
            paint_box(p, QRectF(self.rect()), QColor(t.raised), QColor(t.accent), theme.R2, 1.5)
            p.end()
        else:
            super().paintEvent(e)
        color = {"review": t.accent, "prompt": t.warn, "running": t.info, "stuck": t.err, "queued": t.hair_3}.get(
            self.state
        )
        if not color:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(0, 9, 3, self.height() - 18), 1.5, 1.5)

    def mousePressEvent(self, e) -> None:
        self.selected.emit(self.sid)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        self.opened.emit(self.sid)
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Space:
            self.selected.emit(self.sid)
            return
        super().keyPressEvent(e)

    def update_from(self, s: Json, rt: Optional[Json], state: str, now: float, titles: Dict[str, str]) -> None:
        t = theme.tokens()
        self.state = state
        schedule = is_schedule(self.sid)
        wt = s.get("worktree") or {}
        proj = basename(str(s.get("project") or s.get("cwd") or ""))
        if wt.get("branch"):
            proj += f" · {str(wt['branch']).replace('pilcrow/', '', 1)}"
        helper = (rt or {}).get("helper") or s.get("helper")
        if helper:
            proj = f"helper of {((rt or {}).get('helper') or {}).get('leadTitle') or 'a lead session'} · {proj}"
        self.project.setText(proj)
        if schedule:
            self.chip.set_state("wait", "Scheduled")
        else:
            self.chip.set_state(state, CHIP.get(state, ""))
        self.title.setText(str(s.get("title") or ""))
        text, tone = card_line(s, state, rt, titles)
        self.line.setText(text)
        self.line.setVisible(bool(text))
        self.line.set_color("accent_text" if tone == "ask" else "err" if tone == "err" else "text_3")
        rt = rt or {}
        model = short_model(str(rt.get("activeModel") or "")) if rt.get("activeModel") else ""
        self.avatar.set_who(model)
        self.who.setText(model)
        self.who.setVisible(bool(model))
        running = state == "running"
        self.working.setVisible(running)
        started = rt.get("turnStartedAt")
        self.elapsed.setText(
            duration(now - float(started)) if running and started and now - float(started) > 1000 else ""
        )
        files = (s.get("report") or {}).get("files") or []
        if state == "review" and files:
            add = sum(int(f.get("add") or 0) for f in files)
            dele = sum(int(f.get("del") or 0) for f in files)
            self.delta.setText(f'<span style="color:{t.ok}">+{add}</span> <span style="color:{t.err}">−{dele}</span>')
        else:
            self.delta.setText("")
        spent = float((rt.get("usage") or {}).get("costUsd") or s.get("costUsd") or 0)
        self.spent.setText(cost(spent) if spent > 0 else "")
        self.update()


class LaneWidget(Box):
    """One lane (``.bd-lane``): a header with the mark, title, key number and count, then the cards."""

    MARK = {"agent": "info", "wait": "text_4", "me": "accent", "done": "ok"}

    def __init__(self, spec: Json, index: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(bg="inset", line="hair/inset", radius=theme.R3, parent=parent)
        self.key = str(spec["key"])
        self.spec = spec
        self.card_widgets: List[BoardCard] = []
        self.setMinimumWidth(LANE_MIN_W)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 9, 10, 5)
        hl.setSpacing(7)
        self.mark = Box(bg=self.MARK.get(self.key, "hair_3"), radius=2)
        self.mark.setFixedSize(8, 8)
        hl.addWidget(self.mark)
        self.title = QLabel(str(spec["title"]))
        self.title.setFont(theme.font_ui(theme.FS_SMALL, 600))
        theme.tint(self.title, fg="accent_text" if self.key == "me" else "text_2")
        hl.addWidget(self.title)
        self.num = QLabel(str(index + 1))
        self.num.setFont(theme.font_mono(theme.FS_MICRO))
        theme.tint(self.num, fg="text_4")
        hl.addWidget(self.num)
        hl.addStretch(1)
        self.extra = QHBoxLayout()
        self.extra.setSpacing(4)
        hl.addLayout(self.extra)
        self.count = QLabel("0")
        self.count.setFont(theme.font_ui(theme.FS_SMALL, 500))
        theme.tint(self.count, fg="text_4")
        hl.addWidget(self.count)
        lay.addWidget(head)
        self.cards_host = QWidget()
        self.cards_lay = QVBoxLayout(self.cards_host)
        self.cards_lay.setContentsMargins(8, 2, 8, 10)
        self.cards_lay.setSpacing(8)
        self.empty = Text(str(spec["empty"]), theme.FS_SMALL, 400, "text_4")
        self.cards_lay.addWidget(self.empty)
        lay.addWidget(self.cards_host)

    def set_count(self, text: str, full: bool = False) -> None:
        self.count.setText(text)
        theme.tint(self.count, fg="warn" if full else "text_4")

    def set_cards(self, widgets: List[QWidget], hide_empty: bool = False) -> None:
        """Lay these widgets out in order, keeping the ones already here (their state and hover stay)."""
        for i in reversed(range(self.cards_lay.count())):
            it = self.cards_lay.itemAt(i)
            w = it.widget()
            if w is not None and w is not self.empty and w not in widgets:
                self.cards_lay.removeWidget(w)
                w.setParent(None)
        for i, w in enumerate(widgets):
            if self.cards_lay.indexOf(w) != i:
                self.cards_lay.removeWidget(w)
                self.cards_lay.insertWidget(i, w)
            w.show()
        self.cards_lay.removeWidget(self.empty)
        self.cards_lay.addWidget(self.empty)
        self.empty.setVisible(not widgets and not hide_empty)
        self.card_widgets = [w for w in widgets if isinstance(w, BoardCard)]


# ---------------------------------------------------------------- the panel's parts


class PermissionBox(QWidget):
    """The permission waiting on a card, answerable in place; questions and plans open the conversation."""

    def __init__(self, sid: str, on_open: Callable[[], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = sid
        self._perm: Optional[Json] = None
        self._busy = False
        self._decided = ""
        self._allow_always = False
        self._cwd = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.well = Well(theme.R2)
        wl = QVBoxLayout(self.well)
        wl.setContentsMargins(10, 8, 10, 8)
        wl.setSpacing(2)
        self.tool = QLabel()
        self.tool.setFont(theme.font_ui(theme.FS_MICRO, 600))
        theme.tint(self.tool, fg="text_3")
        wl.addWidget(self.tool)
        self.what = QLabel()
        theme.tint(self.what, fg="text")
        self.what.setWordWrap(True)
        self.what.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        wl.addWidget(self.what)
        lay.addWidget(self.well)
        self.note = _lbl("", "faint", "small", wrap=True)
        lay.addWidget(self.note)
        self.wait = _lbl("", "faint", "micro")
        lay.addWidget(self.wait)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.allow_btn = ActionButton("Allow", "1", "primary")
        self.allow_btn.clicked.connect(self.allow)
        row.addWidget(self.allow_btn)
        self.always_btn = ActionButton("Always allow", "2")
        self.always_btn.clicked.connect(self.always)
        row.addWidget(self.always_btn)
        # a helper's request can be answered for every helper of its lead at once (HELPERS.md §5.6)
        self.all_btn = ActionButton("Allow for all helpers")
        self.all_btn.setToolTip("Answer every helper of this lead that asks the same thing, now and later")
        self.all_btn.clicked.connect(self.allow_all)
        self.all_btn.hide()
        row.addWidget(self.all_btn)
        self._helper: Optional[Json] = None  # the lead this session helps, if any
        self._job: str = ""  # the dispatch job this session runs for, if any
        self.deny_btn = ActionButton("Deny", "3", "ghost")
        self.deny_btn.clicked.connect(self.deny)
        row.addWidget(self.deny_btn)
        self.open_btn = button("Open to respond", kind="primary", size="sm", on_click=on_open)
        row.addWidget(self.open_btn)
        self.decided_lb = _lbl("", "ok", "small")
        row.addWidget(self.decided_lb)
        row.addStretch(1)
        lay.addLayout(row)

    @property
    def perm(self) -> Optional[Json]:
        return self._perm

    def update_from(self, s: Json, rt: Json, now: float) -> None:
        pending = rt.get("pending") or []
        perm = pending[0] if pending else None
        self._perm = perm
        self._cwd = str(s.get("cwd") or "")
        if not perm:
            self.hide()
            return
        self.show()
        h = s.get("helper")
        self._helper = h if isinstance(h, dict) and h.get("leadId") else None
        d = s.get("dispatch")
        self._job = str(d.get("jobId")) if isinstance(d, dict) and d.get("jobId") else ""
        self.all_btn.label.setText("Allow for all helpers" if self._helper else "Allow for all")
        self.all_btn.setToolTip(
            "Answer every helper of this lead that asks the same thing, now and later"
            if self._helper
            else "Answer every run of this job that asks the same thing, now and later"
        )
        text, mono, note = perm_summary(perm, self._cwd)
        self.tool.setText(str(perm.get("displayName") or perm.get("toolName") or ""))
        self.what.setText(text)
        self.what.setFont(theme.font_mono(theme.MONO_SIZE) if mono else theme.font_ui(theme.FS_SMALL))
        self.note.setText(note or "")
        self.note.setVisible(bool(note))
        self.wait.setText(f"waiting {max(0, int(round((now - float(perm.get('at') or now)) / 1000)))}s")
        interactive = bool(perm.get("interactive"))
        self._allow_always = bool(not perm.get("suppressAlwaysAllowRule") and perm.get("suggestions"))
        default_no = bool(perm.get("defaultToNo"))
        self.allow_btn.set_kind("" if default_no else "primary")
        self.deny_btn.set_kind("primary" if default_no else "ghost")
        self.allow_btn.kbd.setVisible(not default_no)
        self.deny_btn.kbd.setText("3" if self._allow_always else "2")
        if self._allow_always:
            self.always_btn.setToolTip(suggestion_label(perm["suggestions"][0], self._cwd))
        tool = perm.get("toolName")
        self.open_btn.setText(
            "Answer the question"
            if tool == "AskUserQuestion"
            else "Review the plan"
            if tool == "ExitPlanMode"
            else "Open to respond"
        )
        decided = bool(self._decided)
        self.allow_btn.setVisible(not interactive and not decided)
        self.always_btn.setVisible(not interactive and not decided and self._allow_always)
        self.deny_btn.setVisible(not interactive and not decided)
        self.all_btn.setVisible(not interactive and not decided and bool(self._helper or self._job))
        self.open_btn.setVisible(interactive and not decided)
        self.decided_lb.setVisible(decided)
        for b in (self.allow_btn, self.always_btn, self.all_btn, self.deny_btn):
            b.setEnabled(not self._busy)

    def decide(self, decision: Json) -> None:
        perm = self._perm
        if not perm or self._busy:
            return
        self._send(
            store.respond(self.sid, str(perm.get("requestId") or ""), decision), decision.get("behavior") == "allow"
        )

    def _send(self, call: Any, allowed: bool) -> None:
        self._busy = True
        buttons = (self.allow_btn, self.always_btn, self.all_btn, self.deny_btn)
        for b in buttons:
            b.setEnabled(False)

        def ok(_r: Any) -> None:
            self._decided = "allow" if allowed else "deny"
            self.decided_lb.setText("Allowed — Claude is continuing" if allowed else "Declined")
            for b in (*buttons, self.open_btn):
                b.hide()
            self.decided_lb.show()
            QTimer.singleShot(900, self._reset)

        def bad(_e: Any) -> None:
            self._busy = False
            for b in buttons:
                b.setEnabled(True)

        call.then(ok).catch(bad)

    def allow_all(self) -> None:
        """Answer this request for every helper of the lead, or every repository of the dispatch job, now and
        later (web CardPanel.tsx allowAllHelpers; dispatch's "Allow for all")."""
        perm = self._perm
        if not perm or self._busy or not (self._helper or self._job):
            return
        rid = str(perm.get("requestId") or "")
        call = (
            store.call("helpers.allow", {"leadId": str(self._helper.get("leadId")), "requestId": rid})
            if self._helper
            else store.call("dispatch.allow", {"id": self._job, "requestId": rid})
        )
        self._send(call, True)

    def _reset(self) -> None:
        self._decided = ""
        self._busy = False

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
        """1/2/3 answer the permission; returns whether the key meant something."""
        perm = self._perm
        if not perm or perm.get("interactive"):
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


class NoteBox(QWidget):
    """``.bd-note``: a text box with a primary button, for a send-back, an answer, or an intent edit.
    Ctrl+Enter submits, Esc cancels."""

    def __init__(
        self,
        placeholder: str,
        primary: str,
        on_submit: Callable[[str], None],
        on_cancel: Callable[[], None],
        text: str = "",
        allow_empty: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._on_submit = on_submit
        self._on_cancel = on_cancel
        self._allow_empty = allow_empty
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.edit = text_area(placeholder, rows=3, name=primary)
        self.edit.setPlainText(text)
        self.edit.installEventFilter(self)
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.submit_btn = button(primary, kind="primary", size="sm", on_click=self.submit)
        row.addWidget(self.submit_btn)
        row.addWidget(button("Cancel", kind="ghost", size="sm", on_click=on_cancel))
        row.addStretch(1)
        row.addWidget(_lbl("Ctrl+Enter", "faint", "micro"))
        lay.addLayout(row)
        self.edit.textChanged.connect(self._changed)
        self._changed()
        QTimer.singleShot(0, self.edit.setFocus)

    def _changed(self) -> None:
        self.submit_btn.setEnabled(self._allow_empty or bool(self.edit.toPlainText().strip()))

    def submit(self) -> None:
        text = self.edit.toPlainText().strip()
        if not text and not self._allow_empty:
            return
        self.submit_btn.setEnabled(False)
        self._on_submit(text)

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if obj is self.edit and e.type() == QEvent.Type.KeyPress:
            k = e.key()
            if k == Qt.Key.Key_Escape:
                self._on_cancel()
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.submit()
                return True
        return super().eventFilter(obj, e)


class WaitOption(LayoutButton):
    """``.bd-waitopt``: a key cap, the condition, and a hint at the end."""

    def __init__(self, key: str, label: str, hint: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.key = key
        self.setObjectName("waitopt")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(label)
        self.setAutoDefault(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(10)
        lay.addWidget(Kbd(key))
        self.label = QLabel(label)
        lay.addWidget(self.label, 1)
        self.hint = QLabel(hint)
        lay.addWidget(self.hint)
        for w in (self.label, self.hint):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label.setFont(theme.font_ui(theme.FS_SMALL))
        self.hint.setFont(theme.font_ui(theme.FS_MICRO))
        theme.tint(self.hint, fg="text_3")
        self.set_look(bg="raised", hover="fill_1/raised", line="hair_2/raised", radius=theme.R2)
        self._tint()

    def _tint(self) -> None:
        theme.tint(self.label, fg="text" if self.isEnabled() else "text_4")

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        if e.type() == QEvent.Type.EnabledChange:
            self._tint()


class WaitPicker(QWidget):
    """``.bd-waitpick``: the conditions a card can wait on — CI on its branch, a time, or another card
    being approved."""

    def __init__(
        self,
        s: Json,
        caps: Json,
        others: List[Json],
        now: float,
        on_pick: Callable[[Json], None],
        on_cancel: Callable[[], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._s = s
        self._now = now
        self._on_pick = on_pick
        self.options: List[WaitOption] = []
        branch = str((s.get("worktree") or {}).get("branch") or s.get("gitBranch") or "")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(
            _lbl(
                "Claude is woken with the intent when the condition is met; until then the card sits in Waiting.",
                "faint",
                "micro",
                wrap=True,
            )
        )
        for q in quick_waits(now):
            ci = q["wait"] == "ci"
            disabled = ci and not caps.get("ci")
            label = f"{q['label']} · {branch}" if ci and branch else str(q["label"])
            opt = WaitOption(str(q["key"]), label, "no gh" if disabled else str(q["hint"]))
            opt.setEnabled(not disabled)
            if disabled:
                opt.setToolTip("Needs the GitHub CLI (gh) on this machine")
            wait = {"kind": "ci", "cwd": s.get("cwd"), "branch": branch, "since": now} if ci else dict(q["wait"])
            opt.clicked.connect(lambda _c=False, w=wait: self._on_pick(w))
            lay.addWidget(opt)
            self.options.append(opt)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.at = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.at.setCalendarPopup(True)
        self.at.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.at.setMinimumDateTime(QDateTime.currentDateTime().addSecs(60))
        self.at.setAccessibleName("Until a time")
        row.addWidget(self.at, 1)
        self.until_btn = button("Until then", size="sm", on_click=self._pick_time)
        row.addWidget(self.until_btn)
        lay.addLayout(row)
        self.other = None
        if others:
            row2 = QHBoxLayout()
            row2.setSpacing(6)
            self.other = Select("Until another card is approved", popup_width=320)
            self.other.addItem("Until another card is approved…", "")
            for c in others:
                self.other.addItem(str(c.get("title") or ""), str(c.get("id")))
            self.other.setCurrentIndex(0)
            row2.addWidget(self.other, 1)
            self.wait_btn = button("Wait for it", size="sm", on_click=self._pick_card)
            row2.addWidget(self.wait_btn)
            lay.addLayout(row2)
        bottom = QHBoxLayout()
        cancel = ActionButton("Cancel", "Esc", "ghost")
        cancel.clicked.connect(on_cancel)
        bottom.addWidget(cancel)
        bottom.addStretch(1)
        lay.addLayout(bottom)

    def _pick_time(self) -> None:
        at = self.at.dateTime().toMSecsSinceEpoch()
        if at > now_ms():
            self._on_pick({"kind": "time", "at": at})

    def _pick_card(self) -> None:
        if self.other is None:
            return
        sid = combo_value(self.other)
        if sid:
            self._on_pick({"kind": "session", "id": sid})

    def pick_key(self, key: str) -> bool:
        for opt in self.options:
            if opt.key == key:
                if opt.isEnabled():
                    opt.click()
                return True
        return False


class _Edged(QWidget):
    """A plain surface with one hairline, along its top or its bottom (``.bd-panel-h``, ``.bd-actions``)."""

    def __init__(self, edge: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.edge = edge
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:
        t = theme.tokens()
        p = QPainter(self)
        y = 0 if self.edge == "top" else self.height() - 1
        p.fillRect(0, y, self.width(), 1, theme.blend(t.hair, t.chrome))


class Panel(QFrame):
    """``.bd-panel``: the chosen card in full — its intent, what Claude is asking or why it stopped, the
    handoff report when there is one, the ledger, and at the bottom the actions its state allows."""

    def __init__(self, board: Board, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.board = board
        self.sid = ""
        self.state = ""
        self.mode: Optional[str] = None
        self.editing = False
        self.perm: Optional[PermissionBox] = None
        self.wait_picker: Optional[WaitPicker] = None
        self.note: Optional[NoteBox] = None
        self.report: Optional[QWidget] = None
        self.action_buttons: List[ActionButton] = []
        self._when_labels: List[tuple] = []
        self._built_for: tuple = ()
        self.setFixedWidth(PANEL_W)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = _Edged("bottom")
        hl = QVBoxLayout(head)
        hl.setContentsMargins(16, 12, 16, 10)
        hl.setSpacing(4)
        meta = QHBoxLayout()
        meta.setSpacing(4)
        self.meta = ElideLabel()
        self.meta.setFont(theme.font_mono(theme.FS_MICRO))
        theme.tint(self.meta, fg="text_4")
        meta.addWidget(self.meta, 1)
        self.open_btn = IconButton("arrow-up-right", "Open the conversation", 14)
        self.open_btn.clicked.connect(lambda: self.board.act(self.sid, "open"))
        meta.addWidget(self.open_btn)
        self.close_btn = IconButton("x", "Close (Esc)", 14)
        self.close_btn.clicked.connect(lambda: self.board.select(None))
        meta.addWidget(self.close_btn)
        hl.addLayout(meta)
        self.title = Text("", theme.FS_TITLE, 600, "text", lh=1.3)
        hl.addWidget(self.title)
        self.where = _lbl("", "faint", "small")
        hl.addWidget(self.where)
        lay.addWidget(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(16, 12, 16, 20)
        self.body_lay.setSpacing(16)
        self.body_lay.addStretch(1)
        self.scroll.setWidget(self.body)
        lay.addWidget(self.scroll, 1)
        self.actions = _Edged("top")
        self.actions_lay = QVBoxLayout(self.actions)
        self.actions_lay.setContentsMargins(16, 10, 16, 10)
        self.actions_lay.setSpacing(6)
        lay.addWidget(self.actions)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:
        # the chrome surface with a hairline down its left edge (``.bd-panel``)
        t = theme.tokens()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(t.chrome))
        p.fillRect(0, 0, 1, self.height(), theme.blend(t.hair, t.chrome))

    # ---- building
    def _sec(self, title: str, extra: Optional[QWidget] = None) -> QVBoxLayout:
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(5)
        hl = QHBoxLayout()
        hl.setSpacing(4)
        h = QLabel(title.upper())
        hf = theme.font_ui(theme.FS_MICRO, 600)
        hf.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106)
        h.setFont(hf)
        theme.tint(h, fg="text_4")
        hl.addWidget(h)
        if extra is not None:
            hl.addWidget(extra)
        hl.addStretch(1)
        bl.addLayout(hl)
        self.body_lay.insertWidget(self.body_lay.count() - 1, box)
        return bl

    def _para(self, text: str, color: str = "text", size: float = theme.FS_BODY) -> Text:
        return Text(text, size, 400, color, lh=1.55)

    def show_card(self, s: Json, rt: Optional[Json], state: str, mode: Optional[str], now: float) -> None:
        schedule = is_schedule(str(s["id"]))
        sid = str(s["id"])
        key = (
            sid,
            state,
            mode,
            self.editing,
            bool(rt and rt.get("pending")),
            bool(s.get("report")),
            len(s.get("decisions") or []),
            (rt or {}).get("error"),
            s.get("intent"),
            str(s.get("wait")),
            str(s.get("queued")),
            (rt or {}).get("activity") == {} if rt else None,
        )
        if key != self._built_for:
            self._built_for = key
            self._build(s, rt or {}, state, mode, schedule, now)
        else:
            self._tick(s, rt or {}, state, now)
        self.sid = sid
        self.state = state
        self.mode = mode

    def _tick(self, s: Json, rt: Json, state: str, now: float) -> None:
        for lb, at in self._when_labels:
            lb.setText(time_ago(at, now))
        if self.perm is not None and self.perm.isVisible():
            self.perm.update_from(s, rt, now)
        if state == "running" and self.right_now is not None:
            a = rt.get("activity") or {}
            self.right_now.setText(
                f"{a.get('tool')} · {a.get('label')}"
                if a.get("kind") == "tool"
                else str(a.get("label") or "")
                if a.get("kind") == "text"
                else "Thinking…"
            )

    def _build(self, s: Json, rt: Json, state: str, mode: Optional[str], schedule: bool, now: float) -> None:
        sid = str(s["id"])
        self.sid = sid
        self.perm = None
        self.wait_picker = None
        self.note = None
        self.report = None
        self.right_now: Optional[Text] = None
        self.action_buttons = []
        self._when_labels = []
        while self.body_lay.count() > 1:
            it = self.body_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        _clear(self.actions_lay)
        titles = self.board.titles
        wt = s.get("worktree") or {}
        meta = basename(str(s.get("project") or s.get("cwd") or ""))
        if wt.get("branch"):
            meta += f" · {wt['branch']}"
        elif s.get("gitBranch"):
            meta += f" · {s['gitBranch']}"
        self.meta.setText(meta)
        self.open_btn.setVisible(not schedule)
        self.title.setText(str(s.get("title") or ""))
        self.where.setText("A schedule · runs on its own" if schedule else WHERE.get(state, ""))

        # intent
        pencil = None
        if not schedule and not self.editing:
            pencil = IconButton("pencil", "Edit the intent", 12)
            pencil.clicked.connect(self._edit_intent)
        sec = self._sec("What it runs" if schedule else "Intent", pencil)
        if self.editing:
            sec.addWidget(
                NoteBox(
                    "What is the work, and when is it done?",
                    "Save",
                    self._save_intent,
                    self._stop_editing,
                    text=str(s.get("intent") or ""),
                    allow_empty=True,
                )
            )
        elif s.get("intent"):
            sec.addWidget(self._para(str(s["intent"])))
        else:
            sec.addWidget(
                _lbl(
                    "No intent yet. Two or three sentences on the goal and when it is done; a card with one can be handed to Claude from here.",
                    "faint",
                    "small",
                    wrap=True,
                )
            )

        if (state == "wait" or schedule) and s.get("wait"):
            w = s["wait"]
            sec = self._sec("Next run" if schedule else "Waiting for")
            text = describe_wait(w, titles)
            if w.get("kind") == "time":
                text += f"  ·  {time_ago(float(w.get('at') or 0), now)}"
            sec.addWidget(self._para(text))
            if w.get("prompt"):
                sec.addWidget(_lbl(f"Then: {w['prompt']}", "faint", "small", wrap=True))
        if state == "queued" and s.get("queued"):
            sec = self._sec("Queued")
            sec.addWidget(
                self._para(
                    "Claude is working on as many cards as the limit allows. This one starts when a slot frees, with:"
                )
            )
            sec.addWidget(_lbl(str(s["queued"].get("text") or ""), "faint", "small", wrap=True))
        if state == "prompt" and rt.get("pending"):
            sec = self._sec("Claude is asking")
            self.perm = PermissionBox(sid, lambda: self.board.act(sid, "open"))
            self.perm.update_from(s, rt, now)
            sec.addWidget(self.perm)
        if state == "stuck" and rt.get("error"):
            sec = self._sec("Why it stopped")
            sec.addWidget(self._para(str(rt["error"]), "err"))
        if state == "running" and rt.get("activity"):
            sec = self._sec("Right now")
            self.right_now = self._para("")
            sec.addWidget(self.right_now)
            self._tick(s, rt, state, now)
        report = s.get("report")
        if report:
            sec = self._sec("Handoff")
            self.report = self._report(report)
            sec.addWidget(self.report)
        elif state == "review" and (rt.get("lastReply") or {}).get("text"):
            sec = self._sec("Last reply")
            sec.addWidget(self._para(str(rt["lastReply"]["text"])))
        if not schedule:
            events = ledger_of(s, rt)
            if events:
                sec = self._sec("Ledger")
                sec.addWidget(self._ledger(events, now))

        # actions
        t = theme.tokens()
        if mode == "wait":
            lanes = self.board.lanes_data
            others = [
                c
                for k in ("backlog", "agent", "wait", "me")
                for c in lanes.get(k, [])
                if c.get("id") != sid and not is_schedule(str(c.get("id")))
            ]
            self.wait_picker = WaitPicker(
                s,
                self.board.caps,
                others,
                now,
                lambda w: self.board.act(sid, "set-wait", w),
                lambda: self.board.set_mode(None),
            )
            self.actions_lay.addWidget(self.wait_picker)
        elif mode in ("reject", "send"):
            kind = "reject" if mode == "reject" else "answer"
            self.note = NoteBox(
                "What should change? It goes to Claude as the next message, in this same conversation."
                if mode == "reject"
                else "What does it need? Your message continues the conversation.",
                "Send back" if mode == "reject" else "Send",
                lambda text: self.board.decide(sid, kind, text),
                lambda: self.board.set_mode(None),
            )
            self.actions_lay.addWidget(self.note)
        else:
            row = FlowLayout(spacing=6)
            acts = schedule_actions() if schedule else actions_for(state, rt)
            for a in acts:
                b = ActionButton(
                    str(a["label"]),
                    str(a["key"]),
                    "primary" if a.get("primary") else "danger-text" if a.get("danger") else "",
                )
                b.action_id = str(a["id"])
                b.clicked.connect(lambda _c=False, aid=a["id"]: self.board.act(sid, str(aid)))
                row.addWidget(b)
                self.action_buttons.append(b)
            usage = rt.get("usage") or {}
            if state == "review" and float(usage.get("costUsd") or 0) > 0:
                lb = _lbl(f"{cost(float(usage['costUsd']))} so far", "faint", "micro")
                row.addWidget(lb)
            wrap = QWidget()
            wrap.setLayout(row)
            self.actions_lay.addWidget(wrap)
        _ = t

    def _report(self, r: Json) -> QWidget:
        t = theme.tokens()
        box = Well(theme.R3)
        gl = QGridLayout(box)
        gl.setContentsMargins(12, 8, 12, 10)
        gl.setHorizontalSpacing(10)
        gl.setVerticalSpacing(6)
        gl.setColumnMinimumWidth(0, 68)
        gl.setColumnStretch(1, 1)
        rows = [("Asked", str(r.get("asked") or ""), "text"), ("Did", str(r.get("did") or ""), "text")]
        files = r.get("files") or []
        for i, (k, v, color) in enumerate(rows):
            gl.addWidget(_dt(k), i, 0, Qt.AlignmentFlag.AlignTop)
            gl.addWidget(Text(v, theme.FS_SMALL, 400, color, lh=1.5), i, 1)
        n = len(rows)
        if files:
            gl.addWidget(_dt("Changed"), n, 0, Qt.AlignmentFlag.AlignTop)
            fl = QVBoxLayout()
            fl.setSpacing(1)
            for f in files:
                lb = QLabel(
                    f'{f.get("path")} <span style="color:{t.ok}">+{f.get("add", 0)}</span> <span style="color:{t.err}">−{f.get("del", 0)}</span>'
                )
                lb.setFont(theme.font_mono(theme.FS_MICRO))
                theme.tint(lb, fg="text_2")
                lb.setWordWrap(True)
                fl.addWidget(lb)
            holder = QWidget()
            holder.setLayout(fl)
            fl.setContentsMargins(0, 0, 0, 0)
            gl.addWidget(holder, n, 1)
            n += 1
        for k, v, color in (
            ("Unverified", str(r.get("unverified") or "nothing"), "warn"),
            ("Decide", str(r.get("ask") or "none"), "accent_text"),
        ):
            gl.addWidget(_dt(k), n, 0, Qt.AlignmentFlag.AlignTop)
            gl.addWidget(Text(v, theme.FS_SMALL, 500 if k == "Decide" else 400, color, lh=1.5), n, 1)
            n += 1
        return box

    def _ledger(self, events: List[Json], now: float) -> QWidget:
        host = QWidget()
        vl = QVBoxLayout(host)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(9)
        for e in events:
            row = QHBoxLayout()
            row.setSpacing(8)
            tone = {"me": "accent", "run": "info", "ok": "ok", "err": "err"}.get(str(e.get("tone") or ""), "text_4")
            dot = Box(bg=tone, radius=3.5)
            dot.setFixedSize(7, 7)
            row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(2)
            top = QHBoxLayout()
            top.setSpacing(6)
            lb = QLabel(str(e["label"]))
            lb.setFont(theme.font_ui(theme.FS_SMALL, 500))
            theme.tint(lb, fg="text")
            top.addWidget(lb)
            when = QLabel(time_ago(float(e["at"]), now))
            when.setFont(theme.font_mono(theme.FS_MICRO))
            theme.tint(when, fg="text_4")
            top.addWidget(when)
            top.addStretch(1)
            col.addLayout(top)
            self._when_labels.append((when, float(e["at"])))
            if e.get("note"):
                note = Text(str(e["note"]), theme.FS_SMALL, 400, "text_3", lh=1.45)
                note.setContentsMargins(8, 0, 0, 0)
                col.addWidget(note)
            row.addLayout(col, 1)
            vl.addLayout(row)
        return host

    # ---- the intent editor
    def _edit_intent(self) -> None:
        self.editing = True
        self.board.refresh()

    def _stop_editing(self) -> None:
        self.editing = False
        self.board.refresh()

    def _save_intent(self, text: str) -> None:
        sid = self.sid

        def done(_r: Any) -> None:
            self.editing = False
            self.board.refresh()

        store.set_intent(sid, text).then(done)


class NewCard(Box):
    """``.bd-new``: a card being written, at the top of the backlog: a title, two or three sentences of
    intent, the folder it is about, and whether Claude works in its own worktree. Enter keeps it in the
    backlog; Ctrl+Enter hands it to Claude straight away."""

    def __init__(self, on_done: Callable[[Optional[str]], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(bg="raised", line="accent_line", radius=theme.R2, line_w=1.5, parent=parent)
        self._on_done = on_done
        self._busy = ""
        self._git: Optional[Json] = None
        config = store.config or {}
        info = store.info or {}
        recents: List[str] = []
        for r in list(config.get("recentProjects") or []) + [
            str(s.get("project") or s.get("cwd") or "") for s in store.sessions
        ]:
            if r and r not in recents and len(recents) < 5:
                recents.append(r)
        self.cwd = recents[0] if recents else str(info.get("home") or "")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 10, 10)
        lay.setSpacing(8)
        self.title = QLineEdit()
        self.title.setObjectName("bdnewtitle")
        self.title.setFont(theme.font_ui(theme.FS_UI, 500))
        theme.tint(self.title, fg="text", bg="transparent")
        # geometry and palette roles only: the hairline under the title, the link colour when it has focus
        self.title.setStyleSheet(
            "QLineEdit#bdnewtitle { background: transparent; border: none; border-bottom: 1px solid palette(dark); padding: 2px 0 5px; }"
            " QLineEdit#bdnewtitle:focus { border-bottom-color: palette(link); }"
        )
        self.title.setPlaceholderText("What is the work?")
        self.title.setAccessibleName("What is the work?")
        self.title.installEventFilter(self)
        lay.addWidget(self.title)
        self.intent = text_area(
            "Two or three sentences: the goal, and when it is done. Claude gets this as its first message; left empty, the title is.",
            rows=3,
            name="Intent",
        )
        self.intent.installEventFilter(self)
        lay.addWidget(self.intent)
        chips = FlowLayout(spacing=5)
        self.chips: List[LayoutButton] = []
        for r in recents:
            b = self._chip(basename(r), tildify(r, info.get("home")))
            b.clicked.connect(lambda _c=False, d=r: self._set_cwd(d))
            b.setProperty("dir", r)
            chips.addWidget(b)
            self.chips.append(b)
        self.other = self._chip("Other…", "")
        self.other.clicked.connect(self._pick)
        chips.addWidget(self.other)
        chips_host = QWidget()
        chips_host.setLayout(chips)
        lay.addWidget(chips_host)
        opts = QHBoxLayout()
        opts.setSpacing(10)
        self.worktree = Switch(True, "Worktree")
        opts.addWidget(self.worktree)
        self.worktree_lb = _lbl("Worktree", "muted", "small")
        opts.addWidget(self.worktree_lb)
        opts.addStretch(1)
        self.more = IconButton("sliders-horizontal", "More options (permission mode, effort, base branch)", 13)
        self.more.clicked.connect(
            lambda: store.set_dialog("newSession", {"cwd": self.cwd, "prompt": self.intent.toPlainText()})
        )
        opts.addWidget(self.more)
        lay.addLayout(opts)
        # the model on its own row: a lane is too narrow for it beside the switch
        self.model = ModelSelect(str(config.get("defaultModel") or ""))
        lay.addWidget(self.model)
        self.error = _lbl("", "err", "small", wrap=True)
        self.error.hide()
        lay.addWidget(self.error)
        acts = FlowLayout(spacing=6)
        self.start_btn = ActionButton("Hand to Claude", "Ctrl ↵", "primary")
        self.start_btn.clicked.connect(lambda: self.submit(True))
        acts.addWidget(self.start_btn)
        self.keep_btn = ActionButton("Keep in backlog", "↵")
        self.keep_btn.clicked.connect(lambda: self.submit(False))
        acts.addWidget(self.keep_btn)
        acts.addWidget(button("Cancel", kind="ghost", size="sm", on_click=lambda: self._on_done(None)))
        acts_host = QWidget()
        acts_host.setLayout(acts)
        lay.addWidget(acts_host)
        self._set_cwd(self.cwd)
        QTimer.singleShot(0, self.title.setFocus)

    def _chip(self, text: str, tip: str) -> LayoutButton:
        b = LayoutButton()
        b.setObjectName("bdchip")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setAccessibleName(text)
        b.setAutoDefault(False)
        if tip:
            b.setToolTip(tip)
        hl = QHBoxLayout(b)
        hl.setContentsMargins(7, 0, 7, 0)
        lb = QLabel(text)
        lb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lb.setFont(theme.font_ui(theme.FS_MICRO))
        hl.addWidget(lb)
        b.label = lb  # type: ignore[attr-defined]
        b.setFixedHeight(18)
        self._chip_look(b, False)
        return b

    @staticmethod
    def _chip_look(b: LayoutButton, on: bool) -> None:
        """``.bd-new-chip`` and its ``.on`` state: a quiet fill, the accent wash when it is the chosen folder."""
        if on:
            b.set_look(bg="accent_wash/raised", hover="accent_wash/raised", radius=9)
            theme.tint(b.label, fg="accent_text")  # type: ignore[attr-defined]
        else:
            b.set_look(bg="fill_1/raised", hover="fill_2/raised", radius=9)
            theme.tint(b.label, fg="text_2")  # type: ignore[attr-defined]

    def _set_cwd(self, d: str) -> None:
        self.cwd = d
        for b in self.chips:
            on = b.property("dir") == d
            b.setProperty("on", "true" if on else "false")
            self._chip_look(b, on)
        in_recents = any(b.property("dir") == d for b in self.chips)
        self.other.label.setText("Other…" if in_recents else (basename(d) or "Folder…"))  # type: ignore[attr-defined]
        self.other.setToolTip(tildify(d, (store.info or {}).get("home")))
        # a worktree only makes sense in a repository; the switch follows the folder
        if not d:
            return

        def got(g: Any) -> None:
            if not isinstance(g, dict) or self.cwd != d:
                return
            self._git = g
            self.worktree.setChecked(bool(g.get("isRepo")))
            self.worktree.setEnabled(bool(g.get("isRepo")))
            self.worktree_lb.setToolTip(
                "Claude works on its own branch in a separate checkout" if g.get("isRepo") else "Not a git repository"
            )

        store.call("git.info", {"cwd": d}, quiet=True).then(got).catch(lambda _e: None)

    def _pick(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        d = QFileDialog.getExistingDirectory(self, "Folder", self.cwd)
        if d:
            self._set_cwd(d.replace("\\", "/"))

    def submit(self, start: bool) -> None:
        if self._busy:
            return
        title = self.title.text().strip()
        if not title:
            self._show_error("Give the card a title.")
            self.title.setFocus()
            return
        intent = self.intent.toPlainText().strip() or title  # a title alone is a one-line intent
        self._busy = "start" if start else "create"
        self.error.hide()
        for b in (self.start_btn, self.keep_btn):
            b.setEnabled(False)
        use_wt = self.worktree.isChecked() and bool((self._git or {}).get("isRepo"))

        def made(sid: str) -> None:
            if start:
                store.start_card(sid).then(lambda _r: self._on_done(sid)).catch(self._fail)
            else:
                self._on_done(sid)

        store.create_session(
            self.cwd,
            worktree={"name": title[:40]} if use_wt else None,
            model=self.model.value or None,
            title=title,
            intent=intent,
            open=False,
            made_cb=made,
        ).catch(self._fail)

    def _fail(self, e: Any) -> None:
        from ..lib.fmt import err_text

        self._busy = ""
        for b in (self.start_btn, self.keep_btn):
            b.setEnabled(True)
        self._show_error(err_text(e))

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.show()

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:
        if e.type() == QEvent.Type.KeyPress:
            k = e.key()
            ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
            if k == Qt.Key.Key_Escape:
                self._on_done(None)
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and ctrl:
                self.submit(True)
                return True
            if (
                k in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and obj is self.title
                and not e.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ):
                self.submit(False)
                return True
        return super().eventFilter(obj, e)


# ---------------------------------------------------------------- the page


class _KeyWatch(QObject):
    """An app-wide event filter owned by the page: it is removed with its owner."""

    def __init__(self, page: Board) -> None:
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


class Board(QWidget):
    state_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.state: Optional[Json] = None
        self.schedules: List[Json] = []
        self.sel: Optional[str] = _remembered["id"]
        self.mode: Optional[str] = None
        self.creating = False
        self.cards: Dict[str, BoardCard] = {}
        self.lanes: Dict[str, LaneWidget] = {}
        self.lanes_data: Dict[str, List[Json]] = {k: [] for k in ("backlog", "agent", "wait", "me", "done")}
        self.titles: Dict[str, str] = {}
        self.caps: Json = {"ci": False}
        self.new_card: Optional[NewCard] = None
        self.now = now_ms()
        self._alive = {"on": True}
        self.destroyed.connect(lambda: self._alive.__setitem__("on", False))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # the strip
        strip = QWidget()
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(20, 14, 20, 8)
        sl.setSpacing(14)
        self.side_btn = IconButton("panel-left-open", "Show sidebar (Ctrl+B)", 16)
        self.side_btn.clicked.connect(lambda: store.set_ui(sidebar=True))
        sl.addWidget(self.side_btn)
        self.date = QLabel()
        self.date.setProperty("fs", "title")
        sl.addWidget(self.date)
        self.facts: List[QLabel] = []
        for _ in range(5):
            lb = QLabel()
            lb.setTextFormat(Qt.TextFormat.RichText)
            sl.addWidget(lb)
            self.facts.append(lb)
        sl.addStretch(1)
        self.limit = QLabel()
        self.limit.setTextFormat(Qt.TextFormat.RichText)
        self.limit.setToolTip(
            "Sessions Claude works on at once from the board (maxConcurrent in the config; 0 for no limit)"
        )
        sl.addWidget(self.limit)
        root.addWidget(strip)
        # the andon
        self.andon = LayoutButton()
        self.andon.set_look(bg="err_wash/canvas", line="err_wash/err_wash/canvas", radius=theme.R3)
        self.andon.setCursor(Qt.CursorShape.PointingHandCursor)
        self.andon.setAccessibleName("Stopped")
        al = QHBoxLayout(self.andon)
        al.setContentsMargins(12, 7, 12, 7)
        al.setSpacing(10)
        self.andon_ico = QLabel()
        self.andon_ico.setFixedSize(14, 14)
        al.addWidget(self.andon_ico)
        self.andon_b = QLabel("Stopped")
        self.andon_b.setFont(theme.font_ui(theme.FS_SMALL, 600))
        theme.tint(self.andon_b, fg="err")
        al.addWidget(self.andon_b)
        self.andon_text = ElideLabel()
        self.andon_text.setFont(theme.font_ui(theme.FS_SMALL))
        theme.tint(self.andon_text, fg="text_2")
        al.addWidget(self.andon_text, 1)
        self.andon_go = QLabel("Look →")
        self.andon_go.setFont(theme.font_ui(theme.FS_SMALL))
        theme.tint(self.andon_go, fg="err")
        al.addWidget(self.andon_go)
        for w in (self.andon_ico, self.andon_b, self.andon_text, self.andon_go):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.andon.clicked.connect(self._andon_click)
        self.andon.hide()
        andon_wrap = QWidget()
        awl = QHBoxLayout(andon_wrap)
        awl.setContentsMargins(20, 0, 20, 4)
        awl.addWidget(self.andon)
        root.addWidget(andon_wrap)
        # the body: lanes (scrolling sideways) and the panel
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lanes_host = QWidget()
        ll = QHBoxLayout(lanes_host)
        ll.setContentsMargins(20, 8, 20, 44)
        ll.setSpacing(12)
        for i, spec in enumerate(LANES):
            lane = LaneWidget(spec, i)
            self.lanes[lane.key] = lane
            ll.addWidget(lane, 1, Qt.AlignmentFlag.AlignTop)
        self.add_btn = IconButton("plus", "New card (n)", 13)
        self.add_btn.clicked.connect(lambda: self.set_creating(True))
        self.lanes["backlog"].extra.addWidget(self.add_btn)
        self.sched_btn = IconButton("calendar-clock", "Schedules: recurring work that lands here before each run", 13)
        self.sched_btn.clicked.connect(self._schedule_menu)
        self.lanes["wait"].extra.addWidget(self.sched_btn)
        self.scroll.setWidget(lanes_host)
        body.addWidget(self.scroll, 1)
        self.panel = Panel(self)
        self.panel.hide()
        body.addWidget(self.panel)
        root.addLayout(body, 1)
        # the key legend
        self.keys = QWidget()
        kl = QHBoxLayout(self.keys)
        kl.setContentsMargins(16, 6, 16, 6)
        kl.setSpacing(14)
        kl.addStretch(1)
        for caps, text in (
            (["h", "l"], "lanes"),
            (["j", "k"], "cards"),
            (["1", "5"], "jump"),
            (["Enter"], "main action"),
            (["e"], "approve"),
            (["r"], "send back"),
            (["w"], "wait / set aside"),
            (["s"], "stop"),
            (["n"], "new"),
        ):
            item = QHBoxLayout()
            item.setSpacing(4)
            for i, c in enumerate(caps):
                if text == "jump" and i == 1:
                    item.addWidget(_lbl("–", "faint", "micro"))
                item.addWidget(Kbd(c))
            item.addWidget(_lbl(text, "faint", "micro"))
            kl.addLayout(item)
        kl.addStretch(1)
        root.addWidget(self.keys)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

        # live state: the store for sessions and runtimes, board.state for the rest, a clock for elapsed times
        self._keys = _KeyWatch(self)
        self._timer = QTimer(self)
        self._timer.setInterval(BOARD_EVERY_MS)
        self._timer.timeout.connect(self.load_state)
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self.refresh)
        bind(store.sessions_changed, self, lambda _s: (self.load_state(), self.refresh()))
        bind(store.rt_changed, self, lambda _sid: self.refresh())
        bind(store.sched_changed, self, lambda _r: self.load_state())
        bind(store.connected_changed, self, lambda on: on and self.load_state())
        self.load_state()
        self.refresh()

    # ---- lifecycle
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._timer.start()
        self._clock.start()
        self._keys.watch(True)
        self.load_state()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._timer.stop()
        self._clock.stop()
        self._keys.watch(False)

    def _paint(self) -> None:
        t = theme.tokens()
        self.andon_ico.setPixmap(icons.pixmap("circle-alert", t.err, 14))
        self.refresh()

    # ---- data
    def load_state(self) -> None:
        if not store.connected:
            return
        alive = self._alive

        def got(b: Any) -> None:
            if not alive["on"] or not isinstance(b, dict):
                return
            self.state = b
            self.caps = b.get("caps") or {"ci": False}
            for sid, st in (b.get("live") or {}).items():
                if isinstance(st, dict):
                    r = store.rt.setdefault(sid, Runtime())
                    r.st = {**r.st, **st}
            self.state_changed.emit(b)
            self.refresh()

        def got_schedules(r: Any) -> None:
            if alive["on"] and isinstance(r, list):
                self.schedules = r
                self.refresh()

        store.board_state().then(got).catch(lambda _e: None)
        store.call("schedules.list", {}, quiet=True).then(got_schedules).catch(lambda _e: None)

    def rt_of(self, sid: str) -> Optional[Json]:
        r = store.rt.get(sid)
        return r.st if r is not None and r.st else None

    def all_cards(self) -> List[Json]:
        return list(store.sessions) + [schedule_card(r) for r in ((self.state or {}).get("scheduled") or [])]

    def refresh(self) -> None:
        self.now = now_ms()
        now = self.now
        lanes: Dict[str, List[Json]] = {k: [] for k in ("backlog", "agent", "wait", "me", "done")}
        states: Dict[str, str] = {}
        rts: Dict[str, Optional[Json]] = {}
        for s in self.all_cards():
            sid = str(s.get("id"))
            rt = self.rt_of(sid)
            lane = lane_of(s, rt, now)
            if lane:
                lanes[lane].append(s)
                states[sid] = card_state(s, rt, now) or ""
                rts[sid] = rt
        lanes["me"].sort(
            key=lambda s: (0 if states[str(s["id"])] == "prompt" else 1, -float(s.get("lastModified") or 0))
        )
        lanes["wait"].sort(
            key=lambda s: (
                (s.get("wait") or {}).get("at") if (s.get("wait") or {}).get("kind") == "time" else float("inf"),
                -float(s.get("lastModified") or 0),
            )
        )
        for k in ("backlog", "agent", "done"):
            lanes[k].sort(key=lambda s: -float(s.get("lastModified") or 0))
        self.lanes_data = lanes
        self.titles = {str(s.get("id")): str(s.get("title") or "") for s in store.sessions}
        st = self.state or {}
        limit = st.get("limit") or {}
        running = int(
            limit.get("running")
            if limit.get("running") is not None
            else sum(1 for s in lanes["agent"] if s.get("status") in ("running", "starting"))
        )
        mx = int(limit.get("max") or 0)
        t = theme.tokens()

        # the strip
        self.date.setText(datetime.fromtimestamp(now / 1000).strftime("%A, %B %d").replace(" 0", " "))
        today = st.get("today") or {}
        facts = [
            (len(lanes["me"]), "needs you" if len(lanes["me"]) == 1 else "need you", True),
            (len(lanes["agent"]), "with Claude", False),
            (len(lanes["wait"]), "waiting", False),
            (int(today.get("done") if today.get("done") is not None else len(lanes["done"])), "done today", False),
            (cost(float(today.get("cost") or 0)), "spent today", False),
        ]
        for lb, (n, text, hot) in zip(self.facts, facts):
            color = t.accent_text if hot and n else t.text
            lb.setText(
                f'<span style="color:{color}; font-weight:500">{n}</span> <span style="color:{t.text_2}">{text}</span>'
            )
            lb.setStyleSheet(f"font-size: {theme.FS_SMALL}px;")
        self.limit.setVisible(mx > 0)
        self.limit.setText(
            f'<span style="color:{t.text_2}">running</span> <span style="color:{t.warn if running >= mx else t.text}; font-weight:500">{running}</span> <span style="color:{t.text_2}">/ {mx}</span>'
        )
        self.limit.setStyleSheet(f"font-size: {theme.FS_SMALL}px;")

        # the andon
        stuck = [s for s in lanes["agent"] if states[str(s["id"])] == "stuck"]
        self.andon.setVisible(bool(stuck))
        if stuck:
            more = f" and {len(stuck) - 1} more" if len(stuck) > 1 else ""
            self.andon_text.setText(f"{stuck[0].get('title')}{more} — Claude could not go on by itself")
            self.andon.setProperty("sid", stuck[0]["id"])

        # the lanes
        for key, lane in self.lanes.items():
            widgets: List[QWidget] = []
            if key == "backlog" and self.creating:
                if self.new_card is None:
                    self.new_card = NewCard(self._new_done)
                widgets.append(self.new_card)
            for s in lanes[key]:
                sid = str(s["id"])
                card = self.cards.get(sid)
                if card is None:
                    card = BoardCard(sid)
                    card.selected.connect(self.select)
                    card.opened.connect(lambda sid=sid: self.act(sid, "open"))
                    self.cards[sid] = card
                card.update_from(s, rts[sid], states[sid], now, self.titles)
                card.set_selected(sid == self.sel)
                widgets.append(card)
            lane.set_cards(widgets, hide_empty=key == "backlog" and self.creating)
            if key == "agent" and mx > 0:
                lane.set_count(f"{running}/{mx}", running >= mx)
            else:
                lane.set_count(str(len(lanes[key])))
        shown = {str(s["id"]) for lst in lanes.values() for s in lst}
        for sid in [c for c in self.cards if c not in shown]:
            self.cards.pop(sid).deleteLater()

        # the panel
        sel = self.sel
        if sel and sel not in shown:
            self.select(None)
            return
        if sel:
            s = next(x for lst in lanes.values() for x in lst if str(x["id"]) == sel)
            self.panel.show_card(s, rts[sel], states[sel], self.mode, now)
            self.panel.show()
        else:
            self.panel.hide()

    # ---- selection, modes, actions
    def select(self, sid: Optional[str]) -> None:
        _remembered["id"] = sid
        self.sel = sid
        self.mode = None
        self.panel.editing = False
        self.refresh()
        if sid and sid in self.cards:
            self.scroll.ensureWidgetVisible(self.cards[sid])

    def set_mode(self, mode: Optional[str]) -> None:
        self.mode = mode
        self.refresh()

    def set_creating(self, on: bool) -> None:
        self.creating = on
        if not on and self.new_card is not None:
            self.new_card.hide()
            self.new_card.setParent(None)
            self.new_card.deleteLater()
            self.new_card = None
        self.refresh()

    def _new_done(self, sid: Optional[str]) -> None:
        self.set_creating(False)
        if sid:
            self.select(sid)

    def _andon_click(self) -> None:
        sid = self.andon.property("sid")
        if sid:
            self.select(str(sid))

    def decide(self, sid: str, kind: str, note: str = "") -> None:
        def done(_r: Any) -> None:
            self.set_mode(None)

        store.decide_card(sid, kind, note or None).then(done).catch(lambda _e: self.refresh())

    def _next_needs(self, sid: str) -> None:
        me = [s for s in self.lanes_data["me"] if str(s["id"]) != sid]
        self.select(str(me[0]["id"]) if me else None)

    def act(self, sid: str, action: str, payload: Optional[Json] = None) -> None:
        """What a button or key does to a card (``act`` in Board.tsx)."""
        if is_schedule(sid):
            real = sid[len(SCHEDULE_PREFIX) :]
            if action in ("schedule-edit", "open"):
                store.set_dialog("schedule", {"id": real})
            elif action == "schedule-run":
                store.run_schedule(real).then(lambda _r: store.toast("Running now", "info"))
            return
        s = store.session(sid)
        if s is None:
            return
        if action == "open":
            rt = self.rt_of(sid)
            if card_state(s, rt, self.now) == "review":
                store.set_ui(return_to="board", panel=True, panel_tab="changes")
            else:
                store.set_ui(return_to="board")
            store.open_session(sid)
        elif action in ("approve", "park"):
            store.decide_card(sid, action).then(lambda _r: self._next_needs(sid))
        elif action == "reject":
            self.set_mode("reject")
        elif action == "send":
            self.set_mode("send")
        elif action == "wait":
            self.set_mode("wait")
        elif action == "set-wait":

            def waited(_r: Any) -> None:
                self.set_mode(None)
                if payload:
                    store.toast("Waiting; Claude is woken when the condition is met", "info")
                    self._next_needs(sid)

            store.set_wait(sid, payload).then(waited)
        elif action == "unwait":
            store.set_wait(sid, None)
        elif action == "wake":
            store.decide_card(sid, "wake")
        elif action == "stop":
            store.decide_card(sid, "stop")
        elif action == "start":
            store.start_card(sid)

    def _schedule_menu(self) -> None:
        items: List[Json] = [{"heading": "Schedules" if self.schedules else "No schedules yet"}]
        for s in self.schedules:
            nxt = s.get("nextRunAt")
            hint = (
                f"{s.get('cadenceLabel')}{f' · next {time_ago(float(nxt), self.now)}' if nxt else ''}"
                if s.get("enabled")
                else f"{s.get('cadenceLabel')} · paused"
            )
            items.append(
                {
                    "label": str(s.get("name") or ""),
                    "hint": hint,
                    "checked": bool(s.get("enabled")),
                    "on_select": lambda sid=s.get("id"): store.set_dialog("schedule", {"id": sid}),
                }
            )
        items.append({"separator": True})
        items.append(
            {"label": "New schedule…", "icon": "calendar-clock", "on_select": lambda: store.set_dialog("schedule", {})}
        )
        show_menu(items, self.sched_btn, width=300, align_right=True)

    # ---- keyboard
    def key_event(self, e: QEvent) -> bool:
        t = e.type()
        if t not in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
            return False
        if not self.isVisible() or store.page != "board" or any(store.dialogs.values()) or store.lightbox:
            return False
        if e.modifiers() & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier
        ):
            return False
        if typing_elsewhere(QApplication.focusWidget()):
            return False
        key, text = e.key(), e.text()
        if t == QEvent.Type.ShortcutOverride:
            if key == Qt.Key.Key_Escape and (self.mode or self.sel or self.creating):
                e.accept()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.sel:
                e.accept()
                return True
            return False
        return self.handle_key(key, text)

    def handle_key(self, key: int, text: str) -> bool:
        """A key press while the board is up (the window's keydown handler in Board.tsx)."""
        sel = self.sel
        state = ""
        rt: Optional[Json] = None
        if sel:
            s = next((x for lst in self.lanes_data.values() for x in lst if str(x["id"]) == sel), None)
            rt = self.rt_of(sel)
            state = card_state(s, rt, self.now) or "" if s else ""
        direction = (
            "down"
            if text == "j" or key == Qt.Key.Key_Down
            else "up"
            if text == "k" or key == Qt.Key.Key_Up
            else "right"
            if text == "l" or key == Qt.Key.Key_Right
            else "left"
            if text == "h" or key == Qt.Key.Key_Left
            else None
        )
        if self.mode == "wait" and text in ("1", "2", "3") and self.panel.wait_picker is not None:
            return self.panel.wait_picker.pick_key(text)
        if state == "prompt" and text in ("1", "2", "3") and self.panel.perm is not None:
            return self.panel.perm.handle_key(text)
        if direction:
            self.select(step(self.lanes_data, sel, direction))
        elif text in ("1", "2", "3", "4", "5"):
            lane = self.lanes_data[str(LANES[int(text) - 1]["key"])]
            if not lane:
                return False
            self.select(str(lane[0]["id"]))
        elif text == "n":
            self.set_creating(True)
        elif key == Qt.Key.Key_Escape:
            if self.mode:
                self.set_mode(None)
            elif self.creating:
                self.set_creating(False)
            elif sel:
                self.select(None)
            else:
                return False
        elif sel and state:
            acts = schedule_actions() if is_schedule(sel) else actions_for(state, rt)
            hit = (
                acts[0]
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                else next((a for a in acts if a["key"] == text), None)
            )
            if hit is None:
                return False
            self.act(sel, str(hit["id"]))
        else:
            return False
        return True
