"""The Timeline tab, the time machine (a port of web/src/components/panels/HistoryPanel.tsx): every
step that could have changed files is a point on the scrubber at the top; the list under it is the
same timeline in words. Pick a point to see what that step changed and what has changed since, and
put files back the way they were there. Measurements are panels.css's ``.tl-*`` rules."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QSlider, QVBoxLayout, QWidget

from ... import icons, theme
from ...lib.fmt import basename, duration, err_text, plural
from ...store import ConfirmSpec, store
from ..ui.bits import Clickable, Pill, Spinner
from ..ui.collapse import Collapse
from ..ui.signals import bind
from .diff_view import DiffView, Num
from .right_panel import Btn, EmptyState, Hairline, Refresher, Toolbar, elide, lbl

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
    return reason or "Snapshots of the folder after every step, so any point can be revisited or restored."


def scrub_positions(steps: List[Json], now: float, track_w: int) -> List[float]:
    """Where each step sits on the scrubber, 0..96 (% of the track): by time, with long pauses squeezed
    so the busy stretches keep their shape, and steps nudged apart so each can still be picked."""
    times = [float(s.get("at") or now) for s in steps] + [now]
    gaps = [max(0.0, times[i + 1] - times[i]) for i in range(len(times) - 1)]
    sorted_gaps = sorted(gaps)
    median = sorted_gaps[len(sorted_gaps) // 2] if sorted_gaps else 1000.0
    cap = max(median * 4, 20_000.0)
    w = [min(g, cap) for g in gaps]
    total = max(1.0, sum(w))
    raw: List[float] = []
    acc = 0.0
    for i in range(len(steps)):
        raw.append(acc / total * 96)
        acc += w[i]
    min_gap = 6 / max(1, track_w) * 100 if track_w else 1.2
    for i in range(1, len(raw)):
        if raw[i] < raw[i - 1] + min_gap:
            raw[i] = raw[i - 1] + min_gap
    over = (raw[-1] - 96) if raw else 0
    if over > 0:
        n = max(1, len(raw) - 1)
        raw = [max(0.0, x - over * (i / n)) for i, x in enumerate(raw)]
    return raw


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


class TickSlider(QSlider):
    """The track (``.tl-scrub-track``): one tick per step where it happened in time, taller for
    messages, amber for outside changes; the accent cursor on the chosen one; "now" at the right end.
    A QSlider for its value/range API; it paints and picks itself."""

    hovered = Signal(int)  # index under the pointer, or -1

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._steps: List[Json] = []
        self._pos: List[float] = []
        self._now = time.time() * 1000
        self._drag = False
        self._hover = -1
        self.setFixedHeight(28)
        self.pad = 20  # .tl-scrub padding: the end marks spill into it
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setTracking(True)
        self.setMouseTracking(True)
        self.setStyleSheet("QSlider { background: transparent; }")
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_steps(self, steps: List[Json], now: float) -> None:
        self._steps = steps
        self._now = now
        self._pos = scrub_positions(steps, now, self.track_w)
        self.update()

    @property
    def track_w(self) -> int:
        return max(1, self.width() - 2 * self.pad)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._pos = scrub_positions(self._steps, self._now, self.track_w)

    def x_at(self, i: int) -> float:
        """Pixel x of step ``i`` (``len(steps)`` is "now", the right end of the track)."""
        pct = 100.0 if i >= len(self._steps) else (self._pos[i] if i < len(self._pos) else 0.0)
        return self.pad + pct / 100 * self.track_w

    def nearest(self, x: float) -> int:
        n = len(self._steps)
        best, dist = n, abs(self.x_at(n) - x)
        for i in range(n):
            d = abs(self.x_at(i) - x)
            if d < dist:
                best, dist = i, d
        return best

    @property
    def dragging(self) -> bool:
        return self._drag

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = True
            self.setValue(self.nearest(e.position().x()))
            self.update()
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        i = self.nearest(e.position().x())
        if self._drag:
            self.setValue(i)
        if i != self._hover:
            self._hover = i
            self.hovered.emit(i)
            self.update()
        e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self._drag = False
        self.update()
        e.accept()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = -1
        self.hovered.emit(-1)
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mid = self.height() / 2
        sel = self.value()
        # the track, and the part before the cursor
        p.fillRect(QRectF(self.pad, mid - 1, self.track_w, 2), theme.blend(t.fill_2, t.canvas))
        p.fillRect(QRectF(self.pad, mid - 1, self.x_at(sel) - self.pad, 2), QColor(t.text_3))
        for i, s in enumerate(self._steps):
            x = self.x_at(i)
            kind = s.get("kind")
            tall = kind in ("message", "start", "restore", "external")
            past = i <= sel
            if kind == "external":
                color = QColor(t.warn)
            elif kind == "message":
                color = QColor(t.text_3)
            elif kind == "restore":
                color = QColor(t.text_2)
            elif s.get("changed"):
                color = QColor(t.text_2 if past else t.text_4)
            else:
                color = theme.blend(t.hair_3, t.canvas)
            h = 14 if tall else 8
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(x - 1, mid - h / 2, 2, h), 1, 1)
        # "now": a hollow dot at the right end
        p.setPen(QPen(QColor(t.text_3), 2))
        p.setBrush(theme.qcolor(t.raised))
        p.drawEllipse(QPointF(self.x_at(len(self._steps)), mid), 3, 3)
        # the cursor: accent, ringed in chrome then accent, with a stem up to the label
        cx = self.x_at(sel)
        scale = 1.25 if self._drag else 1.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.blend(t.hair_3, t.canvas))
        p.drawRect(QRectF(cx - 0.5, mid - 6 * scale - 4 - 14, 1, 14))
        p.setBrush(QColor(t.accent))
        p.drawEllipse(QPointF(cx, mid), 10 * scale, 10 * scale)
        p.setBrush(theme.qcolor(t.chrome))
        p.drawEllipse(QPointF(cx, mid), 9 * scale, 9 * scale)
        p.setBrush(QColor(t.accent))
        p.drawEllipse(QPointF(cx, mid), 6 * scale, 6 * scale)


class Scrubber(QWidget):
    """``.tl-scrub``: the label pill, the track and the axis under it."""

    picked = Signal(object)  # step n, or None for now

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.steps: List[Json] = []
        self._sel: Optional[int] = None
        self._quiet = False
        self._hover = -1
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 28, 0, 8)
        il.setSpacing(0)
        self.slider = TickSlider()
        self.slider.setAccessibleName("Timeline")
        self.slider.valueChanged.connect(self._on_value)
        self.slider.hovered.connect(self._on_hover)
        il.addWidget(self.slider)
        axis = QWidget()
        al = QHBoxLayout(axis)
        al.setContentsMargins(24, 2, 24, 0)
        al.setSpacing(0)
        self.first = lbl("", 10, color="text_4")
        self.note = lbl("", 10, color="text_4")
        self.now = lbl("", 10, color="text_4")
        al.addWidget(self.first)
        al.addStretch(1)
        al.addWidget(self.note)
        al.addStretch(1)
        al.addWidget(self.now)
        il.addWidget(axis)
        lay.addWidget(inner)
        lay.addWidget(Hairline())
        self.label = Pill(bg="raised_2", radius=9, parent=self)
        self.label.setFixedHeight(18)
        self.label.setContentsMargins(7, 0, 7, 0)
        self.label.setFont(theme.font_ui(theme.FS_MICRO))
        theme.tint(self.label, fg="text_2")

    def set_steps(self, steps: List[Json], sel: Optional[int]) -> None:
        self.steps = steps
        self._sel = sel
        now = time.time() * 1000
        self.slider.set_steps(steps, now)
        self._quiet = True
        self.slider.setRange(0, len(steps))
        self.slider.setValue(self._index(sel))
        self._quiet = False
        if steps:
            first = float(steps[0].get("at") or 0)
            self.first.setText(_clock(first))
            self.note.setText("pauses shortened" if now - first > 4 * 60e3 else "")
        self.now.setText(f"now · {_clock(now)}")
        self._relabel()

    def _index(self, sel: Optional[int]) -> int:
        if sel is None:
            return len(self.steps)
        for i, s in enumerate(self.steps):
            if s.get("n") == sel:
                return i
        return len(self.steps)

    def _on_hover(self, i: int) -> None:
        self._hover = i
        self._relabel()

    def _relabel(self) -> None:
        i = self._hover if self._hover >= 0 else self.slider.value()
        s = self.steps[i] if i < len(self.steps) else None
        self.label.setText(f"{_clock_s(float(s.get('at') or 0))} · {step_title(s)}" if s else "Now")
        self.label.adjustSize()
        x = self.slider.x_at(i)
        pct = (x - 20) / max(1, self.width() - 40) * 100
        w = self.label.width()
        if pct > 80:
            left = self.width() - 20 - w
        elif pct < 20:
            left = 20
        else:
            left = int(x - w / 2)
        self.label.move(int(left), 6)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._relabel()

    def _on_value(self, v: int) -> None:
        self._relabel()
        if self._quiet:
            return
        self.picked.emit(None if v >= len(self.steps) else self.steps[v].get("n"))


# ---------------------------------------------------------------- steps


class RestoreButton(QWidget):
    """ "Restore to here" (``.btn.xs``): asks what would change since this step, then confirms."""

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
        self.btn = Btn(
            "Restore to here",
            icon="rotate-ccw",
            size="xs",
            icon_size=11,
            on_click=self.click,
            tooltip="Put every file back to how it was right after this step",
        )
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


class FileCard(QWidget):
    """``.tl-file``: a raised surface for one changed file's header and diff."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.qcolor(theme.tokens().raised))
        p.drawRoundedRect(QRectF(self.rect()), theme.R2, theme.R2)


