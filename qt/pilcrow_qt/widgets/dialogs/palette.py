"""The command palette (CommandPalette in web/src/components/Overlays.tsx): fuzzy search over the
actions and the sessions; ``@`` searches the files of the open session's folder."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from ... import icons, theme
from ...lib.fmt import basename, time_ago
from ...lib.fuzzy import fuzzy_filter
from ...store import store
from ..ui.bits import hairline
from ..ui.signals import bind
from .parts import Keys, sheet_bg, web_font
from .sheet import Sheet

# Permission modes as the composer lists them (MODES in web/src/components/Composer.tsx).
MODES = [
    {
        "id": "default",
        "label": "Ask before edits",
        "hint": "Asks before changing files or running commands",
        "icon": "shield-check",
    },
    {
        "id": "acceptEdits",
        "label": "Edit automatically",
        "hint": "File edits are applied without asking",
        "icon": "file-pen",
    },
    {"id": "plan", "label": "Plan mode", "hint": "Explores and proposes a plan; changes nothing", "icon": "map"},
    {"id": "auto", "label": "Auto", "hint": "Approves routine actions, asks about risky ones", "icon": "zap"},
    {
        "id": "bypassPermissions",
        "label": "Bypass permissions",
        "hint": "Runs everything without asking — sandboxes only",
        "icon": "shield-off",
    },
]


@dataclass
class Cmd:
    id: str
    label: str
    icon: str
    group: str  # "Actions" | "Sessions" | "Files"
    run: Callable[[], None]
    hint: str = ""
    kbd: str = ""


class PalItem(QWidget):
    """One row of the list (``.pal-item``), painted: a 14px icon, the label, the hint, the keys."""

    entered = Signal()
    chosen = Signal()

    def __init__(self, cmd: Cmd, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.cmd = cmd
        self._active = False
        self.setFixedHeight(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(cmd.label)
        self.keys: Optional[Keys] = None
        if cmd.kbd:
            self.keys = Keys(cmd.kbd, quiet=True, parent=self)
        bind(theme.theme.changed, self, lambda _t: self.update())

    @property
    def label(self) -> str:
        return self.cmd.label

    @property
    def hint(self) -> str:
        return self.cmd.hint

    def set_active(self, on: bool) -> None:
        if on != self._active:
            self._active = on
            self.update()

    def resizeEvent(self, e) -> None:  # noqa: N802
        if self.keys is not None:
            kw = self.keys.sizeHint().width()
            self.keys.setGeometry(self.width() - 10 - kw, 9, kw, 18)
        super().resizeEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self._active
        if on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.blend(t.fill_2, sheet_bg(t).name()))
            p.drawRoundedRect(QRectF(0, 0, self.width(), 36), theme.R2, theme.R2)
        p.drawPixmap(10, 11, icons.pixmap(self.cmd.icon, t.text if on else t.text_3, 14))
        x = 10 + 14 + 11
        right = self.width() - 10 - ((self.keys.sizeHint().width() + 11) if self.keys is not None else 0)
        lf = web_font(theme.font_ui(theme.FS_UI))
        hf = web_font(theme.font_ui(theme.FS_SMALL))
        lm, hm = QFontMetricsF(lf), QFontMetricsF(hf)
        avail = max(10.0, right - x)
        lw = min(lm.horizontalAdvance(self.cmd.label), avail)
        label = lm.elidedText(self.cmd.label, Qt.TextElideMode.ElideRight, lw + 1)
        p.setFont(lf)
        p.setPen(QColor(t.text if on else t.text_2))
        p.drawText(QRectF(x, 0, lw + 1, 36), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), label)
        if self.cmd.hint:
            hx = x + lw + 11
            hw = right - hx
            if hw > 12:
                p.setFont(hf)
                p.setPen(QColor(t.text_3))
                p.drawText(
                    QRectF(hx, 0, hw, 36),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    hm.elidedText(self.cmd.hint, Qt.TextElideMode.ElideRight, hw),
                )

    def enterEvent(self, e) -> None:  # noqa: N802
        self.entered.emit()
        super().enterEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.chosen.emit()


class CommandPalette(Sheet):
    def __init__(self, window: QWidget) -> None:
        super().__init__(window, "palette", False, width=600)
        self.set_body_margins(0, 0, 0, 0, 0)
        self._q = ""
        self._idx = 0
        self._files: List[str] = []
        self._files_asked = False
        self.items: List[Cmd] = []
        self.rows: List[PalItem] = []
        t = theme.tokens()

        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(18, 0, 18, 0)
        tl.setSpacing(12)
        top.setFixedHeight(54)
        self.search_ico = QLabel()
        self.search_ico.setPixmap(icons.pixmap("search", t.text_3, 16))
        tl.addWidget(self.search_ico)
        self.input = QLineEdit()
        self.input.setProperty("quiet", "true")
        self.input.setProperty("autofocus", True)
        self.input.setPlaceholderText("Search sessions and commands, or type @ for files")
        self.input.setAccessibleName("Search sessions and commands")
        self.input.setStyleSheet("QLineEdit { background: transparent; border: none; font-size: 15.5px; padding: 0; }")
        theme.tint(
            self.input,
            fg="text",
            placeholder="text_3",
            highlight=lambda tk: theme.over(theme.qcolor(tk.selection), sheet_bg(tk)),
        )
        self.input.textChanged.connect(self._on_text)
        self.input.installEventFilter(self)
        tl.addWidget(self.input, 1)
        self.body_lay.addWidget(top)
        self.body_lay.addWidget(hairline(over=sheet_bg))

        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_scroll.setMaximumHeight(460)
        self.list_host = QWidget()
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(8, 6, 8, 10)
        self.list_lay.setSpacing(0)
        self.list_scroll.setWidget(self.list_host)
        self.body_lay.addWidget(self.list_scroll)
        self.empty = QLabel()
        theme.tint(self.empty, fg="text_3")
        self.empty.setContentsMargins(18, 14, 18, 18)
        self.empty.hide()
        self.body_lay.addWidget(self.empty)
        bind(store.sessions_changed, self, lambda _s: self._rebuild())
        self._rebuild()

    # ---- the commands
    def _actions(self) -> List[Cmd]:
        s = store
        win = self.window_
        active_id = s.active_id
        cfg = s.config or {}
        dark = cfg.get("theme") == "dark"

        def panel(tab: str) -> Callable[[], None]:
            return lambda: s.set_ui(panel=True, panel_tab=tab)

        def find() -> None:
            sv = getattr(win, "session_view", None)
            fn = getattr(sv, "open_find", None)
            if callable(fn):
                QTimer.singleShot(60, fn)

        out: List[Cmd] = [
            Cmd(
                "new",
                "New session",
                "plus",
                "Actions",
                s.quick_new_session,
                "In the current folder, with your defaults",
                "Ctrl+N",
            ),
            Cmd(
                "new-opts",
                "New session with options…",
                "sliders-horizontal",
                "Actions",
                lambda: s.set_dialog("newSession", {}),
                "Folder, worktree, permissions, model",
                "Ctrl+Shift+N",
            ),
            Cmd(
                "side",
                "Toggle sidebar",
                "panel-left",
                "Actions",
                lambda: s.set_ui(sidebar=not s.ui.sidebar),
                "",
                "Ctrl+B",
            ),
            Cmd(
                "panel",
                "Toggle side panel",
                "panel-right",
                "Actions",
                lambda: s.set_ui(panel=not s.ui.panel),
                "",
                "Ctrl+\\",
            ),
            Cmd("view", "Cycle view: Normal → Thinking → Verbose", "eye", "Actions", s.cycle_view, "", "Ctrl+O"),
            Cmd(
                "overview",
                "Control room",
                "layout-grid",
                "Actions",
                lambda: s.show_page("overview"),
                "Every session at a glance; answer prompts in place",
                "Ctrl+Shift+O",
            ),
            Cmd(
                "skills",
                "Skills…",
                "scroll-text",
                "Actions",
                lambda: s.set_dialog("settings", "skills"),
                "Personal and project skills",
            ),
            Cmd(
                "mcp",
                "MCP servers…",
                "plug",
                "Actions",
                lambda: s.set_dialog("settings", "mcp"),
                "Add, sign in, reconnect",
            ),
        ]
        if active_id:
            sid = active_id
            out += [
                Cmd("find", "Find in the conversation", "search", "Actions", find, "", "Ctrl+F"),
                Cmd("changes", "Show changes", "git-compare", "Actions", panel("changes"), "", "Alt+1"),
                Cmd("files", "Show files", "folder-tree", "Actions", panel("files"), "", "Alt+2"),
                Cmd("term", "Open terminal", "square-terminal", "Actions", panel("terminal"), "", "Alt+3"),
                Cmd("preview", "Preview a web app", "app-window", "Actions", panel("preview"), "", "Alt+4"),
                Cmd("tasks", "Show tasks", "list-checks", "Actions", panel("tasks"), "", "Alt+5"),
                Cmd(
                    "history",
                    "Show the timeline",
                    "history",
                    "Actions",
                    panel("history"),
                    "Every step that changed files; restore from any point",
                    "Alt+6",
                ),
                Cmd("info", "Session details and context usage", "info", "Actions", panel("info"), "", "Alt+7"),
                Cmd("stop", "Stop Claude", "circle-stop", "Actions", lambda: s.interrupt(sid), "", "Esc"),
                Cmd(
                    "compact",
                    "Compact the conversation",
                    "minimize-2",
                    "Actions",
                    lambda: s.send_message(sid, "/compact"),
                ),
                Cmd("fork", "Fork this session", "git-fork", "Actions", lambda: s.fork_session(sid)),
            ]
            out += [
                Cmd(
                    "mode-" + m["id"],
                    f"Permission mode: {m['label']}",
                    m["icon"],
                    "Actions",
                    (lambda mid=m["id"]: s.set_mode(sid, mid)),
                    m["hint"],
                )
                for m in MODES
            ]
        out += [
            Cmd(
                "theme",
                f"Switch to {'light' if dark else 'dark'} theme",
                "sun",
                "Actions",
                lambda: s.set_theme("light" if dark else "dark"),
            ),
            Cmd(
                "archived",
                "Show archived sessions",
                "archive",
                "Actions",
                lambda: s.set_ui(show_archived=not s.ui.show_archived),
            ),
            Cmd("refresh", "Reload session list", "refresh-cw", "Actions", lambda: s.refresh_sessions(True)),
            Cmd("settings", "Settings", "settings", "Actions", lambda: s.set_dialog("settings", True), "", "Ctrl+,"),
            Cmd(
                "keys",
                "Keyboard shortcuts",
                "keyboard",
                "Actions",
                lambda: s.set_dialog("shortcuts", True),
                "",
                "Ctrl+/",
            ),
            Cmd("quit", "Quit Pilcrow", "power", "Actions", lambda: _quit(win), "Stops all sessions", "Ctrl+Q"),
        ]
        return out

    def _cwd(self) -> str:
        r = store.rt_of(store.active_id)
        active = store.active_session() or {}
        return (r.cwd if r is not None else "") or str(active.get("cwd") or "")

    def _compute(self) -> List[Cmd]:
        q = self._q
        if q.startswith("@"):
            cwd = self._cwd()
            if cwd and not self._files_asked:
                self._files_asked = True
                store.call("fs.index", {"cwd": cwd}, quiet=True).then(self._got_files).catch(lambda _e: None)
            fq = q[1:]
            files = [f for f in self._files if not f.endswith("/")]
            return [
                Cmd("f:" + m.item, m.item, "file", "Files", (lambda p=m.item: store.open_file(f"{cwd}/{p}")))
                for m in fuzzy_filter(fq, files, lambda f: f, 60)
            ]
        actions = self._actions()
        sess = [
            Cmd(
                "s:" + str(s.get("id")),
                str(s.get("title") or ""),
                "message-square",
                "Sessions",
                (lambda sid=str(s.get("id")): store.open_session(sid)),
                f"{basename(str(s.get('project') or s.get('cwd') or ''))} · {time_ago(s.get('lastModified'))}",
            )
            for s in store.sessions
        ]
        if not q.strip():
            return actions[:8] + sess[:12]
        return [m.item for m in fuzzy_filter(q, actions + sess, lambda c: f"{c.label} {c.hint or ''}", 60)]

    def _got_files(self, files: object) -> None:
        self._files = [str(f) for f in files] if isinstance(files, list) else []
        self._rebuild()

    # ---- the list
    def _on_text(self, q: str) -> None:
        self._q = q
        self._idx = 0
        self._rebuild()

    def _rebuild(self) -> None:
        while self.list_lay.count():
            w = self.list_lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.rows = []
        self.items = self._compute()
        self._idx = max(0, min(self._idx, len(self.items) - 1))
        last_group = ""
        height = 6 + 10
        for i, c in enumerate(self.items):
            if c.group != last_group:
                head = QLabel(c.group)
                head.setFixedHeight(31)  # padding 10 10 5 round an 11px line
                head.setContentsMargins(10, 10, 10, 5)
                head.setFont(theme.font_ui(theme.FS_MICRO, 600))
                theme.tint(head, fg="text_3")
                self.list_lay.addWidget(head)
                last_group = c.group
                height += 31
            height += 36
            row = PalItem(c)
            row.set_active(i == self._idx)
            row.entered.connect(lambda i=i: self._set_idx(i))
            row.chosen.connect(lambda c=c: self.run(c))
            self.rows.append(row)
            self.list_lay.addWidget(row)
        self.list_lay.addStretch(1)
        self.list_scroll.setFixedHeight(min(height, 460))
        self.list_scroll.setVisible(bool(self.items))
        self.empty.setVisible(not self.items)
        self.empty.setText(f"Nothing matches “{self._q}”.")
        if not self._closed:
            self.place()

    def _set_idx(self, i: int) -> None:
        if i == self._idx or not (0 <= i < len(self.rows)):
            return
        self._idx = i
        for k, r in enumerate(self.rows):
            r.set_active(k == i)
        self.list_scroll.ensureWidgetVisible(self.rows[i], 0, 0)

    def selected(self) -> Optional[Cmd]:
        return self.items[self._idx] if 0 <= self._idx < len(self.items) else None

    def run(self, c: Optional[Cmd]) -> None:
        if c is None:
            return
        self.reject()
        QTimer.singleShot(0, c.run)

    def submit(self) -> None:
        self.run(self.selected())

    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        if obj is self.input and e.type() == QEvent.Type.KeyPress:
            k = e.key()
            if k == Qt.Key.Key_Down:
                self._set_idx(min(len(self.items) - 1, self._idx + 1))
                return True
            if k == Qt.Key.Key_Up:
                self._set_idx(max(0, self._idx - 1))
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.run(self.selected())
                return True
        return super().eventFilter(obj, e)


def _quit(window: QWidget) -> None:
    from . import quit_app

    quit_app(window)


_ = QApplication
