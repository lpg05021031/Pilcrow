"""The Changes tab (a port of web/src/components/panels/ChangesPanel.tsx): the working tree's diff
against HEAD (or a worktree's base), unified or side by side, with per-line review notes that go to
Claude as one message, a commit box, discard per file, and a way to ask Claude for a review or a PR.
Folders that are not git repositories show the edits this session's tools made instead."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget

from ... import icons, theme
from ...lib.fmt import basename, dirname, err_text, plural
from ...store import ConfirmSpec, store
from ..ui.bits import IconButton, Spinner, button, hairline, label
from ..ui.menu import show_menu
from ..ui.signals import bind
from .diff_view import DiffView, Num, hunks_from_patch
from .right_panel import EmptyState, Refresher, Toolbar

Json = Dict[str, Any]
NARROW_W = 640  # side by side needs room; under this the view falls back to unified
MAIN_BRANCHES = ("main", "master", "trunk", "develop")
REVIEW_PROMPT = (
    "Review the uncommitted changes in this repository. Look for bugs, missed edge cases and anything that does not match "
    "the surrounding code. Report findings with file and line references; do not change files yet."
)
COMMIT_PROMPT = "Commit the current changes with a clear, conventional commit message. Do not push."


def _skeleton() -> QWidget:
    """Three grey bars per file while the first diff loads (``.changes-loading``)."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(14, 16, 14, 16)
    lay.setSpacing(22)
    t = theme.tokens()
    for k in range(3):
        f = QWidget()
        fl = QVBoxLayout(f)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(9)
        for width in (f"{46 - k * 9}%", "30%", "100%"):
            bar = QWidget()
            bar.setFixedHeight(11)
            bar.setStyleSheet(f"background: {theme.blend(t.fill_1, t.chrome).name()}; border-radius: 5px;")
            if width != "100%":
                bar.setMaximumWidth(int(520 * int(width[:-1]) / 100))
            fl.addWidget(bar)
        lay.addWidget(f)
    w.setAccessibleName("Loading changes")
    return w


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
        self.message.setFixedHeight(70)
        self.message.installEventFilter(self)
        self.message.textChanged.connect(lambda: self.commit.setEnabled(bool(self.message.toPlainText().strip())))
        lay.addWidget(self.message)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(button("Let Claude write it", kind="ghost", size="sm", on_click=panel.commit_by_claude))
        row.addStretch(1)
        row.addWidget(button("Cancel", size="sm", on_click=lambda: panel.set_commit_open(False)))
        self.commit = button("Commit all", kind="primary", size="sm", on_click=panel.commit)
        self.commit.setEnabled(False)
        row.addWidget(self.commit)
        lay.addLayout(row)
        outer.addWidget(inner)
        outer.addWidget(hairline())

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


