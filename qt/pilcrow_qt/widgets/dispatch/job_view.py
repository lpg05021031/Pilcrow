"""A job's view: the header with its state and controls, the brief and the plan, the canary waiting to
be read, the requests waiting for a hand, one row per repository on the canvas engine, and at the foot
the review across repositories with the ways to finish."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QClipboard, QGuiApplication, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ... import theme
from ...canvas import nodes as nd
from ...canvas.blocks.base import NodeBlock
from ...canvas.blocks.fold import reconcile
from ...canvas.paint import Measurer, PaintState, rounded
from ...canvas.scene import Block, Scene, Target
from ...canvas.view import CanvasView
from ...lib.fmt import cost as fmt_cost
from ...lib.fmt import plural
from ...store import ConfirmSpec, store
from ..dialogs.parts import Switch, Text
from ..dialogs.settings_ui import text_area
from ..ui.bits import Box, IconButton, button
from ..ui.menu import show_menu
from ..ui.signals import bind
from .board import CanaryRow, RequestRow
from .common import (
    ACTIVE,
    STATE_TONE,
    STATE_WORD,
    TERMINAL,
    clock_mmss,
    elapsed_text,
    group_requests,
    job_cost_text,
    job_state_text,
    now_ms,
    paint_glyph,
)

Json = Dict[str, Any]
ROW_PAD = (10, 0, 10, 0)
ROW_H = 34
COLS = {"state": 112, "branch": 150, "elapsed": 56, "files": 92, "cost": 58}


class Cell(nd.Container):
    """A fixed-width column holding one node (right-aligned when ``right``)."""

    __slots__ = ("cw", "right")

    def __init__(self, child: nd.Node, cw: int, right: bool = False) -> None:
        super().__init__([child], 0, nd.NO_PAD)
        self.cw, self.right = cw, right

    def natural_w(self, m: Measurer) -> int:
        return self.cw

    def layout(self, x: int, y: int, w: int, m: Measurer) -> int:
        self.x, self.y, self.w = x, y, w
        c = self.children[0]
        if self.right:
            nat = min(c.natural_w(m), w)
            self.h = c.layout(x + w - nat, y, nat, m)
        else:
            self.h = c.layout(x, y, w, m)
        return self.h


class RunBlock(NodeBlock):
    """One run: glyph and state, repository, branch, elapsed, files, cost, what it does now, and the
    actions its state allows (Allow / Deny, Retry / Open, Stop)."""

    KIND = "run"
    MT = 0
    MB = 0

    def __init__(self, run: Json, view: RunList) -> None:
        self.run = run
        self.list = view
        self.now_ms = now_ms()
        super().__init__(f"run:{run.get('id')}", self._build, (0, 0, 0, 0), est_h=ROW_H)
        self.on("row:open", lambda _t, _v: self.list.open_run(self.run))
        self.on("allow", lambda _t, _v: self.list.answer(self.run, True))
        self.on("deny", lambda _t, _v: self.list.answer(self.run, False))
        self.on("retry", lambda _t, _v: self.list.retry(self.run))
        self.on("open", lambda _t, _v: self.list.open_run(self.run))
        self.on("stop", lambda _t, _v: self.list.stop(self.run))
        self.on("skip", lambda _t, _v: self.list.skip(self.run))

    def sync(self, run: Json, now: float) -> bool:
        changed = run != self.run or (run.get("state") in ACTIVE and int(now // 1000) != int(self.now_ms // 1000))
        self.run = run
        self.now_ms = now
        if changed:
            self.rebuild()
        return changed

    def _build(self, m: Measurer) -> nd.Node:
        r = self.run
        state = str(r.get("state") or "queued")
        tone = STATE_TONE.get(state, "text_4")
        glyph = nd.Painted(self._paint_glyph, 18, 18, "", "mark")
        word = nd.Text(
            STATE_WORD.get(state, state),
            theme.FS_SMALL,
            500,
            tone if not tone.startswith("text_4") else "text_3",
            wrap=False,
        )
        state_cell = Cell(nd.hstack(glyph, word, spacing=4), COLS["state"])
        name = nd.Text(str(r.get("name") or ""), theme.FS_SMALL, 500, "text", wrap=False)
        wt = r.get("worktree") or {}
        branch = Cell(
            nd.Text(str(wt.get("branch") or "—"), theme.FS_MICRO, 400, "text_3", mono=True, wrap=False), COLS["branch"]
        )
        started, ended = float(r.get("startedAt") or 0), float(r.get("endedAt") or 0)
        el = clock_mmss((ended or self.now_ms) - started) if started else "—"
        elapsed = Cell(nd.Text(el, theme.FS_MICRO, 400, "text_2", mono=True, wrap=False), COLS["elapsed"], right=True)
        add, dele = int(r.get("add") or 0), int(r.get("del") or 0)
        files = Cell(
            nd.hstack(
                nd.Text(f"+{add}", theme.FS_MICRO, 500, "ok", mono=True, wrap=False),
                nd.Text(f"−{dele}", theme.FS_MICRO, 500, "err", mono=True, wrap=False),
                spacing=5,
            )
            if (add or dele)
            else nd.Text("—", theme.FS_MICRO, 400, "text_3", wrap=False),
            COLS["files"],
            right=True,
        )
        cst = float(r.get("cost") or 0)
        cost = Cell(
            nd.Text(fmt_cost(cst) if cst else "—", theme.FS_MICRO, 400, "text_2", mono=True, wrap=False),
            COLS["cost"],
            right=True,
        )
        now_text, now_tone = self._now_text(r, state)
        now = nd.Text(now_text, theme.FS_SMALL, 400, now_tone, wrap=False)
        acts: List[nd.Node] = []
        if (
            state == "needs_action"
            and r.get("pending")
            and not any(p.get("interactive") for p in r.get("pending") or [])
        ):
            acts += [
                nd.Button("Allow", "allow", kind="primary", size="xs"),
                nd.Button("Deny", "deny", kind="ghost", size="xs"),
            ]
        elif state == "needs_action":
            acts += [nd.Button("Open to respond", "open", kind="primary", size="xs")]
        elif state in ("failed", "stopped"):
            acts += [nd.Button("Retry", "retry", size="xs"), nd.Button("Open", "open", kind="ghost", size="xs")]
        elif state in ("running", "preparing"):
            acts += [nd.Button("Stop", "stop", kind="ghost", size="xs")]
        elif state == "queued":
            acts += [nd.Button("Skip", "skip", kind="ghost", size="xs")]
        elif state == "done" and r.get("pr"):
            acts += [nd.Link("PR", "pr", size=theme.FS_MICRO, data=r.get("pr"))]
        parts: List[nd.Node] = [state_cell, name, branch, elapsed, files, cost, now, *acts]
        return nd.Row(parts, 10, ROW_PAD, [1, 6], "center", ROW_H)

    def _now_text(self, r: Json, state: str) -> tuple:
        if state == "done":
            v = r.get("verify") or {}
            bits = []
            if v.get("ran"):
                bits.append("verify passed" if int(v.get("code") or 0) == 0 else f"verify exit {v.get('code')}")
            if r.get("files"):
                bits.append(plural(len(r.get("files") or []), "file"))
            if r.get("commit"):
                bits.append(f"committed {r.get('commit')}")
            elif r.get("finished"):
                bits.append(str(r.get("finished")))
            return (" · ".join(bits) or (str(r.get("summary") or "")[:80]), "ok" if bits else "text_2")
        if state in ("failed", "stopped", "skipped"):
            return (str(r.get("reason") or STATE_WORD.get(state, state)), "err" if state == "failed" else "text_3")
        if state == "queued":
            deps = [d for d in (r.get("dependsOn") or [])]
            if deps:
                names = [x.get("name") for x in self.list.runs if x.get("path") in deps]
                return (f"after {', '.join(str(n) for n in names if n)}", "text_3")
            return ("waiting for a slot", "text_3")
        return (str(r.get("now") or STATE_WORD.get(state, state)), "text_2")

    def _paint_glyph(self, p: QPainter, r: QRect, st: PaintState) -> None:
        state = str(self.run.get("state") or "queued")
        paint_glyph(
            p,
            QRectF(r.x() + 2, r.y() + 2, 14, 14),
            state,
            st.t,
            self.list.breath0 if state in ("running", "preparing") else None,
        )

    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        if st.hover is not None:
            rounded(p, QRect(x, y, w, self.h), theme.blend(st.t.fill_1, st.t.canvas), None, theme.R2)
        elif self.run.get("state") == "needs_action":
            rounded(p, QRect(x, y, w, self.h), theme.blend(st.t.warn_wash, st.t.canvas), None, theme.R2)
        super().paint(p, x, y, w, st)

    def hit(self, x: int, y: int) -> Optional[Target]:
        t = super().hit(x, y)
        if t is not None and t.kind in ("button", "link"):
            return t
        return Target("row", "open", (0, 0, self.h_width or 0, self.h))

    def activate(self, target: Target, view: Any) -> None:
        if target.kind == "link" and target.id == "pr":
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl(str(self.run.get("pr") or "")))
            return
        super().activate(target, view)

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor

    def text(self) -> str:
        return f"{self.run.get('name')} {STATE_WORD.get(str(self.run.get('state')), '')}"

    def live(self) -> bool:
        return self.run.get("state") in ("running", "preparing")

    def tick(self, now: float) -> Optional[tuple]:
        return (0, 0, 100000, self.h) if self.live() else None


class HeadBlock(NodeBlock):
    """The column titles."""

    KIND = "run-head"

    def __init__(self) -> None:
        super().__init__("run:head", self._build, (0, 0, 0, 0), est_h=24)

    def _build(self, m: Measurer) -> nd.Node:
        def t(s: str, w: Optional[int] = None, right: bool = False) -> nd.Node:
            n = nd.Text(s.upper(), 10.5, 600, "text_4", wrap=False)
            return Cell(n, w, right) if w else n

        return nd.hstack(
            t("state", COLS["state"]),
            t("repository"),
            t("branch", COLS["branch"]),
            t("elapsed", COLS["elapsed"], True),
            t("Δ files", COLS["files"], True),
            t("cost", COLS["cost"], True),
            t("now"),
            spacing=10,
            stretch=[1, 6],
            pad=(10, 4, 10, 6),
        )

    @property
    def static(self) -> bool:
        return True


class RunList(CanvasView):
    """The rows: a run's change repaints its row; the order is stable while you read."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(Scene(), parent, column="fill")
        self.jid = ""
        self.runs: List[Json] = []
        self.gutter = 8
        self.pad_top, self.pad_bottom = 4, 12
        self.follow.following = False
        self.breath0 = now_ms() / 1000
        self.head = HeadBlock()
        self.setAccessibleName("Runs")

    def sync(self, jid: str, runs: List[Json], now: float) -> None:
        """Rows keep their place while you read: the plan's order (dependencies first, then by name);
        the state is the glyph and the colour, not a position."""
        self.jid = jid
        order = sorted(runs, key=lambda r: (int(r.get("level") or 0), str(r.get("name") or "")))
        self.runs = order
        keys = ["run:head"] + [f"run:{r.get('id')}" for r in order]
        by_key = {f"run:{r.get('id')}": r for r in order}
        root = self.scene.root

        def make(i: int) -> Block:
            k = keys[i]
            if k == "run:head":
                return self.head
            return RunBlock(by_key[k], self)

        def update(b: Block, i: int) -> List[Block]:
            if isinstance(b, RunBlock):
                return [b] if b.sync(by_key[keys[i]], now) else []
            return []

        self.begin()
        try:
            for b in reconcile(root, keys, make, update):
                if b.warm:
                    self._exact(b)
                else:
                    self.estimate(b)
        finally:
            self.end()

    # ---- what a row can do
    def open_run(self, run: Json) -> None:
        sid = run.get("sessionId")
        if sid:
            store.open_session(str(sid))
        else:
            store.toast("This run has no session yet.", "info")

    def answer(self, run: Json, allow: bool) -> None:
        pending = run.get("pending") or []
        if not pending or not run.get("sessionId"):
            return
        p = pending[0]
        decision = (
            {"behavior": "allow", "decisionClassification": "user_temporary"}
            if allow
            else {
                "behavior": "deny",
                "message": "The user declined this action.",
                "decisionClassification": "user_reject",
            }
        )
        store.respond(str(run["sessionId"]), str(p.get("requestId") or ""), decision)

    def retry(self, run: Json) -> None:
        store.call("dispatch.retry", {"id": self.jid, "runId": run.get("id")})

    def stop(self, run: Json) -> None:
        store.call("dispatch.stop", {"id": self.jid, "runId": run.get("id")})

    def skip(self, run: Json) -> None:
        store.call("dispatch.skip", {"id": self.jid, "runId": run.get("id")})


