"""Settings → Repositories (REPOSITORIES.md, web/src/components/settings/ReposPage.tsx): the list the
person maintains of the repositories Pilcrow may work in — a name, their own aliases, one line about
it, the policy for starting helpers and Dispatch runs there, defaults, standing notes — one row each,
edited in place. Facts are scanned, never typed."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QWidget

from ... import icons, theme
from ...lib.fmt import basename, err_text, tildify
from ...store import ConfirmSpec, store
from ..folder_picker import FolderPicker
from ..ui.bits import Box, Check, IconButton, button, label
from ..ui.signals import bind
from .parts import ElideLabel, Text
from .settings_ui import (
    Flow,
    Panel,
    Seg,
    Sheet,
    Switch,
    chip,
    clear_layout,
    combo,
    combo_value,
    hint,
    home,
    line_edit,
    select_value,
    styled,
    text_area,
    vbox,
)

Json = Dict[str, Any]

POLICIES = [("allowed", "Allowed"), ("ask", "Ask"), ("never", "Never")]
POLICY_HINT = {
    "allowed": "Helpers and Dispatch runs start here without asking",
    "ask": "Each start shows a permission card",
    "never": "Nothing starts here from a lead or a job",
}
MODES = [("", "Session default"), ("default", "Default"), ("acceptEdits", "Accept edits"), ("plan", "Plan")]
FILTER_FROM = 10


def _save(eid: str, patch: Json, then: Optional[Any] = None) -> None:
    """One field on one entry; the server answers with the entry and every client hears repos.changed."""

    def ok(e: Json) -> None:
        if then:
            then(e)

    store.call("repos.save", {"id": eid, **patch}).then(ok).catch(lambda e: store.toast(err_text(e), "error"))


def _facts_text(e: Json) -> str:
    f = e.get("facts") or {}
    bits: List[str] = []
    if f.get("manifest"):
        bits.append(str(f["manifest"]))
    if f.get("packageName"):
        bits.append(str(f["packageName"]))
    if f.get("branch"):
        bits.append(str(f["branch"]))
    if f.get("dirty"):
        bits.append("uncommitted changes")
    if f.get("hasClaudeMd"):
        bits.append("CLAUDE.md")
    return " · ".join(bits) if bits else ("not a git repository" if f.get("isRepo") is False else "not scanned yet")


class ReposPage(QWidget):
    def __init__(self, sheet: Sheet, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sheet = sheet
        self.rows: Dict[str, RepoRow] = {}
        self._query = ""
        self.scan_found: List[Json] = []
        self.lay = vbox(self, 14, (0, 6, 0, 0))

        intro = Box(bg="inset", radius=12)
        intro.setObjectName("intro")
        il = vbox(intro, 8, (20, 18, 20, 18))
        ico = label()
        ico.setFixedSize(16, 16)
        ico.setPixmap(icons.pixmap("folder-git-2", theme.tokens().text, 16))
        il.addWidget(ico)
        col = QWidget()
        cl = vbox(col, 0)
        cl.addWidget(Text("Repositories", theme.FS_BODY, 600))
        cl.addWidget(
            Text(
                "The repositories Pilcrow may work in, by the names you use for them. A lead or a job sent to “the order FE” lands in the right folder; the policy says whether it asks first; the notes travel with every brief for that repository.",
                theme.FS_SMALL,
                400,
                "text_3",
                1.5,
            )
        )
        il.addWidget(col)
        self.lay.addWidget(intro)

        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        self.filter = line_edit("Filter by name, alias or folder", name="Filter repositories")
        self.filter.setMaximumWidth(260)
        self.filter.textChanged.connect(self._filtered)
        self.filter.hide()
        bl.addWidget(self.filter)
        self.count = Text("", theme.FS_SMALL, 400, "text_3", wrap=False)
        bl.addWidget(self.count)
        bl.addStretch(1)
        self.scan_btn = button("Scan a folder…", kind="ghost", size="sm", icon="search", on_click=self.start_scan)
        self.scan_btn.setAccessibleName("Scan a folder")
        bl.addWidget(self.scan_btn)
        self.add_btn = button("Add…", kind="primary", size="sm", icon="plus", on_click=self.start_add)
        self.add_btn.setAccessibleName("Add a repository")
        bl.addWidget(self.add_btn)
        self.lay.addWidget(bar)

        # ---- add: the in-app folder chooser, then the entry is made with the folder's name
        self.add_box = Panel(margins=(12, 10, 12, 10), border=True)
        self.add_box.lay.setSpacing(8)
        self.add_box.lay.addWidget(Text("Pick the repository's folder", theme.FS_SMALL, 600))
        self.add_picker = FolderPicker(home(), self._add_path)
        self.add_box.lay.addWidget(self.add_picker)
        ar = QWidget()
        arl = QHBoxLayout(ar)
        arl.setContentsMargins(0, 0, 0, 0)
        arl.addStretch(1)
        arl.addWidget(button("Cancel", size="sm", on_click=lambda: self._show_box(None)))
        self.add_box.lay.addWidget(ar)
        self.add_box.hide()
        self.lay.addWidget(self.add_box)

        # ---- scan: a root, then the repositories under it with checks
        self.scan_box = Panel(margins=(12, 10, 12, 10), border=True)
        self.scan_box.lay.setSpacing(8)
        self.scan_box.lay.addWidget(Text("Pick a folder to scan for repositories", theme.FS_SMALL, 600))
        self.scan_picker = FolderPicker(home(), self.scan_root)
        self.scan_box.lay.addWidget(self.scan_picker)
        self.scan_list = QWidget()
        self.scan_lay = vbox(self.scan_list, 2)
        self.scan_list.hide()
        self.scan_box.lay.addWidget(self.scan_list)
        sr = QWidget()
        srl = QHBoxLayout(sr)
        srl.setContentsMargins(0, 0, 0, 0)
        srl.setSpacing(8)
        self.scan_note = hint("")
        srl.addWidget(self.scan_note, 1)
        srl.addWidget(button("Cancel", size="sm", on_click=lambda: self._show_box(None)))
        self.register_btn = button("Register", kind="primary", size="sm", on_click=self.register_checked)
        self.register_btn.setAccessibleName("Register the checked repositories")
        self.register_btn.setEnabled(False)
        srl.addWidget(self.register_btn)
        self.scan_box.lay.addWidget(sr)
        self.scan_box.hide()
        self.lay.addWidget(self.scan_box)

        self.list_w = QWidget()
        self.list_lay = vbox(self.list_w, 8)
        self.lay.addWidget(self.list_w)
        self.empty = QWidget()
        el = vbox(self.empty, 8, (20, 36, 20, 36))
        eico = label()
        eico.setPixmap(icons.pixmap("folder-git-2", theme.tokens().text_3, 22))
        el.addWidget(eico, 0, Qt.AlignmentFlag.AlignHCenter)
        t = label("No repositories registered yet.", tone="faint")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.addWidget(t)
        h = hint(
            "Add one, or scan a folder that holds several. A session's menu also offers “Register this repository…”."
        )
        h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        measure = QWidget()
        measure.setFixedWidth(460)
        vbox(measure).addWidget(h)
        el.addWidget(measure, 0, Qt.AlignmentFlag.AlignHCenter)
        self.lay.addWidget(self.empty)

        bind(store.repos_changed, self, lambda _r: self.sync())
        bind(store.config_changed, self, lambda _c: self.sync())
        self.sync()
        store.call("repos.refresh", {}, quiet=True).catch(lambda _e: None)

    # ---- the list
    def entries(self) -> List[Json]:
        q = self._query.lower()
        out = []
        for e in store.repos:
            hay = f"{e.get('name')} {' '.join(e.get('aliases') or [])} {e.get('path')} {e.get('about') or ''}".lower()
            if not q or q in hay:
                out.append(e)
        return out

    def sync(self) -> None:
        all_ = store.repos
        shown = self.entries()
        self.filter.setVisible(len(all_) >= FILTER_FROM or bool(self._query))
        self.count.setText(
            f"{len(shown)} of {len(all_)}" if self._query else (f"{len(all_)} registered" if all_ else "")
        )
        wanted = [str(e.get("id")) for e in shown]
        for eid in list(self.rows):
            if eid not in wanted:
                row = self.rows.pop(eid)
                self.list_lay.removeWidget(row)
                row.hide()
                row.setParent(None)
                row.deleteLater()
        for i, e in enumerate(shown):
            eid = str(e.get("id"))
            row = self.rows.get(eid)
            if row is None:
                row = self.rows[eid] = RepoRow(e, self)
            else:
                row.sync(e)
            if self.list_lay.indexOf(row) != i:
                self.list_lay.removeWidget(row)
                self.list_lay.insertWidget(i, row)
        self.empty.setVisible(not all_)
        self.sheet.schedule_relayout()

    def _filtered(self, text: str) -> None:
        self._query = text.strip()
        self.sync()

    # ---- add and scan
    def _show_box(self, which: Optional[QWidget]) -> None:
        for b in (self.add_box, self.scan_box):
            b.setVisible(b is which)
        self.sheet.schedule_relayout()

    def start_add(self) -> None:
        self._show_box(self.add_box)

    def _add_path(self, path: str) -> None:
        def ok(e: Json) -> None:
            self._show_box(None)
            store.toast(f"Registered {e.get('name')}", "success")
            row = self.rows.get(str(e.get("id")))
            if row is not None:
                row.set_open(True)

        store.call("repos.save", {"path": path}).then(ok).catch(lambda e: store.toast(err_text(e), "error"))

    def start_scan(self) -> None:
        self.scan_found = []
        self.scan_list.hide()
        self.scan_note.setText("")
        self.register_btn.setEnabled(False)
        self._show_box(self.scan_box)

    def scan_root(self, root: str) -> None:
        self.scan_note.setText(f"Scanning {tildify(root, home())}…")

        def ok(r: Json) -> None:
            self.scan_found = [f for f in (r or {}).get("found") or [] if isinstance(f, dict)]
            self._render_scan()

        store.call("repos.scan", {"root": root}).then(ok).catch(lambda e: store.toast(err_text(e), "error"))

    def _render_scan(self) -> None:
        clear_layout(self.scan_lay)
        self.scan_checks: Dict[str, Check] = {}
        new = [f for f in self.scan_found if not f.get("registered")]
        for f in self.scan_found:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(4, 2, 4, 2)
            rl.setSpacing(8)
            ck = Check(str(f.get("name") or basename(str(f.get("path")))))
            ck.setEnabled(not f.get("registered"))
            ck.setChecked(not f.get("registered"))
            ck.toggled.connect(lambda _v: self._count_checked())
            rl.addWidget(ck)
            self.scan_checks[str(f.get("path"))] = ck
            meta = ElideLabel(
                f"{f.get('packageName') or f.get('kind') or ''} · {f.get('branch') or '—'} · {tildify(str(f.get('path')), home())}".strip(
                    " ·"
                )
            )
            meta.setFont(theme.font_mono(theme.FS_MICRO))
            theme.tint(meta, fg="text_3")
            rl.addWidget(meta, 1)
            if f.get("registered"):
                rl.addWidget(chip("registered", "plan", tiny=True))
            self.scan_lay.addWidget(row)
        self.scan_list.setVisible(bool(self.scan_found))
        self.scan_note.setText(
            f"{len(new)} new repositor{'y' if len(new) == 1 else 'ies'}, {len(self.scan_found) - len(new)} already registered"
            if self.scan_found
            else "No repositories under this folder."
        )
        self._count_checked()
        self.sheet.schedule_relayout()

    def _count_checked(self) -> None:
        n = sum(1 for ck in getattr(self, "scan_checks", {}).values() if ck.isEnabled() and ck.isChecked())
        self.register_btn.setEnabled(n > 0)
        self.register_btn.setText(f"Register {n}" if n else "Register")

    def register_checked(self) -> None:
        paths = [p for p, ck in self.scan_checks.items() if ck.isEnabled() and ck.isChecked()]
        if not paths:
            return
        left = {"n": len(paths), "bad": 0}

        def done() -> None:
            left["n"] -= 1
            if left["n"] == 0:
                self._show_box(None)
                ok_n = len(paths) - left["bad"]
                store.toast(f"Registered {ok_n} repositor{'y' if ok_n == 1 else 'ies'}", "success" if ok_n else "error")

        for p in paths:
            found = next((f for f in self.scan_found if f.get("path") == p), {})
            store.call("repos.save", {"path": p, "name": found.get("name") or ""}).then(lambda _e: done()).catch(
                lambda e: (left.__setitem__("bad", left["bad"] + 1), store.toast(err_text(e), "error"), done())
            )


class RepoRow(Panel):
    """One registered repository: the head (name, folder, policy, chevron, remove), the person's aliases,
    and, open, the rest of the entry."""

    def __init__(self, e: Json, page: ReposPage) -> None:
        super().__init__(margins=(12, 8, 10, 8), border=True)
        self.e = e
        self.page = page
        self.eid = str(e.get("id") or "")
        self.open = False
        self._snapshot = ""
        self.lay.setSpacing(6)

        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)
        self.chev = IconButton("chevron-right", "Show the rest of the entry", 14)
        self.chev.clicked.connect(lambda: self.set_open(not self.open))
        hl.addWidget(self.chev)
        self.name_btn = QPushButton()
        self.name_btn.setAutoDefault(False)
        self.name_btn.setToolTip("Click to rename")
        self.name_btn.setAccessibleName("Rename repository")
        self.name_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        styled(
            self.name_btn,
            lambda t: (
                f"QPushButton {{ border: none; background: transparent; text-align: left; padding: 0; font-size: {theme.FS_UI}px; font-weight: 600; color: {t.text}; }} QPushButton:hover {{ color: {t.text_2}; }}"
            ),
        )
        self.name_btn.clicked.connect(self.start_rename)
        hl.addWidget(self.name_btn)
        self.name_edit = line_edit("Name", name="Repository name")
        self.name_edit.setMaximumWidth(220)
        self.name_edit.hide()
        self.name_edit.installEventFilter(self)
        self.name_edit.editingFinished.connect(self.commit_rename)
        hl.addWidget(self.name_edit)
        self.path_lb = ElideLabel("")
        self.path_lb.setFont(theme.font_mono(theme.FS_MICRO))
        theme.tint(self.path_lb, fg="text_3")
        hl.addWidget(self.path_lb, 1)
        self.missing_chip = chip("not found", "warn", tiny=True)
        self.missing_chip.hide()
        hl.addWidget(self.missing_chip)
        self.locate_btn = button("Locate…", kind="ghost", size="xs", on_click=self.start_locate)
        self.locate_btn.hide()
        hl.addWidget(self.locate_btn)
        self.policy = Seg(POLICIES, str(e.get("policy") or "ask"), small=True, name=f"Policy for {e.get('name')}")
        self.policy.changed.connect(lambda v: _save(self.eid, {"policy": v}))
        hl.addWidget(self.policy)
        self.trash = IconButton("trash", "Remove from the registry", 13)
        self.trash.clicked.connect(self.remove)
        hl.addWidget(self.trash)
        self.lay.addWidget(head)

        arow = QWidget()
        al = QHBoxLayout(arow)
        al.setContentsMargins(26, 0, 0, 0)
        al.setSpacing(6)
        self.aliases = Flow(6)
        al.addWidget(self.aliases, 1)
        self.alias_edit = line_edit("add a name you use for it…", name=f"Add an alias for {e.get('name')}")
        self.alias_edit.setFixedWidth(200)
        self.alias_edit.returnPressed.connect(self.add_alias)
        al.addWidget(self.alias_edit)
        self.lay.addWidget(arow)

        self.locate_box = QWidget()
        lb = vbox(self.locate_box, 6, (26, 4, 0, 0))
        lb.addWidget(Text("Where is it now?", theme.FS_SMALL, 600))
        self.locate_picker: Optional[FolderPicker] = None
        self.locate_box.hide()
        self.lay.addWidget(self.locate_box)

        # ---- the rest, shown when open
        self.detail = QWidget()
        dl = vbox(self.detail, 8, (26, 6, 0, 2))
        self.about = line_edit("One line: what it is, what it talks to", name=f"About {e.get('name')}")
        self.about.editingFinished.connect(lambda: self._changed("about", self.about.text().strip()))
        dl.addWidget(_field("About", self.about, "Read by the lead when it decides where a task belongs."))
        self.mode = combo(
            MODES, str((e.get("defaults") or {}).get("mode") or ""), name=f"Mode for helpers in {e.get('name')}"
        )
        self.mode.currentIndexChanged.connect(lambda _i: self._default("mode", combo_value(self.mode)))
        self.worktree = Switch(
            (e.get("defaults") or {}).get("worktree") is not False, name=f"Worktree for {e.get('name')}"
        )
        self.worktree.toggled.connect(lambda v: self._default("worktree", bool(v)))
        dl.addWidget(
            _field("Mode", self.mode, "How a helper or a run here may act, unless the lead or the job says otherwise.")
        )
        dl.addWidget(
            _field(
                "Worktree", self.worktree, "Work on a branch in a worktree of its own, never in the checkout you use."
            )
        )
        self.base = line_edit("the checkout's branch", name=f"Base branch for {e.get('name')}")
        self.base.editingFinished.connect(lambda: self._default("base", self.base.text().strip()))
        dl.addWidget(_field("Base branch", self.base, "What a worktree starts from."))
        self.prepare = line_edit("npm ci · cp ../.env .", mono=True, name=f"Prepare command for {e.get('name')}")
        self.prepare.editingFinished.connect(lambda: self._default("prepare", self.prepare.text().strip()))
        dl.addWidget(_field("Prepare", self.prepare, "Runs in a fresh worktree before the agent starts."))
        self.verify = line_edit("npm test", mono=True, name=f"Verify command for {e.get('name')}")
        self.verify.editingFinished.connect(lambda: self._default("verify", self.verify.text().strip()))
        dl.addWidget(
            _field("Verify", self.verify, "Runs after the agent finishes; its exit code is part of the result.")
        )
        self.notes = text_area(
            "What every agent sent here should know — “pnpm, not npm”, “the ESLint config lives in config/eslint.cjs”.",
            rows=3,
            name=f"Notes for {e.get('name')}",
        )
        self.notes.installEventFilter(self)
        dl.addWidget(
            _field(
                "Notes", self.notes, "Appended to every helper brief and Dispatch brief for this repository.", top=True
            )
        )
        deps_w = QWidget()
        dpl = QHBoxLayout(deps_w)
        dpl.setContentsMargins(0, 0, 0, 0)
        dpl.setSpacing(6)
        self.deps = Flow(6)
        dpl.addWidget(self.deps, 1)
        self.dep_pick = combo([("", "Add a dependency…")], "", name=f"Add a dependency for {e.get('name')}")
        self.dep_pick.setMinimumWidth(180)
        self.dep_pick.currentIndexChanged.connect(lambda _i: self.add_dep(combo_value(self.dep_pick)))
        dpl.addWidget(self.dep_pick)
        dl.addWidget(
            _field(
                "Depends on", deps_w, "Runs after these in a job; a helper that waits for them is told what they did."
            )
        )
        facts_w = QWidget()
        fl = QHBoxLayout(facts_w)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(6)
        self.facts = ElideLabel("")
        self.facts.setFont(theme.font_mono(theme.FS_MICRO))
        theme.tint(self.facts, fg="text_3")
        fl.addWidget(self.facts, 1)
        refresh = IconButton("refresh-cw", "Read the folder again", 12)
        refresh.clicked.connect(
            lambda: store.call("repos.refresh", {"ids": [self.eid], "force": True}, quiet=True).catch(lambda _e: None)
        )
        fl.addWidget(refresh)
        dl.addWidget(_field("Scanned", facts_w, ""))
        self.detail.hide()
        self.lay.addWidget(self.detail)
        self.sync(e, force=True)

    # ---- data in
    def sync(self, e: Json, force: bool = False) -> None:
        import json

        snap = json.dumps(e, sort_keys=True, default=str)
        if snap == self._snapshot and not force:
            return
        self._snapshot = snap
        self.e = e
        name = str(e.get("name") or "")
        self.name_btn.setText(name)
        self.name_edit.setText(name)
        missing = bool(e.get("missing"))
        self.path_lb.setText(tildify(str(e.get("path") or ""), home()))
        self.path_lb.setToolTip(str(e.get("path") or ""))
        self.missing_chip.setVisible(missing)
        self.locate_btn.setVisible(missing)
        self.name_btn.setEnabled(not missing)
        self.policy.setToolTip(POLICY_HINT.get(str(e.get("policy") or "ask"), ""))
        self.policy.blockSignals(True)
        self.policy.set_value(str(e.get("policy") or "ask"))
        self.policy.blockSignals(False)
        self.aliases.clear()
        for a in e.get("aliases") or []:
            self.aliases.add(_alias_chip(str(a), lambda al=str(a): self.remove_alias(al)))
        self.alias_edit.setAccessibleName(f"Add an alias for {name}")
        d = e.get("defaults") or {}
        if not self.about.hasFocus():
            self.about.setText(str(e.get("about") or ""))
        self.mode.blockSignals(True)
        select_value(self.mode, str(d.get("mode") or ""))
        self.mode.blockSignals(False)
        self.worktree.blockSignals(True)
        self.worktree.setChecked(d.get("worktree") is not False)
        self.worktree.blockSignals(False)
        for w, k in ((self.base, "base"), (self.prepare, "prepare"), (self.verify, "verify")):
            if not w.hasFocus():
                w.setText(str(d.get(k) or ""))
        if not self.notes.hasFocus():
            self.notes.setPlainText(str(e.get("notes") or ""))
        self.deps.clear()
        by_id = {str(x.get("id")): x for x in store.repos}
        for did in e.get("deps") or []:
            other = by_id.get(str(did))
            if other:
                self.deps.add(_alias_chip(str(other.get("name")), lambda d_=str(did): self.remove_dep(d_)))
        self.dep_pick.blockSignals(True)
        self.dep_pick.clear()
        self.dep_pick.addItem("Add a dependency…", "")
        for x in store.repos:
            xid = str(x.get("id"))
            if xid != self.eid and xid not in (e.get("deps") or []):
                self.dep_pick.addItem(str(x.get("name")), xid)
        self.dep_pick.setCurrentIndex(0)
        self.dep_pick.blockSignals(False)
        self.facts.setText(_facts_text(e))
        self.facts.setToolTip(_facts_text(e))

    def set_open(self, on: bool) -> None:
        self.open = on
        self.detail.setVisible(on)
        self.chev.set_icon_name("chevron-down" if on else "chevron-right")
        self.chev.setToolTip("Hide the rest of the entry" if on else "Show the rest of the entry")
        self.page.sheet.schedule_relayout()

    # ---- data out
    def _changed(self, key: str, value: Any) -> None:
        if value != (self.e.get(key) or ""):
            _save(self.eid, {key: value})

    def _default(self, key: str, value: Any) -> None:
        d = dict(self.e.get("defaults") or {})
        if key == "worktree":
            if value:
                d.pop("worktree", None)
            else:
                d["worktree"] = False
        elif value:
            d[key] = value
        else:
            d.pop(key, None)
        if d != (self.e.get("defaults") or {}):
            _save(self.eid, {"defaults": d})

    def add_alias(self) -> None:
        a = self.alias_edit.text().strip()
        if not a:
            return
        self.alias_edit.setText("")
        _save(self.eid, {"aliases": [*(self.e.get("aliases") or []), a]})

    def remove_alias(self, a: str) -> None:
        _save(self.eid, {"aliases": [x for x in (self.e.get("aliases") or []) if x != a]})

    def add_dep(self, did: str) -> None:
        if did:
            _save(self.eid, {"deps": [*(self.e.get("deps") or []), did]})

    def remove_dep(self, did: str) -> None:
        _save(self.eid, {"deps": [x for x in (self.e.get("deps") or []) if x != did]})

    def start_rename(self) -> None:
        self.name_btn.hide()
        self.name_edit.show()
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def commit_rename(self) -> None:
        if not self.name_edit.isVisible():
            return
        text = self.name_edit.text().strip()
        self.name_edit.hide()
        self.name_btn.show()
        if text and text != self.e.get("name"):
            _save(self.eid, {"name": text})
        else:
            self.name_edit.setText(str(self.e.get("name") or ""))

    def start_locate(self) -> None:
        if self.locate_picker is None:
            self.locate_picker = FolderPicker(home(), self._located)
            self.locate_box.layout().addWidget(self.locate_picker)
        self.locate_box.show()
        self.page.sheet.schedule_relayout()

    def _located(self, path: str) -> None:
        def ok(_e: Json) -> None:
            self.locate_box.hide()
            self.page.sheet.schedule_relayout()

        _save(self.eid, {"path": path}, ok)

    def remove(self) -> None:
        def go(_c: bool) -> None:
            store.call("repos.remove", {"id": self.eid}).catch(lambda e: store.toast(err_text(e), "error"))

        store.confirm_action(
            ConfirmSpec(
                title=f"Remove {self.e.get('name')} from the registry?",
                body="The folder and its files stay. Helpers and jobs will ask before working there again.",
                confirm="Remove",
                danger=True,
                on_confirm=go,
            )
        )

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        if obj is self.name_edit and ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
            self.name_edit.setText(str(self.e.get("name") or ""))
            self.name_edit.hide()
            self.name_btn.show()
            return True
        if obj is getattr(self, "notes", None) and ev.type() == QEvent.Type.FocusOut:
            text = self.notes.toPlainText().strip()
            if text != (self.e.get("notes") or ""):
                _save(self.eid, {"notes": text})
        return super().eventFilter(obj, ev)


def _field(title: str, control: QWidget, hint_text: str, top: bool = False) -> QWidget:
    """A label and a hint on the left, the control on the right (``.set-row`` in the web's settings)."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(14)
    left = QWidget()
    left.setFixedWidth(150)
    ll = vbox(left, 2)
    ll.addWidget(Text(title, theme.FS_SMALL, 600))
    if hint_text:
        ll.addWidget(Text(hint_text, theme.FS_MICRO, 400, "text_3", 1.4))
    ll.addStretch(1)
    lay.addWidget(left, 0, Qt.AlignmentFlag.AlignTop if top else Qt.AlignmentFlag.AlignVCenter)
    control.setSizePolicy(QSizePolicy.Policy.Expanding, control.sizePolicy().verticalPolicy())
    lay.addWidget(control, 1, Qt.AlignmentFlag.AlignTop if top else Qt.AlignmentFlag.AlignVCenter)
    return w


def _alias_chip(text: str, on_remove: Any) -> QPushButton:
    """``.chip``: one of the person's names, removed by a click."""
    b = QPushButton(text)
    b.setAutoDefault(False)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setToolTip(f"Remove “{text}”")
    b.setAccessibleName(f"Remove alias {text}")
    styled(
        b,
        lambda t: (
            f"QPushButton {{ border: 1px solid transparent; border-radius: 9px; padding: 1px 8px; background: {theme.blend(t.fill_1, t.raised).name()}; color: {t.text_2}; font-size: 11px; font-weight: 600; }} QPushButton:hover {{ color: {t.err}; background: {theme.blend(t.err_wash, t.raised).name() if hasattr(t, 'err_wash') else theme.blend(t.fill_2, t.raised).name()}; }}"
        ),
    )
    b.clicked.connect(lambda _checked=False, fn=on_remove: fn())  # clicked carries a bool the callback must not get
    return b
