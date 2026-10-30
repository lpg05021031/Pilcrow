"""The session page (a port of web/src/components/SessionView.tsx).

The header (title, crumbs with the folder picker while the session is fresh, status pill, diffstat,
side-panel toggle and the ⋯ menu), the MCP notice, the error bar, then the Transcript and the
Composer stacked. The git information behind the crumbs and the diffstat is refreshed like
``useGitInfo``: on every status change and file-system change, at most every 1.5 seconds.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import icons, theme
from ..lib.fmt import basename, cost, tildify
from ..store import ConfirmSpec, store
from .composer import Composer, LayoutButton
from .folder_picker import FolderPicker
from .mcp_notice import McpNotice
from .sidebar import IconBtn
from .ui.bits import Mark, copy_text, hairline, label
from .ui.menu import show_menu
from .ui.signals import bind

Json = Dict[str, Any]

STATUS_TEXT = {
    "running": "Working",
    "starting": "Starting",
    "requires_action": "Needs you",
    "idle": "Ready",
    "stopped": "Ready",
    "error": "Stopped with an error",
}

EXAMPLES = [
    "Give me a tour of this codebase",
    "Find the riskiest code path and explain why",
    "Write tests for the module I changed last",
    "Review my uncommitted changes",
]


def running_workflow(tasks: Optional[List[Json]]) -> Optional[str]:
    for t in tasks or []:
        if t.get("type") == "local_workflow" and t.get("status") == "running":
            return str(t.get("workflowName") or t.get("description") or "workflow")
    return None


def _make_transcript(session_id: str, cwd: str) -> QWidget:
    """The conversation on the canvas engine, otherwise a stand-in with the same surface so this page runs alone."""
    try:
        from ..canvas.transcript import Transcript

        return Transcript(session_id, cwd)
    except Exception as e:  # noqa: BLE001 — the transcript is built separately; this page must not depend on it
        print(f"[session_view] transcript unavailable, using a placeholder: {e!r}")
        return _TranscriptPlaceholder()


class _TranscriptPlaceholder(QScrollArea):
    """What the page shows for the conversation until the Transcript lands: the empty state, or nothing."""

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._host = QWidget()
        self._lay = QVBoxLayout(self._host)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.addStretch(1)
        self.setWidget(self._host)
        self._empty: Optional[QWidget] = None

    def set_empty(self, w: QWidget) -> None:
        if self._empty is not None:
            self._empty.deleteLater()
        self._empty = w
        self._lay.insertWidget(0, w, 0, Qt.AlignmentFlag.AlignHCenter)

    def open_find(self) -> None:
        pass

    def scroll_to_bottom(self) -> None:
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def set_scrolled_callback(self, fn: Callable[[bool], None]) -> None:
        self.verticalScrollBar().valueChanged.connect(lambda v: fn(v > 0))


class SessionView(QWidget):
    def __init__(self, session: Json, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.session = session
        self.sid = str(session.get("id"))
        self.git: Optional[Json] = None
        self._git_last = 0.0
        self._git_timer = QTimer(self)
        self._git_timer.setSingleShot(True)
        self._git_timer.timeout.connect(self._fetch_git)
        self._git_cwd = ""
        self._git_tick = ""
        self._picker: Optional[QFrame] = None
        self._title_edit: Optional[QLineEdit] = None
        self._status_seen = ""
        self.setObjectName("session")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- header
        self.head = QWidget()
        self.head.setObjectName("session-head")
        self.head.setFixedHeight(theme.HEADER_H)
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(20, 0, 10, 0)
        hl.setSpacing(12)
        # .icon-btn.head-sidebar: 28px, shown only while the sidebar is hidden
        self.side_btn = IconBtn("panel-left-open", "Show sidebar (Ctrl+B)", 16, 28)
        self.side_btn.clicked.connect(lambda: store.set_ui(sidebar=True))
        hl.addWidget(self.side_btn)

        wrap = QWidget()
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(10)
        self.title = _TitleLabel()
        self.title.setObjectName("session-title")
        self.title.setToolTip("Double-click to rename")
        self.title.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.title.setMinimumWidth(40)
        # .session-title: 13.5px at 600 (Qt sizes fonts in whole pixels; 13.5 rounds up like the CSS engines)
        self.title.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.title.double_clicked.connect(self.start_rename)
        wl.addWidget(self.title)
        self.crumbs = QWidget()
        self.crumbs.setObjectName("session-crumbs")
        cl = QHBoxLayout(self.crumbs)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)
        self.folder_crumb = Crumb()
        cl.addWidget(self.folder_crumb)
        self.folder_btn = CrumbButton("Change the folder for this session")
        self.folder_btn.clicked.connect(self.pick_folder)
        self.folder_btn.hide()
        cl.addWidget(self.folder_btn)
        self.branch_crumb = Crumb()
        self.branch_crumb.hide()
        cl.addWidget(self.branch_crumb)
        wl.addWidget(self.crumbs)
        self.pill = StatusPill()
        self.pill.hide()
        self.pill.link.clicked.connect(lambda: store.set_ui(panel=True, panel_tab="tasks"))
        wl.addWidget(self.pill)
        wl.addStretch(1)
        hl.addWidget(wrap, 1)

        actions = QWidget()
        al = QHBoxLayout(actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(2)
        self.diffstat = Diffstat()
        self.diffstat.hide()
        self.diffstat.clicked.connect(self._toggle_changes)
        al.addWidget(self.diffstat)
        self.panel_btn = IconBtn("panel-right", "Side panel (Ctrl+\\)", 16, 28)
        self.panel_btn.clicked.connect(lambda: store.set_ui(panel=not store.ui.panel))
        al.addWidget(self.panel_btn)
        self.menu_btn = IconBtn("ellipsis", "Session actions", 16, 28)
        self.menu_btn.clicked.connect(self.open_menu)
        al.addWidget(self.menu_btn)
        hl.addWidget(actions)
        root.addWidget(self.head)
        self.head_line = hairline()
        self.head_line.hide()
        root.addWidget(self.head_line)

        # ---- notices
        self.mcp = McpNotice(self.sid)
        root.addWidget(self.mcp)
        self.error_bar = ErrorBar(self.sid)
        self.error_bar.hide()
        root.addWidget(self.error_bar)

        # ---- conversation and composer
        cwd = self.cwd
        self.transcript = _make_transcript(self.sid, cwd)
        self.empty = SessionEmpty(session, cwd, self)
        self.transcript.set_empty(self.empty)
        self.transcript.set_scrolled_callback(self._set_scrolled)
        root.addWidget(self.transcript, 1)
        self.composer = Composer(self.sid, cwd, self)
        root.addWidget(self.composer)

        bind(store.rt_changed, self, self._on_rt)
        bind(store.ui_changed, self, lambda _u: self._on_ui())
        bind(store.info_changed, self, lambda _i: self.render())
        bind(store.fs_changed, self, lambda _r: self._schedule_git())
        bind(store.connected_changed, self, lambda on: on and self._schedule_git(force=True))
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()
        self._on_ui()
        self.render()
        self._schedule_git()

    # ---------------------------------------------------------------- state
    @property
    def rt(self):
        return store.rt.get(self.sid)

    @property
    def cwd(self) -> str:
        r = self.rt
        return str((r.cwd if r else "") or self.session.get("cwd") or "")

    @property
    def status(self) -> str:
        r = self.rt
        return str((r.status if r else "") or self.session.get("status") or "")

    @property
    def fresh(self) -> bool:
        """Until the first message goes out, the folder is still a choice, not a fact."""
        r = self.rt
        if r is None or not r.loaded or r.live or self.session.get("worktree"):
            return False
        if store.get_model(self.sid).items:
            return False
        return r.status in ("idle", "stopped", "")

    def update_session(self, session: Json) -> None:
        self.session = session
        self.render()

    def open_find(self) -> None:
        self.transcript.open_find()

    def _on_rt(self, sid: str) -> None:
        if sid != self.sid:
            return
        self.render()
        self._schedule_git()

    def _on_ui(self) -> None:
        ui = store.ui
        self.side_btn.setVisible(not ui.sidebar)
        self.panel_btn.set_active(ui.panel)
        self.diffstat.set_on(bool(ui.panel and ui.panel_tab == "changes"))

    def _set_scrolled(self, on: bool) -> None:
        self.head_line.setVisible(bool(on))

    def _paint(self) -> None:
        t = theme.tokens()
        # The head sits in the rounded sheet (main_window.Card) and must not paint over its corners.
        self.head.setStyleSheet("#session-head { background: transparent; }")
        _ = t
        self.render()

    # ---------------------------------------------------------------- rendering
    def render(self) -> None:
        s = self.session
        r = self.rt
        cwd = self.cwd
        home = (store.info or {}).get("home")
        if self._title_edit is None:
            self._set_title(str(s.get("title") or ""))
        self.crumbs.setToolTip(tildify(cwd, home))
        fresh = self.fresh
        self.folder_btn.setVisible(fresh)
        self.folder_crumb.setVisible(not fresh)
        wt = s.get("worktree")
        if fresh:
            self.folder_btn.set_text(basename(cwd))
        else:
            self.folder_crumb.set(
                "folder-git-2" if wt else "folder", basename(str((wt or {}).get("repoRoot") or s.get("project") or cwd))
            )
        git = self.git or {}
        branch = git.get("branch")
        self.branch_crumb.setVisible(bool(branch))
        if branch:
            self.branch_crumb.set("git-branch", str(branch), tone="plan" if wt else "")
            self.branch_crumb.setToolTip(
                f"Worktree at {tildify(str(wt.get('path') or ''), home)}" if wt else "Current branch"
            )
        status = self.status
        tasks = r.get("tasks") if r else None
        self.pill.set_status(status, running_workflow(tasks) if status == "running" else None)
        changes = int(git.get("changed") or 0) + int(git.get("untracked") or 0)
        self.diffstat.setVisible(bool(git.get("isRepo")) and changes > 0)
        if changes > 0:
            self.diffstat.set_values(int(git.get("insertions") or 0), int(git.get("deletions") or 0), changes)
        err = str(r.get("error") or "") if r else ""
        self.error_bar.setVisible(status == "error" and bool(err))
        if err:
            self.error_bar.set_error(err)
        self.empty.refresh_state(s, git if self.git else None, cwd, fresh)
        if cwd != self.composer.cwd:
            self.composer.set_cwd(cwd)

    def _set_title(self, text: str) -> None:
        self.title.setText(text)
        self.title.setToolTip(f"{text}\nDouble-click to rename" if text else "Double-click to rename")

    # ---------------------------------------------------------------- git
    def _schedule_git(self, force: bool = False) -> None:
        cwd = self.cwd
        r = self.rt
        status = self.status
        tick = f"{math.floor((r.rev if r else 0) / 40) if status == 'running' else status}:{store.fs_rev}"
        if cwd == self._git_cwd and tick == self._git_tick and not force:
            return
        self._git_cwd, self._git_tick = cwd, tick
        if not cwd:
            return
        wait = max(0.0, 1.5 - (time.time() - self._git_last))
        self._git_timer.start(int(wait * 1000))

    def _fetch_git(self) -> None:
        cwd = self.cwd
        if not cwd:
            return
        self._git_last = time.time()

        def ok(info: Any) -> None:
            if isinstance(info, dict):
                self.git = info
                self.render()

        def bad(_e: Any) -> None:
            self._git_tick = ""  # a lost answer (a reconnect, say) is asked for again on the next change

        store.call("git.info", {"cwd": cwd}, quiet=True).then(ok).catch(bad)

    # ---------------------------------------------------------------- actions
    def _toggle_changes(self) -> None:
        ui = store.ui
        on = ui.panel and ui.panel_tab == "changes"
        store.set_ui(panel=not on, panel_tab="changes")

    def start_rename(self) -> None:
        if self._title_edit is not None:
            return
        edit = _TitleEdit(str(self.session.get("title") or ""))
        self._title_edit = edit
        lay = self.title.parentWidget().layout()
        lay.replaceWidget(self.title, edit)
        self.title.hide()
        edit.setFocus()
        edit.selectAll()

        def done(save: bool) -> None:
            if self._title_edit is None:
                return
            text = edit.text().strip()
            self._title_edit = None
            lay.replaceWidget(edit, self.title)
            edit.deleteLater()
            self.title.show()
            if save and text and text != self.session.get("title"):
                store.rename_session(self.sid, text)

        edit.finished.connect(done)

    def pick_folder(self) -> None:
        if self._picker is not None:
            self._picker.close()
            return
        pop = QFrame(self, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        pop.setObjectName("popover")
        pop.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pop.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        t = theme.tokens()
        glass, line = theme.blend(t.glass, t.canvas).name(), theme.blend(t.hair_2, t.raised).name()
        pop.setStyleSheet(f"#popover {{ background: {glass}; border: 1px solid {line}; border-radius: {theme.R3}px; }}")
        lay = QVBoxLayout(pop)
        lay.setContentsMargins(1, 1, 1, 1)
        cwd = self.cwd

        def picked(d: str) -> None:
            pop.close()
            if d != cwd:
                store.set_cwd(self.sid, d).catch(lambda _e: None)

        picker = FolderPicker(cwd, picked)
        lay.addWidget(picker)
        self._picker = pop
        self.folder_btn.set_on(True)

        def closed(*_a: Any) -> None:
            self._picker = None
            try:
                self.folder_btn.set_on(False)
            except RuntimeError:
                pass

        pop.destroyed.connect(closed)
        pop.move(self.folder_btn.mapToGlobal(QPoint(0, self.folder_btn.height() + 4)))
        pop.show()
        picker.q.setFocus()

    def open_menu(self) -> None:
        show_menu(self.menu_items(), self.menu_btn, 250, align_right=True)

    def menu_items(self) -> List[Json]:
        s, r, sid = self.session, self.rt, self.sid
        cwd = self.cwd
        wt = s.get("worktree")
        usage = (r.get("usage") if r else None) or {}
        items: List[Json] = [
            {"label": "Rename", "icon": "pencil", "on_select": self.start_rename},
            {"label": "Fork into a new session", "icon": "git-fork", "on_select": lambda: store.fork_session(sid)},
            {
                "label": "Open folder",
                "icon": "folder-open",
                "on_select": lambda: store.call("shell.openPath", {"path": cwd}),
            },
            {
                "label": "Open a terminal here",
                "icon": "square-terminal",
                "on_select": lambda: store.set_ui(panel=True, panel_tab="terminal"),
            },
            {"separator": True},
        ]
        from ..store import VIEW_MODES

        for v in VIEW_MODES:
            items.append(
                {
                    "label": f"{v['label']} view",
                    "hint": v["hint"],
                    "checked": v["id"] == store.ui.view,
                    "on_select": lambda vid=v["id"]: store.set_ui(view=vid),
                }
            )
        items.append({"separator": True})
        items.append(
            {
                "label": "Copy session ID",
                "icon": "copy",
                "on_select": lambda: (copy_text(sid), store.toast("Session ID copied", "success")),
            }
        )
        if (usage.get("costUsd") or 0) > 0:
            items.append(
                {
                    "label": f"Cost so far {cost(float(usage['costUsd']))}",
                    "icon": "circle-dollar-sign",
                    "on_select": lambda: store.set_ui(panel=True, panel_tab="info"),
                }
            )
        items.append({"separator": True})
        if r is not None and r.live:
            items.append(
                {
                    "label": "Stop Claude Code process",
                    "hint": "It restarts on your next message",
                    "icon": "power",
                    "on_select": lambda: store.call("session.close", {"id": sid}),
                }
            )
        items.append(
            {
                "label": "Unarchive" if s.get("archived") else "Archive",
                "icon": "archive",
                "on_select": lambda: store.archive_session(sid, not s.get("archived")),
            }
        )
        items.append(
            {
                "label": "Delete…",
                "icon": "trash",
                "danger": True,
                "on_select": lambda: store.confirm_action(
                    ConfirmSpec(
                        title="Delete this session?",
                        body="The conversation history is removed from ~/.claude. "
                        "Files Claude changed stay as they are.",
                        confirm="Delete session",
                        danger=True,
                        checkbox={"label": f"Also remove the worktree and branch {wt.get('branch')}", "value": False}
                        if wt
                        else None,
                        on_confirm=lambda checked: store.delete_session(sid, checked),
                    )
                ),
            }
        )
        return items


class _TitleLabel(QLabel):
    double_clicked = Signal()

    def mouseDoubleClickEvent(self, _e) -> None:  # noqa: N802
        self.double_clicked.emit()


class _TitleEdit(QLineEdit):
    """The inline rename field: Enter or leaving saves, Escape puts the old title back."""

    finished = Signal(bool)

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self._done = False
        self.setProperty("quiet", "true")
        self.setAccessibleName("Session title")
        self.returnPressed.connect(lambda: self._finish(True))

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self._finish(False)
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e) -> None:  # noqa: N802
        super().focusOutEvent(e)
        self._finish(True)

    def _finish(self, save: bool) -> None:
        if self._done:
            return
        self._done = True
        self.finished.emit(save)


class Crumb(QWidget):
    """An icon and a name in the header's second row (``.crumb``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._icon = ""
        self._tone = ""
        self._text = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.ico = QLabel()
        lay.addWidget(self.ico)
        self.lb = QLabel()
        lay.addWidget(self.lb)
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def set(self, icon: str, text: str, tone: str = "") -> None:
        self._icon, self._text, self._tone = icon, text, tone
        self._paint()

    def text(self) -> str:
        return self._text

    def _paint(self) -> None:
        t = theme.tokens()
        color = t.plan if self._tone == "plan" else t.text_3
        if self._icon:
            self.ico.setPixmap(icons.pixmap(self._icon, color, 12))
        fm = QFontMetrics(self.lb.font())
        self.lb.setText(fm.elidedText(self._text, Qt.TextElideMode.ElideRight, 220))
        self.lb.setStyleSheet(f"color: {color}; font-size: {theme.FS_SMALL}px;")