class StepDetail(QWidget):
    """What a step changed (``.tl-detail``): its diff, file by file, each with open and restore."""

    def __init__(self, step: Json, sid: str, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.step, self.sid, self.cwd = step, sid, cwd
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
            lay.addWidget(lbl(note, theme.FS_SMALL, tone="faint", wrap=True))
            lay.addWidget(RestoreButton(step, sid), 0, Qt.AlignmentFlag.AlignLeft)
        else:
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(lbl("Changed in this step", theme.FS_MICRO, 600, tone="faint"))
            head.addStretch(1)
            head.addWidget(RestoreButton(step, sid))
            lay.addLayout(head)
            self.loading = QWidget()
            ll = QHBoxLayout(self.loading)
            ll.setContentsMargins(0, 0, 0, 0)
            ll.setSpacing(6)
            ll.addWidget(Spinner(12))
            ll.addWidget(lbl("Loading the diff…", theme.FS_SMALL, tone="faint"), 1)
            lay.addWidget(self.loading)
            self.card = FileCard()
            cl = QVBoxLayout(self.card)
            cl.setContentsMargins(0, 0, 0, 0)
            self.view = DiffView(
                mode="unified", wrap=True, can_comment=False, max_rows=400, open_threshold=3, head_pad_left=4
            )
            self.view.set_actions(
                [
                    ("arrow-up-right", lambda _f: "Open the file as it is now", self._open),
                    ("undo-2", lambda _f: "Put this file back to how it was after this step", self._restore),
                ]
            )
            cl.addWidget(self.view)
            self.card.hide()
            lay.addWidget(self.card)
            store.call("timeline.stepDiff", {"id": sid, "n": step.get("n")}, quiet=True).then(self._got).catch(
                lambda _e: self._got({"files": []})
            )
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.blend(t.fill_1, t.canvas))
        p.drawRoundedRect(QRectF(0, -theme.R2, self.width(), self.height() + theme.R2), theme.R2, theme.R2)

    def _got(self, r: Any) -> None:
        self.loading.hide()
        files = list((r or {}).get("files") or [])
        if self.view is not None:
            self.view.set_files(files)
            self.card.setVisible(bool(files))

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


