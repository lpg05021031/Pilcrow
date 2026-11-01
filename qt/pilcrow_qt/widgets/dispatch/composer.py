"""The new-job composer: name, brief, targets, how to run, how to finish; the plan runs as you edit
and paints its verdicts on the targets, and Start is enabled when nothing is blocked."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ... import icons, theme
from ...lib.fmt import dirname
from ...store import store
from ..dialogs.parts import Switch, Text, hint, page_column
from ..dialogs.schedule import NumInput
from ..dialogs.settings_ui import Select, line_edit, text_area
from ..ui.bits import Box, IconButton, LayoutButton, button
from ..ui.collapse import Collapse
from ..ui.signals import bind
from .cards import switch_line
from .targets import TargetPicker

Json = Dict[str, Any]
PLAN_DEBOUNCE_MS = 600
VARIABLES = "{{repo.name}} {{repo.path}} {{repo.branch}} {{job.name}} {{notes}}"


def field(title: str, w: QWidget, note: Optional[QWidget] = None, aside: Optional[QWidget] = None) -> QWidget:
    """A label over a control (``.sd-field``); ``aside`` sits at the right of the label line."""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(5)
    lb = QLabel(title)
    lb.setProperty("fs", "small")
    theme.tint(lb, fg="text_2")
    if aside is not None:
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(10)
        line.addWidget(lb)
        line.addStretch(1)
        line.addWidget(aside)
        lay.addLayout(line)
    else:
        lay.addWidget(lb)
    lay.addWidget(w)
    if note is not None:
        lay.addWidget(note)
    return box


def group(title: str, *rows: QWidget) -> QWidget:
    """A titled group inside Options: the word over its rows."""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    lay.addWidget(Text(title, theme.FS_SMALL, 600, "text_2", wrap=False))
    for r in rows:
        lay.addWidget(r)
    return box


def inline(*pairs: Any) -> QWidget:
    """Label–control pairs in one row (``.sd-inline``): ``inline("parallel", w, "max", w2)``."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    for i in range(0, len(pairs), 2):
        lb = QLabel(str(pairs[i]))
        lb.setProperty("fs", "small")
        theme.tint(lb, fg="text_3")
        if i:
            lay.addSpacing(6)
        lay.addWidget(lb)
        lay.addWidget(pairs[i + 1])
    lay.addStretch(1)
    return box


def plan_summary(plan: Optional[Json], pending: bool, selected: int) -> str:
    """The plan in one line beside the Targets label: what is ready, what is not, the worst case."""
    if plan is None:
        return "planning…" if pending else ""
    ok, warn, blocked = int(plan.get("ok") or 0), int(plan.get("warnings") or 0), int(plan.get("blocked") or 0)
    parts = [f"{ok + warn} of {selected or ok + warn + blocked} ready"]
    if warn:
        parts.append(f"{warn} warning{'s' if warn != 1 else ''}")
    if blocked:
        parts.append(f"{blocked} blocked")
    w = plan.get("worst") or {}
    if int(w.get("minutes") or 0):
        parts.append(f"up to {int(w.get('minutes') or 0)} min")
    if int(w.get("levels") or 0) > 1:
        parts.append(f"{int(w.get('levels') or 0)} levels")
    return " · ".join(parts) + (" · planning…" if pending else "")