class CrumbButton(LayoutButton):
    """The folder crumb while the folder can still be changed (``.crumb-btn``): a quiet button with a chevron."""

    def __init__(self, tooltip: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._on = False
        self._text = ""
        self.setFlat(True)
        self.setFixedHeight(22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 7, 0)
        lay.setSpacing(4)
        self.ico = QLabel()
        lay.addWidget(self.ico)
        self.lb = QLabel()
        lay.addWidget(self.lb)
        self.chev = QLabel()
        lay.addWidget(self.chev)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def set_text(self, text: str) -> None:
        self._text = text
        self._paint()

    def text(self) -> str:  # type: ignore[override]
        return self._text

    def set_on(self, on: bool) -> None:
        self._on = on
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        color = t.text if self._on else t.text_2
        self.ico.setPixmap(icons.pixmap("folder", color, 12))
        self.chev.setPixmap(icons.pixmap("chevron-down", color, 11))
        self.lb.setText(QFontMetrics(self.lb.font()).elidedText(self._text, Qt.TextElideMode.ElideRight, 220))
        self.lb.setStyleSheet(f"color: {color}; font-size: {theme.FS_SMALL}px;")
        fill = theme.blend(t.fill_2, t.canvas).name()
        bg = fill if self._on else "transparent"
        self.setStyleSheet(
            f"QPushButton {{ border: none; border-radius: {theme.R2}px; background: {bg}; }}"
            f" QPushButton:hover {{ background: {fill}; }}"
        )


class StatusPill(QWidget):
    """The session's state beside the title (``.status-pill``): a dot that breathes while working, and the word."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.status = ""
        self._t0 = time.monotonic()
        self.setFixedHeight(22)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(7)
        self.dot = _Dot(self)
        lay.addWidget(self.dot)
        self.lb = QLabel()
        lay.addWidget(self.lb)
        self.link = QPushButton()
        self.link.setFlat(True)
        self.link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.link.setToolTip("Show the workflow in the Tasks panel")
        self.link.setAccessibleName("Show the workflow in the Tasks panel")
        self.link.hide()
        lay.addWidget(self.link)
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def set_status(self, status: str, workflow: Optional[str] = None) -> None:
        self.status = status
        show = bool(status) and status not in ("idle", "stopped")
        self.setVisible(show)
        if not show:
            self.dot.set_live(False)
            return
        self.lb.setText(STATUS_TEXT.get(status, status))
        self.link.setVisible(bool(workflow))
        if workflow:
            self.link.setText(f"· {workflow}")
        self.dot.set_live(status in ("running", "starting"))
        self._paint()

    def text(self) -> str:
        return self.lb.text()

    def _paint(self) -> None:
        t = theme.tokens()
        color = t.accent_text if self.status == "requires_action" else t.err if self.status == "error" else t.text_3
        weight = 600 if self.status == "requires_action" else 500
        self.lb.setStyleSheet(f"color: {color}; font-size: {theme.FS_SMALL}px; font-weight: {weight};")
        self.link.setStyleSheet(
            f"QPushButton {{ border: none; padding: 0; color: {color}; font-size: {theme.FS_SMALL}px; }}"
            " QPushButton:hover { text-decoration: underline; }"
        )
        self.dot.color = color
        self.dot.update()


class _Dot(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.color = ""
        self.setFixedSize(6, 6)
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self.update)

    def set_live(self, on: bool) -> None:
        if on and not store.reduced_motion:
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self.color or theme.tokens().text_3)
        if self._timer.isActive():
            phase = (time.monotonic() - self._t0) % 1.2 / 1.2
            c.setAlphaF(0.45 + 0.55 * (0.5 - 0.5 * math.cos(phase * 2 * math.pi)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawEllipse(QPointF(3, 3), 3, 3)


class Diffstat(LayoutButton):
    """``+14 −3`` in a pill: how much changed in the working tree; opens the Changes panel."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._on = False
        self.setFlat(True)
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 0, 9, 0)
        lay.setSpacing(6)
        self.add = QLabel()
        self.dele = QLabel()
        lay.addWidget(self.add)
        lay.addWidget(self.dele)
        self.add_n = self.del_n = 0
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def set_values(self, ins: int, dele: int, changes: int) -> None:
        self.add_n, self.del_n = ins, dele
        self.setToolTip(f"{changes} changed file{'s' if changes > 1 else ''} — review (Alt+1)")
        self.setAccessibleName(self.toolTip())
        self._paint()

    def set_on(self, on: bool) -> None:
        self._on = on
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.add.setText(f"+{self.add_n}")
        self.dele.setText(f"−{self.del_n}")
        self.add.setStyleSheet(
            f"color: {t.ok if self.add_n else t.text_4}; font-size: {theme.FS_SMALL}px; font-weight: 600;"
        )
        self.dele.setStyleSheet(
            f"color: {t.err if self.del_n else t.text_4}; font-size: {theme.FS_SMALL}px; font-weight: 600;"
        )
        bg = theme.blend(t.fill_2 if self._on else t.fill_1, t.canvas).name()
        hover = theme.blend(t.fill_2, t.canvas).name()
        line = theme.blend(t.hair, t.canvas).name()
        self.setStyleSheet(
            f"QPushButton {{ background: {bg}; border: 1px solid {line}; border-radius: 13px; margin-right: 4px; }}"
            f" QPushButton:hover {{ background: {hover}; }}"
        )


