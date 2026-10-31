"""The target picker: the repositories under a folder (the server's ``workspace.scan``), each with a
check, its branch, what the scan knows (dirty, depends on, no CLAUDE.md) and the plan's verdict once
there is one; a filter by name, path or kind; a note per repository that reaches the brief as
``{{notes}}``."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from ... import icons, theme
from ...lib.fmt import plural
from ...store import store
from ..dialogs.parts import ElideLabel, Text, hint
from ..dialogs.schedule import FolderButton
from ..dialogs.settings_ui import line_edit
from ..ui.bits import Box, Check, IconButton, Pill, Spinner, button
from ..ui.signals import bind

Json = Dict[str, Any]
VERDICT_TONE = {"ok": ("ok", "ok_wash"), "warning": ("warn", "warn_wash"), "blocked": ("err", "err_wash")}


def chip(text: str, fg: str, bg: str) -> Pill:
    c = Pill(text, bg=f"{bg}/canvas", radius=9)
    c.setContentsMargins(7, 1, 7, 1)
    c.setProperty("fs", "micro")
    theme.tint(c, fg=fg)
    return c


class TargetRow(QWidget):
    """One repository: check, name, branch, chips, the notes pencil; the note field opens under it."""

    changed = Signal()

    def __init__(self, repo: Json, checked: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.repo = repo
        self.notes = ""
        self.verdict: Optional[Json] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        row = QHBoxLayout()
        row.setContentsMargins(6, 0, 4, 0)
        row.setSpacing(8)
        self.check = Check()
        self.check.setChecked(checked)
        self.check.setAccessibleName(f"Include {repo.get('name')}")
        self.check.toggled.connect(lambda _c: self.changed.emit())
        row.addWidget(self.check)
        self.name = Text(str(repo.get("name") or ""), theme.FS_UI, 500, "text", wrap=False)
        self.name.setToolTip(str(repo.get("path") or ""))
        row.addWidget(self.name)
        self.branch = Text(str(repo.get("branch") or "—"), theme.FS_MICRO, 400, "text_3", wrap=False, mono=True)
        row.addWidget(self.branch)
        row.addStretch(1)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(4)
        row.addLayout(self.chips)
        self.pen = IconButton("pencil", "Notes for this repository (reach the brief as {{notes}})", 13)
        self.pen.setFixedSize(24, 24)
        self.pen.clicked.connect(self.toggle_notes)
        row.addWidget(self.pen)
        lay.addLayout(row)
        self.note_edit = line_edit(
            "Notes for this repository, appended to its brief", name=f"Notes for {repo.get('name')}"
        )
        self.note_edit.setFixedHeight(30)
        self.note_edit.textChanged.connect(self._noted)
        self.note_edit.hide()
        wrap = QWidget()
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(29, 0, 4, 4)
        wl.addWidget(self.note_edit)
        lay.addWidget(wrap)
        self.setFixedHeight(30)
        self._wrap = wrap
        self.set_verdict(None)

    def toggle_notes(self) -> None:
        on = self.note_edit.isHidden()
        self.note_edit.setVisible(on)
        self.setFixedHeight(30 + (34 if on else 0))
        if on:
            self.note_edit.setFocus()

    def _noted(self, text: str) -> None:
        self.notes = text
        self.changed.emit()

    @property
    def selected(self) -> bool:
        return self.check.isChecked()

    def target(self) -> Json:
        r = self.repo
        return {
            "path": r.get("path"),
            "name": r.get("name"),
            "base": r.get("branch") or "",
            "notes": self.notes,
            "size": r.get("size"),
            "deps": list(r.get("deps") or []),
        }

    def set_verdict(self, v: Optional[Json]) -> None:
        """The plan's verdict for this target (or the scan's own facts when there is no plan yet)."""
        self.verdict = v
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        r = self.repo
        if v is not None and self.selected:
            fg, bg = VERDICT_TONE.get(str(v.get("verdict")), ("text_3", "fill_1"))
            notes = [n for n in (v.get("notes") or []) if isinstance(n, dict)]
            first = next((n for n in notes if n.get("kind") == v.get("verdict")), None)
            word = {"ok": "ok", "warning": "warning", "blocked": "blocked"}.get(str(v.get("verdict")), "")
            c = chip(word, fg, bg)
            c.setToolTip("\n".join(str(n.get("text")) for n in notes))
            if first is not None and v.get("verdict") != "ok":
                c.setText(f"{word} · {str(first.get('text')).split('.')[0][:48]}")
            self.chips.addWidget(c)
            if int(v.get("level") or 0) > 0:
                self.chips.addWidget(chip(f"after {', '.join(self._dep_names())}", "text_2", "fill_1"))
            return
        if r.get("dirty"):
            n = int(r.get("changed") or 0) + int(r.get("untracked") or 0)
            self.chips.addWidget(chip(f"{n} uncommitted", "warn", "warn_wash"))
        if r.get("deps"):
            self.chips.addWidget(chip(f"depends on {', '.join(self._dep_names())}", "text_2", "fill_1"))
        if r.get("kind"):
            self.chips.addWidget(chip(str(r.get("kind")), "text_3", "fill_1"))

    def _dep_names(self) -> List[str]:
        return [str(d).rstrip("/").split("/")[-1] for d in (self.repo.get("deps") or [])]


class TargetPicker(QWidget):
    """The workspace: a folder, its repositories, a filter, the selection, and the plan's verdicts
    painted on the rows."""

    changed = Signal()

    def __init__(self, root: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.repos: Optional[List[Json]] = None
        self.rows: Dict[str, TargetRow] = {}
        self._checked: Dict[str, bool] = {}
        self._filter = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.folder = FolderButton(root)
        self.folder.setMaximumWidth(260)
        self.folder.textChanged.connect(lambda _v: self.scan(True))
        top.addWidget(self.folder)
        self.rescan = IconButton("refresh-cw", "Scan the folder again", 14)
        self.rescan.clicked.connect(lambda: self.scan(True))
        top.addWidget(self.rescan)
        self.filter = line_edit("Filter by name, path or kind", name="Filter repositories")
        self.filter.textChanged.connect(self._filtered)
        top.addWidget(self.filter, 1)
        self.count = Text("", theme.FS_SMALL, 400, "text_3", wrap=False)
        top.addWidget(self.count)
        self.all_btn = button("All", kind="ghost", size="xs", on_click=lambda: self.select_all(True))
        self.none_btn = button("None", kind="ghost", size="xs", on_click=lambda: self.select_all(False))
        top.addWidget(self.all_btn)
        top.addWidget(self.none_btn)
        lay.addLayout(top)
        self.box = Box(bg="inset/canvas", line="hair/canvas", radius=theme.R3)
        bl = QVBoxLayout(self.box)
        bl.setContentsMargins(6, 6, 6, 6)
        bl.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumHeight(120)
        self.scroll.setMaximumHeight(300)
        self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.list = QWidget()
        self.list_lay = QVBoxLayout(self.list)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(0)
        self.list_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.list)
        bl.addWidget(self.scroll)
        self.spinner = Spinner(14)
        bl.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty = hint("No repositories under this folder. Pick another, or deepen the scan.")
        self.empty.hide()
        bl.addWidget(self.empty)
        lay.addWidget(self.box)
        bind(store.connected_changed, self, lambda on: on and self.repos is None and self.scan())
        self.scan()

    # ---- data
    def root(self) -> str:
        return self.folder.text()

    def scan(self, force: bool = False) -> None:
        root = self.root()
        if not root or not store.connected:
            return
        self.spinner.show()
        self.empty.hide()

        def got(r: Any) -> None:
            if not isinstance(r, dict) or self.root() != root:
                return
            self.repos = [x for x in (r.get("repos") or []) if isinstance(x, dict)]
            self.spinner.hide()
            self._rebuild()

        def bad(_e: Any) -> None:
            self.repos = []
            self.spinner.hide()
            self._rebuild()

        store.call("workspace.scan", {"root": root, "force": force}, quiet=True).then(got).catch(bad)

    def _rebuild(self) -> None:
        for row in self.rows.values():
            self._checked[str(row.repo.get("path"))] = row.selected
        while self.list_lay.count():
            w = self.list_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.rows = {}
        q = self._filter.lower()
        shown = 0
        for r in self.repos or []:
            path = str(r.get("path") or "")
            hay = f"{r.get('name')} {path} {r.get('kind') or ''} {r.get('packageName') or ''}".lower()
            if q and q not in hay:
                continue
            row = TargetRow(r, self._checked.get(path, False))
            row.changed.connect(self._row_changed)
            self.rows[path] = row
            self.list_lay.addWidget(row)
            shown += 1
        self.empty.setVisible(self.repos is not None and not self.repos)
        self._count()
        self.changed.emit()

    def _filtered(self, text: str) -> None:
        self._filter = text.strip()
        self._rebuild()

    def _row_changed(self) -> None:
        for row in self.rows.values():
            self._checked[str(row.repo.get("path"))] = row.selected
        self._count()
        self.changed.emit()

    def _count(self) -> None:
        n = sum(1 for v in self._checked.values() if v)
        total = len(self.repos or [])
        self.count.setText(f"{n} of {total} selected" if total else "")

    def select_all(self, on: bool) -> None:
        for row in self.rows.values():
            row.check.setChecked(on)

    def set_selected(self, paths: List[str]) -> None:
        want = set(paths)
        for path in list(self._checked):
            self._checked[path] = path in want
        for row in self.rows.values():
            row.check.setChecked(str(row.repo.get("path")) in want)

    def targets(self) -> List[Json]:
        """The selected repositories as the job's targets (filtered-out rows keep their selection)."""
        out: List[Json] = []
        by_path = {str(r.get("path")): r for r in self.repos or []}
        for path, on in self._checked.items():
            if not on or path not in by_path:
                continue
            row = self.rows.get(path)
            if row is not None:
                out.append(row.target())
            else:
                r = by_path[path]
                out.append(
                    {
                        "path": path,
                        "name": r.get("name"),
                        "base": r.get("branch") or "",
                        "notes": "",
                        "size": r.get("size"),
                        "deps": list(r.get("deps") or []),
                    }
                )
        out.sort(key=lambda t: str(t.get("name") or ""))
        return out

    def apply_plan(self, plan: Optional[Json]) -> None:
        by_path = {str(t.get("path")): t for t in ((plan or {}).get("targets") or []) if isinstance(t, dict)}
        for path, row in self.rows.items():
            row.set_verdict(by_path.get(path))


_ = (QLineEdit, QSize, ElideLabel, icons, plural, Callable)
