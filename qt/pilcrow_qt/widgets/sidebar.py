"""The sidebar (a port of web/src/components/Sidebar.tsx).

Brand and the new-session row at the top, then the search row, the Control room and Inbox pages
under a hairline, the session list grouped by project with "Needs you" and "Pinned" sections above
it, and Settings, archive and appearance at the foot. Every measure is app.css's: 30px rows with
8px of padding and 6px corners, 24px group heads 14px below the rows above them, a 10px gutter.

The rows are painted by hand (QPainter) rather than styled: the hover and active fills, the icon
tints, the count pills and the key hint are all one paint each, which is what a machine without a
GPU can afford while a list of a hundred sessions scrolls.
"""

from __future__ import annotations

import time as _time
from html import escape
from typing import Dict, List, Optional

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
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
from .ui.bits import Mark
from .ui.menu import show_menu, show_menu_at
from .ui.signals import bind

Json = dict

ROW_H = 30  # .side-row min-height
ROW_META_H = 47  # a row with the project / branch line: 13px and 11px lines at 1.5, 1px apart, 5px padding
HEAD_H = 24  # .side-group-head, .side-section-label
GROUP_GAP = 14  # .side-group margin-top
PAD = 8  # the rows' horizontal padding
GUTTER = 10  # .side-top / .side-list / .side-foot horizontal padding


def _fill(name: str) -> QColor:
    """A translucent fill token as the eye sees it on the chrome."""
    t = theme.tokens()
    return theme.blend(getattr(t, name), t.chrome)


