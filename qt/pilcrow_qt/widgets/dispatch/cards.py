"""What a job that needs you shows (DISPATCH.md §4.2–4.3), in the control room's grammar. A request
is a well: the tool, what it asks, who asked, and the key buttons; the canary that finished is a well
with its forecast and Continue. On the board the wells sit inside the job's card; in the job view each
is a card of its own, tinted and railed like a session that waits in the control room, with a head,
the well and the actions."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ... import theme
from ...lib.fmt import cost as fmt_cost
from ...lib.fmt import duration, plural
from ...store import store
from ..dialogs.parts import Card, Switch, Text, Well
from ..ui.bits import button
from ..ui.sections import KeyButton
from ..ui.signals import bind
from .common import asked_by, clock_mmss

Json = Dict[str, Any]


def switch_line(sw: Switch, text: str) -> QWidget:
    """A switch with its words beside it (``.dp-learn``)."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    lay.addWidget(sw)
    lay.addWidget(Text(text, theme.FS_SMALL, 400, "text_2", wrap=False))
    lay.addStretch(1)
    return box


class _Answers:
    """The ways to answer a request group, shared by the well and the card."""

    jid: str
    entry: Json
    _busy: bool

    def _first(self) -> Optional[tuple]:
        asks = self.entry.get("asks") or []
        return asks[0] if asks else None

    def _lock(self) -> None:
        self._busy = True
        for b in self.buttons():
            b.setEnabled(False)

    def buttons(self) -> List[QWidget]:
        return []

    def allow_one(self) -> None:
        first = self._first()
        if not first or self._busy:
            return
        run, p = first
        self._lock()
        store.respond(
            str(run.get("sessionId") or ""),
            str(p.get("requestId") or ""),
            {"behavior": "allow", "decisionClassification": "user_temporary"},
        )

    def allow_everyone(self) -> None:
        if self._busy:
            return
        self._lock()
        store.call("dispatch.allow", {"id": self.jid, "key": self.entry.get("key")})

    def deny_one(self) -> None:
        first = self._first()
        if not first or self._busy:
            return
        run, p = first
        self._lock()
        store.respond(
            str(run.get("sessionId") or ""),
            str(p.get("requestId") or ""),
            {"behavior": "deny", "message": "The user declined this action.", "decisionClassification": "user_reject"},
        )

    def open_session(self) -> None:
        first = self._first()
        if first and first[0].get("sessionId"):
            store.open_session(str(first[0]["sessionId"]))


def answer_buttons(owner: _Answers, n: int, interactive: bool) -> List[QWidget]:
    """Allow / Allow for all / Deny, or Open to respond for a question or a plan."""
    allow = KeyButton("Allow", "primary")
    allow.clicked.connect(owner.allow_one)
    allow_all = KeyButton(f"Allow for all{f' · {n}' if n > 1 else ''}")
    allow_all.setToolTip("Answer every run of this job that asks the same thing, now and later")
    allow_all.clicked.connect(owner.allow_everyone)
    deny = KeyButton("Deny", "ghost")
    deny.clicked.connect(owner.deny_one)
    open_btn = button("Open to respond", kind="primary", size="sm", on_click=owner.open_session)
    for b in (allow, allow_all, deny):
        b.setVisible(not interactive)
    open_btn.setVisible(interactive)
    return [allow, allow_all, deny, open_btn]


class RequestRow(Well, _Answers):
    """A request waiting on a job, as one line in a well (``.ovc-well``): the tool, what it asks, who
    asked, and the answers."""

    def __init__(self, jid: str, entry: Json, parent: Optional[QWidget] = None) -> None:
        super().__init__(8, parent)
        self.jid = jid
        self.entry = entry
        self._busy = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 8, 6)
        lay.setSpacing(10)
        label = str(entry.get("label") or "")
        tool, _sep, what = label.partition(": ")
        self.tool = Text(tool if what else label, theme.FS_SMALL, 600, "text_2", wrap=False)
        lay.addWidget(self.tool)
        if what:
            self.what = Text(what, 12, 400, "text", wrap=False, mono=True)
            lay.addWidget(self.what, 1)
        else:
            lay.addStretch(1)
        self.who = Text(asked_by(entry.get("names") or []), theme.FS_MICRO, 400, "text_3", wrap=False)
        lay.addWidget(self.who)
        self.allow, self.allow_all, self.deny, self.open = answer_buttons(
            self, len(entry.get("asks") or []), bool(entry.get("interactive"))
        )
        for b in self.buttons():
            lay.addWidget(b)

    def buttons(self) -> List[QWidget]:
        return [self.allow, self.allow_all, self.deny, self.open]

    def set_keys(self, on: bool) -> None:
        """The caps of the board's keys (a, A, d) on the focused card's first request."""
        self.allow.set_key("a" if on else None)
        self.allow_all.set_key("A" if on else None)
        self.deny.set_key("d" if on else None)


