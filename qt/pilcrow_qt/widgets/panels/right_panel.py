"""The side panel (a port of web/src/components/RightPanel.tsx): a strip of tabs over the chosen tab's
content. Changes, Files, Terminal, Preview, Tasks, Timeline and Session, each built the first time it
is opened and kept across switches (the web unmounts most of them; here Changes and Timeline refresh
when shown again instead); every page is rebuilt when the session or its folder changes.

This module also holds the small pieces every tab shares: the toolbar row, the section with a heading
(``.ps``), the empty state, and :class:`Refresher`, which re-renders a tab on the session's runtime
changes without doing so more than twice a second while Claude streams.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QRect, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from ... import icons, theme
from ...store import store
from ..ui.bits import IconButton, hairline, label
from ..ui.signals import bind

TABS = [
    {"id": "changes", "label": "Changes", "icon": "git-compare", "key": "1"},
    {"id": "files", "label": "Files", "icon": "folder-tree", "key": "2"},
    {"id": "terminal", "label": "Terminal", "icon": "square-terminal", "key": "3"},
    {"id": "preview", "label": "Preview", "icon": "app-window", "key": "4"},
    {"id": "tasks", "label": "Tasks", "icon": "list-checks", "key": "5"},
    {"id": "history", "label": "Timeline", "icon": "history", "key": "6"},
    {"id": "info", "label": "Session", "icon": "info", "key": "7"},
]
NARROW_W = 760  # below this only the chosen tab keeps its name (panels.css container query)


# ---------------------------------------------------------------- shared bits


class Refresher(QObject):
    """Runs ``fn`` when the session's runtime changes (at most once per ``interval`` ms) and at once
    when the file system changes (``Store.bump_fs``)."""

    def __init__(self, owner: QWidget, sid: str, fn: Callable[[], None], interval: int = 500, fs: bool = True) -> None:
        super().__init__(owner)
        self._sid = sid
        self._fn = fn
        self._interval = interval
        self._last = 0.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.now)
        bind(store.rt_changed, owner, self._on_rt)
        if fs:
            bind(store.fs_changed, owner, lambda _r: self.now())

    def _on_rt(self, sid: str) -> None:
        if sid == self._sid:
            self.request()

    def request(self) -> None:
        """Run soon: right away if the last run was long enough ago, else once the interval is up."""
        wait = self._interval / 1000 - (time.monotonic() - self._last)
        if wait <= 0:
            self.now()
        elif not self._timer.isActive():
            self._timer.start(int(wait * 1000) + 1)

    def now(self) -> None:
        self._last = time.monotonic()
        self._timer.stop()
        self._fn()


class Toolbar(QWidget):
    """The one row of borderless controls at the top of a tab (``.panel-toolbar``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        row = QWidget()
        row.setMinimumHeight(42)
        self.box = QHBoxLayout(row)
        self.box.setContentsMargins(12, 6, 10, 6)
        self.box.setSpacing(4)
        outer.addWidget(row)
        outer.addWidget(hairline())

    def add(self, w: QWidget, stretch: int = 0) -> QWidget:
        self.box.addWidget(w, stretch)
        return w

    def stretch(self) -> None:
        self.box.addStretch(1)


class Section(QWidget):
    """A titled block of a scrolling tab (``.ps``): the heading row, then its content, a hairline under."""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 16, 0, 18)
        lay.setSpacing(10)
        head = QWidget()
        self.head = QHBoxLayout(head)
        self.head.setContentsMargins(0, 0, 0, 0)
        self.head.setSpacing(8)
        self.title = label(title, size="h")
        self.head.addWidget(self.title)
        self.head.addStretch(1)
        lay.addWidget(head)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(10)
        lay.addLayout(self.body)
        self.line = hairline()
        lay.addWidget(self.line)
        lay.setAlignment(self.line, Qt.AlignmentFlag.AlignBottom)

    def add(self, w: QWidget) -> QWidget:
        self.body.addWidget(w)
        return w

    def head_add(self, w: QWidget) -> QWidget:
        self.head.addWidget(w)
        return w

    def set_last(self, last: bool) -> None:
        self.line.setVisible(not last)


def empty_text(text: str) -> QLabel:
    """A section's quiet explanation when there is nothing to show (``.ps-empty``)."""
    lb = label(text, tone="faint", size="small", wrap=True)
    return lb


