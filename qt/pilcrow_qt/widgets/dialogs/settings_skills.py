"""Settings → Skills (web/src/components/settings/SkillsPage.tsx): the skills Claude can open, by scope,
with create / import / copy / delete, and SKILL.md one click away."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QWidget

from ... import icons, theme
from ...lib.fmt import err_text, tildify, time_ago
from ...store import ConfirmSpec, store
from ..ui.bits import IconButton, Spinner, button, label
from ..ui.menu import show_menu
from ..ui.signals import bind
from .settings_ui import (
    Intro,
    Seg,
    Sheet,
    chip,
    clear_layout,
    count_chip,
    elide,
    field,
    folder_picker,
    hint,
    home,
    icon_box,
    line_edit,
    mono_label,
    text_area,
    vbox,
)

Json = Dict[str, Any]


def active_cwd() -> str:
    """The folder the active session works in: project skills live there."""
    sid = store.active_id
    r = store.rt_of(sid)
    if r is not None and r.cwd:
        return r.cwd
    s = store.active_session()
    return str((s or {}).get("cwd") or "")


def search_box(placeholder: str) -> QLineEdit:
    """``.cust-search``: a 260px search field with the magnifier in it."""
    e = line_edit(placeholder, name=placeholder)
    e.setFixedWidth(260)
    e.setClearButtonEnabled(True)

    def paint(t: theme.Tokens) -> None:
        for a in e.actions():
            e.removeAction(a)
        e.addAction(icons.icon("search", t.text_3, 13), QLineEdit.ActionPosition.LeadingPosition)

    paint(theme.tokens())
    bind(theme.theme.changed, e, paint)
    return e


class SkillsPage(QWidget):
    def __init__(self, sheet: Sheet, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sheet = sheet
        self.cwd = active_cwd()
        self.data: Optional[Json] = None
        self.loading = False
        self.lay = vbox(self, 14, (0, 6, 0, 0))

        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        self.search = search_box("Search skills")
        self.search.textChanged.connect(lambda _t: self.render())
        bl.addWidget(self.search)
        bl.addStretch(1)
        self.spinner = Spinner(13)
        self.spinner.hide()
        bl.addWidget(self.spinner)
        self.reload_btn = IconButton("refresh-cw", "Reload skills in running sessions", 13)
        self.reload_btn.clicked.connect(self.reload)
        bl.addWidget(self.reload_btn)
        self.new_btn = button("New", kind="primary", size="sm", icon="plus", on_click=self._new_menu)
        self.new_btn.setAccessibleName("New")
        bl.addWidget(self.new_btn)
        self.lay.addWidget(bar)

        self.list_w = QWidget()
        self.list_lay = vbox(self.list_w, 14)
        self.lay.addWidget(self.list_w)
        self.load()

    # ---- data
    def load(self, then: Optional[Any] = None) -> None:
        self.loading = True
        self.spinner.show()

        def ok(d: Json) -> None:
            self.loading = False
            self.spinner.hide()
            self.data = d or {"roots": {}, "user": [], "project": [], "plugin": []}
            self.render()
            if then:
                then()

        def bad(e: Any) -> None:
            self.loading = False
            self.spinner.hide()
            store.toast(err_text(e) or "Could not list skills", "error")

        store.call("skills.list", {"cwd": self.cwd}, quiet=True).then(ok).catch(bad)

    def reload(self) -> None:
        def after() -> None:
            def told(n: Any) -> None:
                if n:
                    store.toast(f"Skills reloaded in {n} running session{'s' if int(n) > 1 else ''}", "success")

            store.call("skills.reloadAll", {}, quiet=True).then(told).catch(lambda _e: None)

        self.load(after)

    def _filter(self, items: List[Json]) -> List[Json]:
        n = self.search.text().strip().lower()
        if not n:
            return items
        return [
            s for s in items if n in str(s.get("name") or "").lower() or n in str(s.get("description") or "").lower()
        ]

    # ---- actions
    def _new_menu(self) -> None:
        cwd = self.cwd
        show_menu(
            [
                {
                    "label": "New personal skill",
                    "hint": "For every project, in ~/.claude/skills",
                    "icon": "scroll-text",
                    "on_select": lambda: self.create("user"),
                },
                {
                    "label": "New project skill",
                    "hint": f"In {tildify(cwd, home())}/.claude/skills" if cwd else "Open a session first",
                    "icon": "scroll-text",
                    "disabled": not cwd,
                    "on_select": lambda: self.create("project"),
                },
                {"separator": True},
                {
                    "label": "Import a folder…",
                    "hint": "Copy a folder that has a SKILL.md",
                    "icon": "folder-input",
                    "on_select": lambda: self.import_("user"),
                },
            ],
            self.new_btn,
            align_right=True,
        )

    def create(self, scope: str) -> None:
        NewSkillSheet(self.sheet.parentWidget().window(), scope, self.cwd, on_created=self._created).open()

    def _created(self, s: Json) -> None:
        self.reload()
        self.edit(s)

    def import_(self, scope: str) -> None:
        has_project = bool(((self.data or {}).get("roots") or {}).get("project"))
        ImportSkillSheet(self.sheet.parentWidget().window(), scope, self.cwd, has_project, on_done=self.reload).open()

    def edit(self, s: Json) -> None:
        self.sheet.dismiss()
        store.open_file(str(s.get("file") or ""))

    def remove(self, s: Json) -> None:
        def go(_checked: bool) -> None:
            def done(_r: Any) -> None:
                store.toast(f"Deleted {s.get('name')}", "success")
                self.reload()

            store.call("skills.remove", {"dir": s.get("dir"), "cwd": self.cwd}).then(done)

        store.confirm_action(
            ConfirmSpec(
                title=f"Delete the skill “{s.get('name')}”?",
                body=f"The folder {tildify(str(s.get('dir') or ''), home())} and everything in it is removed. This cannot be undone.",
                confirm="Delete skill",
                danger=True,
                on_confirm=go,
            )
        )

    def duplicate(self, s: Json, scope: str) -> None:
        def done(r: Json) -> None:
            store.toast(
                f"Copied {(r or {}).get('name')} to {'your personal skills' if scope == 'user' else 'this project'}",
                "success",
            )
            self.reload()

        store.call(
            "skills.import", {"scope": scope, "cwd": self.cwd, "from": s.get("dir"), "name": s.get("dirName")}
        ).then(done)

    # ---- rendering
    def render(self) -> None:
        clear_layout(self.list_lay)
        d = self.data
        if d is None:
            return
        q = self.search.text().strip()
        roots = d.get("roots") or {}
        groups = [
            {
                "id": "user",
                "title": "Personal",
                "hint": tildify(str(roots.get("user") or ""), home()),
                "items": self._filter(list(d.get("user") or [])),
                "editable": True,
            },
            {
                "id": "project",
                "title": "This project",
                "hint": tildify(str(roots["project"]), home())
                if roots.get("project")
                else "Open a session to see project skills",
                "items": self._filter(list(d.get("project") or [])),
                "editable": bool(roots.get("project")),
            },
            {
                "id": "plugin",
                "title": "From plugins",
                "hint": "Installed with a plugin; edit them there",
                "items": self._filter(list(d.get("plugin") or [])),
                "editable": False,
            },
        ]
        total = len(d.get("user") or []) + len(d.get("project") or []) + len(d.get("plugin") or [])
        if total == 0 and not q:
            intro = Intro(
                "Teach Claude how you work",
                "A skill is a folder with a SKILL.md: a short description of when it applies and the steps to follow. Claude reads the description on every turn and opens the skill only when the task matches.",
            )
            intro.add_action(
                button("New skill", kind="primary", size="sm", icon="plus", on_click=lambda: self.create("user"))
            )
            intro.add_action(
                button(
                    "Import a folder…",
                    kind="ghost",
                    size="sm",
                    icon="folder-input",
                    on_click=lambda: self.import_("user"),
                )
            )
            self.list_lay.addWidget(intro)
        for g in groups:
            self.list_lay.addWidget(self._group(g, q, bool(roots.get("project"))))

    def _group(self, g: Json, q: str, has_project: bool) -> QWidget:
        box = QWidget()
        lay = vbox(box, 0)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 2, 0, 6)
        hl.setSpacing(8)
        t = label(str(g["title"]))
        t.setStyleSheet("font-weight: 600;")
        hl.addWidget(t)
        hl.addWidget(count_chip(len(g["items"])))
        hl.addStretch(1)
        h = elide(mono_label(str(g["hint"])))
        h.setStyleSheet("font-size: 11.5px;")
        h.setProperty("tone", "faint")
        h.setToolTip(str(g["hint"]))
        h.setMaximumWidth(360)
        h.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(h, 1)
        lay.addWidget(head)
        items: List[Json] = g["items"]
        if not items:
            text = (
                "No match."
                if q
                else "No plugin skills installed."
                if g["id"] == "plugin"
                else str(g["hint"])
                if g["id"] == "project" and not has_project
                else "None yet."
            )
            e = hint(text)
            e.setContentsMargins(0, 8, 0, 10)
            lay.addWidget(e)
            return box
        for s in items:
            lay.addWidget(self._row(s, g, has_project))
        return box

    def _row(self, s: Json, g: Json, has_project: bool) -> QWidget:
        r = QWidget()
        rl = QHBoxLayout(r)
        rl.setContentsMargins(0, 10, 0, 10)
        rl.setSpacing(12)
        rl.addWidget(icon_box("puzzle" if s.get("scope") == "plugin" else "scroll-text"))
        main = QWidget()
        ml = vbox(main, 2)
        name_row = QWidget()
        nl = QHBoxLayout(name_row)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(6)
        name = label(str(s.get("name") or ""))
        name.setStyleSheet("font-weight: 600;")
        nl.addWidget(name)
        if s.get("hasScripts"):
            nl.addWidget(chip("scripts", tiny=True))
        nl.addStretch(1)
        ml.addWidget(name_row)
        desc_text = str(s.get("description") or "")
        prefix = f"{s['plugin']} · " if s.get("plugin") else ""
        desc = elide(hint(prefix + (desc_text or "No description — Claude will rarely pick this skill"), wrap=False))
        desc.setToolTip(desc_text)
        ml.addWidget(desc)
        rl.addWidget(main, 1)
        when = label(time_ago(s.get("mtime")), tone="faint", size="small")
        rl.addWidget(when)
        more = IconButton("ellipsis", f"More for {s.get('name')}", 14)
        more.clicked.connect(lambda: self._row_menu(s, g, has_project, more))
        rl.addWidget(more)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.blend(theme.tokens().hair, theme.tokens().raised).name()};")
        box = QWidget()
        bl = vbox(box, 0)
        bl.addWidget(line)
        bl.addWidget(r)
        return box

    def _row_menu(self, s: Json, g: Json, has_project: bool, anchor: QWidget) -> None:
        items: List[Json] = [
            {
                "label": "Edit SKILL.md" if g["editable"] else "Open SKILL.md",
                "icon": "pencil",
                "on_select": lambda: self.edit(s),
            },
            {
                "label": "Show folder",
                "icon": "folder-open",
                "on_select": lambda: store.call("shell.openPath", {"path": s.get("dir")}),
            },
            {"separator": True},
        ]
        if s.get("scope") != "user":
            items.append(
                {"label": "Copy to personal skills", "icon": "copy", "on_select": lambda: self.duplicate(s, "user")}
            )
        if s.get("scope") != "project" and has_project:
            items.append(
                {"label": "Copy to this project", "icon": "copy", "on_select": lambda: self.duplicate(s, "project")}
            )
        if g["editable"]:
            items += [
                {"separator": True},
                {"label": "Delete…", "icon": "trash", "danger": True, "on_select": lambda: self.remove(s)},
            ]
        show_menu(items, anchor, align_right=True)


def slugify(name: str) -> str:
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", name.strip().lower()))


class NewSkillSheet(Sheet):
    """``NewSkillDialog``: a name and the sentence Claude reads to decide whether to open it."""

    def __init__(self, window: QWidget, scope: str, cwd: str, on_created: Any) -> None:
        super().__init__(
            window, "", "New personal skill" if scope == "user" else "New project skill", 480, on_submit=self.submit
        )
        self.scope, self.cwd, self.on_created = scope, cwd, on_created
        self.busy = False
        form = QWidget()
        fl = vbox(form, 14)
        self.name = line_edit("release-checklist", name="Name")
        self.name.textChanged.connect(self._changed)
        name_f = field("Name", self.name)
        self.slug_hint = mono_label("")
        self.slug_hint.setProperty("size", "small")
        self.slug_hint.hide()
        name_f.layout().addWidget(self.slug_hint)
        fl.addWidget(name_f)
        self.desc = text_area(
            "Use when preparing a release: bump the version, update the changelog and tag the commit.",
            rows=3,
            name="When should Claude use it?",
        )
        desc_f = field("When should Claude use it?", self.desc)
        desc_f.layout().addWidget(
            hint("This sentence is what Claude reads to decide whether to open the skill. Be specific.")
        )
        fl.addWidget(desc_f)
        self.add(form)
        self.add_foot(button("Cancel", on_click=self.dismiss))
        self.create_btn = button("Create and edit", kind="primary", on_click=self.submit)
        self.create_btn.setEnabled(False)
        self.add_foot(self.create_btn)

    def _changed(self, text: str) -> None:
        slug = slugify(text)
        self.slug_hint.setText(f"Saved as {slug}")
        self.slug_hint.setVisible(bool(slug) and slug != text.strip())
        self.create_btn.setEnabled(bool(slug) and not self.busy)

    def submit(self) -> None:
        slug = slugify(self.name.text())
        if not slug or self.busy:
            return
        self.busy = True
        self.create_btn.setEnabled(False)

        def ok(s: Json) -> None:
            store.toast(f"Created {(s or {}).get('name')}", "success")
            self.dismiss()
            self.on_created(s or {})

        def bad(_e: Any) -> None:
            self.busy = False
            self.create_btn.setEnabled(True)

        store.call(
            "skills.create",
            {"scope": self.scope, "cwd": self.cwd, "name": slug, "description": self.desc.toPlainText()},
        ).then(ok).catch(bad)


class ImportSkillSheet(Sheet):
    """``ImportSkillDialog``: pick the folder that has a SKILL.md; it is copied into a scope."""

    def __init__(self, window: QWidget, scope: str, cwd: str, has_project: bool, on_done: Any) -> None:
        super().__init__(window, "", "Import a skill folder", 520, on_submit=self.submit)
        self.cwd, self.on_done = cwd, on_done
        self.busy = False
        self.from_path = ""
        form = QWidget()
        fl = vbox(form, 14)
        fl.addWidget(hint("Pick the folder that contains SKILL.md. It is copied, so the original stays where it is."))
        picker = folder_picker("", self._pick)
        if picker is not None:
            fl.addWidget(picker)
        else:
            self.path = line_edit("/path/to/skill", mono=True, name="Folder")
            self.path.textChanged.connect(self._pick)
            browse = button("Browse…", size="sm", on_click=self._browse)
            r = QWidget()
            rl = QHBoxLayout(r)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)
            rl.addWidget(self.path, 1)
            rl.addWidget(browse)
            fl.addWidget(r)
        self.chosen = mono_label("")
        self.chosen.setProperty("size", "small")
        self.chosen.hide()
        fl.addWidget(self.chosen)
        self.add(form)
        self.scope = Seg([("user", "Personal"), ("project", "This project")], scope, small=True, name="Scope")
        if not has_project:
            self.scope.buttons["project"].setEnabled(False)
        self.add_foot(self.scope)
        self.foot_stretch()
        self.add_foot(button("Cancel", on_click=self.dismiss))
        self.import_btn = button("Import", kind="primary", on_click=self.submit)
        self.import_btn.setEnabled(False)
        self.add_foot(self.import_btn)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Pick the folder that contains SKILL.md", self.from_path or home())
        if d:
            self.path.setText(d)

    def _pick(self, path: str) -> None:
        self.from_path = path.strip()
        self.chosen.setText(self.from_path)
        self.chosen.setVisible(bool(self.from_path))
        self.import_btn.setEnabled(bool(self.from_path) and not self.busy)

    def submit(self) -> None:
        if not self.from_path or self.busy:
            return
        self.busy = True
        self.import_btn.setEnabled(False)

        def ok(s: Json) -> None:
            store.toast(f"Imported {(s or {}).get('name')}", "success")
            self.dismiss()
            self.on_done()

        def bad(_e: Any) -> None:
            self.busy = False
            self.import_btn.setEnabled(True)

        store.call("skills.import", {"scope": self.scope.value, "cwd": self.cwd, "from": self.from_path}).then(
            ok
        ).catch(bad)