class PlanBox(Box):
    """What the plan has to say about the targets, under the picker: one row per target that is not
    plain ok (blocked first) or that runs after another, each with its note. Shown only when there
    is something to say."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(bg="raised/canvas", line="hair/canvas", radius=theme.R3, parent=parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(4)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(4)
        lay.addLayout(self.rows)
        self.hide()

    def show_plan(self, plan: Optional[Json], pending: bool = False) -> None:
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        if plan is None:
            self.hide()
            return
        t = theme.tokens()
        targets = [x for x in (plan.get("targets") or []) if isinstance(x, dict)]
        order = {"blocked": 0, "warning": 1, "ok": 2}
        for tg in sorted(targets, key=lambda x: (order.get(str(x.get("verdict")), 3), str(x.get("name")))):
            notes = [n for n in (tg.get("notes") or []) if isinstance(n, dict) and n.get("kind") != "note"]
            deps = [
                n
                for n in (tg.get("notes") or [])
                if isinstance(n, dict) and n.get("kind") == "note" and "Runs after" in str(n.get("text"))
            ]
            if tg.get("verdict") == "ok" and not deps:
                continue
            for n in notes or deps:
                row = QWidget()
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                rl.setSpacing(8)
                kind = str(n.get("kind"))
                fg = {"blocked": t.err, "warning": t.warn}.get(kind, t.text_3)
                tag = QLabel({"blocked": "blocked", "warning": "warning"}.get(kind, "order"))
                tag.setProperty("fs", "micro")
                tag.setFixedWidth(56)
                theme.tint(tag, fg=fg)
                rl.addWidget(tag, 0, Qt.AlignmentFlag.AlignTop)
                txt = Text(f"{tg.get('name')} · {n.get('text')}", theme.FS_SMALL, 400, "text_2", 1.45)
                rl.addWidget(txt, 1)
                self.rows.addWidget(row)
        self.setVisible(self.rows.count() > 0)


class JobComposer(QWidget):
    """A new job, or the edit of a draft."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.plan: Optional[Json] = None
        self._plan_seq = 0
        self._starting = False
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
        self.title = QLabel("New job")
        self.title.setProperty("fs", "title")
        hl.addWidget(self.title)
        hl.addStretch(1)
        self.playbook = Select("Playbook", popup_width=300, ghost=True)
        self.playbook.currentIndexChanged.connect(lambda _i: self._playbook_picked())
        hl.addWidget(self.playbook)
        self.save_pb_btn = button(
            "Save as playbook", kind="ghost", size="sm", icon="book-marked", on_click=self.save_playbook
        )
        hl.addWidget(self.save_pb_btn)
        self.start_btn = button("Start", kind="primary", size="sm", icon="play", on_click=self.start)
        self.start_btn.setEnabled(False)
        hl.addWidget(self.start_btn)
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
        bl.setContentsMargins(20, 8, 20, 48)
        bl.setSpacing(18)
        hh.addWidget(self.body)
        hh.addStretch(1)
        self.scroll.setWidget(host)
        self.scroll.viewport().installEventFilter(self)
        root.addWidget(self.scroll, 1)

        # ---- name, brief
        self.name = line_edit("Bump eslint to v9", name="Name")
        bl.addWidget(field("Name", self.name))
        self.brief = text_area(
            "Upgrade eslint to v9 with the flat config. Keep the rules we have; drop the deprecated ones. "
            "Run the linter and fix what it reports. Repository: {{repo.name}} on {{repo.branch}}. {{notes}}",
            rows=6,
            name="Brief",
        )
        bl.addWidget(
            field(
                "Brief",
                self.brief,
                hint(
                    f"Variables: {VARIABLES}. Each repository gets its own copy; a target's notes land in {{{{notes}}}}."
                ),
            )
        )

        # ---- targets, with the plan's verdict beside the label and its notes under the picker
        default_root = dirname(store.default_cwd()) or str((store.info or {}).get("home") or "")
        self.targets = TargetPicker(default_root)
        self.plan_summary = Text("", theme.FS_SMALL, 400, "text_3", wrap=False)
        bl.addWidget(field("Targets", self.targets, aside=self.plan_summary))
        self.plan_box = PlanBox()
        bl.addWidget(self.plan_box)

        # ---- the rest: sensible defaults, one line to read them, Options to change them
        self.disclose = LayoutButton()
        self.disclose.setAccessibleName("Options")
        self.disclose.setCursor(Qt.CursorShape.PointingHandCursor)
        self.disclose.setFixedHeight(36)
        self.disclose.set_look(hover="fill_1/canvas", radius=theme.R2)
        dl = QHBoxLayout(self.disclose)
        dl.setContentsMargins(8, 0, 8, 0)
        dl.setSpacing(8)
        self.chev = QLabel()
        self.chev.setFixedSize(14, 14)
        dl.addWidget(self.chev)
        dl.addWidget(Text("Options", theme.FS_UI, 600, "text_2", wrap=False))
        self.summary = Text("", theme.FS_SMALL, 400, "text_3", wrap=False)
        dl.addWidget(self.summary, 1)
        self.disclose.clicked.connect(self.toggle_options)
        bl.addWidget(self.disclose)
        self.options = Collapse(self._build_options, False)
        bl.addWidget(self.options)
        bl.addStretch(1)
        bind(theme.theme.changed, self, lambda _t: self._paint_disclose())
        self._build_controls()
        self._paint_disclose()
        self._wire()

    def _build_controls(self) -> None:
        """The settings' controls exist from the start (values() reads them); Options lays them out."""
        # ---- run
        self.mode = Select("Permissions")
        self.mode.addItem("Allow edits in the worktree", "acceptEdits")
        self.mode.addItem("Ask for everything", "ask")
        self.mode.setCurrentIndex(0)
        self.model = line_edit("default model", name="Model")
        self.model.setMaximumWidth(220)
        self.parallel = NumInput(1, 16, 3, "Parallel runs")
        self.max_min = NumInput(1, 240, 20, "Minutes per run")
        self.cap = NumInput(0, 100000, 0, "Cost cap in dollars")
        self.canary = NumInput(0, 8, 1, "Canary runs")
        self.learn = Switch(True, "the rest learn from the canary")
        # ---- prepare, verify
        self.prepare = line_edit("npm ci", mono=True, name="Prepare command")
        self.verify = line_edit("npm test", mono=True, name="Verify command")
        self.verify_retry = Switch(True, "send a failing verify back to the agent once")
        # ---- finish
        self.branch = line_edit("pilcrow/<job>", mono=True, name="Branch")
        self.message = line_edit("chore: {{job.name}}", name="Commit message")
        self.pr = Switch(False, "open pull requests with gh after committing")
        self.report = Select("Report", popup_width=320)
        self.report.addItem("The table of repositories", "table", hint="What each run did, landed and cost")
        self.report.addItem(
            "The table, with prose written by an agent",
            "agent",
            hint="One more session reads the summaries and writes three paragraphs",
        )
        self.report.setCurrentIndex(0)

    def _build_options(self) -> QWidget:
        """Options, opened: how the runs go, what happens before and after each, how the job finishes."""
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 6, 8, 4)
        lay.setSpacing(18)
        run_grid = QWidget()
        rg = QGridLayout(run_grid)
        rg.setContentsMargins(0, 0, 0, 0)
        rg.setHorizontalSpacing(14)
        rg.setVerticalSpacing(10)
        rg.addWidget(field("Permissions", self.mode), 0, 0)
        rg.addWidget(field("Model", self.model), 0, 1)
        rg.addWidget(
            inline(
                "parallel", self.parallel, "minutes each", self.max_min, "cost cap $", self.cap, "canary", self.canary
            ),
            1,
            0,
            1,
            2,
        )
        rg.addWidget(switch_line(self.learn, "the rest learn from the canary"), 2, 0, 1, 2)
        lay.addWidget(group("Run", run_grid))
        pv = QWidget()
        pg = QGridLayout(pv)
        pg.setContentsMargins(0, 0, 0, 0)
        pg.setHorizontalSpacing(14)
        pg.setVerticalSpacing(10)
        pg.addWidget(field("Prepare (before the agent, in the worktree)", self.prepare), 0, 0)
        pg.addWidget(field("Verify (after the agent)", self.verify), 0, 1)
        pg.addWidget(switch_line(self.verify_retry, "send a failing verify back to the agent once"), 1, 0, 1, 2)
        lay.addWidget(group("Before and after each run", pv))
        fin = QWidget()
        fg = QGridLayout(fin)
        fg.setContentsMargins(0, 0, 0, 0)
        fg.setHorizontalSpacing(14)
        fg.setVerticalSpacing(10)
        fg.addWidget(field("Branch in every repository", self.branch), 0, 0)
        fg.addWidget(field("Commit message", self.message), 0, 1)
        fg.addWidget(field("Report", self.report), 1, 0)
        fg.addWidget(switch_line(self.pr, "open pull requests with gh after committing"), 2, 0, 1, 2)
        lay.addWidget(group("Finish", fin))
        return box

    def toggle_options(self) -> None:
        self.options.toggle()
        self._paint_disclose()

    def set_options_open(self, open_: bool) -> None:
        if open_ != self.options.is_open:
            self.toggle_options()

    def _paint_disclose(self) -> None:
        t = theme.tokens()
        self.chev.setPixmap(icons.pixmap("chevron-down" if self.options.is_open else "chevron-right", t.text_4, 14))
        self.summary.setVisible(not self.options.is_open)

    def _summarize(self) -> None:
        """One line of what Options hold: the defaults read as a sentence, so most people never open it."""
        s = self.values()["settings"]
        bits = [
            "allow edits" if s["mode"] == "acceptEdits" else "ask for everything",
            f"{s['parallel']} parallel",
            f"{s['maxMinutes']} min each",
            f"canary {s['canary']}" if s["canary"] else "no canary",
        ]
        if s["costCap"]:
            bits.append(f"cap ${s['costCap']}")
        if s["model"]:
            bits.append(s["model"])
        if s["prepare"]:
            bits.append(f"prepare: {s['prepare']}")
        if s["verify"]:
            bits.append(f"verify: {s['verify']}")
        if s["branch"]:
            bits.append(f"branch pilcrow/{s['branch']}")
        if s["pr"]:
            bits.append("pull requests")
        if s["report"] == "agent":
            bits.append("written report")
        self.summary.setText(" · ".join(bits))

    def _wire(self) -> None:
        self._plan_timer = QTimer(self)
        self._plan_timer.setSingleShot(True)
        self._plan_timer.setInterval(PLAN_DEBOUNCE_MS)
        self._plan_timer.timeout.connect(self.replan)
        for sig in (
            self.name.textChanged,
            self.brief.textChanged,
            self.targets.changed,
            self.prepare.textChanged,
            self.verify.textChanged,
        ):
            sig.connect(self._changed)
        for n in (self.parallel, self.max_min, self.cap, self.canary):
            n.valueChanged.connect(lambda _v: self._changed())
        self.mode.currentIndexChanged.connect(lambda _i: self._changed())
        for sw in (self.learn, self.verify_retry, self.pr):
            sw.toggled.connect(lambda _v: self._summarize())
        self.report.currentIndexChanged.connect(lambda _i: self._summarize())
        self.model.textChanged.connect(lambda _t: self._summarize())
        self.message.textChanged.connect(lambda _t: self._summarize())
        self.branch.textChanged.connect(lambda _t: self._summarize())
        self.name.textChanged.connect(self._name_changed)
        self.branch.textEdited.connect(lambda _t: self.branch.setProperty("auto", False))
        bind(store.connected_changed, self, lambda on: on and self._changed())
        self._playbook_id = ""
        self._filling = False
        bind(store.playbooks_changed, self, lambda _p: self._fill_playbooks())
        self._fill_playbooks()
        self._summarize()

    # ---- lifecycle
    def eventFilter(self, obj, e) -> bool:
        if obj is self.scroll.viewport() and e.type() == QEvent.Type.Resize:
            vw = self.scroll.viewport().width()
            self.body.setFixedWidth(max(320, min(page_column(vw), vw)))
        return False

    def reset(self) -> None:
        """A fresh form (the previous job's text is not what the next one wants)."""
        self._filling = True
        self.name.setText("")
        self.brief.setPlainText("")
        self.plan = None
        self.plan_box.show_plan(None)
        self.plan_summary.setText("")
        self.start_btn.setEnabled(False)
        self.targets.set_selected([])
        self.set_options_open(False)
        self._playbook_id = ""
        self.playbook.setCurrentIndex(0)
        self._filling = False
        self.name.setFocus()

    # ---- playbooks: a brief with its settings and repositories, kept to run again
    def _fill_playbooks(self) -> None:
        cur = self._playbook_id
        self._filling = True
        self.playbook.clear()
        self.playbook.addItem("No playbook", "", hint="Start from a blank brief")
        for pb in store.playbooks:
            self.playbook.addItem(
                str(pb.get("name") or "Untitled playbook"),
                str(pb.get("id")),
                hint=f"{len(pb.get('targets') or [])} repositories · used {int(pb.get('uses') or 0)}×",
            )
        i = self.playbook.findData(cur) if cur else -1
        self.playbook.setCurrentIndex(i if i >= 0 else 0)
        self._filling = False

    def _playbook_picked(self) -> None:
        if self._filling:
            return
        pid = str(self.playbook.currentData() or "")
        self._playbook_id = pid
        pb = next((p for p in store.playbooks if str(p.get("id")) == pid), None)
        if pb is None:
            return
        self.load_playbook(pb)

    def load_playbook(self, pb: Json) -> None:
        """The playbook's brief, settings and repositories into the form; the name follows it when empty."""
        self._playbook_id = str(pb.get("id") or "")
        st = pb.get("settings") or {}
        if not self.name.text().strip():
            self.name.setText(str(pb.get("name") or ""))
        self.brief.setPlainText(str(pb.get("brief") or ""))
        i = self.mode.findData(str(st.get("mode") or "acceptEdits"))
        self.mode.setCurrentIndex(i if i >= 0 else 0)
        self.model.setText(str(st.get("model") or ""))
        self.parallel.setValue(int(st.get("parallel") or 3))
        self.max_min.setValue(int(st.get("maxMinutes") or 20))
        self.cap.setValue(int(st.get("costCap") or 0))
        self.canary.setValue(int(st.get("canary") if st.get("canary") is not None else 1))
        self.learn.setChecked(bool(st.get("learn", True)))
        self.prepare.setText(str(st.get("prepare") or ""))
        self.verify.setText(str(st.get("verify") or ""))
        self.verify_retry.setChecked(bool(st.get("verifyRetry", True)))
        self.message.setText(str(st.get("commitMessage") or ""))
        self.pr.setChecked(bool(st.get("pr")))
        ri = self.report.findData(str(st.get("report") or "table"))
        self.report.setCurrentIndex(ri if ri >= 0 else 0)
        self.targets.set_targets(
            str(pb.get("root") or ""), [t for t in (pb.get("targets") or []) if isinstance(t, dict)]
        )
        self._changed()

    def save_playbook(self) -> None:
        v = self.values()
        name = v["name"] or v["brief"].strip().split("\n")[0][:60]
        if not v["brief"].strip():
            store.toast("Write the brief first.", "warning")
            return
        payload = {
            "name": name or "Untitled playbook",
            "brief": v["brief"],
            "root": v["root"],
            "targets": v["targets"],
            "settings": v["settings"],
        }
        if self._playbook_id:
            payload["id"] = self._playbook_id

        def ok(pb: Any) -> None:
            self._playbook_id = str((pb or {}).get("id") or "")
            store.toast(f"Saved as playbook: {name}", "success")
            store.load_playbooks()

        store.call("dispatch.savePlaybook", payload).then(ok)

    # ---- the job as the form holds it
    def _name_changed(self, text: str) -> None:
        if not self.branch.text().strip() or self.branch.property("auto") is not False:
            slug = "-".join(w for w in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split())[:40]
            self.branch.blockSignals(True)
            self.branch.setText(f"pilcrow/{slug}" if slug else "")
            self.branch.setProperty("auto", True)
            self.branch.blockSignals(False)

    def values(self) -> Json:
        branch = self.branch.text().strip()
        return {
            "playbookId": self._playbook_id or None,
            "name": self.name.text().strip(),
            "brief": self.brief.toPlainText(),
            "root": self.targets.root(),
            "targets": self.targets.targets(),
            "settings": {
                "mode": str(self.mode.currentData() or "acceptEdits"),
                "model": self.model.text().strip(),
                "parallel": self.parallel.value(),
                "maxMinutes": self.max_min.value(),
                "costCap": self.cap.value(),
                "canary": self.canary.value(),
                "learn": self.learn.isChecked(),
                "prepare": self.prepare.text().strip(),
                "verify": self.verify.text().strip(),
                "verifyRetry": self.verify_retry.isChecked(),
                "branch": branch[len("pilcrow/") :] if branch.startswith("pilcrow/") else branch,
                "commitMessage": self.message.text().strip(),
                "pr": self.pr.isChecked(),
                "report": str(self.report.currentData() or "table"),
            },
        }

    # ---- the plan
    def _changed(self) -> None:
        self.start_btn.setEnabled(False)
        self.plan_summary.setText(plan_summary(self.plan, True, len(self.targets.targets())))
        self._summarize()
        self._plan_timer.start()

    def replan(self) -> None:
        v = self.values()
        if not v["targets"] or not store.connected:
            self.plan = None
            self.plan_box.show_plan(None)
            self.plan_summary.setText("")
            self.targets.apply_plan(None)
            self.start_btn.setEnabled(False)
            return
        self._plan_seq += 1
        seq = self._plan_seq

        def got(p: Any) -> None:
            if seq != self._plan_seq or not isinstance(p, dict):
                return
            self.plan = p
            self.plan_box.show_plan(p)
            self.plan_summary.setText(plan_summary(p, False, len(v["targets"])))
            self.targets.apply_plan(p)
            self.start_btn.setEnabled(
                bool(p.get("targets")) and not int(p.get("blocked") or 0) and bool(v["brief"].strip())
            )

        def bad(_e: Any) -> None:
            self.plan_box.show_plan(self.plan)
            self.plan_summary.setText(plan_summary(self.plan, False, len(v["targets"])))

        store.call("dispatch.plan", v, quiet=True).then(got).catch(bad)

    # ---- go
    def start(self) -> None:
        if self._starting:
            return
        v = self.values()
        if not v["name"]:
            v["name"] = v["brief"].strip().split("\n")[0][:60] or "Untitled job"
        self._starting = True
        self.start_btn.setEnabled(False)

        def saved(job: Any) -> None:
            jid = str((job or {}).get("id") or "")

            def started(_j: Any) -> None:
                self._starting = False
                store.open_job(jid)
                self.reset()

            def failed(_e: Any) -> None:
                self._starting = False
                self.start_btn.setEnabled(True)
                store.open_job(jid)

            store.call("dispatch.start", {"id": jid}).then(started).catch(failed)

        def not_saved(_e: Any) -> None:
            self._starting = False
            self.start_btn.setEnabled(True)

        store.call("dispatch.save", v).then(saved).catch(not_saved)


_ = (Callable, List)