class ErrorBar(QWidget):
    """The line under the header when the session stopped with an error (``.session-error``)."""

    def __init__(self, session_id: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session_id
        self._error = ""
        host = QWidget()
        host.setObjectName("session-error")
        host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 4, 20, 0)
        outer.addWidget(host)
        lay = QHBoxLayout(host)
        lay.setContentsMargins(14, 8, 8, 8)
        lay.setSpacing(10)
        self.text = QLabel()
        self.text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.text, 1)
        self.btn = QPushButton()
        self.btn.setProperty("fs", "sm")
        self.btn.clicked.connect(self._act)
        lay.addWidget(self.btn)
        self.host = host
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def set_error(self, error: str) -> None:
        import re

        self._error = error
        self.text.setText(error.split("\n")[0])
        t = theme.tokens()
        if re.search(r"Settings|not found|No Claude Code", error, re.I):
            self.btn.setText("Settings")
            self.btn.setIcon(icons.icon("settings-2", t.text_2, 13))
        else:
            self.btn.setText("Retry")
            self.btn.setIcon(icons.icon("rotate-ccw", t.text_2, 13))
        self.btn.setAccessibleName(self.btn.text())

    def _act(self) -> None:
        if self.btn.text() == "Settings":
            store.set_dialog("settings", "claude")
        else:
            store.insert_into_composer(self.sid, "continue")

    def _paint(self) -> None:
        t = theme.tokens()
        self.host.setStyleSheet(
            f"#session-error {{ background: {theme.blend(t.err_wash, t.canvas).name()}; border-radius: {theme.R3}px; }}"
        )
        self.text.setStyleSheet(f"color: {t.err}; font-size: {theme.FS_SMALL}px;")


