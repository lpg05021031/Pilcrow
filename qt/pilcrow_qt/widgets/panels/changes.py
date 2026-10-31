"""The Changes tab (a port of web/src/components/panels/ChangesPanel.tsx): the working tree's diff
against HEAD (or a worktree's base), unified or side by side, with per-line review notes that go to
Claude as one message, a commit box, discard per file, and a way to ask Claude for a review or a PR.
Folders that are not git repositories show the edits this session's tools made instead (``SessionEdits``)."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QKeyEvent, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget

from ... import icons, theme
from ...lib.fmt import basename, dirname, err_text, lang_from_path, plural
from ...store import ConfirmSpec, store
from ..ui.bits import Spinner
from ..ui.menu import show_menu
from ..ui.signals import bind
from .diff_view import DiffView, Num, hunks_from_patch, style_textarea
from .editor import lexer_for_path
from .right_panel import Btn, EmptyState, Hairline, Refresher, Seg, Toolbar, icon_btn, lbl

Json = Dict[str, Any]
NARROW_W = 640  # side by side needs room; under this the view falls back to unified
MAIN_BRANCHES = ("main", "master", "trunk", "develop")
REVIEW_PROMPT = (
    "Review the uncommitted changes in this repository. Look for bugs, missed edge cases and anything that does not match "
    "the surrounding code. Report findings with file and line references; do not change files yet."
)
COMMIT_PROMPT = "Commit the current changes with a clear, conventional commit message. Do not push."


class Skeleton(QWidget):
    """Three grey bars per file while the first diff loads (``.changes-loading``, ``.sk``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("Loading changes")
        self.setFixedHeight(16 + 3 * (3 * 11 + 2 * 9) + 2 * 22 + 16)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.blend(t.fill_1, t.canvas))
        inner = self.width() - 28
        y = 16
        for k in range(3):
            for frac in ((46 - k * 9) / 100, 0.6, 1.0):
                p.drawRoundedRect(14, y, int(inner * frac), 11, 5.5, 5.5)
                y += 11 + 9
            y += 22 - 9


