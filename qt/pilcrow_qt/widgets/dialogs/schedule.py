"""Create or edit a schedule (web/src/components/ScheduleDialog.tsx): a name, a folder, the prompt,
the cadence (previewed by ``schedules.preview``), what an unattended run may do, and under More the
model, the time limit, the worktree and whether it is enabled. ``schedules.save`` stores it."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QSize, Qt, QTime, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ... import icons, theme
from ...lib.fmt import basename, tildify
from ...store import store
from ..ui.bits import IconButton, button
from ..ui.signals import bind
from .parts import ElideLabel, LayoutButton, Switch, field_label, hint
from .sheet import Sheet

Json = Dict[str, Any]

KINDS = [
    {"value": "daily", "label": "Every day", "hint": "At a time you pick"},
    {"value": "weekdays", "label": "Weekdays", "hint": "Monday to Friday"},
    {"value": "weekly", "label": "Every week", "hint": "One day of the week"},
    {"value": "hourly", "label": "Every hour", "hint": "At a minute past the hour"},
    {"value": "interval", "label": "Every few hours", "hint": "Anchored to a time of day"},
    {"value": "cron", "label": "Custom (cron)", "hint": "Five-field cron expression"},
]
DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

# How an unattended run may act. Prompts cannot be answered, so the choice is what it may do without asking.
RUN_MODES = [
    {
        "id": "acceptEdits",
        "label": "Edit files",
        "hint": "Edits are applied; commands that would ask are skipped and reported",
        "icon": "file-pen",
    },
    {"id": "plan", "label": "Read only", "hint": "Explores and reports; changes nothing", "icon": "map"},
    {
        "id": "default",
        "label": "Ask-level only",
        "hint": "Only what your settings already allow; everything else is skipped",
        "icon": "shield-check",
    },
    {
        "id": "bypassPermissions",
        "label": "Everything",
        "hint": "Runs any command without asking — only for folders you trust",
        "icon": "shield-off",
    },
]


def _field(title: str, w: QWidget, note: Optional[QWidget] = None) -> QWidget:
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(7)
    lay.addWidget(field_label(title))
    lay.addWidget(w)
    if note is not None:
        lay.addWidget(note)
    return box


class RadioRow(LayoutButton):
    """One choice of a radio list (``.radio-row``): icon, label, hint, and a check when chosen."""

    def __init__(
        self, icon: str, label: str, hint_text: str, danger: bool = False, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._icon, self._danger = icon, danger
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(label)
        self.setMinimumHeight(35)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(10)
        self.ico = QLabel()
        lay.addWidget(self.ico)
        self.label = QLabel(label)
        lay.addWidget(self.label)
        self.hint = ElideLabel(hint_text)
        self.hint.setProperty("size", "small")
        lay.addWidget(self.hint, 1)
        self.check = QLabel()
        self.check.setFixedWidth(16)
        lay.addWidget(self.check)
        self.toggled.connect(lambda _c: self._paint())
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        on = self.isChecked()
        fg = t.err if self._danger else (t.text if on else t.text_2)
        self.ico.setPixmap(icons.pixmap(self._icon, t.err if self._danger else (t.text if on else t.text_3), 14))
        self.label.setStyleSheet(f"color: {fg}; font-weight: 550;")
        self.hint.setStyleSheet(f"color: {t.text_3}; font-size: {theme.FS_SMALL}px;")
        self.check.setPixmap(icons.pixmap("check", t.text, 14) if on else icons.pixmap("check", "transparent", 14))
        bg = theme.blend(t.fill_2, t.canvas).name() if on else "transparent"
        self.setStyleSheet(
            f"QPushButton {{ background: {bg}; border: none; border-radius: 0; text-align: left; padding: 0; }} "
            f"QPushButton:hover {{ background: {theme.blend(t.fill_1 if not on else t.fill_2, t.canvas).name()}; }}"
        )


class ScheduleDialog(Sheet):
    def __init__(self, window: QWidget, spec: Json) -> None:
        self.spec = spec or {}
        self.schedule: Optional[Json] = None
        editing = bool(self.spec.get("id"))
        super().__init__(
            window,
            "schedule",
            None,
            width=580,
            title="Edit schedule" if editing else "New schedule",
            icon="calendar-clock",
        )
        self._busy = False
        self._more = False
        self.preview: Optional[Json] = None
        self.preview_state = "idle"  # idle | loading | ready | error
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(150)
        self._preview_timer.timeout.connect(self._ask_preview)
        self.body_lay.setSpacing(16)
        t = theme.tokens()
        info = store.info or {}
        config = store.config or {}
        first = next((s for s in store.sessions if not s.get("archived")), None)
        default_cwd = str(
            self.spec.get("cwd")
            or (first or {}).get("project")
            or ((config.get("recentProjects") or [""])[0])
            or info.get("home")
            or ""
        )

        # ---- name, folder
        two = QWidget()
        tl = QGridLayout(two)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setHorizontalSpacing(14)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Nightly dependency audit")
        self.name.setProperty("autofocus", True)
        self.name.setAccessibleName("Name")
        tl.addWidget(_field("Name", self.name), 0, 0)
        folder = QWidget()
        fl = QHBoxLayout(folder)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(4)
        self.cwd = QLineEdit(default_cwd)
        self.cwd.addAction(icons.icon("folder", t.text_3, 14), QLineEdit.ActionPosition.LeadingPosition)
        self.cwd.setPlaceholderText("Pick a folder")
        self.cwd.setAccessibleName("Folder")
        self.cwd.setToolTip(tildify(default_cwd, info.get("home")))
        self.cwd.textChanged.connect(lambda p: self.cwd.setToolTip(tildify(p, (store.info or {}).get("home"))))
        fl.addWidget(self.cwd, 1)
        self.browse = IconButton("folder-open", "Pick a folder", 14)
        self.browse.clicked.connect(self._browse)
        fl.addWidget(self.browse)
        tl.addWidget(_field("Folder", folder), 0, 1)
        self.body_lay.addWidget(two)

        # ---- prompt
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText(
            "Run the test suite, fix any failing tests you can, and summarise what changed. Do not push."
        )
        self.prompt.setFixedHeight(96)
        self.prompt.setAccessibleName("What Claude does each time")
        self.body_lay.addWidget(
            _field(
                "What Claude does each time",
                self.prompt,
                hint(
                    "Nobody is there to answer questions during a run, so say what to assume and what to leave alone."
                ),
            )
        )

        # ---- when
        when = QWidget()
        wl = QHBoxLayout(when)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(8)
        self.kind = QComboBox()
        for k in KINDS:
            self.kind.addItem(k["label"], k["value"])
            self.kind.setItemData(self.kind.count() - 1, k["hint"], Qt.ItemDataRole.ToolTipRole)
        self.kind.setMinimumWidth(200)
        self.kind.setAccessibleName("Cadence")
        self.kind.currentIndexChanged.connect(lambda _i: self._on_cadence())
        wl.addWidget(self.kind)
        self.weekday = QComboBox()
        for i, d in enumerate(DAYS):
            self.weekday.addItem(d, i)
        self.weekday.setCurrentIndex(1)
        self.weekday.setAccessibleName("Day of the week")
        self.weekday.currentIndexChanged.connect(lambda _i: self._on_cadence())
        wl.addWidget(self.weekday)
        self.every_lb = QLabel("every")
        wl.addWidget(self.every_lb)
        self.every = QSpinBox()
        self.every.setRange(2, 48)
        self.every.setValue(6)
        self.every.setAccessibleName("Every how many hours")
        self.every.valueChanged.connect(lambda _v: self._on_cadence())
        wl.addWidget(self.every)
        self.every_lb2 = QLabel("hours, from")
        wl.addWidget(self.every_lb2)
        self.at_lb = QLabel("at")
        wl.addWidget(self.at_lb)
        self.minute = QSpinBox()
        self.minute.setRange(0, 59)
        self.minute.setAccessibleName("Minute past the hour")
        self.minute.valueChanged.connect(lambda _v: self._on_cadence())
        wl.addWidget(self.minute)
        self.time = QTimeEdit(QTime(9, 0))
        self.time.setDisplayFormat("HH:mm")
        self.time.setAccessibleName("Time")
        self.time.timeChanged.connect(lambda _t: self._on_cadence())
        wl.addWidget(self.time)
        self.cron = QLineEdit()
        self.cron.setFont(theme.font_mono())
        self.cron.setPlaceholderText("0 9 * * 1-5")
        self.cron.setAccessibleName("Cron expression")
        self.cron.textChanged.connect(lambda _s: self._on_cadence())
        wl.addWidget(self.cron, 1)
        wl.addStretch(1)
        self.body_lay.addWidget(_field("When", when))

        # ---- mode
        radios = QWidget()
        radios.setObjectName("radio-list")
        radios.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        radios.setStyleSheet(
            f"#radio-list {{ background: {theme.blend(t.fill_1, t.canvas).name()}; border: 1px solid {theme.blend(t.hair, t.canvas).name()}; border-radius: {theme.R3}px; }}"
        )
        rl = QVBoxLayout(radios)
        rl.setContentsMargins(1, 1, 1, 1)
        rl.setSpacing(0)
        self.mode_rows: Dict[str, RadioRow] = {}
        for m in RUN_MODES:
            if m["id"] == "bypassPermissions" and info.get("isRoot"):
                continue
            row = RadioRow(m["icon"], m["label"], m["hint"], danger=m["id"] == "bypassPermissions")
            row.clicked.connect(lambda _c=False, mid=m["id"]: self.set_mode(mid))
            self.mode_rows[m["id"]] = row
            rl.addWidget(row)
        self.body_lay.addWidget(_field("While it runs, Claude may", radios))

        # ---- more
        self.more_btn = LayoutButton()
        self.more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_btn.setFixedHeight(36)
        self.more_btn.setAccessibleName("More")
        ml = QHBoxLayout(self.more_btn)
        ml.setContentsMargins(8, 0, 8, 0)
        ml.setSpacing(8)
        self.more_chev = QLabel()
        ml.addWidget(self.more_chev)
        more_title = QLabel("More")
        more_title.setStyleSheet("font-weight: 550;")
        ml.addWidget(more_title)
        self.more_sum = QLabel()
        self.more_sum.setStyleSheet(f"color: {t.text_3}; font-size: {theme.FS_SMALL}px;")
        ml.addWidget(self.more_sum, 1)
        self.more_btn.setStyleSheet(
            f"QPushButton {{ border: none; border-radius: {theme.R2}px; text-align: left; color: {t.text_2}; }} QPushButton:hover {{ background: {theme.blend(t.fill_1, t.canvas).name()}; }}"
        )
        self.more_btn.clicked.connect(self.toggle_more)
        self.body_lay.addWidget(self.more_btn)

        self.more_box = QWidget()
        mb = QVBoxLayout(self.more_box)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.setSpacing(16)
        two2 = QWidget()
        g2 = QGridLayout(two2)
        g2.setContentsMargins(0, 0, 0, 0)
        g2.setHorizontalSpacing(14)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItem("Default model", "")
        self.model.setAccessibleName("Model")
        self.model.currentTextChanged.connect(lambda _s: self._summary())
        bind(store.gateway_models_changed, self, lambda _m: self._fill_models())
        self._fill_models()
        g2.addWidget(_field("Model", self.model), 0, 0)
        stop = QWidget()
        sl = QHBoxLayout(stop)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(8)
        self.max_minutes = QSpinBox()
        self.max_minutes.setRange(1, 240)
        self.max_minutes.setValue(45)
        self.max_minutes.setAccessibleName("Stop after minutes")
        self.max_minutes.valueChanged.connect(lambda _v: self._summary())
        sl.addWidget(self.max_minutes)
        sl.addWidget(QLabel("minutes"))
        sl.addStretch(1)
        g2.addWidget(_field("Stop after", stop), 0, 1)
        mb.addWidget(two2)
        self.worktree = Switch(False, "Use a worktree")
        self.worktree.toggled.connect(lambda _c: self._summary())
        mb.addWidget(
            _row(
                "Isolated worktree",
                "Each run gets its own branch and checkout, so it never collides with your work.",
                self.worktree,
            )
        )
        self.enabled = Switch(True, "Enabled")
        self.enabled.toggled.connect(lambda _c: self._summary())
        mb.addWidget(
            _row("Enabled", "Paused schedules keep their settings and can still be run by hand.", self.enabled)
        )
        self.more_box.hide()
        self.body_lay.addWidget(self.more_box)

        # ---- footer
        self.preview_lb = QLabel("")
        self.preview_lb.setProperty("tone", "faint")
        self.preview_lb.setProperty("size", "small")
        self.preview_lb.setWordWrap(True)
        self.foot_lay.addWidget(self.preview_lb, 1)
        self.cancel_btn = button("Cancel", on_click=self.reject)
        self.save_btn = button("Save" if editing else "Create schedule", kind="primary", on_click=self.save)
        self.save_btn.setDefault(False)
        self.add_footer(self.cancel_btn, self.save_btn, stretch_before=False)

        self._mode = "acceptEdits"
        self.set_mode(self._mode)
        self._on_cadence()
        self._summary()
        if editing:
            store.call("schedules.list", {}, quiet=True).then(self._got_schedules).catch(lambda _e: None)

    # ---- loading an existing schedule
    def _got_schedules(self, lst: Any) -> None:
        if self._closed or not isinstance(lst, list):
            return
        s = next((x for x in lst if isinstance(x, dict) and x.get("id") == self.spec.get("id")), None)
        if s is None:
            return
        self.schedule = s
        self.name.setText(str(s.get("name") or ""))
        self.cwd.setText(str(s.get("cwd") or ""))
        self.prompt.setPlainText(str(s.get("prompt") or ""))
        self.set_cadence(s.get("cadence") or {"kind": "daily", "time": "09:00"})
        self.set_mode(str(s.get("mode") or "acceptEdits"))
        model = str(s.get("model") or "")
        i = self.model.findData(model)
        if i >= 0:
            self.model.setCurrentIndex(i)
        else:
            self.model.setEditText(model)
        self.worktree.setChecked(bool(s.get("worktree")))
        self.max_minutes.setValue(int(s.get("maxMinutes") or 45))
        self.enabled.setChecked(bool(s.get("enabled", True)))
        self._summary()

    def _fill_models(self) -> None:
        gm = store.gateway_models
        cur = self.model.currentData() or self.model.currentText()
        while self.model.count() > 1:
            self.model.removeItem(1)
        if isinstance(gm, dict):
            for m in gm.get("models") or []:
                if isinstance(m, dict) and m.get("id"):
                    self.model.addItem(str(m.get("displayName") or m["id"]), str(m["id"]))
        i = self.model.findData(cur)
        if i >= 0:
            self.model.setCurrentIndex(i)

    # ---- cadence
    def cadence(self) -> Json:
        kind = str(self.kind.currentData())
        time_s = self.time.time().toString("HH:mm")
        c: Json = {"kind": kind}
        if kind == "hourly":
            c["time"] = f"00:{self.minute.value():02d}"
        elif kind == "cron":
            c["cron"] = self.cron.text()
        else:
            c["time"] = time_s
        if kind == "weekly":
            c["weekday"] = int(self.weekday.currentData())
        if kind == "interval":
            c["everyHours"] = int(self.every.value())
        return c

    def set_cadence(self, c: Json) -> None:
        kind = str(c.get("kind") or "daily")
        i = self.kind.findData(kind)
        self.kind.blockSignals(True)
        self.kind.setCurrentIndex(max(0, i))
        self.kind.blockSignals(False)
        t = str(c.get("time") or "09:00")
        hh, _, mm = t.partition(":")
        if kind == "hourly":
            self.minute.setValue(int(mm or 0))
        else:
            self.time.setTime(QTime(int(hh or 9), int(mm or 0)))
        if c.get("weekday") is not None:
            self.weekday.setCurrentIndex(int(c["weekday"]))
        if c.get("everyHours"):
            self.every.setValue(int(c["everyHours"]))
        if c.get("cron"):
            self.cron.setText(str(c["cron"]))
        self._on_cadence()

    def _on_cadence(self) -> None:
        kind = str(self.kind.currentData())
        self.weekday.setVisible(kind == "weekly")
        for w in (self.every_lb, self.every, self.every_lb2):
            w.setVisible(kind == "interval")
        self.at_lb.setText("at minute" if kind == "hourly" else "at")
        self.at_lb.setVisible(kind != "cron")
        self.minute.setVisible(kind == "hourly")
        self.time.setVisible(kind not in ("hourly", "cron"))
        self.cron.setVisible(kind == "cron")
        self.preview_state = "loading"
        self._preview_timer.start()

    def _ask_preview(self) -> None:
        if self._closed:
            return
        cadence = self.cadence()

        def got(r: Any) -> None:
            if self._closed or self.cadence() != cadence:
                return
            self._apply_preview(r if isinstance(r, dict) else {})

        def bad(_e: Any) -> None:
            if self._closed:
                return
            self.preview = None
            self.preview_state = "error"
            self.preview_lb.setText("")

        store.call("schedules.preview", {"cadence": cadence}, quiet=True).then(got).catch(bad)

    def _apply_preview(self, r: Json) -> None:
        """The server may answer with null fields (no next run known); show what it did say."""
        self.preview = r
        self.preview_state = "ready"
        err = r.get("error")
        nxt = r.get("nextRunAt")
        label = str(r.get("label") or "")
        t = theme.tokens()
        if err:
            self.preview_lb.setStyleSheet(f"color: {t.err}; font-size: {theme.FS_SMALL}px;")
            self.preview_lb.setText(str(err))
        elif isinstance(nxt, (int, float)) and nxt > 0:
            when = datetime.fromtimestamp(nxt / 1000).strftime("%a, %b %d %H:%M").replace(" 0", " ")
            self.preview_lb.setStyleSheet(f"color: {t.text_3}; font-size: {theme.FS_SMALL}px;")
            self.preview_lb.setText(f"{label} · first run {when}")
        else:
            self.preview_lb.setStyleSheet(f"color: {t.text_3}; font-size: {theme.FS_SMALL}px;")
            self.preview_lb.setText(label)

    # ---- mode, more
    def set_mode(self, mode: str) -> None:
        self._mode = mode if mode in self.mode_rows else "acceptEdits"
        for mid, row in self.mode_rows.items():
            row.setChecked(mid == self._mode)

    def toggle_more(self) -> None:
        self._more = not self._more
        self.more_box.setVisible(self._more)
        self._summary()
        self.place()

    def _summary(self) -> None:
        t = theme.tokens()
        self.more_chev.setPixmap(icons.pixmap("chevron-down" if self._more else "chevron-right", t.text_4, 14))
        model = self.model.currentData() if self.model.currentData() else self.model.currentText()
        if model == "Default model":
            model = ""
        parts = [
            model.replace("claude-", "", 1) if model else "default model",
            f"stops after {self.max_minutes.value()} min",
        ]
        if self.worktree.isChecked():
            parts.append("isolated worktree")
        if not self.enabled.isChecked():
            parts.append("paused")
        self.more_sum.setText("" if self._more else " · ".join(parts))

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self,
            "Pick a folder",
            self.cwd.text() or "",
            QFileDialog.Option.DontUseNativeDialog | QFileDialog.Option.ShowDirsOnly,
        )
        if d:
            self.cwd.setText(d)

    # ---- save
    def values(self) -> Json:
        model = self.model.currentData() if self.model.currentData() else self.model.currentText()
        if model == "Default model":
            model = ""
        out: Json = {
            "name": self.name.text().strip(),
            "cwd": self.cwd.text().strip(),
            "prompt": self.prompt.toPlainText(),
            "cadence": self.cadence(),
            "mode": self._mode,
            "model": model,
            "worktree": self.worktree.isChecked(),
            "maxMinutes": int(self.max_minutes.value()),
            "enabled": self.enabled.isChecked(),
        }
        if self.spec.get("id"):
            out["id"] = self.spec["id"]
        return out

    def save(self) -> None:
        if self._busy:
            return
        v = self.values()
        if not v["name"]:
            store.toast("Give the schedule a name.", "warning")
            return
        if not v["prompt"].strip():
            store.toast("Write what Claude should do each time.", "warning")
            return
        self._busy = True
        self.save_btn.setEnabled(False)
        editing = bool(self.spec.get("id"))

        def ok(_r: Any) -> None:
            store.toast("Schedule updated" if editing else f"Scheduled: {v['name']}", "success")
            self.reject()

        def bad(_e: Any) -> None:
            self._busy = False
            if not self._closed:
                self.save_btn.setEnabled(True)

        store.call("schedules.save", v).then(ok).catch(bad)

    def submit(self) -> None:
        self.save()


def _row(title: str, note: str, control: QWidget) -> QWidget:
    """A settings row (``.ns-row``): a title and a hint on the left, the control on the right."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(16)
    col = QVBoxLayout()
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(2)
    t = QLabel(title)
    t.setStyleSheet("font-weight: 550;")
    col.addWidget(t)
    col.addWidget(hint(note))
    lay.addLayout(col, 1)
    lay.addWidget(control, 0, Qt.AlignmentFlag.AlignTop)
    return box


_ = (QSize, basename, List)