class ChangesPanel(QWidget):
    """Working-tree changes for one session's folder."""

    def __init__(self, session_id: str, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session_id
        self.cwd = cwd
        self.data: Optional[Json] = None
        self.loading = False
        self._last_load = 0.0
        self._last_status = ""
        self._narrow = False
        self._commit_open = False
        session = store.session(session_id) or {}
        self.worktree: Optional[Json] = session.get("worktree") or None
        self.base = str((self.worktree or {}).get("base") or "HEAD")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # toolbar
        self.bar = Toolbar()
        self.scope = (
            button("Uncommitted", kind="ghost", size="sm", on_click=self._pick_scope)
            if self.worktree
            else label("Uncommitted")
        )
        if not self.worktree:
            self.scope.setStyleSheet("font-weight: 600;")
        self.bar.add(self.scope)
        self.stat = QWidget()
        sl = QHBoxLayout(self.stat)
        sl.setContentsMargins(4, 0, 0, 0)
        sl.setSpacing(6)
        self.stat_add = Num("add")
        self.stat_del = Num("del")
        self.stat_files = label("", tone="faint", size="small")
        sl.addWidget(self.stat_add)
        sl.addWidget(self.stat_del)
        sl.addWidget(self.stat_files)
        self.stat.hide()
        self.bar.add(self.stat)
        self.bar.stretch()
        self.pr_btn = button(
            "PR",
            kind="ghost",
            size="sm",
            icon="git-pull-request-arrow",
            on_click=self.ask_pr,
            tooltip="Ask Claude to push this branch and open a pull request",
        )
        self.pr_btn.setText("PR")
        self.pr_btn.hide()
        self.bar.add(self.pr_btn)
        self.review_btn = button(
            "Review",
            kind="ghost",
            size="sm",
            icon="eye",
            on_click=self.review,
            tooltip="Ask Claude to review these changes",
        )
        self.review_btn.setText("Review")
        self.review_btn.hide()
        self.bar.add(self.review_btn)
        self.commit_btn = button(
            "Commit",
            size="sm",
            icon="git-commit-horizontal",
            on_click=lambda: self.set_commit_open(not self._commit_open),
        )
        self.commit_btn.setText("Commit")
        self.commit_btn.setAccessibleName("Commit")
        self.commit_btn.hide()
        self.bar.add(self.commit_btn)
        self.seg_unified = IconButton("rows-2", "Unified", 13)
        self.seg_unified.clicked.connect(lambda: store.set_ui(diff_mode="unified"))
        self.seg_split = IconButton("columns-2", "Side by side", 13)
        self.seg_split.clicked.connect(lambda: store.set_ui(diff_mode="split"))
        self.bar.add(self.seg_unified)
        self.bar.add(self.seg_split)
        self.refresh_btn = IconButton("refresh-cw", "Refresh", 13)
        self.refresh_btn.clicked.connect(self.load)
        self.spinner = Spinner(13)
        self.spinner.hide()
        self.bar.add(self.spinner)
        self.bar.add(self.refresh_btn)
        lay.addWidget(self.bar)

        self.commit_box = CommitBox(self)
        self.commit_box.hide()
        lay.addWidget(self.commit_box)

        # the files
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        host = QWidget()
        self._host_lay = QVBoxLayout(host)
        self._host_lay.setContentsMargins(0, 0, 0, 24)
        self._host_lay.setSpacing(0)
        self.summary = label(
            "Not a git repository — showing edits made in this session.", tone="faint", size="small", wrap=True
        )
        self.summary.setContentsMargins(14, 10, 14, 10)
        self.summary.hide()
        self._host_lay.addWidget(self.summary)
        self.view = DiffView(mode=self._view_mode(), wrap=self._narrow, can_comment=True, max_rows=600)
        self.view.set_actions(
            [
                ("file-text", lambda _f: "Open file", self.open_file),
                ("undo-2", lambda f: "Delete new file" if f.get("untracked") else "Discard changes", self.discard),
            ]
        )
        self.view.notes_changed.connect(self._on_notes)
        self._host_lay.addWidget(self.view)
        self.skeleton = _skeleton()
        self._host_lay.addWidget(self.skeleton)
        self.empty = EmptyState(
            "circle-check",
            "Working tree is clean.",
            "Changes Claude makes will show up here for review. Hover a line and click the note icon to leave feedback.",
        )
        self.empty.hide()
        self._host_lay.addWidget(self.empty)
        self.empty_edits = label("No file edits in this session yet.", tone="faint")
        self.empty_edits.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_edits.setContentsMargins(20, 44, 20, 44)
        self.empty_edits.hide()
        self._host_lay.addWidget(self.empty_edits)
        self._host_lay.addStretch(1)
        self.scroll.setWidget(host)
        lay.addWidget(self.scroll, 1)

        # notes bar
        self.notes_bar = QWidget()
        self.notes_bar.setObjectName("notesBar")
        self.notes_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        nl = QHBoxLayout(self.notes_bar)
        nl.setContentsMargins(12, 10, 12, 10)
        nl.setSpacing(8)
        self.notes_ico = QLabel()
        nl.addWidget(self.notes_ico)
        self.notes_text = label("", size="small")
        nl.addWidget(self.notes_text, 1)
        self.add_notes_btn = button("Add to message", kind="ghost", size="sm", on_click=self.add_notes_to_composer)
        nl.addWidget(self.add_notes_btn)
        self.send_notes_btn = button("Send to Claude", kind="primary", size="sm", icon="send", on_click=self.send_notes)
        self.send_notes_btn.setText("Send to Claude")
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
        t = theme.tokens()
        self.notes_bar.setStyleSheet(
            f"#notesBar {{ background: {t.raised}; border-top: 1px solid {theme.blend(t.hair_2, t.raised).name()}; }}"
        )
        self.notes_ico.setPixmap(icons.pixmap("message-square-text", t.text, 14))

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
        self.seg_unified.set_active(mode == "unified")
        self.seg_split.set_active(mode == "split")
        self.seg_split.setEnabled(not self._narrow)
        self.seg_split.setToolTip("Side by side (widen the panel to use it)" if self._narrow else "Side by side")

    def showEvent(self, e) -> None:  # noqa: N802
        # The web remounts this tab each time it is shown; here it stays built and refreshes instead.
        if self.data is not None and time.monotonic() - self._last_load > 2.5:
            self.load()
        super().showEvent(e)

    def resizeEvent(self, e) -> None:  # noqa: N802
        narrow = self.width() < NARROW_W
        if narrow != self._narrow:
            self._narrow = narrow
            self._apply_mode()
        super().resizeEvent(e)

    def _on_rt(self) -> None:
        rt = store.rt_of(self.sid)
        status = rt.status if rt else ""
        if status == "idle" and self._last_status != "idle":
            self.load()
        elif status in ("running", "requires_action") and time.monotonic() - self._last_load > 2.5:
            QTimer.singleShot(1200, self.load)
        self._last_status = status
        if self.data is not None and not self.data.get("isRepo"):
            self._render_session_edits()

    # ---- loading
    def load(self) -> None:
        self.loading = True
        self._last_load = time.monotonic()
        self.spinner.show()
        self.refresh_btn.hide()
        params: Json = {"cwd": self.cwd}
        if self.base != "HEAD":
            params["base"] = self.base

        def ok(r: Any) -> None:
            if isinstance(r, list):
                r = {"isRepo": True, "files": r}
            self.set_data(r or {"isRepo": True, "files": []})

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
        self.summary.hide()
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
        """Not a repository: the edits this session's Edit/Write tools made, from the transcript."""
        self.summary.show()
        self.stat.hide()
        self.pr_btn.hide()
        self.review_btn.hide()
        self.commit_btn.hide()
        self.empty.hide()
        m = store.models.get(self.sid)
        edits: List[Json] = []
        for t in m.tools.values() if m else []:
            if t.name not in ("Edit", "Write", "MultiEdit") or t.status != "done":
                continue
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
            add = sum(1 for h in hunks for ln in h["lines"] if ln["t"] == "+")
            dele = sum(1 for h in hunks for ln in h["lines"] if ln["t"] == "-")
            shown = dirname(path).replace(self.cwd, ".") + "/" + basename(path) if path else t.id
            edits.append(
                {
                    "path": shown,
                    "oldPath": None,
                    "status": "M",
                    "binary": False,
                    "additions": add,
                    "deletions": dele,
                    "hunks": hunks,
                    "truncated": False,
                    "_abs": path,
                }
            )
        self.view.set_files(edits)
        self.view.set_wrap(True)
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
        self.commit_btn.setProperty("active", "true" if open_ else "false")
        self.commit_btn.style().unpolish(self.commit_btn)
        self.commit_btn.style().polish(self.commit_btn)
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