class SessionEmpty(QWidget):
    """What a conversation shows before its first message: the folder, its branch, the memory note, example prompts."""

    def __init__(self, session: Json, cwd: str, view: SessionView, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.view = view
        self.sid = str(session.get("id"))
        self._memory = 0
        self._memory_for = ""
        self.setObjectName("session-empty")
        self.setMaximumWidth(620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 90, 20, 24)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(Mark(56), 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(8)
        self.title = QLabel()
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet(f"font-size: {theme.FS_DISPLAY}px; font-weight: 600;")
        lay.addWidget(self.title)
        path_row = QWidget()
        pl = QHBoxLayout(path_row)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(10)
        pl.addStretch(1)
        self.path = label("", tone="faint", mono=True)
        pl.addWidget(self.path)
        self.change = QPushButton("Change folder")
        self.change.setProperty("kind", "link")
        self.change.setCursor(Qt.CursorShape.PointingHandCursor)
        self.change.setAccessibleName("Change folder")
        self.change.clicked.connect(view.pick_folder)
        self.change.hide()
        pl.addWidget(self.change)
        pl.addStretch(1)
        lay.addWidget(path_row)
        self.git = label("", tone="faint", size="small")
        self.git.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.git.hide()
        lay.addWidget(self.git)
        self.mem = QPushButton()
        self.mem.setProperty("kind", "ghost")
        self.mem.setProperty("fs", "sm")
        self.mem.setToolTip("Notes from earlier sessions in this folder ride along in this session")
        self.mem.clicked.connect(lambda: store.set_dialog("settings", "memory"))
        self.mem.hide()
        lay.addWidget(self.mem, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(14)
        self.examples = QWidget()
        self.examples.setMaximumWidth(560)
        el = _Flow(self.examples)
        self.example_buttons: List[QPushButton] = []
        for e in EXAMPLES:
            b = QPushButton(e)
            b.setFlat(True)
            b.setFixedHeight(32)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setAccessibleName(e)
            b.clicked.connect(lambda _c=False, text=e: store.insert_into_composer(self.sid, text, True))
            el.add(b)
            self.example_buttons.append(b)
        lay.addWidget(self.examples, 0, Qt.AlignmentFlag.AlignHCenter)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.change.setIcon(icons.icon("folder", t.text_2, 12))
        self.mem.setIcon(icons.icon("book-marked", t.text_2, 12))
        fill, line = theme.blend(t.fill_1, t.canvas).name(), theme.blend(t.hair, t.canvas).name()
        for b in self.example_buttons:
            b.setStyleSheet(
                f"QPushButton {{ height: 32px; padding: 0 14px; border-radius: 16px; font-size: {theme.FS_UI}px;"
                f" color: {t.text_2}; background: {fill}; border: 1px solid {line}; }}"
                f" QPushButton:hover {{ background: {theme.blend(t.fill_2, t.canvas).name()}; color: {t.text}; }}"
            )

    def refresh_state(self, session: Json, git: Optional[Json], cwd: str, fresh: bool) -> None:
        home = (store.info or {}).get("home")
        self.title.setText(basename(cwd))
        self.path.setText(tildify(cwd, home))
        self.change.setVisible(fresh)
        wt = session.get("worktree")
        if git and git.get("isRepo"):
            bits = [str(git.get("branch") or "detached")]
            if wt:
                bits.append(f"isolated worktree from {wt.get('base')}")
            n = int(git.get("changed") or 0) + int(git.get("untracked") or 0)
            if n:
                bits.append(f"{n} uncommitted")
            self.git.setText("⎇ " + " · ".join(bits))
            self.git.show()
        else:
            self.git.hide()
        key = str(session.get("project") or cwd)
        if key != self._memory_for:
            self._memory_for = key

            def ok(r: Any) -> None:
                self._memory = len((r or {}).get("notes") or [])
                self._render_memory()

            store.call("memory.list", {"cwd": key}, quiet=True).then(ok).catch(lambda _e: None)
        self._render_memory()

    def _render_memory(self) -> None:
        n = self._memory
        self.mem.setVisible(n > 0)
        if n > 0:
            self.mem.setText(f"Claude starts with {n} note{'s' if n > 1 else ''} from earlier sessions here")


class _Flow:
    """Buttons laid out in wrapping rows of at most two (the four examples wrap that way at the web's width)."""

    def __init__(self, host: QWidget) -> None:
        self.lay = QVBoxLayout(host)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(8)
        self.row: Optional[QHBoxLayout] = None
        self.n = 0

    def add(self, w: QWidget) -> None:
        if self.row is None or self.n % 2 == 0:
            self.row = QHBoxLayout()
            self.row.setSpacing(8)
            self.row.setContentsMargins(0, 0, 0, 0)
            self.row.addStretch(1)
            self.row.addStretch(1)
            self.lay.addLayout(self.row)
        self.row.insertWidget(self.row.count() - 1, w)
        self.n += 1