class Sidebar(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._q = ""
        self._collapsed: Dict[str, bool] = {}
        self._show_all: Dict[str, bool] = {}
        self._rows: Dict[str, SessionRow] = {}
        self._heads: Dict[str, GroupHead] = {}
        self._archived_view = store.ui.show_archived
        self._now_timer = QTimer(self)
        self._now_timer.setInterval(30_000)
        self._now_timer.timeout.connect(self._tick)
        self._now_timer.start()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- .side-top: brand, new session, search, pages
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(GUTTER, 12, GUTTER, 6)
        tl.setSpacing(2)
        brand = QWidget()
        brand.setFixedHeight(30)
        bl = QHBoxLayout(brand)
        bl.setContentsMargins(PAD, 0, PAD, 6)
        bl.setSpacing(9)
        bl.addWidget(Mark(15))
        self.brand_name = QLabel("Pilcrow")
        bl.addWidget(self.brand_name, 1)
        self.collapse_btn = IconBtn("panel-left-close", "Hide sidebar (Ctrl+B)", 15, 24)
        self.collapse_btn.clicked.connect(lambda: store.set_ui(sidebar=False))
        bl.addWidget(self.collapse_btn)
        tl.addWidget(brand)

        self.new_wrap = _HoverWrap()
        nl = QHBoxLayout(self.new_wrap)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(2)
        self.new_btn = NavButton("square-pen", "New session", kbd="Ctrl N", hover_fill="fill_2")
        self.new_btn.setToolTip("New session in the current folder (Ctrl+N) · Shift+click for options")
        self.new_btn.clicked.connect(self._new_session)
        nl.addWidget(self.new_btn, 1)
        self.new_opts = IconBtn(
            "sliders-horizontal", "New session with options… (Ctrl+Shift+N)", 13, 26, "text_4", True
        )
        self.new_opts.clicked.connect(lambda: store.set_dialog("newSession", {}))
        nl.addWidget(self.new_opts)
        self.new_wrap.revealed.connect(self.new_opts.set_shown)
        tl.addWidget(self.new_wrap)

        self.search_row = SearchRow()
        self.search = self.search_row.edit
        self.search.textChanged.connect(self._on_search)
        tl.addWidget(self.search_row)

        pages = _Pages()
        pl = QVBoxLayout(pages)
        pl.setContentsMargins(0, 4 + 6, 0, 0)  # margin-top 4px, padding-top 6px
        pl.setSpacing(1)
        self.page_overview = NavButton("layout-grid", "Control room")
        self.page_overview.setToolTip("Every session at a glance (Ctrl+Shift+O)")
        self.page_overview.clicked.connect(
            lambda: store.show_page("session" if store.page == "overview" else "overview")
        )
        self.page_inbox = NavButton("inbox", "Inbox")
        self.page_inbox.setToolTip("Results of scheduled sessions")
        self.page_inbox.clicked.connect(lambda: store.show_page("session" if store.page == "inbox" else "inbox"))
        pl.addWidget(self.page_overview)
        pl.addWidget(self.page_inbox)
        tl.addWidget(pages)
        root.addWidget(top)

        # ---- .side-list
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_host = QWidget()
        self.list_host.setObjectName("side-list")
        self.list = QVBoxLayout(self.list_host)
        self.list.setContentsMargins(GUTTER, 2, GUTTER, 14)
        self.list.setSpacing(0)
        self.list.addStretch(1)
        self.scroll.setWidget(self.list_host)
        root.addWidget(self.scroll, 1)

        # ---- .side-foot
        foot = QWidget()
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(GUTTER, 8, GUTTER, 10)
        fl.setSpacing(2)
        self.settings_btn = NavButton(
            "settings", "Settings", hover_fill="fill_2", tone="text_3", height=28, gap=8, fit=True
        )
        self.settings_btn.setToolTip("Settings (Ctrl+,)")
        self.settings_btn.clicked.connect(lambda: store.set_dialog("settings", True))
        fl.addWidget(self.settings_btn)
        fl.addStretch(1)
        self.archive_btn = IconBtn("archive", "Archived sessions", 15, 28)
        self.archive_btn.clicked.connect(lambda: store.set_ui(show_archived=not store.ui.show_archived))
        fl.addWidget(self.archive_btn)
        self.theme_btn = IconBtn("monitor", "Appearance", 15, 28)
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
        self._on_config()
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
        t = theme.tokens()
        cur = (store.config or {}).get("theme") or "system"
        nxt = "dark" if cur == "system" else "light" if cur == "dark" else "system"
        self.theme_btn.set_icon_name("monitor" if cur == "system" else "moon" if cur == "dark" else "sun")
        self.theme_btn.setToolTip(f"Appearance: {cur} — click for {nxt}")
        # .brand-name: 13.5px at 600, a touch tighter
        self.brand_name.setStyleSheet(f"color: {t.text}; font-size: 14px; font-weight: 600;")

    def _on_ui(self) -> None:
        self.setFixedWidth(store.ui.sidebar_w)
        archived = sum(1 for s in store.sessions if s.get("archived"))
        self.archive_btn.set_active(store.ui.show_archived)
        self.archive_btn.setToolTip(
            "Back to active sessions" if store.ui.show_archived else f"Archived sessions ({archived})"
        )
        if store.ui.show_archived != self._archived_view:
            self._archived_view = store.ui.show_archived
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
        # The conversation on screen never jumps into "Needs you": you are already looking at it.
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

        # Rebuild the layout, reusing the row and group-head widgets that are still listed (so their
        # state, their hover and their place stay).
        while self.list.count() > 1:
            item = self.list.takeAt(0)
            w = item.widget()
            if w is None:
                continue
            if isinstance(w, (SessionRow, GroupHead)):
                w.setParent(None)
            else:
                w.hide()  # gone from the screen now; the C++ object follows when the loop gets to it
                w.setParent(None)
                w.deleteLater()
        keep = {s.get("id") for s in lst}
        for sid in list(self._rows):
            if sid not in keep:
                row = self._rows.pop(sid)
                row.hide()
                row.setParent(None)
                row.deleteLater()
        keys = {k for k, _ in ordered}
        for key in list(self._heads):
            if key not in keys:
                head = self._heads.pop(key)
                head.hide()
                head.setParent(None)
                head.deleteLater()
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
            head = self._heads.get(key)
            if head is None:
                head = self._heads[key] = GroupHead(basename(key), key)
                head.toggled.connect(lambda k=key: self._toggle_group(k))
                head.add_clicked.connect(lambda k=key: self._new_in(k))
            head.set_collapsed(self._collapsed.get(key, False))
            add(head)
            if self._collapsed.get(key):
                continue
            limit = len(items) if (self._show_all.get(key) or q) else 6
            for s in items[:limit]:
                add(row_for(s))
            if len(items) > limit:
                more = MoreButton(f"Show {len(items) - limit} more")
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
        mods = getattr(self.sender(), "last_modifiers", Qt.KeyboardModifier.NoModifier)
        if mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier):
            store.set_dialog("newSession", {"cwd": path})
        else:
            store.quick_new_session(path)