class SinceBar(QWidget):
    """The footer while a past step is picked (``.tl-since``): how far the folder has moved since then."""

    def __init__(self, step: Json, steps: List[Json], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(Hairline())
        later = [s for s in steps if int(s.get("n") or 0) > int(step.get("n") or 0) and s.get("changed")]
        files: Dict[str, Dict[str, int]] = {}
        for s in later:
            for f in s.get("files") or []:
                cur = files.setdefault(str(f.get("path")), {"add": 0, "del": 0})
                cur["add"] += int(f.get("add") or 0)
                cur["del"] += int(f.get("del") or 0)
        if not later:
            lb = lbl("Nothing has changed since this point.", theme.FS_SMALL, tone="faint")
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
        self.chev.setFixedSize(12, 12)
        hl.addWidget(self.chev)
        hl.addWidget(
            lbl(
                f"Since this point: {plural(len(later), 'step')} changed {plural(len(files), 'file')}",
                theme.FS_SMALL,
                tone="muted",
            ),
            1,
        )
        stat = QHBoxLayout()
        stat.setSpacing(5)
        stat.addWidget(Num("add", add))
        stat.addWidget(Num("del", dele))
        hl.addLayout(stat)
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
                icons.pixmap("chevron-down" if self.body.is_open else "chevron-right", theme.tokens().text_2, 12)
            )

    def _build(self, files: Dict[str, Dict[str, int]]) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(30, 0, 12, 8)
        lay.setSpacing(2)
        for path, s in files.items():
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(elide(lbl(path, theme.FS_MICRO, tone="muted", mono=True)), 1)
            stat = QHBoxLayout()
            stat.setSpacing(5)
            stat.addWidget(Num("add", s["add"]))
            stat.addWidget(Num("del", s["del"]))
            row.addLayout(stat)
            lay.addLayout(row)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setMaximumHeight(180)
        sc.setWidget(w)
        return sc


