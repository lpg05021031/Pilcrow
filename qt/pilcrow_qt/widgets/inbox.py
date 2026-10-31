"""The inbox (a port of web/src/components/Inbox.tsx): what each scheduled run produced, newest first,
under day headings, with the schedules themselves in a rail on the right. Runs open as ordinary
sessions, so a result can be continued by hand."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import icons, theme
from ..lib.fmt import basename, cost, duration, plural, tildify, time_ago
from ..store import ConfirmSpec, store
from .dialogs.parts import Badge, Card, ElideLabel, LayoutButton, Switch, Text, hint, page_column
from .ui.bits import Box, IconButton, Mark, Pill, Spinner, button
from .ui.menu import show_menu
from .ui.signals import bind

Json = Dict[str, Any]


def now_ms() -> float:
    return time.time() * 1000


def clock(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


def day_label(ms: float, now: float) -> str:
    d = datetime.fromtimestamp(ms / 1000).date()
    t = datetime.fromtimestamp(now / 1000).date()
    if d == t:
        return "Today"
    if d == t - timedelta(days=1):
        return "Yesterday"
    return f"{d.strftime('%A')}, {d.strftime('%b')} {d.day}"


def until(ms: float, now: float) -> str:
    s = max(0, int(round((ms - now) / 1000)))
    if s < 60:
        return "in under a minute"
    m = int(round(s / 60))
    if m < 60:
        return f"in {m} min"
    h = m // 60
    if h < 24:
        return f"in {h} h{f' {m % 60} min' if m % 60 else ''}"
    d = int(round((ms - now) / 86400e3))
    return f"{'tomorrow' if d == 1 else f'in {d} days'} at {clock(ms)}"


class SectionTitle(QWidget):
    """``.inbox-day-h`` / ``.inbox-rail-h``: a 15px heading with a count and room at the end."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 2)
        lay.setSpacing(8)
        self.title = Text(text, theme.FS_H, 600, "text", wrap=False, letter_spacing=-0.01)
        lay.addWidget(self.title)
        self.count = Badge(0)
        lay.addWidget(self.count)
        lay.addStretch(1)
        self.end = QHBoxLayout()
        self.end.setSpacing(4)
        lay.addLayout(self.end)