# ---------------------------------------------------------------- the top and the foot


class NavButton(QAbstractButton):
    """``.side-new`` / ``.side-page`` / ``.side-foot-btn``: a row with an icon and a label, and at its
    end a count pill or a key hint that shows on hover."""

    def __init__(
        self,
        icon_name: str,
        text: str,
        kbd: str = "",
        hover_fill: str = "fill_1",
        tone: str = "text_2",
        height: int = 30,
        gap: int = 9,
        fit: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._icon = icon_name
        self._text = text
        self._kbd = kbd
        self._hover_fill = hover_fill
        self._tone = tone
        self._gap = gap
        self._fit = fit
        self._on = False
        self._count = 0
        self._count_tone = ""
        self.last_modifiers = Qt.KeyboardModifier.NoModifier
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Fixed if fit else QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(text)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(theme.font_ui(theme.FS_UI))
        # +2: a fitted button elides on Linux when the room equals the advance exactly (rounding)
        return QSize(PAD + 15 + self._gap + fm.horizontalAdvance(self._text) + 2 + PAD, self.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint() if self._fit else QSize(PAD + 15 + PAD, self.height())

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.last_modifiers = e.modifiers()
        super().mousePressEvent(e)

    def enterEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(e)

    def set_on(self, on: bool) -> None:
        self._on = on
        self.update()

    def set_count(self, n: int, tone: str = "") -> None:
        self._count, self._count_tone = n, tone
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        hover = self.underMouse()
        h = self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill = ""
        if self._on:
            fill = "fill_2"
        elif hover:
            fill = "fill_3" if (self.isDown() and self._hover_fill == "fill_2") else self._hover_fill
        if fill:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_fill(fill))
            p.drawRoundedRect(QRectF(self.rect()), theme.R2, theme.R2)
        colour = t.text if (self._on or hover) else getattr(t, self._tone)
        p.drawPixmap(PAD, (h - 15) // 2, icons.pixmap(self._icon, colour, 15))
        x = PAD + 15 + self._gap
        right = self.width() - PAD
        if self._count:
            f = theme.font_ui(theme.FS_MICRO)
            s = str(self._count)
            pw = max(18, QFontMetrics(f).horizontalAdvance(s) + 10)
            pr = QRectF(right - pw, (h - 18) / 2, pw, 18)
            if self._count_tone == "needs":
                bg, fg = QColor(t.accent), QColor(t.accent_ink)
            elif self._count_tone == "unread":
                bg, fg = _fill("fill_3"), QColor(t.text)
            else:
                bg, fg = _fill("fill_2"), QColor(t.text_3)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(pr, 9, 9)
            p.setFont(f)
            p.setPen(fg)
            p.drawText(pr, Qt.AlignmentFlag.AlignCenter, s)
            right = int(pr.left()) - self._gap
        elif self._kbd and hover:
            f = theme.font_ui(theme.FS_MICRO)
            kw = QFontMetrics(f).horizontalAdvance(self._kbd)
            p.setFont(f)
            p.setPen(QColor(t.text_4))
            p.drawText(
                QRectF(right - kw, 0, kw, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self._kbd
            )
            right -= kw + self._gap
        f = theme.font_ui(theme.FS_UI)
        p.setFont(f)
        p.setPen(QColor(colour))
        text = QFontMetrics(f).elidedText(self._text, Qt.TextElideMode.ElideRight, max(0, right - x))
        p.drawText(QRectF(x, 0, max(0, right - x), h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)


class IconBtn(QAbstractButton):
    """``.icon-btn`` (28px) and ``.icon-btn.sm`` (24px): a square with only an icon in it. With
    ``reveal`` it keeps its place but shows nothing until ``set_shown(True)`` (the buttons that
    appear when the row around them is hovered)."""

    def __init__(
        self,
        name: str,
        tooltip: str = "",
        icon_size: int = 15,
        size: int = 28,
        tone: str = "text_3",
        reveal: bool = False,
        parent: Optional[QWidget] = None,
        width: int = 0,
    ) -> None:
        super().__init__(parent)
        self._name, self._icon_size, self._tone, self._reveal = name, icon_size, tone, reveal
        self._shown = not reveal
        self._active = False
        self.last_modifiers = Qt.KeyboardModifier.NoModifier
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(width or size, size)
        if tooltip:
            self.setToolTip(tooltip)
            self.setAccessibleName(tooltip)
        bind(theme.theme.changed, self, lambda _t: self.update())

    @property
    def shown(self) -> bool:
        return self._shown

    def set_shown(self, on: bool) -> None:
        if self._shown != on:
            self._shown = on
            self.update()

    def set_active(self, on: bool) -> None:
        self._active = on
        self.update()

    def set_icon_name(self, name: str) -> None:
        self._name = name
        self.update()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.last_modifiers = e.modifiers()
        super().mousePressEvent(e)

    def enterEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        if self._reveal and not self._shown:
            return
        t = theme.tokens()
        hover = self.underMouse()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if hover or self._active:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_fill("fill_2"))
            r = min(theme.R2, self.width() / 2)
            p.drawRoundedRect(QRectF(self.rect()), r, r)
        colour = t.text if (hover or self._active) else getattr(t, self._tone)
        s = self._icon_size
        p.drawPixmap((self.width() - s) // 2, (self.height() - s) // 2, icons.pixmap(self._name, colour, s))


class _HoverWrap(QWidget):
    """``.side-new-wrap``: tells its options button when the pointer is over the pair."""

    revealed = Signal(bool)

    def enterEvent(self, e) -> None:  # noqa: N802
        self.revealed.emit(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.revealed.emit(False)
        super().leaveEvent(e)


class SearchRow(QWidget):
    """``.side-search``: a row like the ones above it with a bare field in it. The hover tints it a
    little, focus a little more; the x at the end clears it; Escape too."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(PAD, 0, PAD, 0)
        lay.setSpacing(9)
        self.icon = QLabel()
        self.icon.setFixedSize(15, 15)
        lay.addWidget(self.icon)
        self.edit = _SearchEdit()
        self.edit.setPlaceholderText("Search")
        self.edit.setFrame(False)
        self.edit.setAccessibleName("Search sessions")
        self.edit.installEventFilter(self)
        lay.addWidget(self.edit, 1)
        self.clear_btn = IconBtn("x", "Clear search", 12, 18)
        self.clear_btn.hide()
        self.clear_btn.clicked.connect(self.edit.clear)
        lay.addWidget(self.clear_btn)
        self.edit.textChanged.connect(lambda s: self.clear_btn.setVisible(bool(s)))
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.icon.setPixmap(icons.pixmap("search", t.text_3, 15))
        colour = t.text_2 if self.edit.hasFocus() else t.text_3
        self.edit.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; padding: 0; margin: 0; font-size: {theme.FS_UI}px; color: {colour}; }}"
        )
        pal = self.edit.palette()
        pal.setColor(pal.ColorRole.PlaceholderText, theme.qcolor(t.text_3))
        self.edit.setPalette(pal)
        self.update()

    def eventFilter(self, obj, e: QEvent) -> bool:  # noqa: N802
        if obj is self.edit and e.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self._paint()
        return False

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self.edit.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(e)

    def enterEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        fill = "fill_2" if self.edit.hasFocus() else "fill_1" if self.underMouse() else ""
        if not fill:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_fill(fill))
        p.drawRoundedRect(QRectF(self.rect()), theme.R2, theme.R2)


class _SearchEdit(QLineEdit):
    def event(self, e: QEvent) -> bool:
        # Escape clears the search here before the window's shortcut sees it.
        if e.type() == QEvent.Type.ShortcutOverride and e.key() == Qt.Key.Key_Escape:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape:
            self.clear()
            e.accept()
            return
        super().keyPressEvent(e)


class _Pages(QWidget):
    """``.side-pages``: the Control room and Inbox rows under a hairline (inset 0 1px 0 var(--hair))."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.fillRect(0, 4, self.width(), 1, theme.blend(t.hair, t.chrome))


# ---------------------------------------------------------------- the list


class SectionLabel(QLabel):
    """``.side-section-label``: "Needs you" / "Pinned", 14px under whatever is above it."""

    def __init__(self, text: str, tone: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._tone = tone
        self.setFixedHeight(GROUP_GAP + HEAD_H)
        self.setContentsMargins(PAD, GROUP_GAP, PAD, 0)
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.setStyleSheet(
            f"color: {t.accent_text if self._tone == 'accent' else t.text_3}; font-size: {theme.FS_MICRO}px; font-weight: 600;"
        )


class GroupHead(QWidget):
    """``.side-group-head``: the project's name, a chevron and a + that show while the head is hovered."""

    toggled = Signal()
    add_clicked = Signal()

    def __init__(self, label_text: str, path: str, collapsed: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.last_modifiers = Qt.KeyboardModifier.NoModifier
        self.setFixedHeight(GROUP_GAP + HEAD_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, GROUP_GAP, 0, 0)
        lay.setSpacing(0)
        self.btn = _GroupToggle(label_text, collapsed)
        self.btn.setToolTip(tildify(path, (store.info or {}).get("home")))
        self.btn.clicked.connect(self.toggled.emit)
        lay.addWidget(self.btn, 1)
        self.add = IconBtn("plus", f"New session in {label_text} · Shift+click for options", 13, 22, "text_3", True)
        self.add.clicked.connect(self._add)
        lay.addWidget(self.add)

    def _add(self) -> None:
        self.last_modifiers = self.add.last_modifiers
        self.add_clicked.emit()

    def set_collapsed(self, collapsed: bool) -> None:
        self.btn.set_collapsed(collapsed)

    def enterEvent(self, e) -> None:  # noqa: N802
        self.btn.set_revealed(True)
        self.add.set_shown(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.btn.set_revealed(False)
        self.add.set_shown(False)
        super().leaveEvent(e)


class _GroupToggle(QAbstractButton):
    """``.side-group-toggle``: 11px at 600, and the chevron that turns 90° as the group opens."""

    def __init__(self, text: str, collapsed: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setText(text)
        self._collapsed = collapsed
        self._angle = 0.0 if collapsed else 90.0
        self._revealed = False
        self._anim: Optional[QVariantAnimation] = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(HEAD_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(PAD * 2 + 16, HEAD_H)

    def set_revealed(self, on: bool) -> None:
        self._revealed = on
        self.update()

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        target = 0.0 if collapsed else 90.0
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if store.reduced_motion:
            self._angle = target
            self.update()
            return
        a = QVariantAnimation(self)
        a.setDuration(theme.T_MED)
        a.setStartValue(self._angle)
        a.setEndValue(target)
        a.setEasingCurve(theme.ease())
        a.valueChanged.connect(self._turn)
        a.start()
        self._anim = a

    def _turn(self, v) -> None:
        self._angle = float(v)
        self.update()

    def enterEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = t.text_2 if self.underMouse() else t.text_3
        f = theme.font_ui(theme.FS_MICRO, 600)
        fm = QFontMetrics(f)
        avail = self.width() - PAD * 2 - 12 - 4
        text = fm.elidedText(self.text(), Qt.TextElideMode.ElideRight, max(0, avail))
        p.setFont(f)
        p.setPen(QColor(colour))
        p.drawText(
            QRectF(PAD, 0, max(0, avail), self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            text,
        )
        if self._revealed:
            pm = icons.pixmap("chevron-right", colour, 12)
            cx = PAD + fm.horizontalAdvance(text) + 4 + 6
            p.save()
            p.translate(cx, self.height() / 2)
            p.rotate(self._angle)
            p.drawPixmap(-6, -6, pm)
            p.restore()


class MoreButton(QPushButton):
    """``.side-more``: "Show N more"."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(26)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent; text-align: left; padding: 0 {PAD}px; min-height: 26px; max-height: 26px; color: {t.text_3}; font-size: {theme.FS_SMALL}px; font-weight: 400; }}"
            f" QPushButton:hover {{ color: {t.text}; }}"
        )


class EmptyNote(QLabel):
    """``.side-empty``."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setContentsMargins(12, 28, 12, 28)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.setStyleSheet(f"color: {t.text_3}; font-size: {theme.FS_SMALL}px;")


class SessionRow(QWidget):
    """``.side-row``: the title, a project / branch line when there is one to tell, and at the end
    the status mark and the time, which give way to the ⋯ menu while the row is hovered."""

    def __init__(self, sid: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = sid
        self.s: Json = {}
        self._active = False
        self._unread = False
        self._hover = False
        self._menu_open = False
        self._show_project = False
        self._q = ""
        self.setProperty("data-sid", sid)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(ROW_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(PAD, 0, 4, 0)  # .side-row-main padding-left, .side-row-end margin-right
        lay.setSpacing(4)  # .side-row-main padding-right
        col = QVBoxLayout()
        col.setContentsMargins(0, 5, 0, 5)
        col.setSpacing(1)
        self.title = _ElideLabel()
        self.title.setFixedHeight(20)
        col.addWidget(self.title)
        self.meta = _MetaRow()
        self.meta.hide()
        col.addWidget(self.meta)
        lay.addLayout(col, 1)
        self.end = _RowEnd()
        lay.addWidget(self.end, 0, Qt.AlignmentFlag.AlignVCenter)
        self.glyph = self.end.glyph
        self.time = self.end.time
        self.menu_btn = self.end.menu
        self.menu_btn.clicked.connect(self._open_menu)
        self.rename_edit: Optional[QLineEdit] = None
        bind(theme.theme.changed, self, lambda _t: self._paint())

    # ---- data
    def update(self, s: Json, active: bool, unread: bool, show_project: bool, q: str) -> None:  # type: ignore[override]
        self.s = s
        self._active, self._unread, self._show_project = active, unread, show_project
        self._q = q
        title = str(s.get("title") or "")
        self.title.set_full(title, q)
        self.title.setToolTip(title)
        wt = s.get("worktree")
        branch = s.get("gitBranch")
        project = basename(str(s.get("project") or s.get("cwd") or "")) if show_project else ""
        if wt:
            icon, ref = "folder-git-2", str(wt.get("branch", "")).replace("pilcrow/", "", 1)
        elif branch and branch not in ("main", "master", "HEAD"):
            icon, ref = "git-branch", str(branch)
        else:
            icon, ref = "", ""
        has_meta = bool(project or ref)
        self.meta.set(project, icon, ref)
        self.meta.setVisible(has_meta)
        self.setFixedHeight(ROW_META_H if has_meta else ROW_H)
        status = str(s.get("status") or "")
        pending = int(s.get("pending") or 0)
        self.glyph.set_state(status, pending, unread and not active)
        quiet = not (
            status in ("running", "starting", "requires_action", "error") or pending or (unread and not active)
        )
        self.end.set_quiet(quiet)
        self.end.set_hover(self._hover or self._menu_open)
        self.refresh_time()
        self._paint()

    def refresh_time(self) -> None:
        self.time.setText(time_ago(self.s.get("lastModified"), _time.time() * 1000, True))

    def _paint(self) -> None:
        t = theme.tokens()
        lit = self._active or self._hover or self._menu_open or self._unread
        weight = 600 if self._unread else 400
        self.title.setStyleSheet(
            f"color: {t.text if lit else t.text_2}; font-size: {theme.FS_UI}px; font-weight: {weight};"
        )
        self.title.refresh()
        QWidget.update(self)

    def paintEvent(self, _e) -> None:  # noqa: N802
        fill = "fill_2" if self._active else "fill_1" if (self._hover or self._menu_open) else ""
        if not fill:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_fill(fill))
        p.drawRoundedRect(QRectF(self.rect()), theme.R2, theme.R2)

    # ---- interaction
    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.end.set_hover(True)
        self._paint()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        if not self._menu_open:
            self.end.set_hover(False)
        self._paint()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if (
            e.button() == Qt.MouseButton.LeftButton
            and self.rename_edit is None
            and self.rect().contains(e.position().toPoint())
        ):
            store.open_session(self.sid)
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        if self.rename_edit is not None:
            return
        self._menu_opened(show_menu_at(self._items(), e.globalPos(), self, 210))

    def _open_menu(self) -> None:
        self._menu_opened(show_menu(self._items(), self.menu_btn, 210, align_right=True))

    def _menu_opened(self, m) -> None:
        if m is None:
            return
        self._menu_open = True
        self.end.set_hover(True)
        self._paint()
        m.closed.connect(self._menu_closed)

    def _menu_closed(self) -> None:
        self._menu_open = False
        self._hover = self.underMouse()
        self.end.set_hover(self._hover)
        self._paint()

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
        edit.setFixedHeight(20)
        edit.setStyleSheet(f"font-size: {theme.FS_UI}px; padding: 0 2px;")  # .side-rename: 12.5px
        self.rename_edit = edit
        lay = self.title.parentWidget().layout() if self.title.parentWidget() else None
        col = self.layout().itemAt(0).layout()
        col.replaceWidget(self.title, edit)
        self.title.hide()
        edit.setFocus()
        edit.selectAll()

        def done() -> None:
            if self.rename_edit is None:
                return
            text = edit.text().strip()
            self.rename_edit = None
            col.replaceWidget(edit, self.title)
            edit.deleteLater()
            self.title.show()
            if text and text != self.s.get("title"):
                store.rename_session(self.sid, text)

        edit.editingFinished.connect(done)
        edit.returnPressed.connect(edit.clearFocus)
        _ = lay


class _ElideLabel(QLabel):
    """``.side-row-title``: elided to the room it has; the search match marked (``.side-match``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._full = ""
        self._q = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    def set_full(self, text: str, q: str = "") -> None:
        self._full, self._q = text, q
        self.refresh()

    def refresh(self) -> None:
        text = QFontMetrics(self.font()).elidedText(self._full, Qt.TextElideMode.ElideRight, max(0, self.width()))
        if self._q.strip():
            self.setTextFormat(Qt.TextFormat.RichText)
            self.setText(_highlight(text, self._q))
        else:
            self.setTextFormat(Qt.TextFormat.PlainText)
            self.setText(text)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self.refresh()


class _MetaRow(QWidget):
    """``.side-row-meta``: the project (in the sections) and the worktree or branch, 11px."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._icon = ""
        self.setFixedHeight(16)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.project = _ElideLabel()
        self.project.setFixedHeight(16)
        lay.addWidget(self.project, 1)
        self.wt_icon = QLabel()
        self.wt_icon.setFixedSize(11, 11)
        lay.addWidget(self.wt_icon)
        self.ref = _ElideLabel()
        self.ref.setFixedHeight(16)
        lay.addWidget(self.ref, 2)
        lay.setStretch(0, 0)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def set(self, project: str, icon: str, ref: str) -> None:
        self._icon = icon
        self.project.set_full(project)
        self.project.setVisible(bool(project))
        self.project.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.wt_icon.setVisible(bool(icon))
        self.ref.set_full(ref)
        self.ref.setVisible(bool(ref))
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        css = f"color: {t.text_3}; font-size: {theme.FS_MICRO}px;"
        self.project.setStyleSheet(css)
        self.ref.setStyleSheet(css)
        if self._icon:
            self.wt_icon.setPixmap(icons.pixmap(self._icon, t.text_3, 11))


class _RowEnd(QWidget):
    """``.side-row-end``: the status (glyph, time) and the ⋯ menu share one spot; the menu takes it
    while the row is hovered."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(HEAD_H)
        self.setMinimumWidth(30)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 4, 0)  # .side-row-status padding-right
        lay.setSpacing(6)
        lay.addStretch(1)
        self.glyph = _RowGlyph()
        lay.addWidget(self.glyph)
        self.time = QLabel()
        lay.addWidget(self.time)
        self.menu = IconBtn("ellipsis", "Session actions", 14, 24)
        self.menu.setParent(self)
        self.menu.hide()
        self._quiet = True
        self._hover = False
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()

    def _paint(self) -> None:
        t = theme.tokens()
        self.time.setStyleSheet(f"color: {t.text_4}; font-size: {theme.FS_MICRO}px;")

    def set_quiet(self, quiet: bool) -> None:
        self._quiet = quiet
        self._apply()

    def set_hover(self, on: bool) -> None:
        self._hover = on
        self._apply()

    def _apply(self) -> None:
        # .side-row:hover .side-row-status:not(:has(.glyph)) { opacity: 0 }: the time steps aside for the menu
        self.time.setVisible(self._quiet and not self._hover)
        self.glyph.setVisible(not self._quiet and not self._hover)
        self.menu.setVisible(self._hover)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self.menu.move(self.width() - 24, 0)


class _RowGlyph(QWidget):
    """``.glyph``: an 11px turning arc while working, the accent dot with its halo when a session
    needs you, red on error, a small neutral dot for unread activity."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._status = ""
        self._pending = 0
        self._unread = False
        self._t0 = _time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self.update)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_state(self, status: str, pending: int = 0, unread: bool = False) -> None:
        self._status, self._pending, self._unread = status, pending, unread
        live = status in ("running", "starting") and not store.reduced_motion
        if live and not self._timer.isActive():
            self._timer.start()
        elif not live:
            self._timer.stop()
        self.update()

    def hideEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    def showEvent(self, e) -> None:  # noqa: N802
        if self._status in ("running", "starting") and not store.reduced_motion:
            self._timer.start()
        super().showEvent(e)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(7, 7)
        p.setPen(Qt.PenStyle.NoPen)
        if self._pending or self._status == "requires_action":
            p.setBrush(theme.qcolor(t.accent_wash))
            p.drawEllipse(c, 7, 7)
            p.setBrush(QColor(t.accent))
            p.drawEllipse(c, 4, 4)
        elif self._status in ("running", "starting"):
            # 11px, 1.5px ring of fill-3 with the top quarter in text-2, turning once every 0.9s
            r = QRectF(2.25, 2.25, 9.5, 9.5)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(theme.qcolor(t.fill_3), 1.5))
            p.drawEllipse(r)
            ang = int(((_time.monotonic() - self._t0) % 0.9) / 0.9 * 360)
            p.setPen(QPen(QColor(t.text_2), 1.5))
            p.drawArc(r, (90 - ang) * 16, -90 * 16)
        elif self._status == "error":
            p.setBrush(QColor(t.err))
            p.drawEllipse(c, 4, 4)
        elif self._unread:
            p.setBrush(QColor(t.text_2))
            p.drawEllipse(c, 3, 3)


def _highlight(text: str, q: str) -> str:
    """``.side-match``: the matched run in the text colour at 600, underlined."""
    t = escape(text)
    if not q.strip():
        return t
    i = text.lower().find(q.strip().lower())
    if i < 0:
        return t
    n = len(q.strip())
    tk = theme.tokens()
    return (
        f"{escape(text[:i])}<span style='color:{tk.text}; font-weight:600; text-decoration: underline'>"
        f"{escape(text[i : i + n])}</span>{escape(text[i + n :])}"
    )