class CanaryRow(Well):
    """The canary finished, as one line in a well: read it and continue, with the forecast."""

    def __init__(self, job: Json, parent: Optional[QWidget] = None) -> None:
        super().__init__(8, parent)
        self.job = job
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 8, 6)
        lay.setSpacing(10)
        self.text = Text(f"Canary {canary_names(job)} finished.", theme.FS_SMALL, 500, "text", wrap=False)
        lay.addWidget(self.text, 1)
        self.forecast = Text(forecast_text(job), theme.FS_MICRO, 400, "text_3", wrap=False)
        self.forecast.setVisible(bool(job.get("forecast")))
        lay.addWidget(self.forecast)
        self.open = button("Open canary", kind="ghost", size="sm", on_click=lambda: open_canary(self.job))
        lay.addWidget(self.open)
        self.cont = button("Continue", kind="primary", size="sm", icon="play", on_click=self.go_on)
        lay.addWidget(self.cont)

    def go_on(self) -> None:
        self.cont.setEnabled(False)
        store.call("dispatch.continue", {"id": self.job.get("id")}).catch(lambda _e: self.cont.setEnabled(True))


def canary_names(job: Json) -> str:
    learned = [x for x in (job.get("learned") or []) if isinstance(x, dict)]
    return ", ".join(str(x.get("name")) for x in learned) or "the canary"


def forecast_text(job: Json) -> str:
    f = job.get("forecast") or {}
    if not f:
        return ""
    return (
        f"forecast {fmt_cost(float(f.get('cost') or 0))} · {int(f.get('minutes') or 0)} min "
        f"for {plural(int(f.get('runs') or 0), 'repo')}"
    )


def open_canary(job: Json) -> None:
    jid = str(job.get("id"))
    run = next((r for r in store.runs_of(jid) if r.get("wave") == "canary" and r.get("sessionId")), None)
    if run is not None:
        store.open_session(str(run["sessionId"]))
    else:
        store.open_job(jid)


class AskCard(Card):
    """A card for something that needs you (``.ovc.needs``): the tint, the rail, a head with a title and
    a line under it, a micro at the right, then whatever the subclass adds."""

    def __init__(self, title: str, sub: str, aside: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(14, parent)
        self.set_tone("needs")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(19, 13, 15, 12)
        self.lay.setSpacing(9)
        row = QHBoxLayout()
        row.setSpacing(10)
        head = QVBoxLayout()
        head.setSpacing(2)
        self.title_lb = Text(title, theme.FS_UI, 560, "text", wrap=False)
        head.addWidget(self.title_lb)
        self.sub_lb = Text(sub, theme.FS_MICRO, 400, "text_3", wrap=False)
        self.sub_lb.setVisible(bool(sub))
        head.addWidget(self.sub_lb)
        row.addLayout(head, 1)
        self.aside = Text(aside, theme.FS_MICRO, 400, "accent_text", wrap=False)
        self.aside.setVisible(bool(aside))
        row.addWidget(self.aside, 0, Qt.AlignmentFlag.AlignTop)
        self.lay.addLayout(row)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.accent))
        p.drawRoundedRect(0, 12, 3, self.height() - 24, 1.5, 1.5)