class JobView(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.jid = ""
        self.review: Optional[Json] = None
        self._review_for: Any = None
        self.now = now_ms()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        head = QWidget()
        head.setFixedHeight(theme.HEADER_H)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(14, 0, 20, 0)
        hl.setSpacing(10)
        self.back = IconButton("arrow-left", "Back to the board", 16)
        self.back.clicked.connect(lambda: store.show_dispatch(None))
        hl.addWidget(self.back)
        self.title = QLabel("Job")
        self.title.setProperty("fs", "title")
        hl.addWidget(self.title)
        self.meta = QLabel()
        theme.tint(self.meta, fg="text_3")
        self.meta.setProperty("fs", "small")
        hl.addWidget(self.meta)
        hl.addStretch(1)
        self.pause_btn = button("Pause", kind="ghost", size="sm", icon="pause", on_click=self.toggle_pause)
        hl.addWidget(self.pause_btn)
        self.stop_btn = button("Stop", kind="ghost", size="sm", icon="square", on_click=self.stop_job)
        hl.addWidget(self.stop_btn)
        self.more_btn = IconButton("ellipsis", "More", 16)
        self.more_btn.clicked.connect(self.open_menu)
        hl.addWidget(self.more_btn)
        root.addWidget(head)

        top = QWidget()
        self.top_lay = QVBoxLayout(top)
        self.top_lay.setContentsMargins(20, 6, 20, 8)
        self.top_lay.setSpacing(8)
        self.brief_box = Box(bg="raised/canvas", line="hair/canvas", radius=theme.R3)
        bb = QVBoxLayout(self.brief_box)
        bb.setContentsMargins(12, 9, 12, 9)
        bb.setSpacing(4)
        bh = QHBoxLayout()
        bh.setSpacing(10)
        bh.addWidget(Text("Brief", theme.FS_MICRO, 600, "text_3", wrap=False))
        self.plan_line = Text("", theme.FS_MICRO, 400, "text_3", wrap=False)
        bh.addWidget(self.plan_line, 1)
        self.edit_btn = button("Edit", kind="link", size="sm", on_click=self.toggle_edit)
        self.edit_btn.setAccessibleName("Edit the brief")
        bh.addWidget(self.edit_btn)
        bb.addLayout(bh)
        self.brief = Text("", theme.FS_SMALL, 400, "text_2", 1.5)
        bb.addWidget(self.brief)
        self.brief_edit = text_area("The brief", rows=5, name="Brief")
        self.brief_edit.hide()
        bb.addWidget(self.brief_edit)
        self.rules = Text("", theme.FS_MICRO, 400, "text_3", 1.45)
        self.rules.hide()
        bb.addWidget(self.rules)
        self.top_lay.addWidget(self.brief_box)
        self.prose_box = Box(bg="raised/canvas", line="hair/canvas", radius=theme.R3)
        pb = QVBoxLayout(self.prose_box)
        pb.setContentsMargins(12, 9, 12, 9)
        pb.setSpacing(4)
        pb.addWidget(Text("Report", theme.FS_MICRO, 600, "text_3", wrap=False))
        self.prose = Text("", theme.FS_SMALL, 400, "text_2", 1.5)
        pb.addWidget(self.prose)
        self.prose_box.hide()
        self.top_lay.addWidget(self.prose_box)
        self.extra = QVBoxLayout()
        self.extra.setSpacing(6)
        self.top_lay.addLayout(self.extra)
        root.addWidget(top)

        self.list = RunList()
        root.addWidget(self.list, 1)

        self.foot = QWidget()
        fl = QHBoxLayout(self.foot)
        fl.setContentsMargins(20, 8, 20, 10)
        fl.setSpacing(8)
        self.review_text = Text("", theme.FS_SMALL, 400, "text_2", wrap=False)
        fl.addWidget(self.review_text, 1)
        self.commit_btn = button(
            "Commit all…", size="sm", icon="git-commit-horizontal", on_click=lambda: self.finish("commit")
        )
        self.pr_btn = button(
            "Open PRs…", size="sm", icon="git-pull-request-arrow", on_click=lambda: self.finish("commit", pr=True)
        )
        self.keep_btn = button("Keep", kind="ghost", size="sm", on_click=lambda: self.finish("keep"))
        self.discard_btn = button("Discard…", kind="ghost", size="sm", on_click=lambda: self.finish("discard"))
        for b in (self.commit_btn, self.pr_btn, self.keep_btn, self.discard_btn):
            fl.addWidget(b)
        self.foot.hide()
        root.addWidget(self.foot)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick)
        bind(store.dispatch_changed, self, self._on_change)
        self._extra_sig: Any = None

    # ---- lifecycle
    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._tick_timer.start()

    def hideEvent(self, e) -> None:  # noqa: N802
        self._tick_timer.stop()
        super().hideEvent(e)

    def show_job(self, jid: str) -> None:
        if jid != self.jid:
            self.review = None
            self._review_for = None
            self._extra_sig = None
        self.jid = jid
        self.refresh()

    def _on_change(self, d: Any) -> None:
        if d is None or (isinstance(d, dict) and d.get("id") == self.jid):
            self.refresh()

    @property
    def job(self) -> Json:
        return store.jobs.get(self.jid) or {}

    # ---- rendering
    def refresh(self) -> None:
        self.now = now_ms()
        job = self.job
        runs = store.runs_of(self.jid)
        if not job:
            self.title.setText("Job")
            self.meta.setText("")
            return
        self.title.setText(str(job.get("name") or "Untitled job"))
        self.meta.setText(
            " · ".join(
                x
                for x in (
                    plural(int(job.get("total") or 0), "repo"),
                    job_state_text(job, self.now),
                    job_cost_text(job) if float(job.get("cost") or 0) else "",
                    elapsed_text(job, self.now) if job.get("state") in ("finished", "stopped") else "",
                )
                if x
            )
        )
        st = str(job.get("state") or "")
        self.pause_btn.setVisible(st == "running")
        self.edit_btn.setVisible(
            st in ("running", "reviewing")
            and st != "finished"
            and (job.get("canaryWaiting") or job.get("paused") or st == "running")
        )
        self.pause_btn.setText("Resume" if job.get("paused") else "Pause")
        self.stop_btn.setVisible(st in ("running", "reviewing") and st != "finished")
        self.brief.setText(str(job.get("brief") or ""))
        p = job.get("plan") or {}
        bits = []
        if p:
            bits.append(f"Plan: {int(p.get('ok') or 0)} ok")
            if int(p.get("warnings") or 0):
                bits.append(f"{int(p.get('warnings') or 0)} warning{'s' if int(p.get('warnings') or 0) != 1 else ''}")
            if int(p.get("blocked") or 0):
                bits.append(f"{int(p.get('blocked') or 0)} blocked")
            if int(p.get("levels") or 0) > 1:
                bits.append(f"{int(p.get('levels') or 0)} levels")
        self.plan_line.setText(" · ".join(bits))
        rules = [r for r in (job.get("rules") or []) if isinstance(r, dict)]
        self.rules.setText(
            "Allowed for every repository: " + "; ".join(str(r.get("label") or r.get("key")) for r in rules)
            if rules
            else ""
        )
        self.rules.setVisible(bool(rules))
        rep = job.get("report") or {}
        prose_state = str(rep.get("proseState") or "")
        if rep.get("prose"):
            self.prose.setText(str(rep.get("prose")))
            self.prose.set_color("text_2")
        elif prose_state == "writing":
            self.prose.setText("An agent is writing the report from the runs' summaries…")
            self.prose.set_color("text_3")
        elif prose_state == "failed":
            self.prose.setText(
                f"The written report did not come: {rep.get('proseError') or 'unknown reason'}. The table stands."
            )
            self.prose.set_color("text_3")
        self.prose_box.setVisible(bool(rep.get("prose")) or prose_state in ("writing", "failed"))
        # the canary card and the requests, above the rows
        groups = group_requests(runs)
        sig = (
            bool(job.get("canaryWaiting")),
            [(g["key"], tuple(g["names"])) for g in groups],
            (job.get("forecast") or {}).get("cost"),
        )
        if sig != self._extra_sig:
            self._extra_sig = sig
            while self.extra.count():
                w = self.extra.takeAt(0).widget()
                if w is not None:
                    w.deleteLater()
            if job.get("canaryWaiting"):
                card = CanaryRow(job)
                self.extra.addWidget(card)
                self.extra.addWidget(self._learn_row(job))
            for g in groups[:6]:
                self.extra.addWidget(RequestRow(self.jid, g))
        self.list.sync(self.jid, runs, self.now)
        # the foot: the review and the ways to finish
        reviewing = st in ("reviewing", "finished", "stopped") and not job.get("canaryWaiting")
        self.foot.setVisible(reviewing and bool(runs))
        if reviewing:
            open_runs = [r for r in runs if r.get("state") in TERMINAL and not r.get("finished") and r.get("worktree")]
            done_open = [r for r in open_runs if r.get("state") == "done"]
            self.commit_btn.setVisible(bool(done_open))
            self.pr_btn.setVisible(bool(done_open))
            self.keep_btn.setVisible(bool(open_runs))
            self.discard_btn.setVisible(bool(open_runs))
            key = (st, tuple(sorted(str(r.get("id")) for r in runs if r.get("state") == "done")))
            if key != self._review_for:
                self._review_for = key
                self.review_text.setText("Review: reading the changes…")
                self.load_review()
            else:
                self._review_text()

    def _learn_row(self, job: Json) -> QWidget:
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(14, 0, 0, 0)
        rl.setSpacing(8)
        sw = Switch(
            bool((job.get("settings") or {}).get("learn", True)), "let the rest learn from the canary's summary"
        )
        sw.toggled.connect(
            lambda on: store.call("dispatch.save", {"id": self.jid, "settings": {"learn": bool(on)}}, quiet=True)
        )
        rl.addWidget(sw)
        rl.addStretch(1)
        return row

    def load_review(self) -> None:
        jid = self.jid

        def got(r: Any) -> None:
            if jid != self.jid or not isinstance(r, dict):
                return
            self.review = r
            self._review_text()

        store.call("dispatch.review", {"id": jid}, quiet=True).then(got).catch(lambda _e: self.review_text.setText(""))

    def _review_text(self) -> None:
        job = self.job
        r = self.review
        runs = store.runs_of(self.jid)
        done = [x for x in runs if x.get("state") == "done"]
        if job.get("state") == "finished":
            rep = job.get("report") or {}
            prs = sum(1 for t in (rep.get("targets") or []) if t.get("pr"))
            committed = sum(1 for t in (rep.get("targets") or []) if t.get("finished") == "commit")
            self.review_text.setText(
                f"Finished: {len(done)} of {len(runs)} done · {committed} committed"
                + (f" · {prs} PRs" if prs else "")
                + " · report in the inbox"
            )
            return
        if r is None:
            return
        changed = [x for x in (r.get("runs") or []) if x.get("files")]
        same = r.get("same") or []
        bits = [
            f"{plural(len(changed), 'repository', 'repositories')} changed",
            plural(int(r.get("files") or 0), "file"),
        ]
        if same:
            top = same[0]
            bits.append(
                f"the same change in {len(top.get('runs') or [])} repos ({top.get('path')})"
                + (f" and {len(same) - 1} more" if len(same) > 1 else "")
            )
        differ = r.get("differ") or []
        if differ and same:
            bits.append(f"{len(differ)} differ")
        self.review_text.setText("Review: " + " · ".join(bits))

    def _tick(self) -> None:
        self.now = now_ms()
        job = self.job
        if not job:
            return
        if job.get("state") == "running":
            self.meta.setText(
                " · ".join(
                    x
                    for x in (
                        plural(int(job.get("total") or 0), "repo"),
                        job_state_text(job, self.now),
                        job_cost_text(job) if float(job.get("cost") or 0) else "",
                    )
                    if x
                )
            )
            self.list.sync(self.jid, store.runs_of(self.jid), self.now)

    # ---- actions
    def toggle_edit(self) -> None:
        """The brief opens for editing (the runs still to come get the new text); Save sends it."""
        if self.brief_edit.isHidden():
            self.brief_edit.setPlainText(str(self.job.get("brief") or ""))
            self.brief.hide()
            self.brief_edit.show()
            self.brief_edit.setFocus()
            self.edit_btn.setText("Save")
            return
        text = self.brief_edit.toPlainText()
        self.brief_edit.hide()
        self.brief.show()
        self.edit_btn.setText("Edit")
        if text.strip() and text != self.job.get("brief"):
            self.brief.setText(text)
            store.call("dispatch.save", {"id": self.jid, "brief": text})

    def toggle_pause(self) -> None:
        store.call("dispatch.resume" if self.job.get("paused") else "dispatch.pause", {"id": self.jid})

    def stop_job(self) -> None:
        name = str(self.job.get("name") or "this job")
        store.confirm_action(
            ConfirmSpec(
                title=f"Stop {name}?",
                body="Runs under way are interrupted and kept as they are; queued ones are skipped. Nothing is discarded.",
                confirm="Stop the job",
                danger=True,
                on_confirm=lambda ok: ok and store.call("dispatch.stop", {"id": self.jid}),
            )
        )

    def finish(self, action: str, pr: bool = False) -> None:
        runs = store.runs_of(self.jid)
        open_runs = [r for r in runs if r.get("state") in TERMINAL and not r.get("finished") and r.get("worktree")]
        done = [r for r in open_runs if r.get("state") == "done"]
        n = len(done if action == "commit" else open_runs)
        if not n:
            return
        settings = self.job.get("settings") or {}
        if action == "keep":
            store.call("dispatch.finish", {"id": self.jid, "action": "keep"})
            return
        branch = f"pilcrow/{self.job.get('slug') or 'job'}"
        if action == "commit":
            title = f"{'Open pull requests for' if pr else 'Commit'} {plural(n, 'repository', 'repositories')}?"
            body = (
                f"Each run's changes are committed on its branch {branch} with the message “{settings.get('commitMessage') or self.job.get('name')}”. "
                + (
                    "The branches are pushed and a pull request opened with gh where the repository has a GitHub remote. "
                    if pr
                    else ""
                )
                + "The worktree directories are removed; the branches stay."
            )
        else:
            title = f"Discard the changes in {plural(n, 'repository', 'repositories')}?"
            body = f"The worktrees and the branches {branch} are removed. This cannot be undone."
        store.confirm_action(
            ConfirmSpec(
                title=title,
                body=body,
                confirm="Open pull requests" if pr else "Commit" if action == "commit" else "Discard",
                danger=action == "discard",
                list=[str(r.get("name")) for r in (done if action == "commit" else open_runs)][:12],
                on_confirm=lambda ok: (
                    ok and store.call("dispatch.finish", {"id": self.jid, "action": action, "pr": pr})
                ),
            )
        )

    def open_menu(self) -> None:
        job = self.job
        items: List[Json] = [
            {
                "label": "Run again",
                "icon": "rotate-cw",
                "on_select": self.run_again,
                "hint": "A new job from this brief",
            },
            {"label": "Save as playbook", "icon": "book-marked", "on_select": self.save_playbook},
            {"label": "Schedule this…", "icon": "calendar-clock", "on_select": self.schedule_playbook},
            {"separator": True},
            {"label": "Copy the report as Markdown", "icon": "clipboard-paste", "on_select": self.export_markdown},
            {"separator": True},
            {"label": "Remove the job", "icon": "trash", "danger": True, "on_select": self.remove_job},
        ]
        if job.get("state") == "running" and job.get("capReached"):
            items.insert(
                0, {"label": "Raise the cost cap and resume", "icon": "circle-dollar-sign", "on_select": self.raise_cap}
            )
        show_menu(items, self.more_btn, align_right=True)

    def _ensure_playbook(self, then: Callable[[str], None]) -> None:
        """The job's playbook id, saving one from the job when it has none yet."""
        pid = str(self.job.get("playbookId") or "")
        if pid and any(str(p.get("id")) == pid for p in store.playbooks):
            then(pid)
            return
        store.call("dispatch.savePlaybook", {"fromJob": self.jid}).then(
            lambda pb: (store.load_playbooks(), then(str((pb or {}).get("id") or "")))
        )

    def save_playbook(self) -> None:
        name = str(self.job.get("name") or "Untitled playbook")
        store.call("dispatch.savePlaybook", {"fromJob": self.jid, "name": name}).then(
            lambda _pb: (store.load_playbooks(), store.toast(f"Saved as playbook: {name}", "success"))
        )

    def run_again(self) -> None:
        name = str(self.job.get("name") or "Job")

        def go(pid: str) -> None:
            if pid:
                store.call("dispatch.startFromPlaybook", {"id": pid, "name": name}).then(
                    lambda j: store.open_job(str((j or {}).get("id") or ""))
                )

        self._ensure_playbook(go)

    def schedule_playbook(self) -> None:
        self._ensure_playbook(lambda pid: pid and store.set_dialog("schedule", {"kind": "dispatch", "playbookId": pid}))

    def export_markdown(self) -> None:
        def got(md: Any) -> None:
            cb: QClipboard = QGuiApplication.clipboard()
            cb.setText(str(md or ""))
            store.toast("Report copied as Markdown.", "info")

        store.call("dispatch.export", {"id": self.jid}).then(got)

    def raise_cap(self) -> None:
        cap = float((self.job.get("settings") or {}).get("costCap") or 0)
        store.call("dispatch.save", {"id": self.jid, "settings": {"costCap": cap * 2 if cap else 10}}).then(
            lambda _j: store.call("dispatch.resume", {"id": self.jid})
        )

    def remove_job(self) -> None:
        name = str(self.job.get("name") or "this job")
        running = self.job.get("state") == "running"
        store.confirm_action(
            ConfirmSpec(
                title=f"Remove {name}?",
                body=("It is still running: its runs are stopped first. " if running else "")
                + "Worktrees that were kept are removed with their branches; committed branches stay.",
                confirm="Remove",
                danger=True,
                on_confirm=lambda ok: (
                    ok
                    and store.call("dispatch.remove", {"id": self.jid, "removeWorktrees": True}).then(
                        lambda _r: store.show_dispatch(None)
                    )
                ),
            )
        )


_ = (Callable, Optional)
