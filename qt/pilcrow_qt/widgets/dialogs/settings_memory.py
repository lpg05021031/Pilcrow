"""Settings → Memory (web/src/components/settings/MemoryPage.tsx): the notes earlier sessions left about
a folder, which every new session there gets to read."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QSizePolicy, QWidget

from ... import icons, theme
from ...lib.fmt import basename, tildify, time_ago
from ...store import ConfirmSpec, store
from ..ui.bits import IconButton, Spinner, button, label
from ..ui.signals import bind
from .parts import Text
from .settings_general import save
from .settings_ui import (
    ModelSelect,
    Panel,
    Sheet,
    Switch,
    clear_layout,
    combo,
    combo_value,
    hint,
    home,
    mono_label,
    select_value,
    styled,
    text_area,
    vbox,
)

Json = Dict[str, Any]


def projects() -> List[str]:
    """The folders memory can be about: the active session's, the recent ones, every live session's."""
    out: List[str] = []
    active = store.active_session()
    if active:
        out.append(str(active.get("project") or active.get("cwd") or ""))
    for p in (store.config or {}).get("recentProjects") or []:
        out.append(str(p))
    for s in store.sessions:
        if not s.get("archived"):
            out.append(str(s.get("project") or s.get("cwd") or ""))
    seen: List[str] = []
    for p in out:
        if p and p not in seen:
            seen.append(p)
    return seen


class MemoryPage(QWidget):
    def __init__(self, sheet: Sheet, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sheet = sheet
        c = store.config or {}
        self.projects = projects()
        self.cwd = self.projects[0] if self.projects else ""
        self.notes: Optional[List[Json]] = None
        self.busy = False
        self.lay = vbox(self, 14, (0, 6, 0, 0))

        intro = QFrame()
        intro.setObjectName("intro")
        intro.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        styled(intro, lambda t: f"#intro {{ background: {t.inset}; border-radius: 12px; }}")
        il = vbox(intro, 8, (20, 18, 20, 18))
        ico = label()
        ico.setFixedSize(16, 16)
        ico.setPixmap(icons.pixmap("book-marked", theme.tokens().text, 16))
        il.addWidget(ico)
        col = QWidget()
        cl = vbox(col, 0)
        cl.addWidget(Text("Project memory", theme.FS_BODY, 600))
        cl.addWidget(
            Text(
                "What earlier sessions learned about a folder — decisions, dead ends, conventions — handed to every new session there. Pin what must never be forgotten; edit anything that drifts.",
                theme.FS_SMALL,
                400,
                "text_3",
                1.5,
            )
        )
        il.addWidget(col)
        self.lay.addWidget(intro)

        settings = Panel()
        self.auto = Switch(c.get("memoryAuto") is not False, name="Take notes automatically")
        self.auto.toggled.connect(lambda v: save({"memoryAuto": bool(v)}))
        settings.lay.addWidget(
            _setting(
                "Take notes automatically",
                "A short run at the end of each turn writes down anything worth keeping. It costs a few cents per turn.",
                self.auto,
            )
        )
        settings.lay.addWidget(_sep())
        self.inject = Switch(c.get("memoryInject") is not False, name="Give notes to new sessions")
        self.inject.toggled.connect(lambda v: save({"memoryInject": bool(v)}))
        settings.lay.addWidget(
            _setting(
                "Give notes to new sessions",
                "The notes ride along in the system prompt of every new session in that folder.",
                self.inject,
            )
        )
        settings.lay.addWidget(_sep())
        self.model = ModelSelect(str(c.get("memoryModel") or ""), name="Model for note-taking")
        self.model.setFixedWidth(240)
        self.model.changed.connect(lambda v, typing: not typing and save({"memoryModel": v}))
        settings.lay.addWidget(_setting("Model for note-taking", "A smaller model is plenty here.", self.model))
        self.lay.addWidget(settings)

        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        fico = label()
        fico.setPixmap(icons.pixmap("folder", theme.tokens().text_3, 14))
        bl.addWidget(fico)
        self.proj = combo(
            [(p, basename(p)) for p in self.projects],
            self.cwd,
            hints={p: tildify(p, home()) for p in self.projects},
            name="Project folder",
        )
        self.proj.setMinimumWidth(200)
        self.proj.currentIndexChanged.connect(lambda _i: self.set_cwd(combo_value(self.proj)))
        bl.addWidget(self.proj)
        bl.addStretch(1)
        self.distill_btn = button(
            "Save what we learned",
            kind="ghost",
            size="sm",
            icon="sparkles",
            on_click=self.distill,
            tooltip="Write notes from the session that is open now",
        )
        self.distill_btn.setText("Save what we learned")
        self.distill_btn.setAccessibleName("Save what we learned")
        bl.addWidget(self.distill_btn)
        self.add_btn = button("Add note", kind="primary", size="sm", icon="plus", on_click=self.start_add)
        self.add_btn.setAccessibleName("Add note")
        bl.addWidget(self.add_btn)
        self.lay.addWidget(bar)

        self.add_box = Panel(margins=(12, 10, 12, 10))
        self.draft = text_area(
            "One durable fact about this project — e.g. “Tests must run from the repo root; the fixtures use relative paths.”",
            rows=2,
            name="New note",
        )
        self.draft.textChanged.connect(lambda: self.save_btn.setEnabled(bool(self.draft.toPlainText().strip())))
        self.add_box.lay.setSpacing(8)
        self.add_box.lay.addWidget(self.draft)
        arow = QWidget()
        al = QHBoxLayout(arow)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(8)
        al.addWidget(hint("Ctrl+Enter saves"))
        al.addStretch(1)
        al.addWidget(button("Cancel", size="sm", on_click=self.cancel_add))
        self.save_btn = button("Save note", kind="primary", size="sm", on_click=self.add)
        self.save_btn.setEnabled(False)
        al.addWidget(self.save_btn)
        self.add_box.lay.addWidget(arow)
        self.add_box.hide()
        self.lay.addWidget(self.add_box)

        self.list_w = QWidget()
        self.list_lay = vbox(self.list_w, 14)
        self.lay.addWidget(self.list_w)
        self.draft.installEventFilter(self)
        bind(store.sched_changed, self, lambda _r: self.load())
        bind(store.config_changed, self, self._sync)
        self.load()

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent

        if obj is self.draft and e.type() == QEvent.Type.KeyPress:
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.add()
                return True
            if e.key() == Qt.Key.Key_Escape:
                self.cancel_add()
                return True
        return super().eventFilter(obj, e)

    def _sync(self, c: Json) -> None:
        for sw, v in ((self.auto, c.get("memoryAuto") is not False), (self.inject, c.get("memoryInject") is not False)):
            sw.blockSignals(True)
            sw.setChecked(v)
            sw.blockSignals(False)

    # ---- data
    def set_cwd(self, cwd: str) -> None:
        if cwd == self.cwd:
            return
        self.cwd = cwd
        if cwd not in self.projects:
            self.projects.append(cwd)
            self.proj.addItem(basename(cwd), cwd)
        self.proj.blockSignals(True)
        select_value(self.proj, cwd)
        self.proj.blockSignals(False)
        self.notes = None
        self.load()

    def load(self) -> None:
        if not self.cwd:
            self.notes = []
            self.render()
            return
        cwd = self.cwd

        def ok(r: Json) -> None:
            if cwd == self.cwd:
                self.notes = list((r or {}).get("notes") or [])
                self.render()

        def bad(_e: Any) -> None:
            if cwd == self.cwd:
                self.notes = []
                self.render()

        store.call("memory.list", {"cwd": cwd}, quiet=True).then(ok).catch(bad)

    def start_add(self) -> None:
        self.add_box.show()
        self.draft.setFocus()
        self.sheet.schedule_relayout()

    def cancel_add(self) -> None:
        self.add_box.hide()
        self.sheet.schedule_relayout()

    def add(self) -> None:
        text = self.draft.toPlainText().strip()
        if not text:
            return

        def ok(n: Json) -> None:
            self.notes = [n] + (self.notes or [])
            self.draft.setPlainText("")
            self.add_box.hide()
            self.render()

        store.call("memory.add", {"cwd": self.cwd, "text": text}).then(ok)

    def patch(self, nid: str, p: Json, then: Optional[Any] = None) -> None:
        def ok(n: Json) -> None:
            self.notes = [n if x.get("id") == nid else x for x in (self.notes or [])]
            self.render()
            if then:
                then()

        store.call("memory.update", {"cwd": self.cwd, "id": nid, **p}).then(ok)

    def remove(self, nid: str) -> None:
        def ok(_r: Any) -> None:
            self.notes = [x for x in (self.notes or []) if x.get("id") != nid]
            self.render()

        store.call("memory.remove", {"cwd": self.cwd, "id": nid}, quiet=True).then(ok).catch(lambda _e: None)

    def distill(self) -> None:
        s = store.active_session()
        if not s:
            store.toast("Open a session first.", "info")
            return
        self.busy = True
        self.distill_btn.setEnabled(False)

        def done(r: Json) -> None:
            self.busy = False
            self.distill_btn.setEnabled(True)
            added = list((r or {}).get("added") or [])
            n = len(added)
            store.toast(
                f"Saved {n} note{'s' if n > 1 else ''} from this session"
                if n
                else "Nothing new worth keeping from this session",
                "success" if n else "info",
            )
            proj = str(s.get("project") or s.get("cwd") or "")
            if proj != self.cwd:
                self.set_cwd(proj)

        def bad(_e: Any) -> None:
            self.busy = False
            self.distill_btn.setEnabled(True)

        store.call("memory.distill", {"id": s.get("id"), "force": True}).then(done).catch(bad)

    def clear(self) -> None:
        def go(_c: bool) -> None:
            def ok(_r: Any) -> None:
                self.notes = [x for x in (self.notes or []) if x.get("pinned")]
                self.render()

            store.call("memory.clear", {"cwd": self.cwd}).then(ok)

        store.confirm_action(
            ConfirmSpec(
                title=f"Forget the unpinned notes for {basename(self.cwd)}?",
                body="Pinned notes stay. New notes keep being taken.",
                confirm="Forget notes",
                danger=True,
                on_confirm=go,
            )
        )

    # ---- rendering
    def render(self) -> None:
        clear_layout(self.list_lay)
        if self.notes is None:
            self.list_lay.addWidget(Spinner(14))
            return
        pinned = [n for n in self.notes if n.get("pinned")]
        rest = [n for n in self.notes if not n.get("pinned")]
        if not self.notes:
            empty = QWidget()
            el = vbox(empty, 8, (20, 44, 20, 44))
            ico = label()
            ico.setPixmap(icons.pixmap("book-marked", theme.tokens().text_3, 22))
            el.addWidget(ico, 0, Qt.AlignmentFlag.AlignHCenter)
            t = label(f"No notes for {basename(self.cwd) or 'this folder'} yet.", tone="faint")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            el.addWidget(t)
            h = hint(
                "Notes appear after a session that decided something, hit a dead end, or found a quirk. You can also add one by hand."
            )
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # A centred wrapping label takes its minimum width; a fixed-width box gives it room to wrap in.
            measure = QWidget()
            measure.setFixedWidth(460)
            vbox(measure).addWidget(h)
            el.addWidget(measure, 0, Qt.AlignmentFlag.AlignHCenter)
            self.list_lay.addWidget(empty)
        if pinned:
            self.list_lay.addWidget(self._group("Pinned", pinned))
        if rest:
            self.list_lay.addWidget(self._group("Notes" if pinned else "", rest))
        if len(rest) > 3:
            b = button("Forget all unpinned notes", kind="link", size="sm", on_click=self.clear)
            b.setAccessibleName("Forget all unpinned notes")
            self.list_lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        self.sheet.schedule_relayout()

    def _group(self, title: str, notes: List[Json]) -> QWidget:
        box = QWidget()
        lay = vbox(box, 4)
        if title:
            lay.addWidget(label(title, tone="faint", size="micro"))
        for n in notes:
            lay.addWidget(NoteRow(n, self))
        return box


def _setting(title: str, hint_text: str, control: QWidget) -> QWidget:
    """``.mem-setting``: title + hint on the left, the control on the right."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 9, 0, 9)
    lay.setSpacing(16)
    col = QWidget()
    cl = vbox(col, 2)
    cl.addStretch(1)
    cl.addWidget(Text(title, theme.FS_SMALL, 600))
    cl.addWidget(Text(hint_text, theme.FS_SMALL, 400, "text_3", 1.5))
    cl.addStretch(1)
    lay.addWidget(col, 1)
    lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return w


