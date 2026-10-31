"""The target picker: the repositories under a folder (the server's ``workspace.scan``) as rows on the
canvas engine, so a workspace of hundreds is one scrolling surface that paints only what shows. Each
row: a check, the name, the branch, what the scan knows (dirty, depends on, kind) and the plan's verdict
once there is one; a filter by name, path or kind; a note per repository (the pencil opens it in a
popover) that reaches the brief as ``{{notes}}``."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ... import theme
from ...canvas import nodes as nd
from ...canvas.blocks.base import NodeBlock
from ...canvas.blocks.fold import reconcile
from ...canvas.paint import Measurer, PaintState, rounded
from ...canvas.scene import Block, Scene, Target
from ...canvas.view import CanvasView
from ...store import store
from ..dialogs.parts import Text, hint
from ..dialogs.schedule import FolderButton
from ..dialogs.settings_ui import line_edit
from ..ui.bits import Box, IconButton, Spinner, button
from ..ui.menu import Popover, host_for
from ..ui.signals import bind

Json = Dict[str, Any]
ROW_H = 30
VERDICT_TONE = {"ok": "ok", "warning": "accent", "blocked": "err"}


def dep_names(repo: Json) -> List[str]:
    return [str(d).rstrip("/").split("/")[-1] for d in (repo.get("deps") or [])]


class TargetBlock(NodeBlock):
    """One repository (a row): the check, the name, the branch, the chips and the notes pencil."""

    KIND = "target"
    MT = 0
    MB = 0

    def __init__(self, repo: Json, picker: TargetPicker) -> None:
        self.repo = repo
        self.picker = picker
        self.path = str(repo.get("path") or "")
        self.checked = False
        self.notes = ""
        self.verdict: Optional[Json] = None
        super().__init__(f"tgt:{self.path}", self._build, (0, 0, 0, 0), est_h=ROW_H)
        self.on("row:toggle", lambda _t, _v: self.picker.toggle(self.path))
        self.on("notes", lambda _t, view: self.picker.edit_notes(self.path, self, view))

    def sync(self, repo: Json, checked: bool, notes: str, verdict: Optional[Json]) -> bool:
        changed = (repo, checked, notes, verdict) != (self.repo, self.checked, self.notes, self.verdict)
        self.repo, self.checked, self.notes, self.verdict = repo, checked, notes, verdict
        if changed:
            self.rebuild()
        return changed

    def chips(self) -> List[tuple]:
        """(label, tone) of every chip the row shows: the plan's verdict when there is one, else the
        scan's facts."""
        r = self.repo
        out: List[tuple] = []
        v = self.verdict
        if v is not None and self.checked:
            word = str(v.get("verdict") or "ok")
            notes = [n for n in (v.get("notes") or []) if isinstance(n, dict)]
            first = next((n for n in notes if n.get("kind") == word), None)
            label = word if (first is None or word == "ok") else f"{word} · {str(first.get('text')).split('.')[0][:40]}"
            out.append((label, VERDICT_TONE.get(word, "")))
            if int(v.get("level") or 0) > 0:
                out.append((f"after {', '.join(dep_names(r))}", ""))
            return out
        if r.get("dirty"):
            out.append((f"{int(r.get('changed') or 0) + int(r.get('untracked') or 0)} uncommitted", "accent"))
        if r.get("deps"):
            out.append((f"depends on {', '.join(dep_names(r))}", ""))
        if r.get("kind"):
            out.append((str(r.get("kind")), ""))
        if self.notes:
            out.append(("notes", "info"))
        return out

    def _build(self, m: Measurer) -> nd.Node:
        r = self.repo
        parts: List[nd.Node] = [
            nd.Painted(self._paint_check, 22, 22, "", "mark"),
            nd.Text(str(r.get("name") or ""), theme.FS_UI, 500, "text", wrap=False),
            nd.Text(str(r.get("branch") or "—"), theme.FS_MICRO, 400, "text_3", mono=True, wrap=False),
        ]
        chips = [nd.Badge(label, tone) for label, tone in self.chips()]
        parts.append(nd.hstack(*chips, spacing=4))
        parts.append(nd.Button("", "notes", icon="pencil", kind="ghost", size="xs", icon_px=13))
        return nd.Row(parts, 8, (6, 0, 4, 0), [2], "center", ROW_H)

    def _paint_check(self, p: QPainter, r: QRect, st: PaintState) -> None:
        t = st.t
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(r.x() + 3.5, r.y() + 3.5, 14, 14)
        on = self.checked
        p.setPen(QPen(QColor(t.accent if on else t.text_4), 1))
        p.setBrush(QColor(t.accent) if on else theme.qcolor(t.inset))
        p.drawRoundedRect(box, theme.R1, theme.R1)
        if on:
            p.drawPixmap(int(box.x() + 2), int(box.y() + 2), st.m.icon("check", t.accent_ink, 11))

    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        if st.hover is not None:
            rounded(p, QRect(x, y, w, self.h), theme.blend(st.t.fill_1, st.t.canvas), None, theme.R2)
        if self.root is not None:
            for node in self.root.children:
                if isinstance(node, nd.Button) and node.tid == "notes":
                    node.hidden = st.hover is None and not self.notes
        super().paint(p, x, y, w, st)

    def hit(self, x: int, y: int) -> Optional[Target]:
        t = super().hit(x, y)
        if t is not None and t.kind == "button":
            return t
        return Target("row", "toggle", (0, 0, self.h_width or 0, self.h))

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor

    def text(self) -> str:
        return f"{self.repo.get('name')}{' (selected)' if self.checked else ''}"

    @property
    def static(self) -> bool:
        return True