class RequestCard(AskCard, _Answers):
    """A request group in the job view: the tool as the title, who asked under it, how long it has
    waited at the right, what it asks in the well, the answers below."""

    def __init__(self, jid: str, entry: Json, now: float, parent: Optional[QWidget] = None) -> None:
        label = str(entry.get("label") or "")
        tool, _sep, what = label.partition(": ")
        super().__init__(tool if what else label, asked_by(entry.get("names") or []), "", parent)
        self.jid = jid
        self.entry = entry
        self._busy = False
        self.tick(now)
        self.tool = self.title_lb
        self.who = self.sub_lb
        self.well = Well(8)
        wl = QHBoxLayout(self.well)
        wl.setContentsMargins(10, 8, 10, 8)
        self.what = Text(what or label, 12, 400, "text", 1.5, mono=True)
        wl.addWidget(self.what, 1)
        self.lay.addWidget(self.well)
        acts = QHBoxLayout()
        acts.setSpacing(6)
        self.allow, self.allow_all, self.deny, self.open = answer_buttons(
            self, len(entry.get("asks") or []), bool(entry.get("interactive"))
        )
        for b in self.buttons():
            acts.addWidget(b)
        acts.addStretch(1)
        self.lay.addLayout(acts)

    def buttons(self) -> List[QWidget]:
        return [self.allow, self.allow_all, self.deny, self.open]

    def tick(self, now: float) -> None:
        """How long the request has waited, at the right (the job view's second ticks it)."""
        at = float(self.entry.get("at") or 0)
        waited = now - at if at else 0.0
        text = f"waiting {duration(waited)}" if waited >= 1000 else ""
        self.aside.setText(text)
        self.aside.setVisible(bool(text))


class CanaryCard(AskCard):
    """The canary finished, in the job view: its name, how it went, its summary in the well, Continue
    with the forecast, and whether the rest learn from it."""

    def __init__(self, job: Json, parent: Optional[QWidget] = None) -> None:
        self.job = job
        jid = str(job.get("id"))
        canary = [r for r in store.runs_of(jid) if r.get("wave") == "canary"]
        bits = []
        for r in canary:
            started, ended = float(r.get("startedAt") or 0), float(r.get("endedAt") or 0)
            if started and ended:
                bits.append(clock_mmss(ended - started))
            if float(r.get("cost") or 0):
                bits.append(fmt_cost(float(r.get("cost") or 0)))
            if r.get("files"):
                bits.append(plural(len(r.get("files") or []), "file"))
        super().__init__(f"Canary {canary_names(job)} finished", " · ".join(bits), "", parent)
        self.text = self.title_lb
        summary = next(
            (str(x.get("summary") or "").strip() for x in (job.get("learned") or []) if isinstance(x, dict)), ""
        )
        self.well = Well(8)
        wl = QHBoxLayout(self.well)
        wl.setContentsMargins(10, 8, 10, 8)
        self.summary = Text(summary.split("\n")[0][:300], theme.FS_SMALL, 400, "text", 1.5)
        wl.addWidget(self.summary, 1)
        self.well.setVisible(bool(summary))
        self.lay.addWidget(self.well)
        acts = QHBoxLayout()
        acts.setSpacing(6)
        self.cont = button("Continue", kind="primary", size="sm", icon="play", on_click=self.go_on)
        acts.addWidget(self.cont)
        self.open = button("Open canary", kind="ghost", size="sm", on_click=lambda: open_canary(self.job))
        acts.addWidget(self.open)
        self.forecast = Text(forecast_text(job), theme.FS_MICRO, 400, "text_3", wrap=False)
        self.forecast.setVisible(bool(job.get("forecast")))
        acts.addSpacing(4)
        acts.addWidget(self.forecast)
        acts.addStretch(1)
        self.lay.addLayout(acts)
        self.learn = Switch(bool((job.get("settings") or {}).get("learn", True)), "the rest learn from its summary")
        self.learn.toggled.connect(
            lambda on: store.call("dispatch.save", {"id": jid, "settings": {"learn": bool(on)}}, quiet=True)
        )
        self.lay.addWidget(switch_line(self.learn, "the rest learn from its summary"))

    def go_on(self) -> None:
        self.cont.setEnabled(False)
        store.call("dispatch.continue", {"id": self.job.get("id")}).catch(lambda _e: self.cont.setEnabled(True))
