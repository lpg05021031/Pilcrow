"""The Timeline tab, the time machine (a port of web/src/components/panels/HistoryPanel.tsx): every
step that could have changed files is a point on the scrubber at the top; the list under it is the
same timeline in words. Pick a point to see what that step changed and what has changed since, and
put files back the way they were there."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget

from ... import icons, theme
from ...lib.fmt import basename, duration, err_text, plural
from ...store import ConfirmSpec, store
from ..ui.bits import Clickable, IconButton, Mark, Spinner, button, hairline, label
from ..ui.collapse import Collapse
from ..ui.signals import bind
from .diff_view import DiffView, Num
from .right_panel import EmptyState, Refresher, Toolbar

Json = Dict[str, Any]


def _clock(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


def _clock_s(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def step_title(s: Json) -> str:
    kind = s.get("kind")
    if kind == "message":
        return str(s.get("label") or "Message sent")
    if kind == "start":
        return str(s.get("label") or "Session started")
    if kind == "external":
        return "Changed outside the conversation"
    if kind == "restore":
        return str(s.get("label") or "Restored")
    return str(s.get("label") or s.get("tool") or "Step")


def step_sub(s: Json) -> str:
    kind = s.get("kind")
    if kind == "tool":
        return str(s.get("tool") or "")
    if kind == "external":
        return plural(len(s.get("files") or []), "file") + " changed between turns"
    if kind == "message":
        return "You"
    if kind == "start":
        return "Claude Code"
    return "Timeline"


def off_reason(reason: Optional[str]) -> str:
    if reason in ("home folder", "root of a drive", "too close to the root"):
        where = "your home folder" if reason == "home folder" else "a folder this close to the root"
        return f"Snapshots are not taken for {where} — it would be too large. Start the session in a project folder."
    return reason or "Turn it on in Settings → General."


def do_restore(sid: str, step: Json, paths: Optional[List[str]] = None) -> None:
    """Put files back as they were after ``step`` (all of them, or ``paths``), with an Undo on the toast."""

    def done(r: Any) -> None:
        r = r or {}
        store.bump_fs()
        safety = r.get("safety")
        action = None
        if safety:

            def undo() -> None:
                store.call("timeline.restore", {"id": sid, "n": safety.get("n")}, quiet=True).then(
                    lambda _r: (store.bump_fs(), store.toast("Put the files back", "success"))
                )

            action = {"label": "Undo", "run": undo}
        what = (
            f"Restored {basename(paths[0]) if len(paths) == 1 else plural(len(paths), 'file')}"
            if paths
            else f"Restored the folder to step {step.get('n')}"
        )
        store.toast(what, "success", action)

    store.call("timeline.restore", {"id": sid, "n": step.get("n"), "paths": paths}).then(done)


# ---------------------------------------------------------------- the scrubber


class Scrubber(QWidget):
    """One tick per step along a thin slider, "now" at the right end; drag or click to pick a point."""

    picked = Signal(object)  # step n, or None for now

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.steps: List[Json] = []
        self._sel: Optional[int] = None
        self._quiet = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 8, 20, 8)
        lay.setSpacing(2)
        self.label = label("Now", tone="muted", size="micro")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.label)
        self.slider = TickSlider(self)
        self.slider.setAccessibleName("Timeline")
        self.slider.valueChanged.connect(self._on_value)
        lay.addWidget(self.slider)
        axis = QHBoxLayout()
        axis.setContentsMargins(4, 0, 4, 0)
        self.first = label("", tone="faint", size="micro")
        self.now = label("", tone="faint", size="micro")
        axis.addWidget(self.first)
        axis.addStretch(1)
        axis.addWidget(self.now)
        lay.addLayout(axis)
        lay.addWidget(hairline())

    def set_steps(self, steps: List[Json], sel: Optional[int]) -> None:
        self.steps = steps
        self._sel = sel
        self.slider.set_steps(steps)
        self._quiet = True
        self.slider.setRange(0, len(steps))
        self.slider.setValue(self._index(sel))
        self._quiet = False
        if steps:
            self.first.setText(_clock(float(steps[0].get("at") or 0)))
        self.now.setText(f"now · {_clock(time.time() * 1000)}")
        self._relabel()

    def _index(self, sel: Optional[int]) -> int:
        if sel is None:
            return len(self.steps)
        for i, s in enumerate(self.steps):
            if s.get("n") == sel:
                return i
        return len(self.steps)

    def _relabel(self) -> None:
        i = self.slider.value()
        s = self.steps[i] if i < len(self.steps) else None
        self.label.setText(f"{_clock_s(float(s.get('at') or 0))} · {step_title(s)}" if s else "Now")

    def _on_value(self, v: int) -> None:
        self._relabel()
        if self._quiet:
            return
        self.picked.emit(None if v >= len(self.steps) else self.steps[v].get("n"))


class TickSlider(QSlider):
    """A QSlider drawn thin, with a tick per step (taller for messages, amber for outside changes)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._steps: List[Json] = []
        self.setFixedHeight(28)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setTracking(True)
        self._paint_style()
        bind(theme.theme.changed, self, lambda _t: self._paint_style())

    def set_steps(self, steps: List[Json]) -> None:
        self._steps = steps
        self.update()

    def _paint_style(self) -> None:
        t = theme.tokens()
        self.setStyleSheet(
            f"QSlider::groove:horizontal {{ height: 2px; background: {theme.blend(t.fill_2, t.chrome).name()}; border-radius: 1px; margin: 0 6px; }}"
            f"QSlider::sub-page:horizontal {{ background: {t.text_3}; border-radius: 1px; }}"
            f"QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -5px -6px; border-radius: 6px; background: {t.accent}; border: 2px solid {t.chrome}; }}"
        )

    def mousePressEvent(self, e) -> None:  # noqa: N802
        # Jump to where the pointer is, then let the drag take over, so a click picks a point too.
        if e.button() == Qt.MouseButton.LeftButton and self.maximum() > 0:
            x = e.position().x() - 6
            w = max(1, self.width() - 12)
            self.setValue(int(round(max(0.0, min(1.0, x / w)) * self.maximum())))
        super().mousePressEvent(e)

    def paintEvent(self, e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        n = max(1, self.maximum())
        mid = self.height() // 2
        w = max(1, self.width() - 12)
        for i, s in enumerate(self._steps):
            x = 6 + int(w * i / n)
            kind = s.get("kind")
            tall = kind in ("message", "start", "restore", "external")
            past = i <= self.value()
            if kind == "external":
                color = QColor(t.warn)
            elif kind == "message":
                color = QColor(t.text_3)
            elif kind == "restore":
                color = QColor(t.text_2)
            elif s.get("changed"):
                color = QColor(t.text_2 if past else t.text_4)
            else:
                color = theme.blend(t.hair_3, t.chrome)
            h = 14 if tall else 8
            p.fillRect(QRect(x - 1, mid - h // 2, 2, h), color)
        # the "now" dot at the right end
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(t.text_3), 2))
        p.setBrush(QColor(t.raised))
        p.drawEllipse(QRect(self.width() - 10, mid - 4, 8, 8))
        p.end()
        super().paintEvent(e)


# ---------------------------------------------------------------- steps


class RailDot(QWidget):
    """The mark on the rail: hollow = nothing changed, filled = files changed, a square = you spoke,
    amber = changed outside the conversation, a ring = restored; the chosen one is the accent."""

    def __init__(self, step: Json, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.step = step
        self.selected = False
        self.last = False
        self.setFixedWidth(18)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = 9, 14
        if not self.last:
            p.setPen(QPen(theme.blend(t.hair_2, t.chrome), 1))
            p.drawLine(cx, cy + 6, cx, self.height())
        kind = self.step.get("kind")
        p.setPen(Qt.PenStyle.NoPen)
        if self.selected:
            halo = QColor(t.accent)
            halo.setAlphaF(0.22)
            p.setBrush(halo)
            p.drawEllipse(QRect(cx - 7, cy - 7, 14, 14))
            p.setBrush(QColor(t.accent))
            p.drawEllipse(QRect(cx - 4, cy - 4, 9, 9))
            return
        if kind in ("message", "start"):
            p.setBrush(QColor(t.text_2))
            p.drawRoundedRect(QRect(cx - 4, cy - 4, 8, 8), 2, 2)
        elif kind == "external":
            p.setBrush(QColor(t.warn))
            p.drawEllipse(QRect(cx - 3, cy - 3, 7, 7))
        elif kind == "restore":
            p.setPen(QPen(QColor(t.text_2), 2))
            p.setBrush(QColor(t.raised))
            p.drawEllipse(QRect(cx - 3, cy - 3, 7, 7))
        elif self.step.get("changed"):
            p.setBrush(QColor(t.text_3))
            p.drawEllipse(QRect(cx - 3, cy - 3, 7, 7))
        else:
            p.setPen(QPen(theme.blend(t.hair_3, t.chrome), 1.5))
            p.setBrush(QColor(t.raised))
            p.drawEllipse(QRect(cx - 3, cy - 3, 7, 7))


class RestoreButton(QWidget):
    """ "Restore to here": asks what would change since this step, then confirms before putting it back."""

    def __init__(self, step: Json, sid: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.step, self.sid = step, sid
        self._busy = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.spinner = Spinner(11)
        self.spinner.hide()
        lay.addWidget(self.spinner)
        self.btn = button(
            "Restore to here",
            size="sm",
            icon="rotate-ccw",
            on_click=self.click,
            tooltip="Put every file back to how it was right after this step",
        )
        self.btn.setText("Restore to here")
        lay.addWidget(self.btn)

    def click(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.spinner.show()
        self.btn.setEnabled(False)

        def settle() -> None:
            self._busy = False
            self.spinner.hide()
            self.btn.setEnabled(True)

        def ok(r: Any) -> None:
            settle()
            files = [str(f.get("path")) for f in (r or {}).get("files") or []]
            if not files:
                store.toast("The folder already matches this point.", "info")
                return
            one = len(files) == 1
            store.confirm_action(
                ConfirmSpec(
                    title="Restore everything to this point?",
                    body=f"{'One file goes' if one else f'{len(files)} files go'} back to how {'it was' if one else 'they were'} right after this step. "
                    "A safety point is recorded first, so you can undo this from the timeline. The conversation stays as it is.",
                    list=files,
                    confirm="Restore everything",
                    danger=True,
                    on_confirm=lambda _c: do_restore(self.sid, self.step),
                )
            )

        store.call("timeline.diff", {"id": self.sid, "from": self.step.get("n")}, quiet=True).then(ok).catch(
            lambda _e: settle()
        )


class StepDetail(QWidget):
    """What a step changed: its diff, file by file, each with open and restore."""

    def __init__(self, step: Json, sid: str, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.step, self.sid, self.cwd = step, sid, cwd
        self.setObjectName("tlDetail")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 8, 8)
        lay.setSpacing(6)
        self.view: Optional[DiffView] = None
        if not step.get("changed"):
            kind = step.get("kind")
            note = (
                "Nothing had changed on disk when this message went out."
                if kind == "message"
                else "This is where the timeline begins."
                if kind == "start"
                else "No files changed in this step."
            )
            lay.addWidget(label(note, tone="faint", size="small", wrap=True))
            lay.addWidget(RestoreButton(step, sid), 0, Qt.AlignmentFlag.AlignLeft)
        else:
            head = QHBoxLayout()
            head.addWidget(label("Changed in this step", tone="faint", size="micro"))
            head.addStretch(1)
            head.addWidget(RestoreButton(step, sid))
            lay.addLayout(head)
            self.loading = QWidget()
            ll = QHBoxLayout(self.loading)
            ll.setContentsMargins(0, 0, 0, 0)
            ll.setSpacing(6)
            ll.addWidget(Spinner(12))
            ll.addWidget(label("Loading the diff…", tone="faint", size="small"), 1)
            lay.addWidget(self.loading)
            self.view = DiffView(mode="unified", wrap=True, can_comment=False, max_rows=400, open_threshold=3)
            self.view.set_actions(
                [
                    ("arrow-up-right", lambda _f: "Open the file as it is now", self._open),
                    ("undo-2", lambda _f: "Put this file back to how it was after this step", self._restore),
                ]
            )
            lay.addWidget(self.view)
            store.call("timeline.stepDiff", {"id": sid, "n": step.get("n")}, quiet=True).then(self._got).catch(
                lambda _e: self._got({"files": []})
            )
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _paint(self) -> None:
        t = theme.tokens()
        self.setStyleSheet(
            f"#tlDetail {{ background: {theme.blend(t.fill_1, t.chrome).name()}; border-bottom-left-radius: {theme.R2}px; border-bottom-right-radius: {theme.R2}px; }}"
        )

    def _got(self, r: Any) -> None:
        self.loading.hide()
        if self.view is not None:
            self.view.set_files(list((r or {}).get("files") or []))

    def _open(self, f: Json) -> None:
        store.open_file(f"{self.cwd}/{f.get('path')}")

    def _restore(self, f: Json) -> None:
        path = str(f.get("path") or "")
        store.confirm_action(
            ConfirmSpec(
                title=f"Put {basename(path)} back to this point?",
                body="The file goes back to how it was right after this step. A safety point is recorded first, so you can undo this from the timeline.",
                confirm="Restore file",
                on_confirm=lambda _c: do_restore(self.sid, self.step, [path]),
            )
        )


class StepRow(QWidget):
    """One step in the list: the rail, title and time, +add −del, a way to the conversation; opens to its detail."""

    def __init__(
        self, step: Json, sid: str, cwd: str, on_pick: Callable[[], None], last: bool, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.step = step
        self.n = int(step.get("n") or 0)
        self._selected = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.btn = QWidget()
        self.btn.setObjectName("tlRow")
        self.btn.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setAccessibleName(step_title(step))
        bl = QHBoxLayout(self.btn)
        bl.setContentsMargins(4, 5, 6, 5)
        bl.setSpacing(6)
        self.dot = RailDot(step)
        self.dot.last = last
        bl.addWidget(self.dot)
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(1)
        title = step_title(step)
        is_msg = step.get("kind") == "message"
        self.title = label(f"“{title}”" if is_msg else title, tone="muted" if is_msg else "", size="small")
        if is_msg:
            self.title.setFont(theme.font_ui(theme.FS_SMALL, italic=True))
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        ml.addWidget(self.title)
        sub = QHBoxLayout()
        sub.setSpacing(8)
        sub.addWidget(label(step_sub(step), tone="faint", size="micro"))
        sub.addWidget(label(_clock_s(float(step.get("at") or 0)), tone="faint", size="micro"))
        sub.addStretch(1)
        ml.addLayout(sub)
        bl.addWidget(main, 1)
        if step.get("changed"):
            bl.addWidget(Num("add", int(step.get("add") or 0)))
            bl.addWidget(Num("del", int(step.get("del") or 0)))
        else:
            bl.addWidget(label("—", tone="faint", size="micro"))
        if step.get("toolUseId") or step.get("uuid"):
            jump = IconButton("arrow-up-right", "Show in the conversation", 13)
            jump.clicked.connect(lambda: store.toast("That step is not in the conversation view.", "info"))
            bl.addWidget(jump)
        Clickable(self.btn).clicked.connect(on_pick)
        lay.addWidget(self.btn)
        self.detail = Collapse(lambda: StepDetail(step, sid, cwd), False)
        self.detail.setContentsMargins(22, 0, 0, 6)
        lay.addWidget(self.detail)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def set_selected(self, on: bool) -> None:
        if on == self._selected:
            return
        self._selected = on
        self.dot.selected = on
        self.dot.update()
        self.detail.set_open(on)
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        bg = theme.blend(t.fill_1, t.chrome).name() if self._selected else "transparent"
        radius = (
            f"border-top-left-radius: {theme.R2}px; border-top-right-radius: {theme.R2}px;"
            if self._selected
            else f"border-radius: {theme.R2}px;"
        )
        self.btn.setStyleSheet(
            f"#tlRow {{ background: {bg}; {radius} }} #tlRow:hover {{ background: {theme.blend(t.fill_1, t.chrome).name()}; }}"
        )


class SinceBar(QWidget):
    """The footer while a past step is picked: how far the folder has moved since then."""

    def __init__(self, step: Json, steps: List[Json], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(hairline())
        later = [s for s in steps if int(s.get("n") or 0) > int(step.get("n") or 0) and s.get("changed")]
        files: Dict[str, Dict[str, int]] = {}
        for s in later:
            for f in s.get("files") or []:
                cur = files.setdefault(str(f.get("path")), {"add": 0, "del": 0})
                cur["add"] += int(f.get("add") or 0)
                cur["del"] += int(f.get("del") or 0)
        if not later:
            lb = label("Nothing has changed since this point.", tone="faint", size="small")
            lb.setContentsMargins(14, 10, 14, 10)
            lay.addWidget(lb)
            return
        add = sum(f["add"] for f in files.values())
        dele = sum(f["del"] for f in files.values())
        head = QWidget()
        head.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 8, 12, 8)
        hl.setSpacing(6)
        self.chev = QLabel()
        hl.addWidget(self.chev)
        hl.addWidget(
            label(
                f"Since this point: {plural(len(later), 'step')} changed {plural(len(files), 'file')}",
                tone="muted",
                size="small",
            ),
            1,
        )
        hl.addWidget(Num("add", add))
        hl.addWidget(Num("del", dele))
        lay.addWidget(head)
        self.body = Collapse(lambda: self._build(files), False)
        lay.addWidget(self.body)
        Clickable(head).clicked.connect(self._toggle)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _toggle(self) -> None:
        self.body.toggle()
        self._paint()

    def _paint(self) -> None:
        if hasattr(self, "chev"):
            self.chev.setPixmap(
                icons.pixmap("chevron-down" if self.body.is_open else "chevron-right", theme.tokens().text_3, 12)
            )

    def _build(self, files: Dict[str, Dict[str, int]]) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(30, 0, 12, 8)
        lay.setSpacing(2)
        for path, s in files.items():
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(label(path, tone="muted", size="micro", mono=True), 1)
            row.addWidget(Num("add", s["add"]))
            row.addWidget(Num("del", s["del"]))
            lay.addLayout(row)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setMaximumHeight(180)
        sc.setWidget(w)
        return sc


# ---------------------------------------------------------------- the panel


class HistoryPanel(QWidget):
    """The timeline of one session's folder."""

    def __init__(self, session_id: str, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session_id
        self.cwd = cwd
        self.meta: Optional[Json] = None
        self.sel: Optional[int] = None
        self.follow = True
        self._rows: List[StepRow] = []
        self._since: Optional[QWidget] = None
        self._off: Optional[QWidget] = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._lay = lay
        self.bar = Toolbar()
        scope = label("Timeline")
        scope.setStyleSheet("font-weight: 600;")
        self.bar.add(scope)
        self.summary = label("", tone="faint", size="small")
        self.bar.add(self.summary)
        self.bar.stretch()
        self.now_btn = button(
            "Now",
            kind="ghost",
            size="sm",
            icon="arrow-down-to-line",
            on_click=lambda: self.pick(None),
            tooltip="Back to the present (End)",
        )
        self.now_btn.setText("Now")
        self.now_btn.hide()
        self.bar.add(self.now_btn)
        lay.addWidget(self.bar)
        self.scrubber = Scrubber()
        self.scrubber.picked.connect(self.pick)
        self.scrubber.hide()
        lay.addWidget(self.scrubber)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.host = QWidget()
        self._list = QVBoxLayout(self.host)
        self._list.setContentsMargins(8, 10, 12, 12)
        self._list.setSpacing(0)
        self.loading = label("Loading the timeline…", tone="faint", size="small")
        self.loading.setContentsMargins(6, 6, 6, 6)
        self._list.addWidget(self.loading)
        self.empty = EmptyState(
            "history",
            "Nothing on the timeline yet.",
            "Each step that can change files — an edit, a command, a message — becomes a point here. Pick any point to see what changed and put files back the way they were.",
        )
        self.empty.hide()
        self._list.addWidget(self.empty)
        self.now_row = self._make_now_row()
        self.now_row.hide()
        self._list.addWidget(self.now_row)
        self._list.addStretch(1)
        self.scroll.setWidget(self.host)
        lay.addWidget(self.scroll, 1)
        bind(store.timeline_changed, self, self._on_timeline)
        self._refresher = Refresher(self, session_id, self._update_now, fs=False)
        self._load()

    # ---- data
    def _load(self) -> None:
        def ok(r: Any) -> None:
            r = r or {}
            self.meta = {"enabled": bool(r.get("enabled")), "reason": r.get("reason")}
            store.set_timeline(self.sid, list(r.get("steps") or []))
            self.render()

        def bad(e: Any) -> None:
            self.meta = {"enabled": False, "reason": err_text(e)}
            self.render()

        store.call("timeline.steps", {"id": self.sid}, quiet=True).then(ok).catch(bad)

    def showEvent(self, e) -> None:  # noqa: N802
        # Shown again: ask whether the timeline is on now and pick up steps missed while hidden.
        if self.meta is not None:
            self._load()
        super().showEvent(e)

    def _on_timeline(self, sid: str) -> None:
        if sid != self.sid:
            return
        if self.follow:
            self.sel = None
        self.render()

    @property
    def steps(self) -> List[Json]:
        return list(store.timeline.get(self.sid) or [])

    def pick(self, n: Optional[int]) -> None:
        self.sel = n
        self.follow = n is None
        self.render()
        target = self.now_row if n is None else next((r for r in self._rows if r.n == n), None)
        if target is not None:
            self.scroll.ensureWidgetVisible(target, 0, 24)

    def move(self, d: int) -> None:
        steps = self.steps
        if not steps:
            return
        idx = (
            len(steps)
            if self.sel is None
            else next((i for i, s in enumerate(steps) if s.get("n") == self.sel), len(steps))
        )
        nxt = max(0, min(len(steps), idx + d))
        self.pick(None if nxt >= len(steps) else int(steps[nxt].get("n") or 0))

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        k = e.key()
        if k == Qt.Key.Key_Left:
            self.move(-1)
        elif k == Qt.Key.Key_Right:
            self.move(1)
        elif k == Qt.Key.Key_End:
            self.pick(None)
        elif k == Qt.Key.Key_Home and self.steps:
            self.pick(int(self.steps[0].get("n") or 0))
        else:
            super().keyPressEvent(e)

    # ---- render
    def render(self) -> None:
        meta = self.meta
        if meta is not None and not meta.get("enabled"):
            self.bar.hide()
            self.scrubber.hide()
            self.scroll.hide()
            if self._off is None:
                self._off = EmptyState(
                    "history", "The timeline is off for this folder.", off_reason(meta.get("reason"))
                )
                self._lay.insertWidget(1, self._off)
            return
        steps = self.steps
        self.loading.setVisible(meta is None)
        self.empty.setVisible(meta is not None and not steps)
        self.scrubber.setVisible(bool(steps))
        self.now_row.setVisible(bool(steps))
        self.now_btn.setVisible(self.sel is not None)
        changing = sum(1 for s in steps if s.get("changed"))
        first = float(steps[0].get("at") or 0) if steps else 0
        span = (float(steps[-1].get("at") or first) - first) if steps else 0
        self.summary.setText(
            f"{plural(changing, 'change')} · {duration(span) if span > 60_000 else 'just started'}" if steps else ""
        )
        if steps:
            self.scrubber.set_steps(steps, self.sel)
        # rebuild the rows only when the steps changed; otherwise just move the selection
        if [r.n for r in self._rows] != [int(s.get("n") or 0) for s in steps]:
            for r in self._rows:
                self._list.removeWidget(r)
                r.setParent(None)
                r.deleteLater()
            self._rows = []
            at = self._list.indexOf(self.now_row)
            for i, s in enumerate(steps):
                n = int(s.get("n") or 0)
                row = StepRow(
                    s, self.sid, self.cwd, lambda n=n: self.pick(None if self.sel == n else n), i == len(steps) - 1
                )
                self._list.insertWidget(at + i, row)
                self._rows.append(row)
        for r in self._rows:
            r.set_selected(self.sel == r.n)
        self._update_now()
        if self._since is not None:
            self._lay.removeWidget(self._since)
            self._since.setParent(None)
            self._since.deleteLater()
            self._since = None
        selected = next((s for s in steps if s.get("n") == self.sel), None) if self.sel is not None else None
        if selected is not None:
            self._since = SinceBar(selected, steps)
            self._lay.addWidget(self._since)

    def _make_now_row(self) -> QWidget:
        w = QWidget()
        w.setObjectName("tlNow")
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setCursor(Qt.CursorShape.PointingHandCursor)
        w.setAccessibleName("Now")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 5, 6, 5)
        lay.setSpacing(6)
        rail = QWidget()
        rail.setFixedWidth(18)
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(0, 8, 0, 0)
        rl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        rl.addWidget(Mark(14))
        lay.addWidget(rail)
        main = QVBoxLayout()
        main.setSpacing(1)
        main.addWidget(label("Now", size="small"))
        self.now_sub = label("The folder as it is right now", tone="faint", size="micro", wrap=True)
        main.addWidget(self.now_sub)
        lay.addLayout(main, 1)
        Clickable(w).clicked.connect(lambda: self.pick(None))
        return w

    def _update_now(self) -> None:
        rt = store.rt_of(self.sid)
        busy = rt is not None and rt.status in ("running", "requires_action")
        self.now_sub.setText(
            "Claude is working — new steps appear as they happen" if busy else "The folder as it is right now"
        )
        t = theme.tokens()
        on = self.sel is None
        self.now_row.setStyleSheet(
            f"#tlNow {{ background: {theme.blend(t.fill_1, t.chrome).name() if on else 'transparent'}; border-radius: {theme.R2}px; }} #tlNow:hover {{ background: {theme.blend(t.fill_1, t.chrome).name()}; }}"
        )
