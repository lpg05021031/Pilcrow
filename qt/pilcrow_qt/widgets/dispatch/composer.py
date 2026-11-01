"""The new-job composer: name, brief, targets, how to run, how to finish; the plan runs as you edit
and paints its verdicts on the targets, and Start is enabled when nothing is blocked."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ... import theme
from ...lib.fmt import dirname, plural
from ...store import store
from ..dialogs.parts import Switch, Text, hint, page_column
from ..dialogs.schedule import NumInput
from ..dialogs.settings_ui import Select, line_edit, text_area
from ..ui.bits import Box, IconButton, button
from ..ui.signals import bind
from .targets import TargetPicker

Json = Dict[str, Any]
PLAN_DEBOUNCE_MS = 600
VARIABLES = "{{repo.name}} {{repo.path}} {{repo.branch}} {{job.name}} {{notes}}"


def field(title: str, w: QWidget, note: Optional[QWidget] = None) -> QWidget:
    """A label over a control (``.sd-field``)."""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(5)
    lb = QLabel(title)
    lb.setProperty("fs", "small")
    theme.tint(lb, fg="text_2")
    lay.addWidget(lb)
    lay.addWidget(w)
    if note is not None:
        lay.addWidget(note)
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


class PlanBox(Box):
    """The plan as the server returns it: a line of counts and the worst case, then one row per target
    that is not plain ok (blocked first), each with the first note."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(bg="raised/canvas", line="hair/canvas", radius=theme.R3, parent=parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.title = Text("Plan", theme.FS_UI, 600, "text", wrap=False)
        head.addWidget(self.title)
        self.counts = Text("", theme.FS_SMALL, 400, "text_2", wrap=False)
        head.addWidget(self.counts)
        head.addStretch(1)
        self.worst = Text("", theme.FS_SMALL, 400, "text_3", wrap=False)
        head.addWidget(self.worst)
        lay.addLayout(head)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(4)
        lay.addLayout(self.rows)
        self.note = hint("Pick repositories and write the brief; the plan appears here as you type.")
        lay.addWidget(self.note)

    def show_plan(self, plan: Optional[Json], pending: bool = False) -> None:
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        if plan is None:
            self.counts.setText("planning…" if pending else "")
            self.worst.setText("")
            self.note.setVisible(not pending)
            return
        self.note.hide()
        ok, warn, blocked = int(plan.get("ok") or 0), int(plan.get("warnings") or 0), int(plan.get("blocked") or 0)
        parts = [f"{ok} ok"]
        if warn:
            parts.append(f"{warn} warning{'s' if warn != 1 else ''}")
        if blocked:
            parts.append(f"{blocked} blocked")
        self.counts.setText(" · ".join(parts) + (" · planning…" if pending else ""))
        w = plan.get("worst") or {}
        cap = w.get("cost")
        self.worst.setText(
            f"worst case {plural(int(w.get('runs') or 0), 'run')} · {int(w.get('minutes') or 0)} min"
            + (f" · ${float(cap):.0f} cap" if cap else "")
            + (f" · {int(w.get('levels') or 0)} levels" if int(w.get("levels") or 0) > 1 else "")
        )
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
        self.plan_btn = button("Plan", kind="ghost", size="sm", on_click=self.replan)
        hl.addWidget(self.plan_btn)
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

        # ---- targets
        default_root = dirname(store.default_cwd()) or str((store.info or {}).get("home") or "")
        self.targets = TargetPicker(default_root)
        bl.addWidget(field("Targets", self.targets))

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
        rg.addWidget(self.learn, 2, 0, 1, 2)
        bl.addWidget(field("Run", run_grid))

        # ---- prepare, verify
        self.prepare = line_edit("npm ci", mono=True, name="Prepare command")
        self.verify = line_edit("npm test", mono=True, name="Verify command")
        self.verify_retry = Switch(True, "send a failing verify back to the agent once")
        pv = QWidget()
        pg = QGridLayout(pv)
        pg.setContentsMargins(0, 0, 0, 0)
        pg.setHorizontalSpacing(14)
        pg.setVerticalSpacing(10)
        pg.addWidget(field("Prepare (before the agent, in the worktree)", self.prepare), 0, 0)
        pg.addWidget(field("Verify (after the agent)", self.verify), 0, 1)
        pg.addWidget(self.verify_retry, 1, 0, 1, 2)
        bl.addWidget(pv)

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
        fin = QWidget()
        fg = QGridLayout(fin)
        fg.setContentsMargins(0, 0, 0, 0)
        fg.setHorizontalSpacing(14)
        fg.setVerticalSpacing(10)
        fg.addWidget(field("Branch in every repository", self.branch), 0, 0)
        fg.addWidget(field("Commit message", self.message), 0, 1)
        fg.addWidget(field("Report", self.report), 1, 0)
        fg.addWidget(self.pr, 2, 0, 1, 2)
        bl.addWidget(field("Finish", fin))

        # ---- the plan
        self.plan_box = PlanBox()
        bl.addWidget(self.plan_box)
        bl.addStretch(1)

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
        self.name.textChanged.connect(self._name_changed)
        self.branch.textEdited.connect(lambda _t: self.branch.setProperty("auto", False))
        bind(store.connected_changed, self, lambda on: on and self._changed())
        self._playbook_id = ""
        self._filling = False
        bind(store.playbooks_changed, self, lambda _p: self._fill_playbooks())
        self._fill_playbooks()

    # ---- lifecycle
    def eventFilter(self, obj, e) -> bool:  # noqa: N802
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
        self.start_btn.setEnabled(False)
        self.targets.set_selected([])
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
        self.plan_box.show_plan(self.plan, pending=True)
        self._plan_timer.start()

    def replan(self) -> None:
        v = self.values()
        if not v["targets"] or not store.connected:
            self.plan = None
            self.plan_box.show_plan(None)
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
            self.targets.apply_plan(p)
            self.start_btn.setEnabled(
                bool(p.get("targets")) and not int(p.get("blocked") or 0) and bool(v["brief"].strip())
            )

        store.call("dispatch.plan", v, quiet=True).then(got).catch(lambda _e: self.plan_box.show_plan(self.plan))

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