class RunCard(Card):
    """One run (``.run``): status badge, name, when and how long, the summary, files, cost, actions."""

    def __init__(
        self,
        it: Json,
        on_open: Callable[[], None],
        on_read: Callable[[], None],
        on_remove: Callable[[], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(14, parent)
        self.it = it
        self._open = False
        self._on_read = on_read
        status = str(it.get("status") or "")
        t = theme.tokens()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 13, 14, 11)
        lay.setSpacing(12)
        tone, wash = {"completed": ("ok", "ok_wash"), "failed": ("err", "err_wash")}.get(status, ("warn", "warn_wash"))
        self.badge = Pill(bg=f"{wash}/raised", radius=15)
        self.badge.setFixedSize(30, 30)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tone = getattr(t, tone)
        self.badge.setPixmap(
            icons.pixmap({"completed": "circle-check", "failed": "circle-alert"}.get(status, "circle-stop"), tone, 15)
        )
        lay.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        main = QVBoxLayout()
        main.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.dot = Box(bg="accent", line="accent_wash/raised", radius=6, line_w=3)
        self.dot.setFixedSize(13, 13)
        self.dot.setToolTip("New")
        self.dot.setVisible(not it.get("read"))
        head.addWidget(self.dot)
        self.name = Text(str(it.get("scheduleName") or ""), theme.FS_UI, 600, "text", wrap=False)
        head.addWidget(self.name, 1)
        if it.get("manual"):
            chip = Pill("by hand", bg="fill_1/raised", radius=9)
            chip.setContentsMargins(7, 1, 7, 1)
            chip.setProperty("fs", "micro")
            theme.tint(chip, fg="text_3")
            head.addWidget(chip)
        when = Text(
            f"{clock(float(it.get('endedAt') or 0))} · {duration(float(it.get('durationMs') or 0))}",
            theme.FS_MICRO,
            400,
            "text_4",
            wrap=False,
        )
        head.addWidget(when)
        main.addLayout(head)
        self.summary = str(
            it.get("summary")
            or it.get("reason")
            or ("Finished without a written summary." if status == "completed" else "No output.")
        )
        self.long = len(self.summary) > 280
        self.sum = Text("", theme.FS_SMALL, 400, "text_2", 1.55)
        main.addWidget(self.sum)
        self.more = button("Read more", kind="link", size="sm")
        self.more.override = {"fs": theme.FS_MICRO}
        self.more.clicked.connect(self.toggle)
        self.more.setVisible(self.long)
        main.addWidget(self.more, 0, Qt.AlignmentFlag.AlignLeft)
        if status != "completed" and it.get("reason") and it.get("summary"):
            reason = QLabel(str(it.get("reason")))
            reason.setWordWrap(True)
            reason.setProperty("fs", "micro")
            theme.tint(reason, fg="err")
            main.addWidget(reason)
        foot = QHBoxLayout()
        foot.setSpacing(12)
        files = [f for f in (it.get("files") or []) if isinstance(f, dict)]
        if files:
            add = sum(int(f.get("add") or 0) for f in files)
            dele = sum(int(f.get("del") or 0) for f in files)
            fl = QLabel(
                f"{plural(len(files), 'file')} <span style='color:{t.ok}'>+{add}</span> <span style='color:{t.err}'>−{dele}</span>"
            )
            fl.setTextFormat(Qt.TextFormat.RichText)
            fl.setToolTip("\n".join(str(f.get("path") or "") for f in files))
        else:
            fl = QLabel("No files changed")
        fl.setProperty("fs", "micro")
        theme.tint(fl, fg="text_3")
        foot.addWidget(fl)
        if float(it.get("cost") or 0) > 0:
            c = QLabel(cost(float(it.get("cost") or 0)))
            c.setProperty("fs", "micro")
            theme.tint(c, fg="text_3")
            foot.addWidget(c)
        folder = ElideLabel(basename(str(it.get("cwd") or "")))
        folder.setFont(theme.font_ui(theme.FS_MICRO))
        theme.tint(folder, fg="text_3")
        folder.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        foot.addWidget(folder)
        foot.addStretch(1)
        self.rerun_btn = button(
            "Run now", kind="ghost", size="xs", icon="rotate-cw", tooltip="Run this schedule again now"
        )
        self.rerun_btn.setIconSize(QSize(11, 11))
        self.rerun_btn.clicked.connect(self.rerun)
        foot.addWidget(self.rerun_btn)
        self.open_btn = button(
            "Open session", size="xs", icon="arrow-up-right", tooltip="Open the session and continue by hand"
        )
        self.open_btn.setIconSize(QSize(11, 11))
        self.open_btn.clicked.connect(lambda _c=False: on_open())
        self.open_btn.setVisible(bool(it.get("sessionId")))
        foot.addWidget(self.open_btn)
        self.x_btn = IconButton("x", "Dismiss", 13)
        self.x_btn.setFixedSize(24, 24)
        self.x_btn.clicked.connect(lambda _c=False: on_remove())
        foot.addWidget(self.x_btn)
        main.addLayout(foot)
        lay.addLayout(main, 1)
        self._render()
        self.x_btn.setVisible(False)  # ``.run-x { opacity: 0 }`` until the card is hovered

    def enterEvent(self, e) -> None:  # noqa: N802
        self.x_btn.setVisible(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.x_btn.setVisible(False)
        super().leaveEvent(e)

    def _render(self) -> None:
        self.sum.setText(self.summary if self._open or not self.long else self.summary[:280] + "…")
        self.more.setText("Less" if self._open else "Read more")

    def toggle(self) -> None:
        self._open = not self._open
        self._render()

    def rerun(self) -> None:
        name = str(self.it.get("scheduleName") or "")
        store.call("schedules.runNow", {"id": self.it.get("scheduleId")}).then(
            lambda _r: store.toast(f"Running {name} now", "info")
        )

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._on_read()
        super().mouseReleaseEvent(e)


class HelpersCard(Card):
    """A lead's helpers once they are all done (``kind: helpers``, HELPERS.md §6.6): the badge, the lead's
    name, one line per helper with its handoff's first line and numbers, Copy Markdown and Open the lead."""

    def __init__(
        self,
        it: Json,
        on_read: Callable[[], None],
        on_remove: Callable[[], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(14, parent)
        self.it = it
        self._on_read = on_read
        status = str(it.get("status") or "")
        t = theme.tokens()
        helpers = [x for x in (it.get("helpers") or []) if isinstance(x, dict)]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 13, 14, 11)
        lay.setSpacing(12)
        tone, wash = {"completed": ("ok", "ok_wash"), "failed": ("err", "err_wash")}.get(status, ("warn", "warn_wash"))
        self.badge = Pill(bg=f"{wash}/raised", radius=15)
        self.badge.setFixedSize(30, 30)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setPixmap(icons.pixmap("arrow-up-right", getattr(t, tone), 15))
        lay.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        main = QVBoxLayout()
        main.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.dot = Box(bg="accent", line="accent_wash/raised", radius=6, line_w=3)
        self.dot.setFixedSize(13, 13)
        self.dot.setToolTip("New")
        self.dot.setVisible(not it.get("read"))
        head.addWidget(self.dot)
        self.name = Text(str(it.get("title") or it.get("scheduleName") or ""), theme.FS_UI, 600, "text", wrap=False)
        head.addWidget(self.name, 1)
        chip = Pill("helpers", bg="fill_1/raised", radius=9)
        chip.setContentsMargins(7, 1, 7, 1)
        chip.setProperty("fs", "micro")
        theme.tint(chip, fg="text_3")
        head.addWidget(chip)
        when = Text(
            f"{clock(float(it.get('endedAt') or 0))} · {duration(float(it.get('durationMs') or 0))}",
            theme.FS_MICRO,
            400,
            "text_4",
            wrap=False,
        )
        head.addWidget(when)
        main.addLayout(head)
        done = sum(1 for x in helpers if x.get("state") == "done")
        landed = sum(1 for x in helpers if x.get("finished") == "commit")
        prs = sum(1 for x in helpers if x.get("pr"))
        line = f"{done} of {plural(len(helpers), 'helper')} done"
        if landed:
            line += f" · {landed} committed"
        if prs:
            line += f" · {plural(prs, 'pull request')}"
        if it.get("reason"):
            line += f" · {it.get('reason')}"
        self.sum = Text(line, theme.FS_SMALL, 400, "text_2", 1.55)
        main.addWidget(self.sum)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(2)
        for x in helpers[:12]:
            row = QHBoxLayout()
            row.setSpacing(10)
            st = str(x.get("state") or "")
            glyph = Text(
                {"done": "●", "failed": "✕", "stopped": "■", "skipped": "○"}.get(st, "○"),
                theme.FS_MICRO,
                400,
                {"done": "ok", "failed": "err"}.get(st, "text_3"),
                wrap=False,
            )
            glyph.setFixedWidth(12)
            row.addWidget(glyph)
            nm = Text(str(x.get("name") or ""), theme.FS_SMALL, 500, "text", wrap=False)
            nm.setFixedWidth(150)
            row.addWidget(nm)
            handoff = str(x.get("handoff") or "").strip()
            first = next(
                (
                    ln.strip(" #*")
                    for ln in handoff.split("\n")
                    if ln.strip(" #*") and not ln.strip(" #*").lower().startswith("handoff")
                ),
                "",
            )
            what = first[:120] if st == "done" else str(x.get("reason") or st)
            row.addWidget(ElideLabel(what or st), 1)
            nums = []
            if x.get("files"):
                nums.append(
                    f"{plural(int(x.get('files') or 0), 'file')} <span style='color:{t.ok}'>+{int(x.get('add') or 0)}</span> <span style='color:{t.err}'>−{int(x.get('del') or 0)}</span>"
                )
            if float(x.get("cost") or 0):
                nums.append(cost(float(x.get("cost") or 0)))
            if x.get("commit"):
                nums.append(f"<span style='font-family: monospace'>{x.get('commit')}</span>")
            if x.get("pr"):
                nums.append(f"<a href='{x.get('pr')}' style='color:{t.accent_text}'>PR</a>")
            lb = QLabel(" · ".join(nums))
            lb.setTextFormat(Qt.TextFormat.RichText)
            lb.setOpenExternalLinks(True)
            lb.setProperty("fs", "micro")
            theme.tint(lb, fg="text_3")
            row.addWidget(lb)
            w = QWidget()
            w.setLayout(row)
            self.rows.addWidget(w)
        if len(helpers) > 12:
            self.rows.addWidget(hint(f"and {len(helpers) - 12} more"))
        main.addLayout(self.rows)
        foot = QHBoxLayout()
        foot.setSpacing(12)
        c = QLabel(cost(float(it.get("cost") or 0)))
        c.setProperty("fs", "micro")
        theme.tint(c, fg="text_3")
        foot.addWidget(c)
        foot.addStretch(1)
        self.copy_btn = button(
            "Copy Markdown",
            kind="ghost",
            size="xs",
            icon="clipboard-paste",
            tooltip="The helpers' handoffs as Markdown, to the clipboard",
        )
        self.copy_btn.setIconSize(QSize(11, 11))
        self.copy_btn.clicked.connect(self.copy_md)
        foot.addWidget(self.copy_btn)
        self.open_btn = button("Open the lead", size="xs", icon="arrow-up-right", tooltip="The lead's conversation")
        self.open_btn.setIconSize(QSize(11, 11))
        self.open_btn.clicked.connect(lambda _c=False: (self._on_read(), store.open_session(str(it.get("leadId")))))
        foot.addWidget(self.open_btn)
        self.x_btn = IconButton("x", "Dismiss", 13)
        self.x_btn.setFixedSize(24, 24)
        self.x_btn.clicked.connect(lambda _c=False: on_remove())
        foot.addWidget(self.x_btn)
        main.addLayout(foot)
        lay.addLayout(main, 1)
        self.x_btn.setVisible(False)

    def enterEvent(self, e) -> None:  # noqa: N802
        self.x_btn.setVisible(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.x_btn.setVisible(False)
        super().leaveEvent(e)

    def copy_md(self) -> None:
        from PySide6.QtGui import QGuiApplication

        def got(md: Any) -> None:
            QGuiApplication.clipboard().setText(str(md or ""))
            store.toast("Markdown copied", "success")

        store.call("helpers.export", {"leadId": str(self.it.get("leadId"))}).then(got)


class DispatchCard(Card):
    """A dispatch job's report (``kind: dispatch``): the badge, the name, what landed where, cost and
    time, one line per repository, and the ways back to the job."""

    def __init__(
        self,
        it: Json,
        on_read: Callable[[], None],
        on_remove: Callable[[], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(14, parent)
        self.it = it
        self._on_read = on_read
        status = str(it.get("status") or "")
        t = theme.tokens()
        rep = it.get("dispatch") or {}
        targets = [x for x in (rep.get("targets") or []) if isinstance(x, dict)]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 13, 14, 11)
        lay.setSpacing(12)
        tone, wash = {"completed": ("ok", "ok_wash"), "failed": ("err", "err_wash")}.get(status, ("warn", "warn_wash"))
        self.badge = Pill(bg=f"{wash}/raised", radius=15)
        self.badge.setFixedSize(30, 30)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setPixmap(icons.pixmap("send", getattr(t, tone), 15))
        lay.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        main = QVBoxLayout()
        main.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.dot = Box(bg="accent", line="accent_wash/raised", radius=6, line_w=3)
        self.dot.setFixedSize(13, 13)
        self.dot.setToolTip("New")
        self.dot.setVisible(not it.get("read"))
        head.addWidget(self.dot)
        self.name = Text(str(it.get("scheduleName") or ""), theme.FS_UI, 600, "text", wrap=False)
        head.addWidget(self.name, 1)
        chip = Pill("dispatch", bg="fill_1/raised", radius=9)
        chip.setContentsMargins(7, 1, 7, 1)
        chip.setProperty("fs", "micro")
        theme.tint(chip, fg="text_3")
        head.addWidget(chip)
        when = Text(
            f"{clock(float(it.get('endedAt') or 0))} · {duration(float(it.get('durationMs') or 0))}",
            theme.FS_MICRO,
            400,
            "text_4",
            wrap=False,
        )
        head.addWidget(when)
        main.addLayout(head)
        done = sum(1 for x in targets if x.get("state") == "done")
        landed = sum(1 for x in targets if x.get("finished") == "commit")
        prs = sum(1 for x in targets if x.get("pr"))
        line = f"{done} of {plural(len(targets), 'repository', 'repositories')} done"
        if landed:
            line += f" · {landed} committed"
        if prs:
            line += f" · {plural(prs, 'pull request')}"
        if it.get("reason"):
            line += f" · {it.get('reason')}"
        self.sum = Text(line, theme.FS_SMALL, 400, "text_2", 1.55)
        main.addWidget(self.sum)
        self.prose: Optional[Text] = None
        if rep.get("prose"):
            self.prose = Text(str(rep.get("prose")), theme.FS_SMALL, 400, "text_2", 1.55)
            main.addWidget(self.prose)
        elif rep.get("proseState") == "writing":
            self.prose = Text("An agent is writing the report…", theme.FS_SMALL, 400, "text_3", 1.55)
            main.addWidget(self.prose)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(2)
        for x in targets[:12]:
            row = QHBoxLayout()
            row.setSpacing(10)
            st = str(x.get("state") or "")
            glyph = Text(
                {"done": "●", "failed": "✕", "stopped": "■", "skipped": "○"}.get(st, "○"),
                theme.FS_MICRO,
                400,
                {"done": "ok", "failed": "err"}.get(st, "text_3"),
                wrap=False,
            )
            glyph.setFixedWidth(12)
            row.addWidget(glyph)
            nm = Text(str(x.get("name") or ""), theme.FS_SMALL, 500, "text", wrap=False)
            nm.setFixedWidth(150)
            row.addWidget(nm)
            what = str(x.get("summary") or x.get("reason") or "")[:120] if st != "done" or x.get("summary") else st
            row.addWidget(ElideLabel(what if what else st), 1)
            nums = []
            if x.get("files"):
                nums.append(
                    f"{plural(int(x.get('files') or 0), 'file')} <span style='color:{t.ok}'>+{int(x.get('add') or 0)}</span> <span style='color:{t.err}'>−{int(x.get('del') or 0)}</span>"
                )
            if float(x.get("cost") or 0):
                nums.append(cost(float(x.get("cost") or 0)))
            if x.get("commit"):
                nums.append(f"<span style='font-family: monospace'>{x.get('commit')}</span>")
            if x.get("pr"):
                nums.append(f"<a href='{x.get('pr')}' style='color:{t.accent_text}'>PR</a>")
            lb = QLabel(" · ".join(nums))
            lb.setTextFormat(Qt.TextFormat.RichText)
            lb.setOpenExternalLinks(True)
            lb.setProperty("fs", "micro")
            theme.tint(lb, fg="text_3")
            row.addWidget(lb)
            w = QWidget()
            w.setLayout(row)
            self.rows.addWidget(w)
        if len(targets) > 12:
            self.rows.addWidget(hint(f"and {len(targets) - 12} more"))
        main.addLayout(self.rows)
        foot = QHBoxLayout()
        foot.setSpacing(12)
        c = QLabel(cost(float(it.get("cost") or 0)))
        c.setProperty("fs", "micro")
        theme.tint(c, fg="text_3")
        foot.addWidget(c)
        rules = [r for r in (rep.get("rules") or []) if isinstance(r, dict)]
        if rules:
            rl = ElideLabel(
                "allowed for every repository: " + "; ".join(str(r.get("label") or r.get("key")) for r in rules)
            )
            rl.setFont(theme.font_ui(theme.FS_MICRO))
            theme.tint(rl, fg="text_3")
            foot.addWidget(rl, 1)
        else:
            foot.addStretch(1)
        self.copy_btn = button(
            "Copy Markdown",
            kind="ghost",
            size="xs",
            icon="clipboard-paste",
            tooltip="The report as Markdown, to the clipboard",
        )
        self.copy_btn.setIconSize(QSize(11, 11))
        self.copy_btn.clicked.connect(self.copy_md)
        foot.addWidget(self.copy_btn)
        self.open_btn = button("Open job", size="xs", icon="arrow-up-right", tooltip="The job's view, with every run")
        self.open_btn.setIconSize(QSize(11, 11))
        self.open_btn.clicked.connect(lambda _c=False: (self._on_read(), store.open_job(str(it.get("jobId")))))
        foot.addWidget(self.open_btn)
        self.x_btn = IconButton("x", "Dismiss", 13)
        self.x_btn.setFixedSize(24, 24)
        self.x_btn.clicked.connect(lambda _c=False: on_remove())
        foot.addWidget(self.x_btn)
        main.addLayout(foot)
        lay.addLayout(main, 1)
        self.x_btn.setVisible(False)

    def enterEvent(self, e) -> None:  # noqa: N802
        self.x_btn.setVisible(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.x_btn.setVisible(False)
        super().leaveEvent(e)

    def copy_md(self) -> None:
        from PySide6.QtGui import QGuiApplication

        def got(md: Any) -> None:
            QGuiApplication.clipboard().setText(str(md or ""))
            store.toast("Report copied as Markdown.", "info")

        store.call("dispatch.export", {"id": self.it.get("jobId")}).then(got)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._on_read()
        super().mouseReleaseEvent(e)


class ScheduleCard(Card):
    """A schedule: name, cadence and folder, when it next fires, and a thin bar filling toward that moment."""

    def __init__(
        self, s: Json, now: float, active: bool, on_filter: Callable[[], None], parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(12, parent)
        self.s = s
        self.now = now
        self._busy = False
        self._active = active
        self.setFocused_ = active
        off = not s.get("enabled")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 8, 12)
        lay.setSpacing(6)
        self.main = LayoutButton()
        self.main.setCursor(Qt.CursorShape.PointingHandCursor)
        self.main.setToolTip(
            f"{tildify(str(s.get('cwd') or ''), (store.info or {}).get('home'))}\n\n{s.get('prompt') or ''}"
        )
        self.main.setAccessibleName(str(s.get("name") or ""))
        self.main.setStyleSheet("QPushButton { border: none; padding: 0; text-align: left; background: transparent; }")
        self.main.clicked.connect(lambda _c=False: on_filter())
        ml = QVBoxLayout(self.main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(2)
        self.name = Text(str(s.get("name") or ""), theme.FS_SMALL, 600, "text_3" if off else "text", wrap=False)
        ml.addWidget(self.name)
        self.sub = Text(
            f"{s.get('cadenceLabel') or ''} · {basename(str(s.get('cwd') or ''))}",
            theme.FS_MICRO,
            400,
            "text_3",
            wrap=False,
        )
        ml.addWidget(self.sub)
        self.next = QLabel()
        self.next.setWordWrap(True)
        ml.addWidget(self.next)
        lay.addWidget(self.main, 1)
        end = QHBoxLayout()
        end.setSpacing(2)
        self.switch = Switch(bool(s.get("enabled")), "Resume" if off else "Pause")
        self.switch.toggled.connect(lambda on: store.call("schedules.toggle", {"id": s.get("id"), "enabled": on}))
        end.addWidget(self.switch)
        end.addSpacing(4)
        self.run_btn = IconButton("play", "Run now", 13)
        self.run_btn.setFixedSize(24, 24)
        self.run_btn.clicked.connect(self.run)
        self.run_btn.setEnabled(not s.get("running"))
        end.addWidget(self.run_btn)
        self.menu_btn = IconButton("ellipsis", "Schedule actions", 14)
        self.menu_btn.setFixedSize(24, 24)
        self.menu_btn.clicked.connect(self.open_menu)
        end.addWidget(self.menu_btn)
        lay.addLayout(end)
        self.set_focused(active)
        self.refresh_time(now)

    def refresh_time(self, now: float) -> None:
        s = self.s
        t = theme.tokens()
        running = s.get("running") if isinstance(s.get("running"), dict) else None
        if running:
            text = f"running {duration(now - float(running.get('startedAt') or now))}"
        elif s.get("error"):
            text = str(s.get("error"))
        elif not s.get("enabled"):
            text = "Paused"
        elif s.get("nextRunAt"):
            text = f"Next {until(float(s['nextRunAt']), now)}"
        else:
            text = "Never"
        if s.get("lastRunAt") and not running:
            tail = f" · last {time_ago(float(s['lastRunAt']), now)}"
            if s.get("lastStatus") and s.get("lastStatus") != "completed":
                tail += f" ({s.get('lastStatus')})"
            text += f"<span style='color:{t.text_3}'>{tail}</span>"
        self.next.setTextFormat(Qt.TextFormat.RichText)
        self.next.setText(text)
        self.next.setFont(theme.font_ui(theme.FS_MICRO))
        self.next.setContentsMargins(0, 2, 0, 0)
        theme.tint(self.next, fg="err" if s.get("error") and not running else "text_2")
        self.now = now
        self.update()

    def progress(self) -> Optional[float]:
        s = self.s
        if (
            s.get("enabled")
            and not s.get("running")
            and s.get("nextRunAt")
            and s.get("lastRunAt")
            and float(s["nextRunAt"]) > float(s["lastRunAt"])
        ):
            return max(
                0.0, min(1.0, (self.now - float(s["lastRunAt"])) / (float(s["nextRunAt"]) - float(s["lastRunAt"])))
            )
        return None

    def paintEvent(self, e) -> None:  # noqa: N802
        super().paintEvent(e)
        pr = self.progress()
        running = bool(self.s.get("running"))
        if pr is None and not running:
            return
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if not self.s.get("enabled"):
            p.setOpacity(0.4)
        w = self.width() - 24
        p.setBrush(theme.blend(t.fill_2, t.raised))
        p.drawRoundedRect(QRectF(12, self.height() - 3, w, 2), 1, 1)
        p.setBrush(QColor(t.accent if running else t.text_3))
        p.drawRoundedRect(QRectF(12, self.height() - 3, w * (0.4 if running else pr or 0), 2), 1, 1)

    def run(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.run_btn.setEnabled(False)
        name = str(self.s.get("name") or "")

        def done(_x: Any) -> None:
            self._busy = False
            self.run_btn.setEnabled(not self.s.get("running"))

        store.call("schedules.runNow", {"id": self.s.get("id")}).then(
            lambda _r: (store.toast(f"Running {name} now", "info"), done(None))
        ).catch(done)

    def open_menu(self) -> None:
        s = self.s
        on = bool(s.get("enabled"))
        show_menu(
            [
                {
                    "label": "Edit…",
                    "icon": "pencil",
                    "on_select": lambda: store.set_dialog("schedule", {"id": s.get("id")}),
                },
                {"label": "Run now", "icon": "play", "on_select": self.run},
                {
                    "label": "Pause" if on else "Resume",
                    "icon": "pause" if on else "play",
                    "on_select": lambda: store.call("schedules.toggle", {"id": s.get("id"), "enabled": not on}),
                },
                {"separator": True},
                {"label": "Delete…", "icon": "trash", "danger": True, "on_select": self.delete},
            ],
            self.menu_btn,
            200,
            align_right=True,
        )

    def delete(self) -> None:
        s = self.s
        store.confirm_action(
            ConfirmSpec(
                title=f"Delete the schedule “{s.get('name')}”?",
                body="Its past runs stay in the inbox.",
                confirm="Delete schedule",
                danger=True,
                on_confirm=lambda _c: store.call("schedules.remove", {"id": s.get("id")}),
            )
        )


class EmptyHero(QWidget):
    """The hero's ground: on dark the mark casts an orange glow
    (``[data-theme='dark'] .inbox-empty-mark { filter: drop-shadow(0 12px 30px rgba(217,119,87,.3)) }``)."""

    mark: Optional[QWidget] = None

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        if not t.dark or self.mark is None:
            return
        from PySide6.QtGui import QRadialGradient

        c = self.mark.geometry().center()
        cx, cy = c.x() + 0.5, c.y() + 12.5
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QRadialGradient(cx, cy, 70)
        glow = QColor(t.accent)
        for stop, a in ((0.0, 0.3), (0.3, 0.16), (0.6, 0.05), (1.0, 0.0)):
            glow.setAlphaF(a)
            g.setColorAt(stop, QColor(glow))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QRectF(cx - 70, cy - 70, 140, 140))


class InboxPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.items: Optional[List[Json]] = None
        self.schedules: Optional[List[Json]] = None
        self.filter: Optional[str] = None
        self.now = now_ms()
        self.run_cards: List[RunCard] = []
        self.sched_cards: List[ScheduleCard] = []

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
        self.title = QLabel("Inbox")
        self.title.setProperty("fs", "title")
        hl.addWidget(self.title)
        self.summary = QLabel()
        theme.tint(self.summary, fg="text_3")
        self.summary.setProperty("fs", "small")
        hl.addWidget(self.summary)
        hl.addStretch(1)
        self.mark_all = button("Mark all read", kind="ghost", size="sm", icon="mail-open", on_click=self.mark_all_read)
        self.mark_all.setIconSize(QSize(13, 13))
        self.mark_all.setAccessibleName("Mark all read")
        self.mark_all.hide()
        hl.addWidget(self.mark_all)
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
        bl = QGridLayout(self.body)
        bl.setContentsMargins(20, 4, 20, 48)
        bl.setSpacing(28)
        self.grid = bl
        self._cols = 0
        hh.addWidget(self.body)
        hh.addStretch(1)
        self.scroll.setWidget(host)
        self.scroll.viewport().installEventFilter(self)
        root.addWidget(self.scroll, 1)

        # main column
        self.main = QWidget()
        self.main_lay = QVBoxLayout(self.main)
        self.main_lay.setContentsMargins(0, 0, 0, 0)
        self.main_lay.setSpacing(24)
        self.main_lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        # the rail
        self.rail = QWidget()
        self.rail.setFixedWidth(300)
        self.rail_lay = QVBoxLayout(self.rail)
        self.rail_lay.setContentsMargins(0, 0, 0, 0)
        self.rail_lay.setSpacing(8)
        self.rail_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.rail_head = SectionTitle("Schedules")
        self.new_btn = button("New", size="xs", icon="plus", on_click=lambda: store.set_dialog("schedule", {}))
        self.new_btn.setIconSize(QSize(12, 12))
        self.new_btn.setAccessibleName("New schedule")
        self.rail_head.end.addWidget(self.new_btn)
        self.rail_lay.addWidget(self.rail_head)
        self.rail_spinner = Spinner(14)
        self.rail_lay.addWidget(self.rail_spinner)
        self.rail_list = QVBoxLayout()
        self.rail_list.setSpacing(8)
        self.rail_lay.addLayout(self.rail_list)
        self.rail_empty = hint("No schedules yet.")
        self.rail_empty.hide()  # kept for the moment the list empties under a filter; the hero covers the rest
        self.rail_lay.addWidget(self.rail_empty)
        self._place(2)

        self._clock = QTimer(self)
        self._clock.setInterval(30_000)
        self._clock.timeout.connect(self._tick)
        bind(store.sched_changed, self, lambda _r: self.load())
        bind(store.ui_changed, self, lambda _u: self.side_btn.setVisible(not store.ui.sidebar))
        bind(store.connected_changed, self, lambda on: on and self.load())
        self.side_btn.setVisible(not store.ui.sidebar)
        self.render()
        self.load()

    # ---- lifecycle
    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        self._clock.start()

    def hideEvent(self, e) -> None:  # noqa: N802
        self._clock.stop()
        super().hideEvent(e)

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if obj is self.scroll.viewport() and e.type() == QEvent.Type.Resize:
            vw = self.scroll.viewport().width()
            self.body.setFixedWidth(max(320, min(page_column(vw), vw)))
            self._place(1 if self.body.width() - 40 <= 760 else 2)
        return False

    def _place(self, cols: int) -> None:
        """``.inbox-body``: runs | a 300px rail of schedules; under 760px of content the rail comes first,
        in one column (the web's ``@container (max-width: 760px)`` rule)."""
        if cols == self._cols:
            return
        self._cols = cols
        g = self.grid
        while g.count():
            g.takeAt(0)
        if cols == 2:
            g.addWidget(self.main, 0, 0, Qt.AlignmentFlag.AlignTop)
            g.addWidget(self.rail, 0, 1, Qt.AlignmentFlag.AlignTop)
            g.setColumnStretch(0, 1)
            g.setColumnStretch(1, 0)
            g.setRowStretch(1, 1)
            g.setRowStretch(2, 0)
            self.rail.setFixedWidth(300)
        else:
            g.addWidget(self.rail, 0, 0, Qt.AlignmentFlag.AlignTop)
            g.addWidget(self.main, 1, 0, Qt.AlignmentFlag.AlignTop)
            g.setColumnStretch(0, 1)
            g.setColumnStretch(1, 0)
            g.setRowStretch(1, 0)
            g.setRowStretch(2, 1)
            self.rail.setMinimumWidth(0)
            self.rail.setMaximumWidth(16777215)
            self.rail.setFixedWidth(self.body.width() - 40)

    # ---- data
    def load(self) -> None:
        if not store.connected:
            return

        def items(r: Any) -> None:
            self.items = (
                [i for i in ((r or {}).get("items") or []) if isinstance(i, dict)] if isinstance(r, dict) else []
            )
            self.render()

        def scheds(r: Any) -> None:
            self.schedules = [s for s in r if isinstance(s, dict)] if isinstance(r, list) else []
            self.render()

        store.call("inbox.list", {}, quiet=True).then(items).catch(lambda _e: items({}))
        store.call("schedules.list", {}, quiet=True).then(scheds).catch(lambda _e: scheds([]))

    def mark_all_read(self) -> None:
        def done(_r: Any) -> None:
            self.items = [{**i, "read": True} for i in self.items or []]
            self.render()

        store.call("inbox.markRead", {}).then(done)

    def mark_one(self, iid: str) -> None:
        it = next((i for i in self.items or [] if i.get("id") == iid), None)
        if it is None or it.get("read"):
            return
        store.call("inbox.markRead", {"ids": [iid]}, quiet=True).catch(lambda _e: None)
        self.items = [{**i, "read": True} if i.get("id") == iid else i for i in self.items or []]
        self.render()

    def remove(self, iid: str) -> None:
        store.call("inbox.remove", {"id": iid}, quiet=True).catch(lambda _e: None)
        self.items = [i for i in self.items or [] if i.get("id") != iid]
        self.render()

    def set_filter(self, sid: Optional[str]) -> None:
        self.filter = None if sid == self.filter else sid
        self.render()

    def open_item(self, it: Json) -> None:
        self.mark_one(str(it.get("id")))
        if it.get("sessionId"):
            store.open_session(str(it["sessionId"]))

    # ---- rendering
    def render(self) -> None:
        self.now = now_ms()
        items = self.items
        scheds = self.schedules
        unread = sum(1 for i in items or [] if not i.get("read"))
        self.summary.setText(
            (f"{unread} unread" if unread else "All read" if items else "") if items is not None else ""
        )
        self.mark_all.setVisible(unread > 0)

        while self.main_lay.count():
            w = self.main_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.run_cards = []
        if self.filter:
            name = next((str(s.get("name")) for s in scheds or [] if s.get("id") == self.filter), "one schedule")
            chip = Box(bg="fill_1/canvas", radius=15)
            cl = QHBoxLayout(chip)
            cl.setContentsMargins(12, 4, 6, 4)
            cl.setSpacing(6)
            lb = QLabel(f"Showing runs of <b>{name}</b>")
            lb.setTextFormat(Qt.TextFormat.RichText)
            lb.setProperty("fs", "small")
            theme.tint(lb, fg="text_2")
            cl.addWidget(lb)
            x = IconButton("x", "Show every run", 13)
            x.clicked.connect(lambda: self.set_filter(None))
            cl.addWidget(x)
            self.main_lay.addWidget(chip, 0, Qt.AlignmentFlag.AlignLeft)
        if items is None:
            self.main_lay.addWidget(Spinner(14))
        else:
            shown = [
                i
                for i in items
                if not self.filter
                or i.get("scheduleId") == self.filter
                or (self.filter == "dispatch" and i.get("kind") == "dispatch")
            ]
            if not shown:
                self.main_lay.addWidget(self._empty(bool(scheds)), 0, Qt.AlignmentFlag.AlignHCenter)
            groups: List[tuple] = []
            for it in shown:
                lab = day_label(float(it.get("endedAt") or 0), self.now)
                if groups and groups[-1][0] == lab:
                    groups[-1][1].append(it)
                else:
                    groups.append((lab, [it]))
            for lab, its in groups:
                sec = QWidget()
                sl = QVBoxLayout(sec)
                sl.setContentsMargins(0, 0, 0, 0)
                sl.setSpacing(8)
                head = SectionTitle(lab)
                head.count.set_count(len(its))
                sl.addWidget(head)
                for it in its:
                    iid = str(it.get("id"))
                    if it.get("kind") == "dispatch":
                        card = DispatchCard(it, (lambda i=iid: self.mark_one(i)), (lambda i=iid: self.remove(i)))
                    elif it.get("kind") == "helpers":
                        card = HelpersCard(it, (lambda i=iid: self.mark_one(i)), (lambda i=iid: self.remove(i)))
                    else:
                        card = RunCard(
                            it,
                            (lambda i=it: self.open_item(i)),
                            (lambda i=iid: self.mark_one(i)),
                            (lambda i=iid: self.remove(i)),
                        )
                    self.run_cards.append(card)
                    sl.addWidget(card)
                self.main_lay.addWidget(sec)

        while self.rail_list.count():
            w = self.rail_list.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.sched_cards = []
        self.rail_spinner.setVisible(scheds is None)
        self.rail_head.count.setVisible(bool(scheds))
        self.rail_head.count.set_count(len(scheds or []))
        for s in scheds or []:
            sid = str(s.get("id"))
            card = ScheduleCard(s, self.now, self.filter == sid, (lambda i=sid: self.set_filter(i)))
            self.sched_cards.append(card)
            self.rail_list.addWidget(card)
        self.rail_empty.setVisible(scheds is not None and not scheds)
        # With nothing scheduled the rail would only repeat the hero: it comes back with the first schedule.
        self.rail.setVisible(scheds is None or bool(scheds))

    def _empty(self, has_schedules: bool) -> QWidget:
        """``.inbox-empty``: the mark in its glow, a line, a hint and the one button, 440px wide at most."""
        box = EmptyHero()
        box_w = min(440, max(200, self.body.width() - 40))
        box.setFixedWidth(box_w)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(24, 56, 24, 56)
        lay.setSpacing(8)
        mark = Mark(32)
        box.mark = mark
        lay.addWidget(mark, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(6)
        self.hero_title = Text(
            "No runs yet." if has_schedules else "Nothing runs on its own yet.",
            theme.FS_UI,
            600,
            "text",
            align=Qt.AlignmentFlag.AlignHCenter,
        )
        lay.addWidget(self.hero_title)
        h = Text(
            "The first result lands here when a schedule fires. You can also run one now from the list on the right."
            if has_schedules
            else "A schedule runs a prompt in a folder at a set time — a nightly dependency audit, a Monday test sweep — and leaves the result here with a link back to the full session.",
            theme.FS_SMALL,
            400,
            "text_3",
            1.5,
            align=Qt.AlignmentFlag.AlignHCenter,
        )
        h.setFixedSize(box_w - 48, h.heightForWidth(box_w - 48))
        lay.addWidget(h)
        if not has_schedules:
            lay.addSpacing(6)
            b = button(
                "New schedule",
                kind="primary",
                size="sm",
                icon="plus",
                on_click=lambda: store.set_dialog("schedule", {}),
            )
            b.setIconSize(QSize(13, 13))
            b.setAccessibleName("New schedule")
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
        return box

    def _tick(self) -> None:
        self.now = now_ms()
        for c in self.sched_cards:
            c.refresh_time(self.now)