# ---------------------------------------------------------------- the panel


class HistoryPanel(QWidget):
    """The timeline of one session's folder (``.history``)."""

    def __init__(self, session_id: str, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session_id
        self.cwd = cwd
        self.meta: Optional[Json] = None
        self.sel: Optional[int] = None
        self.follow = True
        self._since: Optional[QWidget] = None
        self._off: Optional[QWidget] = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._lay = lay
        self.bar = Toolbar()
        self.bar.add(lbl("Timeline", theme.FS_SMALL, 550))
        self.summary = lbl("", theme.FS_SMALL, tone="faint")
        self.bar.add(self.summary)
        self.bar.stretch()
        self.now_btn = Btn(
            "Now",
            icon="arrow-down-to-line",
            kind="ghost",
            size="sm",
            on_click=lambda: self.pick(None),
            tooltip="Back to the present (End)",
        )
        self.now_btn.hide()
        self.bar.add(self.now_btn)
        lay.addWidget(self.bar)
        self.scrubber = Scrubber()
        self.scrubber.picked.connect(self.pick)
        self.scrubber.hide()
        lay.addWidget(self.scrubber)
        self.loading = lbl("Loading the timeline…", theme.FS_SMALL, tone="faint")
        self.loading.setContentsMargins(14, 16, 14, 6)
        lay.addWidget(self.loading)
        self.empty = EmptyState(
            "history",
            "Nothing on the timeline yet.",
            "Each step that can change files — an edit, a command, a message — becomes a point here. Pick any point to see what changed and put files back the way they were.",
        )
        self.empty.hide()
        lay.addWidget(self.empty)
        # the steps: a list on the canvas engine (one block per step, the detail as an island)
        from .timeline_list import TimelineList

        self.list = TimelineList(session_id, cwd, self.pick)
        self.list.hide()
        lay.addWidget(self.list, 1)
        lay.addStretch(0)
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
        self.list.reveal(n)

    @property
    def _rows(self) -> List[Any]:
        return self.list.rows

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
            self.list.hide()
            self.loading.hide()
            if self._off is None:
                reason = meta.get("reason")
                self._off = EmptyState("history", "The timeline is off for this folder.", off_reason(reason))
                if not reason or reason == "disabled":
                    b = Btn(
                        "Turn on in Settings",
                        icon="settings-2",
                        size="sm",
                        on_click=lambda: store.set_dialog("settings", "general"),
                    )
                    self._off.layout().addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
                self._lay.insertWidget(1, self._off)
                self._lay.addStretch(1)
            return
        steps = self.steps
        self.loading.setVisible(meta is None)
        self.empty.setVisible(meta is not None and not steps)
        self.scrubber.setVisible(bool(steps))
        self.list.setVisible(bool(steps))
        self.now_btn.setVisible(self.sel is not None)
        changing = sum(1 for s in steps if s.get("changed"))
        first = float(steps[0].get("at") or 0) if steps else 0
        span = (float(steps[-1].get("at") or first) - first) if steps else 0
        self.summary.setText(
            f"{plural(changing, 'change')} · {duration(span) if span > 60_000 else 'just started'}" if steps else ""
        )
        if steps:
            self.scrubber.set_steps(steps, self.sel)
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

    def _update_now(self) -> None:
        rt = store.rt_of(self.sid)
        busy = rt is not None and rt.status in ("running", "requires_action")
        self.list.sync(self.steps, self.sel, busy)