class TargetList(CanvasView):
    """The rows; a change of selection or verdict repaints one row."""

    def __init__(self, picker: TargetPicker, parent: Optional[QWidget] = None) -> None:
        super().__init__(Scene(), parent, column="fill")
        self.picker = picker
        self.gutter = 4
        self.pad_top, self.pad_bottom = 4, 4
        self.follow.following = False
        self.blocks: Dict[str, TargetBlock] = {}
        self.setAccessibleName("Repositories")

    def sync(self, repos: List[Json]) -> None:
        pk = self.picker
        keys = [f"tgt:{r.get('path')}" for r in repos]
        by_key = {f"tgt:{r.get('path')}": r for r in repos}

        def state(r: Json) -> tuple:
            path = str(r.get("path") or "")
            return (pk.is_checked(path), pk.notes_of(path), pk.verdict_of(path))

        def make(i: int) -> Block:
            r = by_key[keys[i]]
            b = TargetBlock(r, pk)
            b.sync(r, *state(r))
            return b

        def update(b: Block, i: int) -> List[Block]:
            if isinstance(b, TargetBlock):
                r = by_key[keys[i]]
                return [b] if b.sync(r, *state(r)) else []
            return []

        self.begin()
        try:
            for b in reconcile(self.scene.root, keys, make, update):
                if b.warm:
                    self._exact(b)
                else:
                    self.estimate(b)
        finally:
            self.end()
        self.blocks = {b.path: b for b in self.scene.root.children if isinstance(b, TargetBlock)}
        self.setFixedHeight(max(96, min(300, len(repos) * ROW_H + self.pad_top + self.pad_bottom + 2)))