class CommitBox(QWidget):
    """The commit message box under the toolbar (``.commit-box``)."""

    def __init__(self, panel: ChangesPanel, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._panel = panel
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        self.message = QPlainTextEdit()
        self.message.setPlaceholderText("Commit message")
        self.message.setFixedHeight(78)
        self.message.installEventFilter(self)
        self.message.setAccessibleName("Commit message")
        style_textarea(self.message)
        self.message.textChanged.connect(lambda: self.commit.setEnabled(bool(self.message.toPlainText().strip())))
        lay.addWidget(self.message)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(Btn("Let Claude write it", kind="ghost", size="sm", on_click=panel.commit_by_claude))
        row.addStretch(1)
        row.addWidget(Btn("Cancel", size="sm", on_click=lambda: panel.set_commit_open(False)))
        self.commit = Btn("Commit all", kind="primary", size="sm", on_click=panel.commit)
        self.commit.setEnabled(False)
        row.addWidget(self.commit)
        lay.addLayout(row)
        outer.addWidget(inner)
        outer.addWidget(Hairline())

    def eventFilter(self, obj, e: QEvent) -> bool:  # noqa: N802
        if obj is self.message and e.type() == QEvent.Type.KeyPress:
            ke: QKeyEvent = e  # type: ignore[assignment]
            if (
                ke.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and ke.modifiers() & Qt.KeyboardModifier.ControlModifier
            ):
                self._panel.commit()
                return True
        return super().eventFilter(obj, e)


class NotesBar(QWidget):
    """The footer while notes are pending (``.notes-bar``): raised, with a hairline on top."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.fillRect(self.rect(), theme.qcolor(t.raised))
        p.fillRect(0, 0, self.width(), 1, theme.blend(t.hair_2, t.raised))


EDIT_TOOLS = ("Edit", "Write", "MultiEdit")


def lexer_warmer(sid: str) -> Callable[[], bool]:
    """A step that compiles one more of the syntax lexers the session's edits will need (Pygments
    builds a lexer's state machine on first use, some 30 ms each) and says whether any remain. Run
    at idle, so the Changes tab's first card does not pay for it."""
    m = store.models.get(sid)
    langs: List[str] = []
    seen = set()
    for t in m.tools.values() if m else []:
        if t.name in EDIT_TOOLS:
            path = str((t.input or {}).get("file_path") or "")
            lang = lang_from_path(path)
            if lang not in seen:
                seen.add(lang)
                langs.append(path)

    def step() -> bool:
        if langs:
            lexer_for_path(langs.pop(0))
        return bool(langs)

    return step


class ChangesPanel(QWidget):
    """Working-tree changes for one session's folder (``.changes``)."""

    def __init__(self, session_id: str, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session_id
        self.cwd = cwd
        self.data: Optional[Json] = None
        self.loading = False
        self._last_load = 0.0
        self._last_status = ""
        self._stale = False  # changed while hidden: load when shown
        self._edits_sig: Any = None  # the edits last rendered (tool ids and revisions)
        self._narrow = False
        self._commit_open = False
        session = store.session(session_id) or {}
        self.worktree: Optional[Json] = session.get("worktree") or None
        self.base = str((self.worktree or {}).get("base") or "HEAD")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # toolbar (.changes-bar: gap 6)
        self.bar = Toolbar()
        self.bar.box.setSpacing(6)
        if self.worktree:
            self.scope = Btn("Uncommitted", icon="chevrons-up-down", kind="ghost", size="sm", on_click=self._pick_scope)
            self.scope.set_icon_at_end(True)
        else:
            self.scope = lbl("Uncommitted", theme.FS_SMALL, 550)
        self.bar.add(self.scope)
        self.stat = QWidget()
        sl = QHBoxLayout(self.stat)
        sl.setContentsMargins(4, 0, 0, 0)
        sl.setSpacing(6)
        self.stat_add = Num("add")
        self.stat_del = Num("del")
        self.stat_files = lbl("", theme.FS_SMALL, tone="faint")
        sl.addWidget(self.stat_add)
        sl.addWidget(self.stat_del)
        sl.addWidget(self.stat_files)
        self.stat.hide()
        self.bar.add(self.stat)
        self.bar.stretch()
        self.pr_btn = Btn(
            "PR",
            icon="git-pull-request-arrow",
            kind="ghost",
            size="sm",
            on_click=self.ask_pr,
            tooltip="Ask Claude to push this branch and open a pull request",
        )
        self.pr_btn.hide()
        self.bar.add(self.pr_btn)
        self.review_btn = Btn(
            "Review",
            icon="eye",
            kind="ghost",
            size="sm",
            on_click=self.review,
            tooltip="Ask Claude to review these changes",
        )
        self.review_btn.hide()
        self.bar.add(self.review_btn)
        self.commit_btn = Btn(
            "Commit",
            icon="git-commit-horizontal",
            size="sm",
            on_click=lambda: self.set_commit_open(not self._commit_open),
        )
        self.commit_btn.setAccessibleName("Commit")
        self.commit_btn.hide()
        self.bar.add(self.commit_btn)
        self.seg = Seg(
            [
                {"value": "unified", "icon": "rows-2", "title": "Unified"},
                {"value": "split", "icon": "columns-2", "title": "Side by side"},
            ],
            store.ui.diff_mode,
        )
        self.seg.setAccessibleName("Diff layout")
        self.seg.changed.connect(lambda v: store.set_ui(diff_mode=v))
        self.bar.add(self.seg)
        self.refresh_btn = icon_btn("refresh-cw", "Refresh", 13)
        self.refresh_btn.clicked.connect(self.load)
        self.spinner = Spinner(13)
        self.spinner.hide()
        self.bar.add(self.spinner)
        self.bar.add(self.refresh_btn)
        lay.addWidget(self.bar)

        self.commit_box = CommitBox(self)
        self.commit_box.hide()
        lay.addWidget(self.commit_box)

        # not a repository: the summary line stays put above the scrolling edits (.changes-summary)
        self.summary_wrap = QWidget()
        sw = QVBoxLayout(self.summary_wrap)
        sw.setContentsMargins(0, 0, 0, 0)
        sw.setSpacing(0)
        self.summary = lbl("Not a git repository — showing edits made in this session.", theme.FS_SMALL, tone="faint")
        self.summary.setContentsMargins(14, 10, 14, 10)
        sw.addWidget(self.summary)
        sw.addWidget(Hairline())
        self.summary_wrap.hide()
        lay.addWidget(self.summary_wrap)

        # the files (.changes-files)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        host = QWidget()
        self._host_lay = QVBoxLayout(host)
        self._host_lay.setContentsMargins(0, 0, 0, 24)
        self._host_lay.setSpacing(0)
        self.view = DiffView(mode=self._view_mode(), wrap=self._narrow, can_comment=True, max_rows=600)
        self.view.set_actions(
            [
                ("file-text", lambda _f: "Open file", self.open_file),
                ("undo-2", lambda f: "Delete new file" if f.get("untracked") else "Discard changes", self.discard),
            ]
        )
        self.view.notes_changed.connect(self._on_notes)
        self._host_lay.addWidget(self.view)
        self.edits_view = DiffView(mode="unified", wrap=True, can_comment=False, max_rows=300, dual=False, simple=True)
        self.edits_view.on_open = self.open_file
        self.edits_view.hide()
        self._host_lay.addWidget(self.edits_view)
        self.skeleton = Skeleton()
        self._host_lay.addWidget(self.skeleton)
        self.empty = EmptyState(
            "circle-check",
            "Working tree is clean.",
            "Changes Claude makes will show up here for review. Hover a line and click the note icon to leave feedback.",
        )
        self.empty.hide()
        self._host_lay.addWidget(self.empty)
        self.empty_edits = EmptyState("", "No file edits in this session yet.")
        self.empty_edits.hide()
        self._host_lay.addWidget(self.empty_edits)
        self._host_lay.addStretch(1)
        self.scroll.setWidget(host)
        lay.addWidget(self.scroll, 1)

        # notes bar
        self.notes_bar = NotesBar()
        nl = QHBoxLayout(self.notes_bar)
        nl.setContentsMargins(12, 10, 12, 10)
        nl.setSpacing(8)
        self.notes_ico = QLabel()
        self.notes_ico.setFixedSize(14, 14)
        nl.addWidget(self.notes_ico)
        self.notes_text = lbl("", theme.FS_SMALL)
        nl.addWidget(self.notes_text, 1)
        self.add_notes_btn = Btn("Add to message", kind="ghost", size="sm", on_click=self.add_notes_to_composer)
        nl.addWidget(self.add_notes_btn)
        self.send_notes_btn = Btn("Send to Claude", icon="send", kind="primary", size="sm", on_click=self.send_notes)
        nl.addWidget(self.send_notes_btn)
        self.notes_bar.hide()
        lay.addWidget(self.notes_bar)

        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())
        bind(store.ui_changed, self, lambda _u: self._apply_mode())
        self._refresher = Refresher(self, session_id, self._on_rt, fs=False)
        bind(store.fs_changed, self, lambda _r: self.load())
        self._apply_mode()
        self.load()

    # ---- state
    def _paint(self) -> None:
        self.notes_ico.setPixmap(icons.pixmap("message-square-text", theme.tokens().text, 14))

    def _view_mode(self) -> str:
        mode = store.ui.diff_mode
        return "unified" if (mode == "split" and self._narrow) else mode

    @property
    def view_mode(self) -> str:
        return self._view_mode()

    def _apply_mode(self) -> None:
        mode = self._view_mode()
        self.view.set_mode(mode)
        self.view.set_wrap(self._narrow)
        self.seg.set_value(mode)
        self.seg.set_disabled(
            "split", self._narrow, "Side by side (widen the panel to use it)" if self._narrow else "Side by side"
        )

    def showEvent(self, e) -> None:  # noqa: N802
        # The web remounts this tab each time it is shown; here it stays built and refreshes instead.
        super().showEvent(e)
        if self._stale or (self.data is not None and time.monotonic() - self._last_load > 2.5):
            self.load()

    def resizeEvent(self, e) -> None:  # noqa: N802
        narrow = self.width() < NARROW_W
        if narrow != self._narrow:
            self._narrow = narrow
            self._apply_mode()
        super().resizeEvent(e)

    def _on_rt(self) -> None:
        rt = store.rt_of(self.sid)
        status = rt.status if rt else ""
        was = self._last_status
        self._last_status = status
        if not self.isVisible():
            # a hidden tab does no work: it catches up when it is shown
            self._stale = True
            return
        if status == "idle" and was != "idle":
            self.load()
        elif status in ("running", "requires_action") and time.monotonic() - self._last_load > 2.5:
            QTimer.singleShot(1200, self.load)
        if self.data is not None and not self.data.get("isRepo"):
            self._render_session_edits()

    # ---- loading
    def load(self) -> None:
        if not self.isVisible():
            self._stale = True
            return
        self._stale = False
        self.loading = True
        self._last_load = time.monotonic()
        self.spinner.show()
        self.refresh_btn.hide()
        params: Json = {"cwd": self.cwd}
        if self.base != "HEAD":
            params["base"] = self.base

        def ok(r: Any) -> None:
            # Anything but a result with isRepo is "not a repository", as the web reads it.
            self.set_data(r if isinstance(r, dict) else {"isRepo": False, "files": []})

        def bad(e: Any) -> None:
            self.set_data({"isRepo": False, "files": [], "error": err_text(e)})

        store.call("git.diff", params, quiet=True).then(ok).catch(bad)

    def set_data(self, data: Json) -> None:
        self.loading = False
        self.spinner.hide()
        self.refresh_btn.show()
        self.data = data
        self.skeleton.hide()
        if not data.get("isRepo"):
            self._render_session_edits()
            return
        self.summary_wrap.hide()
        self.bar.show()
        self.edits_view.hide()
        self.empty_edits.hide()
        files: List[Json] = list(data.get("files") or [])
        add = sum(int(f.get("additions") or 0) for f in files)
        dele = sum(int(f.get("deletions") or 0) for f in files)
        self.stat_add.set_value(add)
        self.stat_del.set_value(dele)
        self.stat_files.setText(f"· {plural(len(files), 'file')}")
        self.stat.setVisible(bool(files))
        branch = data.get("branch")
        self.pr_btn.setVisible(bool(branch) and branch not in MAIN_BRANCHES)
        self.review_btn.setVisible(bool(files))
        self.commit_btn.setVisible(bool(files))
        self.view.set_files(files)
        self.view.show()
        self.empty.setVisible(not files)

    def _render_session_edits(self) -> None:
        """Not a repository: the edits this session's Edit/Write tools made, from the transcript
        (``SessionEdits``: no toolbar, the summary line, then the files)."""
        self.summary_wrap.show()
        self.bar.hide()
        self.commit_box.hide()
        self.stat.hide()
        self.pr_btn.hide()
        self.review_btn.hide()
        self.commit_btn.hide()
        self.empty.hide()
        self.view.hide()
        m = store.models.get(self.sid)
        done = [t for t in (m.tools.values() if m else []) if t.name in EDIT_TOOLS and t.status == "done"]
        sig = tuple((t.id, t.rev) for t in done)
        if sig == self._edits_sig:
            return  # the same edits as last time: the grids stand
        self._edits_sig = sig
        edits: List[Json] = []
        for t in done:
            s = t.structured or {}
            path = str((t.input or {}).get("file_path") or "")
            patch = s.get("structuredPatch") if isinstance(s, dict) else None
            if isinstance(patch, list) and patch:
                hunks = hunks_from_patch(patch)
            elif t.name == "Write" and isinstance((t.input or {}).get("content"), str):
                content = str(t.input["content"]).split("\n")
                hunks = [
                    {
                        "oldStart": 0,
                        "oldLines": 0,
                        "newStart": 1,
                        "newLines": 0,
                        "lines": [{"t": "+", "s": s2, "n": k + 1} for k, s2 in enumerate(content)],
                    }
                ]
            else:
                continue
            edits.append(
                {
                    "path": path or t.id,
                    "_dir": dirname(path).replace(self.cwd, ".") if path else "",
                    "_abs": path,
                    "_id": t.id,
                    "status": "M",
                    "binary": False,
                    "additions": sum(1 for h in hunks for ln in h["lines"] if ln["t"] == "+"),
                    "deletions": sum(1 for h in hunks for ln in h["lines"] if ln["t"] == "-"),
                    "hunks": hunks,
                    "truncated": False,
                }
            )
        self.edits_view.set_files(edits)
        self.edits_view.setVisible(bool(edits))
        self.empty_edits.setVisible(not edits)

    # ---- actions
    def _pick_scope(self) -> None:
        base = str((self.worktree or {}).get("base") or "")
        items = [
            {
                "label": "Uncommitted",
                "hint": "Changes not committed yet",
                "checked": self.base == "HEAD",
                "on_select": lambda: self._set_base("HEAD"),
            },
            {
                "label": f"Since {base}",
                "hint": "Everything this worktree changed",
                "checked": self.base == base,
                "on_select": lambda: self._set_base(base),
            },
        ]
        show_menu(items, self.scope, 260)

    def _set_base(self, base: str) -> None:
        self.base = base
        self.scope.setText("Uncommitted" if base == "HEAD" else f"Since {base}")
        self.load()

    def set_commit_open(self, open_: bool) -> None:
        self._commit_open = open_
        self.commit_box.setVisible(open_)
        self.commit_btn.set_on(open_)
        if open_:
            self.commit_box.message.setFocus()

    def commit(self) -> None:
        message = self.commit_box.message.toPlainText().strip()
        if not message:
            return

        def done(r: Any) -> None:
            r = r or {}
            if r.get("ok"):
                store.toast(str(r.get("output") or "").split("\n")[0] or "Committed", "success")
                self.set_commit_open(False)
                self.commit_box.message.setPlainText("")
                store.bump_fs()
            else:
                store.toast(str(r.get("output") or "Commit failed"), "error")

        store.call("git.commit", {"cwd": self.cwd, "message": message}).then(done)

    def commit_by_claude(self) -> None:
        store.send_message(self.sid, COMMIT_PROMPT).then(lambda _r: self.set_commit_open(False))

    def review(self) -> None:
        rt = store.rt_of(self.sid)
        init = (rt.get("init") if rt else None) or {}
        has_review = "code-review" in (init.get("slashCommands") or [])
        store.send_message(self.sid, "/code-review" if has_review else REVIEW_PROMPT)

    def ask_pr(self) -> None:
        branch = (self.data or {}).get("branch") or ""
        files = (self.data or {}).get("files") or []
        text = (
            f"Push the branch {branch} and open a pull request for it. Use the gh or glab CLI if one is installed and signed in; "
            "otherwise push and give me the link to open the request myself. Write a clear title and a short description of the changes."
            + (" Commit the uncommitted changes first." if files else "")
        )
        store.send_message(self.sid, text)

    def open_file(self, f: Json) -> None:
        if f.get("_abs"):
            store.open_file(str(f["_abs"]))
            return
        root = (self.data or {}).get("root") or self.cwd
        store.open_file(f"{root}/{f.get('path')}")

    def discard(self, f: Json) -> None:
        untracked = bool(f.get("untracked"))

        def run(_checked: bool) -> None:
            def done(r: Any) -> None:
                if not (r or {}).get("ok"):
                    store.toast(str((r or {}).get("output") or "Could not discard"), "error")
                store.bump_fs()

            store.call("git.discard", {"cwd": self.cwd, "path": f.get("path"), "untracked": untracked}).then(done)

        store.confirm_action(
            ConfirmSpec(
                title=f"Discard changes to {basename(str(f.get('path') or ''))}?",
                body="This deletes the new file."
                if untracked
                else "The file goes back to its last committed version. This cannot be undone.",
                confirm="Delete file" if untracked else "Discard changes",
                danger=True,
                on_confirm=run,
            )
        )

    # ---- notes
    def _on_notes(self) -> None:
        n = self.view.note_count()
        self.notes_bar.setVisible(n > 0)
        self.notes_text.setText(f"{plural(n, 'note')} on this diff")

    def notes_message(self) -> str:
        return self.view.notes_message()

    def add_notes_to_composer(self) -> None:
        store.insert_into_composer(self.sid, self.notes_message())
        self.view.clear_notes()

    def send_notes(self) -> None:
        text = self.notes_message()

        def sent(_r: Any) -> None:
            self.view.clear_notes()
            store.toast("Sent your notes to Claude", "success")

        store.send_message(self.sid, text).then(sent)