class EmptyState(QWidget):
    """An icon over a sentence and a hint, centred (``.empty``)."""

    def __init__(self, icon: str, text: str, hint: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._icon = icon
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 40, 28, 40)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.ico = QLabel()
        self.ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.ico)
        self.text = label(text, tone="faint", wrap=True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.text)
        self.hint = label(hint, tone="faint", size="small", wrap=True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setVisible(bool(hint))
        lay.addWidget(self.hint)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _paint(self) -> None:
        self.ico.setPixmap(icons.pixmap(self._icon, theme.tokens().text_3, 22))


def placeholder(text: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.addWidget(label(text, tone="faint", wrap=True))
    lay.addStretch(1)
    return w


# ---------------------------------------------------------------- tabs


class TabButton(QAbstractButton):
    """One tab: an icon, its name (hidden in a narrow panel unless chosen) and, for Tasks, a count."""

    def __init__(self, spec: Dict[str, str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.tab_id = spec["id"]
        self._on = False
        self._narrow = False
        self._count = 0
        self._hover = False
        self.setCheckable(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{spec['label']} (Alt+{spec['key']})")
        self.setAccessibleName(spec["label"])
        self.setFixedHeight(30)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        bind(theme.theme.changed, self, lambda _t: self.update())

    @property
    def shows_label(self) -> bool:
        return self._on or not self._narrow

    def set_on(self, on: bool) -> None:
        if on != self._on:
            self._on = on
            self.updateGeometry()
            self.update()

    def set_narrow(self, narrow: bool) -> None:
        if narrow != self._narrow:
            self._narrow = narrow
            self.updateGeometry()
            self.update()

    def set_count(self, n: int) -> None:
        if n != self._count:
            self._count = n
            self.updateGeometry()
            self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        if not self.shows_label:
            return QSize(34, 30)
        fm = QFontMetrics(theme.font_ui(theme.FS_UI, QFont.Weight.DemiBold if self._on else QFont.Weight.Normal))
        w = 8 + 15 + 6 + fm.horizontalAdvance(self.spec["label"]) + 8  # padding, icon, gap, name, padding
        if self._count:
            w += 6 + max(
                16, QFontMetrics(theme.font_ui(10.5, QFont.Weight.DemiBold)).horizontalAdvance(str(self._count)) + 8
            )
        return QSize(w, 30)

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hover and not self._on:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.blend(t.fill_1, t.chrome))
            p.drawRoundedRect(self.rect(), theme.R2, theme.R2)
        color = t.text if (self._on or self._hover) else t.text_3
        pm = icons.pixmap(self.spec["icon"], color, 15)
        if not self.shows_label:
            p.drawPixmap((self.width() - 15) // 2, (self.height() - 15) // 2, pm)
            return
        x = 8
        p.drawPixmap(x, (self.height() - 15) // 2, pm)
        x += 15 + 6
        font = theme.font_ui(theme.FS_UI, QFont.Weight.DemiBold if self._on else QFont.Weight.Normal)
        p.setFont(font)
        p.setPen(QColor(color))
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(self.spec["label"])
        p.drawText(
            QRect(x, 0, tw + 2, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.spec["label"],
        )
        x += tw + 6
        if self._count:
            cf = theme.font_ui(10.5, QFont.Weight.DemiBold)
            cw = max(16, QFontMetrics(cf).horizontalAdvance(str(self._count)) + 8)
            r = QRect(x, (self.height() - 16) // 2, cw, 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.blend(t.fill_3, t.chrome))
            p.drawRoundedRect(r, 8, 8)
            p.setFont(cf)
            p.setPen(QColor(t.text))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(self._count))


class TabStrip(QWidget):
    """The row of tabs with the ink that slides under the chosen one, and the close button."""

    def __init__(self, on_pick: Callable[[str], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(theme.HEADER_H)
        self._active = ""
        self._ink: Optional[QRect] = None
        self._anim: Optional[QVariantAnimation] = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 8, 0)
        lay.setSpacing(2)
        self.buttons: Dict[str, TabButton] = {}
        for spec in TABS:
            b = TabButton(spec)
            b.clicked.connect(lambda _c=False, tid=spec["id"]: on_pick(tid))
            lay.addWidget(b)
            self.buttons[spec["id"]] = b
        lay.addStretch(1)
        self.close = IconButton("x", "Close panel", 14)
        self.close.clicked.connect(lambda: store.set_ui(panel=False))
        lay.addWidget(self.close)
        bind(theme.theme.changed, self, lambda _t: self.update())

    @property
    def active(self) -> str:
        return self._active

    @property
    def narrow(self) -> bool:
        return self.width() < NARROW_W

    def set_active(self, tab: str) -> None:
        self._active = tab
        for tid, b in self.buttons.items():
            b.set_on(tid == tab)
        QTimer.singleShot(0, self._follow)

    def set_count(self, tab: str, n: int) -> None:
        b = self.buttons.get(tab)
        if b is not None:
            b.set_count(n)

    def resizeEvent(self, e) -> None:  # noqa: N802
        narrow = self.narrow
        for b in self.buttons.values():
            b.set_narrow(narrow)
        super().resizeEvent(e)
        QTimer.singleShot(0, self._follow)

    def _follow(self) -> None:
        b = self.buttons.get(self._active)
        if b is None:
            return
        try:
            target = QRect(b.x(), self.height() - 2, b.width(), 2)
        except RuntimeError:  # the strip was torn down before this deferred call ran
            return
        if self._ink is None or store.reduced_motion or not self.isVisible():
            self._ink = target
            self.update()
            return
        if self._anim is not None:
            self._anim.stop()
        anim = QVariantAnimation(self)
        anim.setStartValue(self._ink)
        anim.setEndValue(target)
        anim.setDuration(theme.T_PANEL)
        anim.setEasingCurve(theme.spring_snappy())
        anim.valueChanged.connect(self._set_ink)
        anim.start()
        self._anim = anim

    def _set_ink(self, r: QRect) -> None:
        self._ink = r
        self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setPen(QPen(theme.blend(t.hair, t.chrome), 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        if self._ink is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.text))
            p.drawRoundedRect(self._ink, 2, 2)


class RightPanel(QWidget):
    """The side panel: tabs, and one page per tab under them."""

    def __init__(self) -> None:
        super().__init__()
        self._session: Dict = {}
        self._sid = ""
        self._cwd = ""
        self._pages: Dict[str, QWidget] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.tabs = TabStrip(lambda tid: store.set_ui(panel_tab=tid))
        lay.addWidget(self.tabs)
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self.stack, 1)
        bind(store.ui_changed, self, lambda _u: self._show(store.ui.panel_tab))
        bind(store.rt_changed, self, self._on_rt)

    # ---- public
    def set_session(self, session: Dict) -> None:
        sid = str(session.get("id") or "")
        rt = store.rt_of(sid)
        cwd = (rt.cwd if rt else "") or str(session.get("cwd") or "")
        self._session = session
        if sid != self._sid or cwd != self._cwd:
            self._sid, self._cwd = sid, cwd
            for tid, page in list(self._pages.items()):
                self.stack.removeWidget(page)
                page.deleteLater()
                del self._pages[tid]
        else:
            for page in self._pages.values():
                set_session = getattr(page, "set_session", None)
                if set_session:
                    set_session(session)
        self._show(store.ui.panel_tab)
        self._count_running()

    def page(self, tab: str) -> Optional[QWidget]:
        return self._pages.get(tab)

    def reset(self) -> None:
        """Drop every tab's page; the chosen one is rebuilt at once (after a reconnect, or in tests)."""
        for tid, page in list(self._pages.items()):
            self.stack.removeWidget(page)
            page.setParent(None)
            page.deleteLater()
            del self._pages[tid]
        self._show(store.ui.panel_tab)

    # ---- internal
    def _show(self, tab: str) -> None:
        if not self._sid:
            return
        page = self._pages.get(tab)
        if page is None:
            page = self._make(tab)
            self._pages[tab] = page
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self.tabs.set_active(tab)

    def _make(self, tab: str) -> QWidget:
        sid, cwd, session = self._sid, self._cwd, self._session
        try:
            if tab == "changes":
                from .changes import ChangesPanel

                return ChangesPanel(sid, cwd)
            if tab == "tasks":
                from .tasks import TasksPanel

                return TasksPanel(sid, cwd)
            if tab == "history":
                from .history import HistoryPanel

                return HistoryPanel(sid, cwd)
            if tab == "info":
                from .info import InfoPanel

                return InfoPanel(sid, cwd)
            if tab == "files":
                from .files import FilesPanel

                w = FilesPanel(sid, cwd)
            elif tab == "terminal":
                from .terminal import TerminalPanel

                w = TerminalPanel(sid, cwd)
            elif tab == "preview":
                from .preview import PreviewPanel

                w = PreviewPanel(sid, cwd)
            else:
                return placeholder(f"No such tab: {tab}")
        except Exception as e:  # noqa: BLE001 — a tab that cannot load must not take the panel down
            print(f"[panel] {tab} is not available: {e!r}")
            return placeholder(f"The {tab} tab is not available in this build.")
        set_session = getattr(w, "set_session", None)
        if set_session:
            set_session(session)
        return w

    def _on_rt(self, sid: str) -> None:
        if sid == self._sid:
            self._count_running()

    def _count_running(self) -> None:
        rt = store.rt_of(self._sid)
        tasks: List[Dict] = list(rt.get("tasks") or []) if rt else []
        self.tabs.set_count("tasks", sum(1 for t in tasks if t.get("status") == "running"))
