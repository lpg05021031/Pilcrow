"""A compact folder chooser for popovers (a port of web/src/components/FolderPicker.tsx).

Recent projects first, then a small browser over ``fs.browse``. Used by the session header to
retarget a session that has not started yet.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPainter
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
from ..lib.fmt import basename, tildify
from ..store import store
from .dialogs.parts import well_bg
from .ui.bits import IconButton, Pill, button, hairline, label, paint_box
from .ui.signals import bind

Json = Dict[str, Any]


def _is_path(q: str) -> bool:
    """A typed absolute path: ``/…`` as on the web, and ``C:/…`` or ``C:\\…`` for Windows folders."""
    return q.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", q) is not None


class FolderPicker(QWidget):
    def __init__(self, value: str, on_pick: Callable[[str], None], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.value = value
        self.on_pick = on_pick
        self.browse: Optional[Json] = None
        self.browsing = False
        self.setObjectName("fpick")
        self.setFixedWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        search = QWidget()
        search.setObjectName("fpick-search")
        sl = QHBoxLayout(search)
        sl.setContentsMargins(12, 0, 10, 0)
        sl.setSpacing(8)
        self.search_ico = QLabel()
        sl.addWidget(self.search_ico)
        self.q = QLineEdit()
        self.q.setProperty("quiet", "true")
        self.q.setPlaceholderText("Recent folders, or type a path")
        self.q.setAccessibleName("Recent folders, or type a path")
        self.q.textChanged.connect(lambda _t: self._render())
        self.q.returnPressed.connect(self._enter)
        sl.addWidget(self.q, 1)
        search.setFixedHeight(35)
        lay.addWidget(search)
        self.search_box = search

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        lay.addLayout(self.body)
        bind(theme.theme.changed, self, lambda _t: self._paint())
        self._paint()
        self._render()
        self.q.setFocus()

    def _paint(self) -> None:
        t = theme.tokens()
        self.search_ico.setPixmap(icons.pixmap("search", t.text_3, 13))

    # ---- data
    def _recents(self) -> List[str]:
        seen: List[str] = []
        for p in (store.config or {}).get("recentProjects") or []:
            if p and p not in seen:
                seen.append(str(p))
        for s in store.sessions:
            if len(seen) >= 12:
                break
            p = str(s.get("project") or s.get("cwd") or "")
            if p and not s.get("archived") and p not in seen:
                seen.append(p)
        needle = self.q.text().strip().lower()
        return [p for p in seen if needle in p.lower()] if needle else seen[:8]

    def _enter(self) -> None:
        q = self.q.text().strip()
        if _is_path(q):
            self.on_pick(q)

    def _nav(self, d: str) -> None:
        def ok(r: Any) -> None:
            if isinstance(r, dict):
                self.browse = r
                self._render()

        store.call("fs.browse", {"dir": d}, quiet=True).then(ok).catch(lambda _e: None)

    def _start_browsing(self) -> None:
        self.browsing = True
        if self.browse is None:
            self._nav(self.value or (store.info or {}).get("home") or "/")
        self._render()

    # ---- rendering
    def _clear(self) -> None:
        while self.body.count():
            item = self.body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _render(self) -> None:
        self._clear()
        t = theme.tokens()
        home = (store.info or {}).get("home")
        if not self.browsing:
            lst = _list_area()
            recents = self._recents()
            for p in recents:
                row = _Row(on=p == self.value)
                row.add_icon("folder", t.text_3, 14)
                row.add_text(basename(p), t.text, theme.FS_UI)
                path = row.add_text(tildify(p, home), t.text_4, 11, mono=True, stretch=1)
                path.setMaximumWidth(160)
                if p == self.value:
                    row.add_icon("check", t.accent_text, 13)
                row.setToolTip(p)
                row.setAccessibleName(basename(p))
                row.clicked.connect(lambda _c=False, x=p: self.on_pick(x))
                lst.lay.addWidget(row)
            q = self.q.text().strip()
            if not recents:
                note = label(
                    "No recent folder matches. Type a full path and press Enter." if q else "No recent folders yet.",
                    tone="faint",
                    size="small",
                    wrap=True,
                )
                note.setContentsMargins(10, 14, 10, 14)
                lst.lay.addWidget(note)
            if _is_path(q) and q not in recents:
                row = _Row()
                row.add_icon("corner-left-up", t.text_3, 14)
                row.add_text(f"Use {q}", t.text, theme.FS_UI, stretch=1)
                row.setAccessibleName(f"Use {q}")
                row.clicked.connect(lambda _c=False, x=q: self.on_pick(x))
                lst.lay.addWidget(row)
            lst.lay.addStretch(1)
            self.body.addWidget(lst)
            foot = _Foot()
            foot.lay.addWidget(button("Browse…", kind="ghost", size="sm", on_click=self._start_browsing))
            foot.lay.addStretch(1)
            self.body.addWidget(foot)
            return
        b = self.browse
        if b is None:
            return
        browser = Browser(self._nav, self.on_pick, flat=True)
        browser.show_listing(b)
        self.body.addWidget(browser)
        foot = _Foot()
        foot.lay.addWidget(button("Recent", kind="ghost", size="sm", on_click=self._back_to_recent))
        foot.lay.addStretch(1)
        d = str(b.get("dir") or "")
        foot.lay.addWidget(
            button(f"Use {basename(d) or d}", kind="primary", size="sm", on_click=lambda: self.on_pick(d))
        )
        self.body.addWidget(foot)

    def _back_to_recent(self) -> None:
        self.browsing = False
        self._render()


class Browser(QFrame):
    """``.browser``: a small folder browser over ``fs.browse``. A head with home and up, the path in the
    code font and a ``git`` badge; a 30px row per subfolder (click to enter, double-click to pick).
    ``nav(dir)`` is asked for a listing; ``show_listing`` draws one. ``flat`` drops the corners and ring
    for the picker, where it fills the popover."""

    def __init__(
        self,
        nav: Callable[[str], None],
        pick: Callable[[str], None],
        flat: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._nav, self._pick = nav, pick
        self.listing: Optional[Json] = None
        self.setObjectName("browser")
        self._flat = flat
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = QWidget()
        self.head.setObjectName("browser-head")
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(6, 6, 10, 6)
        hl.setSpacing(4)
        self.home_btn = IconButton("home", "Home", 13)
        self.home_btn.setFixedSize(24, 24)
        self.home_btn.clicked.connect(lambda: self._nav(str((store.info or {}).get("home") or "/")))
        hl.addWidget(self.home_btn)
        self.up_btn = IconButton("corner-left-up", "Up one level", 13)
        self.up_btn.setFixedSize(24, 24)
        self.up_btn.clicked.connect(self._up)
        hl.addWidget(self.up_btn)
        self.display = label("", tone="muted", size="small", mono=True)
        self.display.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        hl.addWidget(self.display, 1)
        self.badge = Pill("git", bg=lambda t: theme.over(theme.qcolor(t.fill_2), well_bg(t)), radius=9)
        self.badge.setObjectName("badge")
        self.badge.setContentsMargins(7, 0, 7, 0)
        self.badge.setFont(theme.font_ui(11, 600))
        theme.tint(self.badge, fg="text_2")
        self.badge.setFixedHeight(19)
        self.badge.hide()
        hl.addWidget(self.badge)
        lay.addWidget(self.head)
        lay.addWidget(hairline(over=well_bg))
        self.list = _ListArea(220 if not flat else 240)
        self.list.lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.list)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        bg = well_bg(t)
        p = QPainter(self)
        if self._flat:
            p.fillRect(self.rect(), bg)
        else:
            paint_box(p, QRectF(self.rect()), bg, theme.over(theme.qcolor(t.hair), bg), theme.R3)

    def _up(self) -> None:
        if self.listing and self.listing.get("parent"):
            self._nav(str(self.listing["parent"]))

    def show_listing(self, b: Json) -> None:
        self.listing = b
        t = theme.tokens()
        home = (store.info or {}).get("home")
        self.up_btn.setEnabled(bool(b.get("parent")))
        self.display.setText(str(b.get("display") or ""))
        _ = home
        self.badge.setVisible(bool(b.get("repo")))
        lay = self.list.lay
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        if b.get("error"):
            note = label(str(b["error"]), tone="faint", size="small", wrap=True)
            note.setContentsMargins(10, 10, 10, 10)
            lay.addWidget(note)
        for d in b.get("dirs") or []:
            row = _Row(height=30)
            row.add_icon("folder-git-2" if d.get("repo") else "folder", t.text_3, 14)
            row.add_text(str(d.get("name") or ""), t.text_2, theme.FS_UI, stretch=1)
            row.add_icon("chevron-right", t.text_3, 13)
            row.setAccessibleName(str(d.get("name") or ""))
            row.clicked.connect(lambda _c=False, x=str(d.get("path") or ""): self._nav(x))
            row.double_clicked = lambda x=str(d.get("path") or ""): self._pick(x)
            lay.addWidget(row)
        if not (b.get("dirs") or []) and not b.get("error"):
            note = label("No subfolders", tone="faint", size="small")
            note.setContentsMargins(10, 10, 10, 10)
            lay.addWidget(note)
        lay.addStretch(1)
        self.updateGeometry()


class _ListArea(QScrollArea):
    def __init__(self, max_h: int) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMaximumHeight(max_h)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        host = QWidget()
        self.lay = QVBoxLayout(host)
        self.lay.setContentsMargins(6, 6, 6, 6)
        self.lay.setSpacing(0)
        self.setWidget(host)

    def sizeHint(self):  # noqa: N802
        w = self.widget()
        h = w.sizeHint().height() if w is not None else 0
        return QSize(super().sizeHint().width(), min(self.maximumHeight(), h))


def _list_area(max_h: int = 300) -> _ListArea:
    return _ListArea(max_h)


class _Foot(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("fpick-foot")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(8, 7, 8, 6)  # a pixel for the hairline along the top
        self.lay.setSpacing(6)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        QPainter(self).fillRect(0, 0, self.width(), 1, theme.blend(t.hair, t.raised))


class _Row(QPushButton):
    """A full-width list row (``.fpick-row`` / ``.browser-item``) built from icons and labels."""

    def __init__(self, on: bool = False, height: int = 34) -> None:
        super().__init__()
        self.double_clicked: Optional[Callable[[], None]] = None
        self.setFlat(True)
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._on = on
        bind(theme.theme.changed, self, lambda _t: self.update())
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(8, 0, 8, 0)
        self.lay.setSpacing(10)

    def add_icon(self, name: str, color: str, size: int) -> QLabel:
        lb = QLabel()
        lb.setPixmap(icons.pixmap(name, color, size))
        self.lay.addWidget(lb)
        return lb

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        if self.underMouse() and self.isEnabled():
            bg = theme.blend(t.fill_2, t.raised)
        elif self._on:
            bg = theme.blend(t.fill_1, t.raised)
        else:
            return
        p = QPainter(self)
        paint_box(p, QRectF(self.rect()), bg, None, theme.R2)

    def add_text(self, text: str, color: str, size: float, mono: bool = False, stretch: int = 0) -> QLabel:
        lb = QLabel(text)
        lb.setFont(theme.font_mono(size) if mono else theme.font_ui(size))
        theme.tint(lb, fg=color)
        if stretch:
            lb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lay.addWidget(lb, stretch)
        return lb

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if self.double_clicked is not None:
            self.double_clicked()
        else:
            super().mouseDoubleClickEvent(e)
