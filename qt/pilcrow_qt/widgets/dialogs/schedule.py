"""Create or edit a schedule (web/src/components/ScheduleDialog.tsx): a name, a folder, the prompt,
the cadence (previewed by ``schedules.preview``), what an unattended run may do, and under More the
model, the time limit, the worktree and whether it is enabled. ``schedules.save`` stores it."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QSize, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ... import icons, theme
from ...lib.fmt import basename, tildify
from ...store import store
from ..folder_picker import FolderPicker
from ..ui.bits import Box, button
from ..ui.menu import Popover, host_for
from ..ui.signals import bind
from .parts import ElideLabel, LayoutButton, Switch, Text, sheet_bg, well_bg, well_line
from .settings_ui import ModelSelect, Select, input_css, line_edit, styled, text_area
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
    """``.field``: a 12px/550 label over the control, 7px apart."""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(7)
    lay.addWidget(Text(title, theme.FS_SMALL, 600, "text_2", wrap=False))
    lay.addWidget(w)
    if note is not None:
        lay.addWidget(note)
    return box


class NumInput(QLineEdit):
    """``input.sd-num``: a 64px number box (the web's ``<input type=number>``), with a spin box's
    ``value``/``setValue``/``valueChanged`` so the form reads it the same way."""

    valueChanged = Signal(int)  # noqa: N815 — QSpinBox's name

    def __init__(self, lo: int, hi: int, value: int, name: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.lo, self.hi = lo, hi
        self._value = value
        self.setObjectName("input")
        self.setFixedSize(64, 34)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAccessibleName(name)
        self.setValidator(QIntValidator(lo, hi, self))
        self.setText(str(value))
        styled(self, lambda t: input_css("#input", t, False, "0 6px"))
        self.textChanged.connect(self._typed)

    def value(self) -> int:
        return self._value

    def setValue(self, v: int) -> None:  # noqa: N802
        v = max(self.lo, min(self.hi, int(v)))
        if v != self._value:
            self._value = v
            self.valueChanged.emit(v)
        if self.text() != str(v):
            self.setText(str(v))

    def _typed(self, text: str) -> None:
        if text.strip().lstrip("-").isdigit():
            v = max(self.lo, min(self.hi, int(text)))
            if v != self._value:
                self._value = v
                self.valueChanged.emit(v)


class TimeInput(QLineEdit):
    """``input.sd-time``: a 112px ``HH:MM`` box (the web's ``<input type=time>``), with ``time``/``setTime``/
    ``timeChanged`` as a QTimeEdit has."""

    timeChanged = Signal(QTime)  # noqa: N815 — QTimeEdit's name

    def __init__(self, t: QTime, name: str = "Time", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._time = t
        self.setObjectName("input")
        self.setFixedSize(112, 34)
        self.setAccessibleName(name)
        self.setInputMask("99:99")
        self.setText(t.toString("HH:mm"))
        styled(self, lambda tk: input_css("#input", tk, False, "0 10px"))
        self.textChanged.connect(self._typed)

    def time(self) -> QTime:
        return self._time

    def setTime(self, t: QTime) -> None:  # noqa: N802
        if t != self._time:
            self._time = t
            self.timeChanged.emit(t)
        if self.text() != t.toString("HH:mm"):
            self.setText(t.toString("HH:mm"))

    def _typed(self, text: str) -> None:
        t = QTime.fromString(text, "HH:mm")
        if t.isValid() and t != self._time:
            self._time = t
            self.timeChanged.emit(t)


class FolderButton(LayoutButton):
    """``.ns-folder.sd-folder``: the folder as a 36px button that opens the folder picker in a popover.
    ``text()``/``setText()`` keep the field's shape for the form."""

    textChanged = Signal(str)  # noqa: N815 — the QLineEdit name, so the form treats both alike

    def __init__(self, value: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._value = value
        self._pop: Optional[Popover] = None
        self.setObjectName("sdfolder")
        self.setAutoDefault(False)
        self.setFixedHeight(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Folder")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 5, 0)
        lay.setSpacing(4)
        self.ico = QLabel()
        self.ico.setFixedSize(14, 14)
        lay.addWidget(self.ico)
        lay.addSpacing(4)
        self.label = Text("", theme.FS_UI, 400, "text", wrap=False)
        lay.addWidget(self.label, 1)
        self.chev = QLabel()
        self.chev.setFixedSize(13, 13)
        lay.addWidget(self.chev)
        for w in (self.ico, self.label, self.chev):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.clicked.connect(self.open)
        self.set_look(
            bg=lambda t: theme.over(theme.qcolor(t.fill_1), sheet_bg(t)),
            hover=lambda t: theme.over(theme.qcolor(t.fill_2), sheet_bg(t)),
            on=lambda t: theme.over(theme.qcolor(t.fill_2), sheet_bg(t)),
            line=lambda t: theme.over(theme.qcolor(t.hair), theme.over(theme.qcolor(t.fill_1), sheet_bg(t))),
            radius=theme.R3,
        )
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.ico.setPixmap(icons.pixmap("folder", t.text_3, 14))
        self.chev.setPixmap(icons.pixmap("chevron-down", t.text_3, 13))
        self.label.setText(basename(self._value) or "Pick a folder")
        self.label.set_color("text" if self._value else "text_3")
        self.setToolTip(tildify(self._value, (store.info or {}).get("home")))

    def text(self) -> str:
        return self._value

    def setText(self, v: str) -> None:  # noqa: N802
        if v != self._value:
            self._value = v
            self._paint()
            self.textChanged.emit(v)

    def open(self) -> None:  # noqa: A003
        if self._pop is not None and self._pop.is_open:
            self._pop.dismiss()
            return
        host = host_for(self)
        if host is None:
            return
        pop = Popover(host, 380)
        pop.lay.setContentsMargins(0, 0, 0, 0)

        def picked(d: str) -> None:
            self.setText(d)
            pop.dismiss()

        pop.set_content(FolderPicker(self._value, picked))
        pop.closed.connect(lambda: self._set_open(False))
        self._pop = pop
        pop.open_at(self, "bottom-start")
        self._set_open(True)

    def _set_open(self, on: bool) -> None:
        self.look_active = on
        self.update()


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
        self.setFixedHeight(35)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(10)
        self.ico = QLabel()
        lay.addWidget(self.ico)
        self.label = QLabel(label)
        self.label.setFont(theme.font_ui(theme.FS_UI, 600))
        lay.addWidget(self.label)
        self.hint = ElideLabel(hint_text)
        self.hint.setFont(theme.font_ui(theme.FS_SMALL))
        theme.tint(self.hint, fg="text_3")
        lay.addWidget(self.hint, 1)
        self.check = QLabel()
        self.check.setFixedWidth(16)
        lay.addWidget(self.check)
        self.set_look(
            hover=lambda t: theme.over(theme.qcolor(t.fill_1), well_bg(t)),
            on=lambda t: theme.over(theme.qcolor(t.fill_2), well_bg(t)),
        )
        self.toggled.connect(lambda _c: self._paint())
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        on = self.isChecked()
        self.ico.setPixmap(icons.pixmap(self._icon, t.err if self._danger else (t.text if on else t.text_3), 14))
        theme.tint(self.label, fg="err" if self._danger else ("text" if on else "text_2"))
        self.check.setPixmap(icons.pixmap("check", t.text, 14) if on else icons.pixmap("check", "transparent", 14))
        self.look_active = on
        self.update()


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
        tl.setHorizontalSpacing(12)
        self.name = line_edit("Nightly dependency audit", name="Name")
        self.name.setProperty("autofocus", True)
        tl.addWidget(_field("Name", self.name), 0, 0)
        self.cwd = FolderButton(default_cwd)
        tl.addWidget(_field("Folder", self.cwd), 0, 1)
        self.body_lay.addWidget(two)

        # ---- prompt
        self.prompt = text_area(
            "Run the test suite, fix any failing tests you can, and summarise what changed. Do not push.",
            rows=4,
            name="What Claude does each time",
        )
        self.body_lay.addWidget(
            _field(
                "What Claude does each time",
                self.prompt,
                Text(
                    "Nobody is there to answer questions during a run, so say what to assume and what to leave alone.",
                    theme.FS_SMALL,
                    400,
                    "text_3",
                    1.5,
                ),
            )
        )

        # ---- when
        when = QWidget()
        wl = QHBoxLayout(when)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(8)
        self.kind = Select("Cadence", popup_width=240)
        for k in KINDS:
            self.kind.addItem(k["label"], k["value"], hint=k["hint"])
        self.kind.setFixedWidth(170)
        self.kind.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.kind.currentIndexChanged.connect(lambda _i: self._on_cadence())
        wl.addWidget(self.kind)
        self.weekday = Select("Day of the week", popup_width=170)
        for i, d in enumerate(DAYS):
            self.weekday.addItem(d, i)
        self.weekday.setCurrentIndex(1)
        self.weekday.setFixedWidth(170)
        self.weekday.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.weekday.currentIndexChanged.connect(lambda _i: self._on_cadence())
        wl.addWidget(self.weekday)
        self.every_lb = Text("every", theme.FS_SMALL, 400, "text_2", wrap=False)
        wl.addWidget(self.every_lb)
        self.every = NumInput(2, 48, 6, "Every how many hours")
        self.every.valueChanged.connect(lambda _v: self._on_cadence())
        wl.addWidget(self.every)
        self.every_lb2 = Text("hours, from", theme.FS_SMALL, 400, "text_2", wrap=False)
        wl.addWidget(self.every_lb2)
        self.at_lb = Text("at", theme.FS_SMALL, 400, "text_2", wrap=False)
        wl.addWidget(self.at_lb)
        self.minute = NumInput(0, 59, 0, "Minute past the hour")
        self.minute.valueChanged.connect(lambda _v: self._on_cadence())
        wl.addWidget(self.minute)
        self.time = TimeInput(QTime(9, 0))
        self.time.timeChanged.connect(lambda _t: self._on_cadence())
        wl.addWidget(self.time)
        self.cron = line_edit("0 9 * * 1-5", mono=True, name="Cron expression")
        self.cron.setFixedWidth(180)
        self.cron.textChanged.connect(lambda _s: self._on_cadence())
        wl.addWidget(self.cron)
        wl.addStretch(1)
        self.body_lay.addWidget(_field("When", when))

        # ---- mode
        radios = Box(bg=well_bg, line=well_line, radius=theme.R3)
        radios.setObjectName("radio-list")
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
        self.more_btn.setObjectName("disclose")
        self.more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_btn.setFixedHeight(36)
        self.more_btn.setAccessibleName("More")
        ml = QHBoxLayout(self.more_btn)
        ml.setContentsMargins(8, 0, 8, 0)
        ml.setSpacing(8)
        self.more_chev = QLabel()
        self.more_chev.setFixedSize(14, 14)
        ml.addWidget(self.more_chev)
        more_title = Text("More", theme.FS_UI, 600, "text_2", wrap=False)
        ml.addWidget(more_title)
        self.more_sum = Text("", theme.FS_SMALL, 400, "text_3", wrap=False)
        ml.addWidget(self.more_sum, 1)
        self.more_btn.set_look(hover=lambda t: theme.over(theme.qcolor(t.fill_1), sheet_bg(t)), radius=theme.R2)
        self.more_btn.clicked.connect(self.toggle_more)
        self.body_lay.addWidget(self.more_btn)

        self.more_box = QWidget()
        mb = QVBoxLayout(self.more_box)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.setSpacing(16)
        two2 = QWidget()
        g2 = QGridLayout(two2)
        g2.setContentsMargins(0, 0, 0, 0)
        g2.setHorizontalSpacing(12)
        self.model = ModelSelect("", name="Model")
        self.model.changed.connect(lambda _v, _typing: self._summary())
        g2.addWidget(_field("Model", self.model), 0, 0)
        stop = QWidget()
        sl = QHBoxLayout(stop)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(8)
        self.max_minutes = NumInput(1, 240, 45, "Stop after minutes")
        self.max_minutes.valueChanged.connect(lambda _v: self._summary())
        sl.addWidget(self.max_minutes)
        sl.addWidget(Text("minutes", theme.FS_SMALL, 400, "text_2", wrap=False))
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
        self.preview_lb = Text("", theme.FS_SMALL, 400, "text_3", 1.5)
        self.preview_lb.set_max_width(300)
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
        self.model.set_value(str(s.get("model") or ""))
        self.worktree.setChecked(bool(s.get("worktree")))
        self.max_minutes.setValue(int(s.get("maxMinutes") or 45))
        self.enabled.setChecked(bool(s.get("enabled", True)))
        self._summary()

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
            self.preview_lb.set_color("err")
            self.preview_lb.setText(str(err))
        elif isinstance(nxt, (int, float)) and nxt > 0:
            when = datetime.fromtimestamp(nxt / 1000).strftime("%a, %b %d %H:%M").replace(" 0", " ")
            self.preview_lb.set_color("text_3")
            self.preview_lb.setText(f"{label} · first run {when}")
        else:
            self.preview_lb.set_color("text_3")
            self.preview_lb.setText(label)
        _ = t

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
        model = self.model.value
        parts = [
            model.replace("claude-", "", 1) if model else "default model",
            f"stops after {self.max_minutes.value()} min",
        ]
        if self.worktree.isChecked():
            parts.append("isolated worktree")
        if not self.enabled.isChecked():
            parts.append("paused")
        self.more_sum.setText("" if self._more else " · ".join(parts))

    # ---- save
    def values(self) -> Json:
        model = self.model.value
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
    col.addWidget(Text(title, theme.FS_UI, 600))
    col.addWidget(Text(note, theme.FS_SMALL, 400, "text_3", 1.5))
    lay.addLayout(col, 1)
    lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return box


_ = (QSize, basename, List)
