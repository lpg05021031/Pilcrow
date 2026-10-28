"""The sidebar (a port of web/src/components/Sidebar.tsx).

Brand and the new-session buttons at the top, then search, the Control room and Inbox pages, the
session list grouped by project with "Needs you" and "Pinned" sections above it, and Settings,
archive and appearance at the foot.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
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
from ..lib.fmt import basename, tildify, time_ago
from ..lib.fuzzy import fuzzy_score
from ..store import ConfirmSpec, store
from .ui.bits import IconButton, Mark, StatusGlyph, label
from .ui.menu import show_menu, show_menu_at
from .ui.signals import bind

Json = dict


class Sidebar(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._q = ""
        self._collapsed: Dict[str, bool] = {}
        self._show_all: Dict[str, bool] = {}
        self._rows: Dict[str, SessionRow] = {}
        self._now_timer = QTimer(self)
        self._now_timer.setInterval(30_000)
        self._now_timer.timeout.connect(self._tick)
        self._now_timer.start()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- top: brand, new session, search, pages
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(12, 12, 12, 8)
        tl.setSpacing(8)
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 0)
        brand.setSpacing(8)
        brand.addWidget(Mark(17))
        name = label("Pilcrow")
        name.setStyleSheet(f"font-weight: 600; font-size: {theme.FS_UI}px;")
        brand.addWidget(name, 1)
        self.collapse_btn = IconButton("panel-left-close", "Hide sidebar (Ctrl+B)", 15)
        self.collapse_btn.clicked.connect(lambda: store.set_ui(sidebar=False))
        brand.addWidget(self.collapse_btn)
        tl.addLayout(brand)

        new_wrap = QHBoxLayout()
        new_wrap.setSpacing(4)
        self.new_btn = NavButton("square-pen", "New session", kbd="Ctrl N")
        self.new_btn.setToolTip("New session in the current folder (Ctrl+N) · Shift+click for options")
        self.new_btn.clicked.connect(self._new_session)
        new_wrap.addWidget(self.new_btn, 1)
        self.new_opts = IconButton("sliders-horizontal", "New session with options… (Ctrl+Shift+N)", 13)
        self.new_opts.clicked.connect(lambda: store.set_dialog("newSession", {}))
        new_wrap.addWidget(self.new_opts)
        tl.addLayout(new_wrap)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search")
        self.search.setClearButtonEnabled(True)
        self.search_icon = self.search.addAction(
            icons.icon("search", theme.tokens().text_3, 14), QLineEdit.ActionPosition.LeadingPosition
        )
        self.search.textChanged.connect(self._on_search)
        self.search.setAccessibleName("Search sessions")
        tl.addWidget(self.search)

        self.page_overview = NavButton("layout-grid", "Control room")
        self.page_overview.setToolTip("Every session at a glance (Ctrl+Shift+O)")
        self.page_overview.clicked.connect(
            lambda: store.show_page("session" if store.page == "overview" else "overview")
        )
        self.page_inbox = NavButton("inbox", "Inbox")
        self.page_inbox.setToolTip("Results of scheduled sessions")
        self.page_inbox.clicked.connect(lambda: store.show_page("session" if store.page == "inbox" else "inbox"))
        tl.addWidget(self.page_overview)
        tl.addWidget(self.page_inbox)
        root.addWidget(top)

        # ---- the list
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_host = QWidget()
        self.list_host.setObjectName("side-list")
        self.list = QVBoxLayout(self.list_host)
        self.list.setContentsMargins(8, 2, 8, 8)
        self.list.setSpacing(2)
        self.list.addStretch(1)
        self.scroll.setWidget(self.list_host)
        root.addWidget(self.scroll, 1)

        # ---- foot
        foot = QWidget()
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(12, 8, 12, 10)
        fl.setSpacing(4)
        self.settings_btn = NavButton("settings", "Settings")
        self.settings_btn.setToolTip("Settings (Ctrl+,)")
        self.settings_btn.clicked.connect(lambda: store.set_dialog("settings", True))
        fl.addWidget(self.settings_btn, 1)
        self.archive_btn = IconButton("archive", "Archived sessions", 15)
        self.archive_btn.clicked.connect(lambda: store.set_ui(show_archived=not store.ui.show_archived))
        fl.addWidget(self.archive_btn)
        self.theme_btn = IconButton("monitor", "Appearance", 15)
        self.theme_btn.clicked.connect(self._cycle_theme)
        fl.addWidget(self.theme_btn)
        root.addWidget(foot)

        bind(store.sessions_changed, self, lambda _s: self.rebuild())
        bind(store.active_changed, self, lambda _a: self.rebuild())
        bind(store.unread_changed, self, lambda _u: self.rebuild())
        bind(store.ui_changed, self, lambda _u: self._on_ui())
        bind(store.page_changed, self, lambda _p: self._on_page())
        bind(store.inbox_unread_changed, self, lambda _n: self._on_page())
        bind(store.config_changed, self, lambda _c: self._on_config())
        bind(theme.theme.changed, self, lambda _t: self._on_config())
        self._on_ui()
        self._on_page()
        self.rebuild()

    # ---- top controls
    def _new_session(self) -> None:
        mods = self.new_btn.last_modifiers
        if mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier):
            store.set_dialog("newSession", {})
        else:
            store.quick_new_session()

    def _on_search(self, q: str) -> None:
        self._q = q
        self.rebuild()

    def _cycle_theme(self) -> None:
        cur = (store.config or {}).get("theme") or "system"
        nxt = "dark" if cur == "system" else "light" if cur == "dark" else "system"
        store.set_theme(nxt)

    def _on_config(self) -> None:
        cur = (store.config or {}).get("theme") or "system"
        nxt = "dark" if cur == "system" else "light" if cur == "dark" else "system"
        self.theme_btn.set_icon_name("monitor" if cur == "system" else "moon" if cur == "dark" else "sun")
        self.theme_btn.setToolTip(f"Appearance: {cur} — click for {nxt}")
        self.search_icon.setIcon(icons.icon("search", theme.tokens().text_3, 14))

    def _on_ui(self) -> None:
        self.setFixedWidth(store.ui.sidebar_w)
        archived = sum(1 for s in store.sessions if s.get("archived"))
        self.archive_btn.set_active(store.ui.show_archived)
        self.archive_btn.setToolTip(
            "Back to active sessions" if store.ui.show_archived else f"Archived sessions ({archived})"
        )
        self.rebuild()

    def _on_page(self) -> None:
        self.page_overview.set_on(store.page == "overview")
        self.page_inbox.set_on(store.page == "inbox")
        working = sum(1 for s in store.sessions if not s.get("archived") and s.get("status") in ("running", "starting"))
        needs = sum(
            1
            for s in store.sessions
            if not s.get("archived") and (s.get("status") == "requires_action" or (s.get("pending") or 0) > 0)
        )
        self.page_overview.set_count(needs if needs else working, "needs" if needs else "")
        self.page_inbox.set_count(store.inbox_unread, "unread")

    def _tick(self) -> None:
        for r in self._rows.values():
            r.refresh_time()

    # ---- the list
    def rebuild(self) -> None:
        self._on_page()
        sessions = store.sessions
        active = store.active_id
        show_archived = store.ui.show_archived
        q = self._q.strip()
        lst = [s for s in sessions if (s.get("archived") if show_archived else not s.get("archived"))]
        if not q:
            # Runs started by a schedule live in the inbox; the active one still shows so its row can be found.
            lst = [s for s in lst if not s.get("scheduled") or s.get("id") == active]
        else:
            lst = [
                s
                for s in lst
                if fuzzy_score(q, f"{s.get('title', '')} {basename(s.get('project') or s.get('cwd') or '')}")
            ]
        attention = [
            s
            for s in lst
            if s.get("id") != active and (s.get("status") == "requires_action" or (s.get("pending") or 0) > 0)
        ]
        pinned = [s for s in lst if s.get("pinned") and s not in attention]
        rest = [s for s in lst if s not in attention and s not in pinned]
        groups: Dict[str, List[Json]] = {}
        for s in rest:
            groups.setdefault(str(s.get("project") or s.get("cwd") or ""), []).append(s)
        ordered = sorted(groups.items(), key=lambda kv: -(kv[1][0].get("lastModified") or 0))

        # Rebuild the layout, reusing the row widgets that are still listed (so their state and their place stay).
        while self.list.count() > 1:
            item = self.list.takeAt(0)
            w = item.widget()
            if w is not None and not isinstance(w, SessionRow):
                w.deleteLater()
            elif w is not None:
                w.setParent(None)
        keep = {s.get("id") for s in lst}
        for sid in list(self._rows):
            if sid not in keep:
                self._rows.pop(sid).deleteLater()
        idx = 0

        def add(w: QWidget) -> None:
            nonlocal idx
            self.list.insertWidget(idx, w)
            idx += 1

        def row_for(s: Json, show_project: bool = False) -> SessionRow:
            sid = str(s.get("id"))
            r = self._rows.get(sid)
            if r is None:
                r = self._rows[sid] = SessionRow(sid)
            r.update(s, active=sid == active, unread=bool(store.unread.get(sid)), show_project=show_project, q=q)
            return r

        if attention:
            add(SectionLabel("Needs you", "accent"))
            for s in attention:
                add(row_for(s, True))
        if pinned:
            add(SectionLabel("Pinned"))
            for s in pinned:
                add(row_for(s, True))
        for key, items in ordered:
            head = GroupHead(basename(key), key, collapsed=self._collapsed.get(key, False))
            head.toggled.connect(lambda k=key: self._toggle_group(k))
            head.add_clicked.connect(lambda k=key: self._new_in(k))
            add(head)
            if self._collapsed.get(key):
                continue
            limit = len(items) if (self._show_all.get(key) or q) else 6
            for s in items[:limit]:
                add(row_for(s))
            if len(items) > limit:
                more = QPushButton(f"Show {len(items) - limit} more")
                more.setProperty("kind", "ghost")
                more.setProperty("size", "sm")
                more.clicked.connect(lambda _c=False, k=key: self._show_more(k))
                add(more)
        if store.sessions_loaded and not sessions:
            add(EmptyNote("No sessions yet. Start one to see it here."))
        elif sessions and not ordered and not attention and not pinned:
            add(EmptyNote("Nothing archived." if show_archived else "No matching sessions."))

    def _toggle_group(self, key: str) -> None:
        self._collapsed[key] = not self._collapsed.get(key, False)
        self.rebuild()

    def _show_more(self, key: str) -> None:
        self._show_all[key] = True
        self.rebuild()

    def _new_in(self, path: str) -> None:
        mods = (
            self.sender().last_modifiers if hasattr(self.sender(), "last_modifiers") else Qt.KeyboardModifier.NoModifier
        )
        if mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier):
            store.set_dialog("newSession", {"cwd": path})
        else:
            store.quick_new_session(path)


class NavButton(QPushButton):
    """A full-width row button with an icon, a label, an optional count and key hint (``.side-new``, ``.side-page``)."""

    def __init__(self, icon_name: str, text: str, kbd: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._icon = icon_name
        self._on = False
        self._count = 0
        self._count_tone = ""
        self.last_modifiers = Qt.KeyboardModifier.NoModifier
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(30)
        self.setAccessibleName(text)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(8)
        self.ico = QLabel()
        lay.addWidget(self.ico)
        self.text_lb = QLabel(text)
        lay.addWidget(self.text_lb, 1)
        self.count_lb = QLabel()
        self.count_lb.setProperty("size", "small")
        lay.addWidget(self.count_lb)
        self.kbd = QLabel(kbd)
        self.kbd.setProperty("tone", "faint")
        self.kbd.setProperty("size", "micro")
        lay.addWidget(self.kbd)
        self.kbd.setVisible(bool(kbd))
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.last_modifiers = e.modifiers()
        super().mousePressEvent(e)

    def set_on(self, on: bool) -> None:
        self._on = on
        self._paint()

    def set_count(self, n: int, tone: str = "") -> None:
        self._count, self._count_tone = n, tone
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.ico.setPixmap(icons.pixmap(self._icon, t.text if self._on else t.text_2, 15))
        self.count_lb.setText(str(self._count) if self._count else "")
        self.count_lb.setStyleSheet(
            f"color: {t.accent_text if self._count_tone == 'needs' else t.accent_text if self._count_tone == 'unread' else t.text_3}; font-weight: 600;"
        )
        bg = theme.blend(t.fill_1, t.chrome).name() if self._on else "transparent"
        self.setStyleSheet(
            f"QPushButton {{ background: {bg}; border: none; border-radius: {theme.R2}px; text-align: left; }} QPushButton:hover {{ background: {theme.blend(t.fill_1, t.chrome).name()}; }}"
        )
        self.text_lb.setStyleSheet(f"color: {t.text if self._on else t.text}; font-weight: {500 if self._on else 400};")


class SectionLabel(QLabel):
    def __init__(self, text: str, tone: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text.upper(), parent)
        self._tone = tone
        self.setContentsMargins(10, 10, 8, 4)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.setStyleSheet(
            f"color: {t.accent_text if self._tone == 'accent' else t.text_3}; font-size: {theme.FS_MICRO}px; font-weight: 600; letter-spacing: 0.6px;"
        )


class GroupHead(QWidget):
    toggled = Signal()
    add_clicked = Signal()

    def __init__(self, label_text: str, path: str, collapsed: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 8, 0, 2)
        lay.setSpacing(2)
        self.btn = QPushButton(label_text)
        self.btn.setFlat(True)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setToolTip(tildify(path, (store.info or {}).get("home")))
        self.btn.clicked.connect(self.toggled.emit)
        self.btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        t = theme.tokens()
        self.btn.setStyleSheet(
            f"QPushButton {{ border: none; text-align: left; padding: 2px 6px; color: {t.text_3}; font-size: {theme.FS_MICRO}px; font-weight: 600; letter-spacing: 0.4px; }} QPushButton:hover {{ color: {t.text_2}; }}"
        )
        self.btn.setIcon(icons.icon("chevron-right" if collapsed else "chevron-down", t.text_4, 12))
        self.btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        lay.addWidget(self.btn, 1)
        self.add = IconButton("plus", f"New session in {label_text} · Shift+click for options", 13)
        self.add.last_modifiers = Qt.KeyboardModifier.NoModifier
        self.add.clicked.connect(self.add_clicked.emit)
        lay.addWidget(self.add)


class EmptyNote(QLabel):
    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setContentsMargins(10, 12, 10, 12)
        self.setProperty("tone", "faint")
        self.setProperty("size", "small")


class SessionRow(QWidget):
    """One session in the list: title, project/branch line, status glyph or time, and the ⋯ menu."""

    def __init__(self, sid: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = sid
        self.s: Json = {}
        self._active = False
        self._unread = False
        self._hover = False
        self.setProperty("data-sid", sid)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)
        self.setMouseTracking(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 4, 0)
        lay.setSpacing(6)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._title_text = ""
        self._q = ""
        self.meta = QLabel()
        self.meta.setProperty("size", "micro")
        self.meta.setProperty("tone", "faint")
        col.addWidget(self.title)
        col.addWidget(self.meta)
        lay.addLayout(col, 1)
        self.glyph = StatusGlyph()
        lay.addWidget(self.glyph)
        self.time = QLabel()
        self.time.setProperty("size", "micro")
        self.time.setProperty("tone", "faint")
        lay.addWidget(self.time)
        self.menu_btn = IconButton("ellipsis", "Session actions", 14)
        self.menu_btn.clicked.connect(self._open_menu)
        self.menu_btn.setVisible(False)
        lay.addWidget(self.menu_btn)
        self.rename_edit: Optional[QLineEdit] = None
        bind(theme.theme.changed, self, lambda _t: self._paint())

    # ---- data
    def update(self, s: Json, active: bool, unread: bool, show_project: bool, q: str) -> None:  # type: ignore[override]
        self.s = s
        self._active, self._unread = active, unread
        self._q = q
        title = str(s.get("title") or "")
        self._title_text = title
        self._elide_title()
        self.title.setToolTip(title)
        bits: List[str] = []
        wt = s.get("worktree")
        branch = s.get("gitBranch")
        if show_project:
            bits.append(basename(str(s.get("project") or s.get("cwd") or "")))
        if wt:
            bits.append("⎇ " + str(wt.get("branch", "")).replace("pilcrow/", "", 1))
        elif branch and branch not in ("main", "master", "HEAD"):
            bits.append("⎇ " + str(branch))
        self.meta.setText(" · ".join(bits))
        self.meta.setVisible(bool(bits))
        self.setFixedHeight(40 if bits else 34)
        status = str(s.get("status") or "")
        pending = int(s.get("pending") or 0)
        self.glyph.set_state(status, pending, unread and not active)
        quiet = not (
            status in ("running", "starting", "requires_action", "error") or pending or (unread and not active)
        )
        self.time.setVisible(quiet and not self._hover)
        self.refresh_time()
        self._paint()

    def refresh_time(self) -> None:
        import time as _t

        self.time.setText(time_ago(self.s.get("lastModified"), _t.time() * 1000, True))

    def _elide_title(self) -> None:
        from PySide6.QtGui import QFontMetrics

        avail = max(
            40,
            self.width()
            - 10
            - 4
            - 6
            - 20
            - (self.time.width() if self.time.isVisible() else 0)
            - (self.menu_btn.width() if self.menu_btn.isVisible() else 0),
        )
        text = QFontMetrics(self.title.font()).elidedText(self._title_text, Qt.TextElideMode.ElideRight, avail)
        self.title.setText(_highlight(text, self._q) if self._q else text)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if hasattr(self, "_title_text"):
            self._elide_title()

    def _paint(self) -> None:
        t = theme.tokens()
        bg = (
            theme.blend(t.fill_2, t.chrome).name()
            if self._active
            else theme.blend(t.fill_1, t.chrome).name()
            if self._hover
            else "transparent"
        )
        self.setStyleSheet(f"SessionRow {{ background: {bg}; border-radius: {theme.R2}px; }}")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        weight = 500 if (self._active or (self._unread and not self._active)) else 400
        self.title.setStyleSheet(
            f"color: {t.text if (self._active or self._unread) else t.text_2}; font-weight: {weight};"
        )

    # ---- interaction
    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.menu_btn.setVisible(True)
        self.time.setVisible(False)
        self._paint()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.menu_btn.setVisible(False)
        self.update(
            self.s,
            self._active,
            self._unread,
            bool(self.meta.isVisible() and not self.s.get("worktree") and not self.s.get("gitBranch")),
            "",
        )
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rename_edit is None and not self.menu_btn.underMouse():
            store.open_session(self.sid)
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        if self.rename_edit is not None:
            return
        show_menu_at(self._items(), e.globalPos(), self, 210)

    def _open_menu(self) -> None:
        show_menu(self._items(), self.menu_btn, 210, align_right=True)

    def _items(self) -> List[dict]:
        s = self.s
        wt = s.get("worktree")
        return [
            {"label": "Rename", "icon": "pencil", "on_select": self._start_rename},
            {
                "label": "Unpin" if s.get("pinned") else "Pin to top",
                "icon": "pin-off" if s.get("pinned") else "pin",
                "on_select": lambda: store.pin_session(self.sid, not s.get("pinned")),
            },
            {"label": "Fork", "icon": "git-fork", "on_select": lambda: store.fork_session(self.sid)},
            {
                "label": "Unarchive" if s.get("archived") else "Archive",
                "icon": "archive-restore" if s.get("archived") else "archive",
                "on_select": lambda: store.archive_session(self.sid, not s.get("archived")),
            },
            {"separator": True},
            {
                "label": "Delete…",
                "icon": "trash",
                "danger": True,
                "on_select": lambda: store.confirm_action(
                    ConfirmSpec(
                        title="Delete this session?",
                        body="The conversation history is removed. Files Claude changed stay as they are.",
                        confirm="Delete session",
                        danger=True,
                        checkbox={"label": f"Also remove the worktree and branch {wt.get('branch')}", "value": False}
                        if wt
                        else None,
                        on_confirm=lambda checked: store.delete_session(self.sid, checked),
                    )
                ),
            },
        ]

    def _start_rename(self) -> None:
        if self.rename_edit is not None:
            return
        edit = QLineEdit(str(self.s.get("title") or ""))
        edit.setProperty("quiet", "true")
        self.rename_edit = edit
        lay = self.layout()
        lay.replaceWidget(self.title, edit)
        self.title.hide()
        edit.setFocus()
        edit.selectAll()

        def done() -> None:
            if self.rename_edit is None:
                return
            text = edit.text().strip()
            self.rename_edit = None
            lay.replaceWidget(edit, self.title)
            edit.deleteLater()
            self.title.show()
            if text and text != self.s.get("title"):
                store.rename_session(self.sid, text)

        edit.editingFinished.connect(done)
        edit.returnPressed.connect(edit.clearFocus)


def _highlight(text: str, q: str) -> str:
    from html import escape

    t = escape(text)
    if not q.strip():
        return t
    i = text.lower().find(q.strip().lower())
    if i < 0:
        return t
    n = len(q.strip())
    tk = theme.tokens()
    return f"{escape(text[:i])}<span style='background:{theme.blend(tk.accent_wash, tk.chrome).name()}; border-radius:3px'>{escape(text[i : i + n])}</span>{escape(text[i + n :])}"


_ = (QPoint, QSize, QColor, QPainter)