def _sep() -> QWidget:
    w = QWidget()
    w.setFixedHeight(1)
    styled(w, lambda t: f"background: {theme.blend(t.hair, theme.blend(t.fill_1, t.raised).name()).name()};")
    return w


class NoteRow(Panel):
    """``.mem-note``: the note's text (click to edit), who wrote it and when, pin and forget."""

    def __init__(self, n: Json, page: MemoryPage) -> None:
        super().__init__(margins=(12, 8, 10, 6), border=True)
        self.n, self.page = n, page
        self.lay.setSpacing(3)
        nid = str(n.get("id") or "")
        self.text_btn = QPushButton(str(n.get("text") or ""))
        self.text_btn.setAutoDefault(False)
        self.text_btn.setToolTip("Click to edit")
        self.text_btn.setAccessibleName("Edit note")
        self.text_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.text_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        styled(
            self.text_btn,
            lambda t: (
                f"QPushButton {{ border: none; background: transparent; text-align: left; padding: 0; font-size: {theme.FS_SMALL}px; color: {t.text}; }} QPushButton:hover {{ color: {t.text_2}; }}"
            ),
        )
        self.text_btn.clicked.connect(self.start_edit)
        self.lay.addWidget(self.text_btn)
        self.edit_box = QWidget()
        el = vbox(self.edit_box, 4)
        self.edit = text_area("", rows=2, name="Note text")
        self.edit.setPlainText(str(n.get("text") or ""))
        el.addWidget(self.edit)
        er = QWidget()
        erl = QHBoxLayout(er)
        erl.setContentsMargins(0, 0, 0, 0)
        erl.setSpacing(4)
        erl.addStretch(1)
        cancel = IconButton("x", "Cancel", 13)
        cancel.clicked.connect(self.cancel_edit)
        erl.addWidget(cancel)
        ok = IconButton("check", "Save (Ctrl+Enter)", 13)
        ok.clicked.connect(self.commit)
        erl.addWidget(ok)
        el.addWidget(er)
        self.edit_box.hide()
        self.lay.addWidget(self.edit_box)
        meta = QWidget()
        ml = QHBoxLayout(meta)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(4)
        who = "You" if n.get("kind") == "manual" else str(n.get("sessionTitle") or "A session")
        ml.addWidget(label(f"{who} · {time_ago(n.get('at'))}", tone="faint", size="micro"), 1)
        pin = IconButton(
            "pin-off" if n.get("pinned") else "pin", "Unpin" if n.get("pinned") else "Pin — never forgotten", 12
        )
        pin.clicked.connect(lambda: page.patch(nid, {"pinned": not n.get("pinned")}))
        ml.addWidget(pin)
        trash = IconButton("trash", "Forget this note", 12)
        trash.clicked.connect(lambda: page.remove(nid))
        ml.addWidget(trash)
        self.lay.addWidget(meta)
        self.edit.installEventFilter(self)

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent

        if obj is self.edit and e.type() == QEvent.Type.KeyPress:
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.commit()
                return True
            if e.key() == Qt.Key.Key_Escape:
                self.cancel_edit()
                return True
        return super().eventFilter(obj, e)

    def start_edit(self) -> None:
        self.text_btn.hide()
        self.edit_box.show()
        self.edit.setFocus()
        self.page.sheet.schedule_relayout()

    def cancel_edit(self) -> None:
        self.edit.setPlainText(str(self.n.get("text") or ""))
        self.edit_box.hide()
        self.text_btn.show()

    def commit(self) -> None:
        text = self.edit.toPlainText().strip()
        if text and text != self.n.get("text"):
            self.page.patch(str(self.n.get("id") or ""), {"text": text})
            return
        self.cancel_edit()


_ = mono_label
