"""The board: every job at a glance. The control room's discipline: sections appear only when they
have something, what needs you comes first, and a request can be answered on the card (once, or once
for every repository that asks the same thing), so most jobs are never opened."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ... import theme
from ...lib.fmt import cost as fmt_cost
from ...lib.fmt import duration, now_ms, plural
from ...store import store
from ..dialogs.parts import Card, Text, half_hairline
from ..overview import EmptyNote, SectionHead
from ..ui.bits import IconButton, LayoutButton, button
from ..ui.signals import bind
from .cards import CanaryRow, RequestRow
from .common import SegmentBar, counts_text, elapsed_text, group_requests, job_cost_text, job_state_text

Json = Dict[str, Any]


class JobCard(Card):
    """One job (``.card``): name, scope, the segmented bar, counts, cost; then what needs a hand, in
    wells, the card tinted and railed like a session that waits in the control room."""

    def __init__(self, jid: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(12, parent)
        self.jid = jid
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sig: Any = None
        self._keys = False
        self._lay = lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 11, 14, 11)
        lay.setSpacing(8)
        line = QHBoxLayout()
        line.setSpacing(10)
        self.name = Text("", theme.FS_UI, 600, "text", wrap=False)
        line.addWidget(self.name)
        self.scope = Text("", theme.FS_SMALL, 400, "text_3", wrap=False)
        line.addWidget(self.scope)
        self.bar = SegmentBar()
        self.bar.setMaximumWidth(220)
        line.addWidget(self.bar, 1)
        self.counts = Text("", theme.FS_SMALL, 400, "text_2", wrap=False)
        line.addWidget(self.counts)
        self.cost = Text("", theme.FS_SMALL, 400, "text_3", wrap=False)
        line.addWidget(self.cost)
        lay.addLayout(line)
        self.extra = QVBoxLayout()
        self.extra.setSpacing(6)
        lay.addLayout(self.extra)

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self._tone != "needs":
            return
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.accent))
        p.drawRoundedRect(0, 12, 3, self.height() - 24, 1.5, 1.5)

    def update_from(self, job: Json, runs: List[Json], now: float, detail: bool) -> None:
        if detail != (self._tone == "needs"):
            self.set_tone("needs" if detail else "")
            self._lay.setContentsMargins(19 if detail else 14, 11, 14, 11)
        self.name.setText(str(job.get("name") or "Untitled job"))
        state = job_state_text(job, now)
        self.scope.setText(f"{plural(int(job.get('total') or len(job.get('targets') or [])), 'repo')} · {state}")
        self.bar.set_counts(job.get("counts") or {})
        self.counts.setText(counts_text(job))
        parts = [job_cost_text(job)] if float(job.get("cost") or 0) else []
        if job.get("state") in ("finished", "stopped"):
            parts.append(elapsed_text(job, now))
        self.cost.setText(" · ".join(p for p in parts if p))
        # the rows under the line: requests grouped by key, and the canary waiting to be read
        sig = (
            [(e["key"], tuple(e["names"])) for e in group_requests(runs)] if detail else [],
            bool(job.get("canaryWaiting")) if detail else False,
            (job.get("forecast") or {}).get("cost"),
        )
        if sig == self._sig:
            return
        self._sig = sig
        while self.extra.count():
            w = self.extra.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        if not detail:
            return
        for e in group_requests(runs)[:4]:
            self.extra.addWidget(RequestRow(self.jid, e))
        if job.get("canaryWaiting"):
            self.extra.addWidget(CanaryRow(job))
        self._apply_keys()

    def set_keys(self, on: bool) -> None:
        if on != self._keys:
            self._keys = on
            self._apply_keys()

    def _apply_keys(self) -> None:
        first = True
        for i in range(self.extra.count()):
            w = self.extra.itemAt(i).widget()
            if isinstance(w, RequestRow):
                w.set_keys(self._keys and first)
                first = False

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            store.open_job(self.jid)
        super().mouseReleaseEvent(e)


class FinishedLine(LayoutButton):
    """A finished job as one quiet row (``.ovr``, the control room's finished rows): name, scope,
    counts, cost, time; a fill under the pointer and while focused."""

    def __init__(self, jid: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.jid = jid
        self._focused = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Open the job")
        self.setAutoDefault(False)
        self.clicked.connect(lambda: store.open_job(self.jid))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 7, 12, 7)
        lay.setSpacing(12)
        self.name = Text("", theme.FS_SMALL, 500, "text", wrap=False)
        lay.addWidget(self.name, 1)
        self.counts = Text("", theme.FS_SMALL, 400, "text_2", wrap=False)
        lay.addWidget(self.counts)
        self.cost = Text("", theme.FS_SMALL, 400, "text_3", wrap=False, mono=True)
        lay.addWidget(self.cost)
        self.time = Text("", theme.FS_SMALL, 400, "text_3", wrap=False, mono=True)
        lay.addWidget(self.time)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def set_focused(self, on: bool) -> None:
        if on != self._focused:
            self._focused = on
            self._paint()

    def _paint(self) -> None:
        self.set_look(hover="fill_1/raised", on="fill_1/raised", radius=8)
        self.look_active = self._focused

    def update_from(self, job: Json, now: float) -> None:
        self.name.setText(f"{job.get('name')} · {plural(int(job.get('total') or 0), 'repo')}")
        c = job.get("counts") or {}
        bits = [f"{int(c.get('done') or 0)} done"]
        failed = int(c.get("failed") or 0) + int(c.get("stopped") or 0)
        if failed:
            bits.append(f"{failed} failed")
        prs = sum(1 for r in store.runs_of(str(job.get("id"))) if r.get("pr"))
        if prs:
            bits.append(f"{prs} PRs")
        if job.get("state") == "stopped":
            bits.append("stopped")
        self.counts.setText(" · ".join(bits))
        self.cost.setText(fmt_cost(float(job.get("cost") or 0)))
        started, ended = float(job.get("startedAt") or 0), float(job.get("finishedAt") or job.get("updatedAt") or 0)
        self.time.setText(duration(ended - started) if started and ended else "")


class Board(QWidget):
    """Every job: needs a hand, running, in review, finished."""

    SECTIONS = ("needs", "running", "review", "drafts", "finished")

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.now = now_ms()
        self.cards: Dict[str, JobCard] = {}
        self.lines: Dict[str, FinishedLine] = {}
        self._focus = -1
        self.ordered: List[str] = []
        self._order: Dict[str, List[str]] = {}  # each section's cards, in order, as laid out
        self._fin_order: List[str] = []
        self._stale = False
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
        self.title = QLabel("Dispatch")
        self.title.setProperty("fs", "title")
        hl.addWidget(self.title)
        self.summary = QLabel()
        theme.tint(self.summary, fg="text_3")
        self.summary.setProperty("fs", "small")
        hl.addWidget(self.summary)
        hl.addStretch(1)
        self.new_btn = button(
            "New job", kind="primary", size="sm", icon="plus", on_click=lambda: store.show_dispatch("new")
        )
        self.new_btn.setIconSize(QSize(13, 13))
        self.new_btn.setAccessibleName("New job")
        hl.addWidget(self.new_btn)
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
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(20, 10, 20, 48)
        bl.setSpacing(26)
        hh.addWidget(self.body)
        hh.addStretch(1)
        self.scroll.setWidget(host)
        self.scroll.viewport().installEventFilter(self)
        root.addWidget(self.scroll, 1)

        self.sections: Dict[str, QWidget] = {}
        self.heads: Dict[str, SectionHead] = {}
        self.lists: Dict[str, QVBoxLayout] = {}
        for key, title, glyph in (
            ("needs", "Needs a hand", True),
            ("running", "Running", False),
            ("review", "In review", False),
            ("drafts", "Drafts", False),
            ("finished", "Finished", False),
        ):
            sec = QWidget()
            sl = QVBoxLayout(sec)
            sl.setContentsMargins(0, 0, 0, 0)
            sl.setSpacing(10)
            h = SectionHead(title, glyph=glyph)
            sl.addWidget(h)
            lst = QVBoxLayout()
            lst.setSpacing(10)
            sl.addLayout(lst)
            sec.hide()
            bl.addWidget(sec)
            self.sections[key], self.heads[key], self.lists[key] = sec, h, lst
        self.finished_card = Card(12)
        self.finished_lay = QVBoxLayout(self.finished_card)
        self.finished_lay.setContentsMargins(4, 4, 4, 4)
        self.finished_lay.setSpacing(0)
        self.lists["finished"].addWidget(self.finished_card)
        self.empty = EmptyNote(
            "No jobs yet. A job runs one brief across many repositories: each in a worktree of its own, "
            "a canary first, one answer for every repository that asks the same thing, one review at the end.",
            mark=True,
        )
        bl.addWidget(self.empty)
        bl.addStretch(1)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick)
        bind(store.dispatch_changed, self, lambda _d: self._on_change())
        bind(store.ui_changed, self, lambda _u: self.side_btn.setVisible(not store.ui.sidebar))
        bind(store.connected_changed, self, lambda on: on and store.load_jobs())
        self.side_btn.setVisible(not store.ui.sidebar)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.refresh()

    # ---- lifecycle
    def _on_change(self) -> None:
        """A job changed: the board refreshes when it shows, else when it shows next."""
        if self.isVisible():
            self.refresh()
        else:
            self._stale = True

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._tick_timer.start()
        if self._stale:
            self.refresh()
        store.load_jobs()

    def hideEvent(self, e) -> None:
        self._tick_timer.stop()
        super().hideEvent(e)

    def eventFilter(self, obj, e) -> bool:
        if obj is self.scroll.viewport() and e.type() == QEvent.Type.Resize:
            vw = self.scroll.viewport().width()
            self.body.setFixedWidth(max(320, min(theme.column_width(vw), vw)))
        return False

    # ---- data
    def buckets(self) -> Dict[str, List[Json]]:
        out: Dict[str, List[Json]] = {k: [] for k in self.SECTIONS}
        jobs = sorted(store.jobs.values(), key=lambda j: -float(j.get("updatedAt") or 0))
        for j in jobs:
            st = str(j.get("state") or "")
            if st in ("running", "reviewing") and (int(j.get("needsAction") or 0) or j.get("canaryWaiting")):
                out["needs"].append(j)
            elif st == "running":
                out["running"].append(j)
            elif st == "reviewing":
                out["review"].append(j)
            elif st in ("draft", "planned"):
                out["drafts"].append(j)
            else:
                out["finished"].append(j)
        out["finished"] = out["finished"][:20]
        return out

    def refresh(self) -> None:
        """Bring the board in line with the jobs. A card is re-laid only when its section's
        membership or order changed; otherwise its texts are set in place, so a run's progress
        touches one card and no layout."""
        self.now = now_ms()
        self._stale = False
        b = self.buckets()
        self.ordered = [str(j.get("id")) for k in self.SECTIONS for j in b[k]]
        focus_id = self.ordered[self._focus] if 0 <= self._focus < len(self.ordered) else None
        running = len(b["running"]) + len(b["needs"])
        self.summary.setText(
            (f"{plural(len(store.jobs), 'job')}" if store.jobs else "")
            + (f" · {len(b['needs'])} need a hand" if b["needs"] else "")
            + (f" · {running} running" if running and not b["needs"] else "")
        )
        keep = set()
        for key in ("needs", "running", "review", "drafts"):
            jobs = b[key]
            ids = [str(j.get("id")) for j in jobs]
            lay = self.lists[key]
            if ids != self._order.get(key):
                while lay.count():
                    lay.takeAt(0)
                for jid in ids:
                    card = self.cards.get(jid)
                    if card is None:
                        card = self.cards[jid] = JobCard(jid)
                    lay.addWidget(card)
                self._order[key] = ids
            for j in jobs:
                jid = str(j.get("id"))
                keep.add(jid)
                card = self.cards[jid]
                card.update_from(j, store.runs_of(jid), self.now, detail=key == "needs")
                card.set_focused(jid == focus_id)
                card.set_keys(jid == focus_id)
            self.heads[key].count.set_count(len(jobs))
            self.sections[key].setVisible(bool(jobs))
        for jid in list(self.cards):
            if jid not in keep:
                self.cards.pop(jid).deleteLater()
        # finished: quiet rows in one card
        fin_ids = [str(j.get("id")) for j in b["finished"]]
        if fin_ids != self._fin_order:
            while self.finished_lay.count():
                w = self.finished_lay.takeAt(0).widget()
                if isinstance(w, FinishedLine):
                    w.hide()
                elif w is not None:
                    w.deleteLater()
            for i, jid in enumerate(fin_ids):
                if i:
                    self.finished_lay.addWidget(half_hairline("raised"))
                line = self.lines.get(jid)
                if line is None:
                    line = self.lines[jid] = FinishedLine(jid)
                line.show()
                self.finished_lay.addWidget(line)
            self._fin_order = fin_ids
        for j in b["finished"]:
            jid = str(j.get("id"))
            self.lines[jid].update_from(j, self.now)
            self.lines[jid].set_focused(jid == focus_id)
        for jid in list(self.lines):
            if jid not in fin_ids:
                self.lines.pop(jid).deleteLater()
        self.heads["finished"].count.set_count(len(b["finished"]))
        self.sections["finished"].setVisible(bool(b["finished"]))
        self.empty.setVisible(store.jobs_loaded and not store.jobs)

    def _tick(self) -> None:
        self.now = now_ms()
        for key in ("needs", "running"):
            for j in self.buckets()[key]:
                card = self.cards.get(str(j.get("id")))
                if card is not None:
                    card.scope.setText(f"{plural(int(j.get('total') or 0), 'repo')} · {job_state_text(j, self.now)}")

    # ---- keys: j/k move, Enter opens, a answers the first request, A for all, d denies
    def handle_key(self, key: int, text: str) -> bool:
        if not self.ordered:
            return False
        if text == "j" or key == Qt.Key.Key_Down:
            self._focus = min(len(self.ordered) - 1, self._focus + 1)
        elif text == "k" or key == Qt.Key.Key_Up:
            self._focus = max(0, self._focus - 1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._focus >= 0:
            store.open_job(self.ordered[self._focus])
            return True
        elif text in ("a", "A", "d") and self._focus >= 0:
            card = self.cards.get(self.ordered[self._focus])
            row = (
                next(
                    (
                        card.extra.itemAt(i).widget()
                        for i in range(card.extra.count())
                        if isinstance(card.extra.itemAt(i).widget(), RequestRow)
                    ),
                    None,
                )
                if card
                else None
            )
            if isinstance(row, RequestRow):
                (row.allow_one if text == "a" else row.allow_everyone if text == "A" else row.deny_one)()
                return True
            return False
        else:
            return False
        self.refresh()
        return True

    def keyPressEvent(self, e) -> None:
        if not self.handle_key(e.key(), e.text()):
            super().keyPressEvent(e)

    @property
    def focus_index(self) -> int:
        return self._focus
