"""The new-session sheet (web/src/components/NewSessionDialog.tsx): a folder (recent projects, a
browser or the system picker), the folded options (worktree, permissions, model, effort) and the first
message. ``open_new_session(window, opts)`` opens it; ``opts`` may carry ``cwd`` and ``prompt``."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from ... import icons, theme
from ...lib.fmt import basename, err_text, tildify
from ...store import store
from ..folder_picker import Browser
from ..ui.bits import Spinner, button, label
from ..ui.collapse import Collapse
from ..ui.signals import bind
from .parts import Keys, Text, sheet_bg
from .settings_ui import (
    EFFORTS,
    MODES,
    Flow,
    FolderChip,
    ModelSelect,
    RadioList,
    Sheet,
    Switch,
    combo,
    combo_value,
    field,
    home,
    modes_for,
    open_sheet,
    set_prop,
    styled,
    text_area,
    two,
    vbox,
)

Json = Dict[str, Any]


def recents() -> List[str]:
    """Recent projects first, then the folders of the listed sessions, eight at most."""
    out: List[str] = []
    for p in (store.config or {}).get("recentProjects") or []:
        if p and p not in out:
            out.append(str(p))
    for s in store.sessions:
        if len(out) >= 10:
            break
        p = str(s.get("project") or s.get("cwd") or "")
        if p and p not in out:
            out.append(p)
    return out[:8]


class NewSessionSheet(Sheet):
    def __init__(self, window: QWidget, opts: Json) -> None:
        super().__init__(window, "newSession", "New session", 580, on_submit=self.start)
        c = store.config or {}
        rec = recents()
        self.cwd = str(opts.get("cwd") or (rec[0] if rec else "") or home())
        self.git: Optional[Json] = None
        self.branches: List[str] = []
        self.busy = False
        self._git_timer = QTimer(self)
        self._git_timer.setSingleShot(True)
        self._git_timer.setInterval(200)
        self._git_timer.timeout.connect(self._load_git)

        # ---- folder
        sec = QWidget()
        sl = vbox(sec, 8)
        sl.addWidget(_ns_label("Folder"))
        box = QFrame()
        box.setObjectName("nsfolder")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box.setFixedHeight(40)
        self.folder_box = box

        def folder_css(t: theme.Tokens) -> str:
            bg = theme.blend(t.fill_1, sheet_bg(t).name()).name()
            return (
                f"#nsfolder {{ background: {bg}; border: 1px solid {theme.blend(t.hair, bg).name()}; border-radius: {theme.R3}px; }}"
                f' #nsfolder[focus="true"] {{ background: {t.canvas}; border-color: {theme.blend(t.hair_3, t.canvas).name()}; }}'
                f" #nsfolder QLineEdit {{ background: transparent; border: none; padding: 0; font-size: 12.5px; color: {t.text}; }}"
            )

        styled(box, folder_css)
        bl = QHBoxLayout(box)
        bl.setContentsMargins(12, 0, 5, 0)
        bl.setSpacing(4)
        self.folder_ico = QLabel()
        self.folder_ico.setPixmap(icons.pixmap("folder-open", theme.tokens().text_3, 16))
        self.folder_ico.setFixedSize(16, 16)
        bl.addWidget(self.folder_ico)
        bl.addSpacing(4)
        from PySide6.QtWidgets import QLineEdit

        self.path = QLineEdit(self.cwd)
        self.path.setPlaceholderText("/path/to/project")
        self.path.setFont(theme.font_mono())
        self.path.setAccessibleName("Folder")
        self.path.textChanged.connect(self._path_typed)
        self.path.installEventFilter(self)
        bl.addWidget(self.path, 1)
        self.browse_btn = button("Browse", kind="ghost", size="sm", on_click=self._toggle_browser)
        self.browse_btn.setAccessibleName("Browse")
        bl.addWidget(self.browse_btn)
        self.system_btn = button(
            "System…", kind="ghost", size="sm", on_click=self._pick_native, tooltip="Use the system folder picker"
        )
        self.system_btn.setText("System…")
        bl.addWidget(self.system_btn)
        sl.addWidget(box)
        self.chips_w = QWidget()
        cl = QHBoxLayout(self.chips_w)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        cl.addWidget(label("Recent", tone="faint", size="micro"), 0, Qt.AlignmentFlag.AlignTop)
        self.chips = Flow(6)
        cl.addWidget(self.chips, 1)
        sl.addWidget(self.chips_w)
        self.picker = Browser(self._browse_to, self._picked)
        self.browse_listing: Optional[Json] = None
        self.show_browser = not opts.get("cwd") and not rec
        self.picker.setVisible(self.show_browser)
        sl.addWidget(self.picker)
        if self.show_browser:
            self._browse_to(self.cwd or home())
        self.add(sec)

        # ---- options
        self.disclose = QPushButton()
        self.disclose.setAutoDefault(False)
        self.disclose.setObjectName("disclose")
        self.disclose.setAccessibleName("Options")
        self.disclose.setCursor(Qt.CursorShape.PointingHandCursor)
        self.disclose.setFixedHeight(36)
        styled(
            self.disclose,
            lambda t: (
                f"#disclose {{ border: none; background: transparent; border-radius: {theme.R2}px; text-align: left; padding: 0; min-height: 36px; max-height: 36px; }}"
                f" #disclose:hover {{ background: {theme.blend(t.fill_1, sheet_bg(t).name()).name()}; }}"
                f" #disclose QLabel {{ background: transparent; }}"
            ),
        )
        dl = QHBoxLayout(self.disclose)
        dl.setContentsMargins(8, 0, 8, 0)
        dl.setSpacing(8)
        self.chev = QLabel()
        self.chev.setFixedSize(14, 14)
        dl.addWidget(self.chev)
        self.disclose_title = Text("Options", theme.FS_UI, 600, "text_2", wrap=False)
        dl.addWidget(self.disclose_title)
        self.summary = Text("", theme.FS_SMALL, 400, "text_3", wrap=False)
        dl.addWidget(self.summary, 1)
        self.disclose.clicked.connect(self._toggle_options)
        self.add(self.disclose)
        self.body.setSpacing(12)

        self.mode = str(c.get("defaultMode") or "default")
        self.model_v = str(c.get("defaultModel") or "")
        self.effort_v = str(c.get("defaultEffort") or "")
        self.use_worktree = False
        self.options = Collapse(self._build_options, False)
        self.add(self.options)

        # ---- first message
        sec2 = QWidget()
        s2 = vbox(sec2, 8)
        s2.addWidget(_ns_label("First message"))
        self.prompt = text_area("", rows=2, name="First message")
        self.prompt.setFixedHeight(76)
        self.prompt.setPlainText(str(opts.get("prompt") or ""))
        self.prompt.textChanged.connect(self._prompt_changed)
        s2.addWidget(self.prompt)
        self.add(sec2)

        # ---- footer
        self.err = label("", tone="err", size="small", wrap=True)
        self.err.hide()
        self.add_foot(self.err, 1)
        self.keys = QWidget()
        kl = QHBoxLayout(self.keys)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.setSpacing(4)
        kl.addWidget(Keys("Ctrl"))
        kl.addWidget(Keys("Enter"))
        kl.addWidget(Text("to start", theme.FS_SMALL, 400, "text_3", wrap=False))
        kl.addStretch(1)
        self.add_foot(self.keys, 1)
        self.add_foot(button("Cancel", kind="ghost", on_click=self.dismiss))
        self.spinner = Spinner(13)
        self.spinner.hide()
        self.add_foot(self.spinner)
        self.start_btn = button("Create session", kind="primary", on_click=self.start)
        self.add_foot(self.start_btn)

        bind(theme.theme.changed, self, lambda _t: self._paint())
        bind(store.sessions_changed, self, lambda _s: self._chips())
        bind(store.config_changed, self, lambda _c: self._chips())
        self._paint()
        self._chips()
        self._prompt_changed()
        self._summary()
        self._git_timer.start()

    def eventFilter(self, obj, e) -> bool:  # noqa: N802
        if obj is getattr(self, "path", None) and e.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            set_prop(self.folder_box, "focus", "true" if e.type() == QEvent.Type.FocusIn else "false")
        return super().eventFilter(obj, e)

    # ---- folder
    def _path_typed(self, text: str) -> None:
        self.cwd = text.strip()
        self.err.hide()
        self.keys.show()
        self._chips()
        self._prompt_changed()
        self.start_btn.setEnabled(bool(self.cwd) and not self.busy)
        self._git_timer.start()

    def set_cwd(self, cwd: str) -> None:
        if cwd == self.path.text():
            self._path_typed(cwd)
            return
        self.path.setText(cwd)

    def _picked(self, cwd: str) -> None:
        self.set_cwd(cwd)
        if self.show_browser:
            self._toggle_browser()

    def _browse_to(self, d: str) -> None:
        """Ask ``fs.browse`` for a folder and show it; entering a folder also makes it the choice."""

        def ok(r: Any) -> None:
            if isinstance(r, dict) and self.show_browser:
                self.browse_listing = r
                self.picker.show_listing(r)
                self.set_cwd(str(r.get("dir") or d))
                self.schedule_relayout()

        store.call("fs.browse", {"dir": d}, quiet=True).then(ok).catch(lambda _e: None)

    def _toggle_browser(self) -> None:
        self.show_browser = not self.show_browser
        self.picker.setVisible(self.show_browser)
        if self.show_browser and self.browse_listing is None:
            self._browse_to(self.cwd or home())
        set_prop(self.browse_btn, "active", "true" if self.show_browser else "false")
        self._chips()
        self.schedule_relayout()

    def _pick_native(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose a folder", self.cwd or home())
        if d:
            self.set_cwd(d)

    def _chips(self) -> None:
        self.chips.clear()
        rec = [r for r in recents() if r != self.cwd]
        show = bool(rec) and not self.show_browser
        self.chips_w.setVisible(show)
        for r in rec:
            ch = FolderChip(basename(r), tildify(r, home()))
            ch.clicked.connect(lambda _c=False, p=r: self.set_cwd(p))
            self.chips.add(ch)
        self.schedule_relayout()

    # ---- git
    def _load_git(self) -> None:
        cwd = self.cwd
        if not cwd:
            return

        def got(g: Json) -> None:
            if cwd != self.cwd:
                return
            self.git = g or {}
            if self.git.get("isRepo"):
                store.call("git.branches", {"cwd": cwd}, quiet=True).then(branches).catch(lambda _e: None)
            else:
                self.use_worktree = False
                self._worktree_ui()

        def branches(b: Json) -> None:
            if cwd != self.cwd:
                return
            raw = (b or {}).get("list") or (b or {}).get("branches") or []
            self.branches = [str(x.get("name") if isinstance(x, dict) else x) for x in raw]
            cur = (b or {}).get("current")
            self.base_v = str(cur or (self.branches[0] if self.branches else "HEAD"))
            self._worktree_ui()

        def bad(_e: Any) -> None:
            if cwd == self.cwd:
                self.git = None
                self._worktree_ui()

        store.call("git.info", {"cwd": cwd}, quiet=True).then(got).catch(bad)

    # ---- options
    def _build_options(self) -> QWidget:
        w = QWidget()
        lay = vbox(w, 18, (0, 6, 0, 0))
        self.wt_sec = QWidget()
        wl = vbox(self.wt_sec, 8)
        r = QWidget()
        rl = QHBoxLayout(r)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(16)
        col = QWidget()
        cl = vbox(col, 2)
        cl.addWidget(Text("Isolated worktree", theme.FS_UI, 600))
        cl.addWidget(
            Text(
                "Claude works on its own branch and checkout, so parallel sessions never collide.",
                theme.FS_SMALL,
                400,
                "text_3",
                1.5,
            )
        )
        rl.addWidget(col, 1)
        self.wt_switch = Switch(self.use_worktree, name="Use a worktree")
        self.wt_switch.toggled.connect(self._wt_toggled)
        rl.addWidget(self.wt_switch)
        wl.addWidget(r)
        self.base = combo([(b, b) for b in self.branches], getattr(self, "base_v", ""), name="Start from")
        from PySide6.QtWidgets import QLineEdit

        self.wt_name_edit = QLineEdit()
        self.wt_name_edit.setPlaceholderText("murphy-yield")
        self.wt_name_edit.setAccessibleName("Branch name")
        self.wt_name_edit.setMinimumHeight(32)
        self.wt_fields = two(field("Start from", self.base, icon="git-branch"), field("Branch name", self.wt_name_edit))
        self.wt_fields.hide()
        wl.addWidget(self.wt_fields)
        self.changed_hint = Text("", theme.FS_SMALL, 400, "text_3", 1.5)
        self.changed_hint.hide()
        wl.addWidget(self.changed_hint)
        lay.addWidget(self.wt_sec)

        perm = QWidget()
        pl = vbox(perm, 8)
        pl.addWidget(_ns_label("Permissions"))
        self.radio = RadioList(modes_for(store.info), self.mode)
        self.radio.changed.connect(self._mode_changed)
        pl.addWidget(self.radio)
        lay.addWidget(perm)

        self.model = ModelSelect(self.model_v, name="Model")
        self.model.changed.connect(self._model_changed)
        self.effort = combo(EFFORTS, self.effort_v, name="Effort")
        self.effort.currentIndexChanged.connect(lambda _i: self._effort_changed())
        lay.addWidget(two(field("Model", self.model), field("Effort", self.effort)))
        self._worktree_ui()
        return w

    def _toggle_options(self) -> None:
        self.options.toggle()
        self._paint()
        self.summary.setVisible(not self.options.is_open)
        self.schedule_relayout()

    def _worktree_ui(self) -> None:
        if self.options.body() is None:
            return
        repo = bool(self.git and self.git.get("isRepo"))
        self.wt_sec.setVisible(repo)
        self.wt_fields.setVisible(repo and self.use_worktree)
        self.base.blockSignals(True)
        self.base.clear()
        for b in self.branches:
            self.base.addItem(b, b)
        i = self.base.findData(getattr(self, "base_v", ""))
        self.base.setCurrentIndex(max(0, i))
        self.base.blockSignals(False)
        changed = int((self.git or {}).get("changed") or 0)
        show_changed = repo and not self.use_worktree and changed > 0
        self.changed_hint.setText(
            f"{changed} uncommitted change{'s' if changed > 1 else ''} here; Claude works on top of them."
        )
        self.changed_hint.setVisible(show_changed)
        self.schedule_relayout()

    def _wt_toggled(self, on: bool) -> None:
        self.use_worktree = bool(on)
        self._worktree_ui()
        self._summary()

    def _mode_changed(self, mid: str) -> None:
        self.mode = mid
        self._summary()

    def _model_changed(self, v: str, _typing: bool) -> None:
        self.model_v = v
        self._summary()

    def _effort_changed(self) -> None:
        self.effort_v = combo_value(self.effort)
        self._summary()

    def _summary(self) -> None:
        mode = next((m["label"] for m in MODES if m["id"] == self.mode), "Ask before edits")
        parts = [str(mode), re.sub(r"^claude-", "", self.model_v) if self.model_v else "default model"]
        if self.effort_v:
            parts.append(f"{'extra high' if self.effort_v == 'xhigh' else self.effort_v} effort")
        if self.use_worktree:
            parts.append("isolated worktree")
        self.summary.setText(" · ".join(parts))

    def _paint(self) -> None:
        t = theme.tokens()
        self.chev.setPixmap(icons.pixmap("chevron-down" if self.options.is_open else "chevron-right", t.text_4, 14))
        self.folder_ico.setPixmap(icons.pixmap("folder-open", t.text_3, 16))

    # ---- prompt and start
    def _prompt_changed(self) -> None:
        self.prompt.setPlaceholderText(f"What should Claude do in {basename(self.cwd) or 'this folder'}? (optional)")
        self.start_btn.setText("Start" if self.prompt.toPlainText().strip() else "Create session")

    def start(self) -> None:
        if not self.cwd or self.busy:
            return
        self.busy = True
        self.err.hide()
        self.keys.show()
        self.spinner.show()
        self.start_btn.setEnabled(False)
        prompt = self.prompt.toPlainText()
        worktree = None
        if self.use_worktree:
            base = combo_value(self.base) if hasattr(self, "base") else getattr(self, "base_v", "")
            wt_name = self.wt_name_edit.text().strip() if hasattr(self, "wt_name_edit") else ""
            worktree = {"base": base}
            if wt_name or prompt[:40]:
                worktree["name"] = wt_name or prompt[:40]

        def made(_r: Json) -> None:
            self.dismiss()

        def bad(e: Any) -> None:
            self.busy = False
            self.spinner.hide()
            self.start_btn.setEnabled(True)
            self.keys.hide()
            self.err.setText(err_text(e))
            self.err.show()
            self.schedule_relayout()

        store.create_session(
            self.cwd, worktree=worktree, mode=self.mode, model=self.model_v, effort=self.effort_v, prompt=prompt
        ).then(made).catch(bad)


def _ns_label(text: str) -> QWidget:
    """``.ns-label``: 12px, 600, text-2."""
    return Text(text, theme.FS_SMALL, 600, "text_2", wrap=False)


def open_new_session(window: QWidget, opts: Any) -> Optional[NewSessionSheet]:
    """Open (or raise) the new-session sheet; a falsy ``opts`` closes it."""
    cur = open_sheet("newSession")
    if opts is None or opts is False:  # an empty dict opens with the defaults
        if cur is not None:
            cur.dismiss(reset=False)
        return None
    if isinstance(cur, NewSessionSheet):
        if isinstance(opts, dict) and opts.get("cwd"):
            cur.set_cwd(str(opts["cwd"]))
        cur.raise_()
        return cur
    s = NewSessionSheet(window, opts if isinstance(opts, dict) else {})
    s.open()
    return s


_ = QSizePolicy