class TargetPicker(QWidget):
    """The workspace: a folder, its repositories, a filter, the selection, the notes, and the plan's
    verdicts painted on the rows."""

    changed = Signal()

    def __init__(self, root: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.repos: Optional[List[Json]] = None
        self._checked: Dict[str, bool] = {}
        self._notes: Dict[str, str] = {}
        self._verdicts: Dict[str, Json] = {}
        self._filter = ""
        self._pop: Optional[Popover] = None
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
        bl.setContentsMargins(4, 4, 4, 4)
        bl.setSpacing(0)
        self.list = TargetList(self)
        bl.addWidget(self.list)
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

    def shown(self) -> List[Json]:
        q = self._filter.lower()
        out = []
        for r in self.repos or []:
            hay = f"{r.get('name')} {r.get('path')} {r.get('kind') or ''} {r.get('packageName') or ''}".lower()
            if not q or q in hay:
                out.append(r)
        return out

    def _rebuild(self) -> None:
        self.list.sync(self.shown())
        self.empty.setVisible(self.repos is not None and not self.repos)
        self.list.setVisible(bool(self.repos))
        self._count()
        self.changed.emit()

    def _filtered(self, text: str) -> None:
        self._filter = text.strip()
        self._rebuild()

    def _count(self) -> None:
        n = sum(1 for v in self._checked.values() if v)
        total = len(self.repos or [])
        self.count.setText(f"{n} of {total} selected" if total else "")

    # ---- what a row asks
    def is_checked(self, path: str) -> bool:
        return bool(self._checked.get(path))

    def notes_of(self, path: str) -> str:
        return self._notes.get(path, "")

    def verdict_of(self, path: str) -> Optional[Json]:
        return self._verdicts.get(path)

    def chips_of(self, path: str) -> List[str]:
        b = self.list.blocks.get(path)
        return [label for label, _tone in b.chips()] if b is not None else []

    def toggle(self, path: str) -> None:
        self._checked[path] = not self._checked.get(path)
        self.list.sync(self.shown())
        self._count()
        self.changed.emit()

    def edit_notes(self, path: str, block: TargetBlock, view: Any) -> None:
        """The repository's notes in a popover under its row; they reach the brief as {{notes}}."""
        if self._pop is not None and self._pop.is_open:
            self._pop.dismiss()
        host = host_for(self)
        if host is None:
            return
        pop = Popover(host, 360)
        pop.lay.setContentsMargins(8, 8, 8, 8)
        edit = line_edit("Notes for this repository, appended to its brief", name=f"Notes for {block.repo.get('name')}")
        edit.setText(self._notes.get(path, ""))

        def typed(text: str) -> None:
            self._notes[path] = text
            self.list.sync(self.shown())
            self.changed.emit()

        edit.textChanged.connect(typed)
        edit.returnPressed.connect(pop.dismiss)
        pop.set_content(edit)
        self._pop = pop
        anchor = QWidget(self.list.viewport())
        anchor.setGeometry(self.list.block_rect(block))
        anchor.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        anchor.show()
        pop.closed.connect(anchor.deleteLater)
        pop.open_at(anchor, "bottom-start")
        edit.setFocus()

    # ---- the selection as a whole
    def select_all(self, on: bool) -> None:
        for r in self.shown():
            self._checked[str(r.get("path"))] = on
        self._rebuild()

    def set_selected(self, paths: List[str]) -> None:
        want = set(paths)
        for path in list(self._checked):
            self._checked[path] = path in want
        for p in want:
            self._checked[p] = True
        self._rebuild()

    def set_targets(self, root: str, targets: List[Json]) -> None:
        """A playbook's repositories: the folder (scanned again when it differs), the selection, the notes."""
        self._notes = {str(t.get("path")): str(t.get("notes") or "") for t in targets if t.get("notes")}
        paths = [str(t.get("path")) for t in targets if t.get("path")]
        if root and root != self.root():
            self._checked = {p: True for p in paths}
            self.folder.setText(root)  # scans; the rows take the selection and the notes when they are built
            return
        self.set_selected(paths)

    def targets(self) -> List[Json]:
        """The selected repositories as the job's targets (filtered-out rows keep their selection)."""
        out: List[Json] = []
        by_path = {str(r.get("path")): r for r in self.repos or []}
        for path, on in self._checked.items():
            r = by_path.get(path)
            if not on or r is None:
                continue
            out.append(
                {
                    "path": path,
                    "name": r.get("name"),
                    "base": r.get("branch") or "",
                    "notes": self._notes.get(path, ""),
                    "size": r.get("size"),
                    "deps": list(r.get("deps") or []),
                }
            )
        out.sort(key=lambda t: str(t.get("name") or ""))
        return out

    def apply_plan(self, plan: Optional[Json]) -> None:
        self._verdicts = {str(t.get("path")): t for t in ((plan or {}).get("targets") or []) if isinstance(t, dict)}
        self.list.sync(self.shown())
