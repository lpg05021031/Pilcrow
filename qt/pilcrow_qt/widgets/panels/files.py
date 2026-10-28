"""The Files panel (a port of web/src/components/panels/FilesPanel.tsx).

A file tree on the left (``fs.list`` per folder, expanded folders remembered per project, dotfiles
hidden by default, a filter box, new file / new folder, the "changed in N sessions" marks from
``project.fileHistory``) and the open files as tabs on the right: source in :class:`CodeEditor`,
Markdown read as a page, HTML read with Qt's basic HTML rendering (there is no web engine here) and
a button to open it in the browser, images from ``fs.read``'s dataUrl, and plain states for binary
or oversized files. Ctrl+S writes through ``fs.write`` with the mtime the file was read at, and a
conflict asks before overwriting. ``store.file_open`` opens a path at a line.
"""

from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QByteArray, QPoint, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QScrollArea,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ... import icons, theme
from ...lib.fmt import basename, bytes_, dirname, err_text, plural, rel_path, time_ago
from ...markdown import render_html
from ...store import ConfirmSpec, store
from ..ui.bits import IconButton, Row, button, hairline, label
from ..ui.menu import show_menu, show_menu_at
from ..ui.signals import bind
from .editor import CodeEditor
from .right_panel import EmptyState, Hairline, Seg, Toolbar, elide, icon_btn, lbl

Json = Dict[str, Any]

TREE_W = 212
ROW_H = 26
INDENT = 16  # one step of nesting; files sit after the space a folder's chevron takes


@dataclass
class Tab:
    path: str
    doc: str = ""
    saved: str = ""
    mtime: Optional[float] = None
    binary: bool = False
    data_url: Optional[str] = None
    too_large: bool = False
    size: int = 0
    error: Optional[str] = None
    loading: bool = False
    view: Optional[str] = None  # 'preview' | 'edit' | 'split' for documents

    @property
    def dirty(self) -> bool:
        return self.doc != self.saved


@dataclass
class FilesState:
    tabs: List[Tab] = field(default_factory=list)
    active: Optional[str] = None
    expanded: Dict[str, bool] = field(default_factory=dict)
    show_tree: bool = True
    show_hidden: bool = False


_per_cwd: Dict[str, FilesState] = {}
_dir_cache: Dict[str, List[Json]] = {}
_history_cache: Dict[str, Dict[str, Any]] = {}  # cwd -> {"at": s, "data": map}


def doc_kind(path: str) -> Optional[str]:
    """Files that read better as a page than as source: 'md', 'html' or None."""
    if re.search(r"\.(md|markdown|mdx)$", path, re.I):
        return "md"
    if re.search(r"\.(html?|xhtml)$", path, re.I):
        return "html"
    return None


def doc_view(t: Tab) -> str:
    """A document opens as a page; a click on Edit (or a file that is not a document) shows the source."""
    return t.view or ("preview" if doc_kind(t.path) else "edit")


def _abs(path: str, cwd: str) -> str:
    if not path or path.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[/\\]", path) or not cwd:
        return path
    return f"{cwd.rstrip('/')}/{path}"


def _rel_key(path: str, cwd: str) -> str:
    return path[len(cwd) + 1 :] if path.startswith(cwd + "/") else path


# ---------------------------------------------------------------- the tree


@dataclass
class TreeRow:
    path: str
    name: str
    depth: int
    kind: str  # 'dir' | 'file' | 'err' | 'loading'
    open: bool = False
    size: int = 0
    touches: int = 0


class FileTree(QAbstractScrollArea):
    """The folder tree, painted row by row: chevrons and folder icons for folders, file icons and the
    session marks for files. ``opened`` fires with a file's path; folders expand on click."""

    opened = Signal(str)
    touches_clicked = Signal(str, QPoint)  # path, global position

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setAccessibleName("File tree")
        self._rows: List[TreeRow] = []
        self._hover = -1
        self._cursor = -1
        self.active: Optional[str] = None
        self.on_toggle: Optional[Callable[[str], None]] = None
        self.setFont(theme.font_ui(12.5))
        bind(theme.theme.changed, self, lambda _t: self.viewport().update())

    # ---- data
    def set_rows(self, rows: List[TreeRow]) -> None:
        self._rows = rows
        self._hover = -1
        self._cursor = min(self._cursor, len(rows) - 1)
        self.verticalScrollBar().setRange(0, max(0, len(rows) * ROW_H + 24 - self.viewport().height()))
        self.verticalScrollBar().setPageStep(self.viewport().height())
        self.verticalScrollBar().setSingleStep(ROW_H)
        self.viewport().update()

    def rows(self) -> List[TreeRow]:
        return list(self._rows)

    def row_at(self, y: int) -> int:
        i = (y + self.verticalScrollBar().value() - 6) // ROW_H
        return i if 0 <= i < len(self._rows) else -1

    def _indent(self, r: TreeRow) -> int:
        return 6 + r.depth * INDENT if r.kind == "dir" else 24 + r.depth * INDENT

    def _touch_rect(self, r: TreeRow, top: int) -> QRect:
        fm = QFontMetrics(theme.font_ui(10))
        w = 11 + 3 + fm.horizontalAdvance(str(r.touches)) + 10
        return QRect(self.viewport().width() - 6 - w, top + 4, w, 18)

    # ---- painting
    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self.set_rows(self._rows)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        vp = self.viewport()
        p = QPainter(vp)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off = self.verticalScrollBar().value()
        fm = QFontMetrics(self.font())
        p.setFont(self.font())
        first = max(0, off // ROW_H)
        last = min(len(self._rows), (off + vp.height()) // ROW_H + 2)
        for i in range(first, last):
            r = self._rows[i]
            top = 6 + i * ROW_H - off
            rect = QRect(6, top, vp.width() - 12, ROW_H)
            x = self._indent(r)
            if r.kind == "err":
                p.setPen(theme.qcolor(t.err))
                p.setFont(theme.font_ui(11.5))
                p.drawText(
                    QRect(x, top, vp.width() - x - 6, ROW_H),
                    Qt.AlignmentFlag.AlignVCenter,
                    fm.elidedText(r.name, Qt.TextElideMode.ElideRight, vp.width() - x - 8),
                )
                p.setFont(self.font())
                continue
            if r.kind == "loading":
                p.setPen(theme.qcolor(t.text_3))
                p.drawText(QRect(x, top, 40, ROW_H), Qt.AlignmentFlag.AlignVCenter, "…")
                continue
            on = r.kind == "file" and r.path == self.active
            if on or i == self._hover or i == self._cursor:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.qcolor(t.fill_2 if on else t.fill_1))
                p.drawRoundedRect(rect, theme.R2, theme.R2)
            fg = t.text if (on or i == self._hover) else t.text_2
            if r.kind == "dir":
                p.drawPixmap(x, top + 7, icons.pixmap("chevron-down" if r.open else "chevron-right", t.text_4, 12))
                x += 12 + 6
                p.drawPixmap(x, top + 6, icons.pixmap("folder-open" if r.open else "folder", t.text_3, 13))
            else:
                p.drawPixmap(x, top + 6, icons.pixmap("file", t.text_3, 13))
            x += 13 + 6
            right = vp.width() - 12
            if r.touches:
                tr = self._touch_rect(r, top)
                right = tr.left() - 4
                p.setPen(Qt.PenStyle.NoPen)
                if i == self._hover:
                    p.setBrush(theme.qcolor(t.fill_2))
                    p.drawRoundedRect(tr, 9, 9)
                p.drawPixmap(tr.left() + 5, tr.top() + 3, icons.pixmap("history", t.text_4, 11))
                p.setPen(theme.qcolor(t.text_4))
                p.setFont(theme.font_ui(10))
                p.drawText(
                    QRect(tr.left() + 5 + 11 + 3, tr.top(), tr.width(), tr.height()),
                    Qt.AlignmentFlag.AlignVCenter,
                    str(r.touches),
                )
                p.setFont(self.font())
            p.setPen(theme.qcolor(fg))
            p.drawText(
                QRect(x, top, max(10, right - x), ROW_H),
                Qt.AlignmentFlag.AlignVCenter,
                fm.elidedText(r.name, Qt.TextElideMode.ElideRight, max(10, right - x)),
            )
        p.end()

    # ---- interaction
    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        i = self.row_at(int(e.position().y()))
        if i != self._hover:
            self._hover = i
            self.viewport().update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = -1
        self.viewport().update()
        super().leaveEvent(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        i = self.row_at(int(e.position().y()))
        if i < 0:
            return
        self._cursor = i
        self.activate(i, int(e.position().x()), e.globalPosition().toPoint())

    def activate(self, i: int, x: Optional[int] = None, gpos: Optional[QPoint] = None) -> None:
        r = self._rows[i]
        if r.kind == "dir" and self.on_toggle:
            self.on_toggle(r.path)
        elif r.kind == "file":
            if r.touches and x is not None and gpos is not None:
                top = 6 + i * ROW_H - self.verticalScrollBar().value()
                if self._touch_rect(r, top).contains(x, top + 10):
                    self.touches_clicked.emit(r.path, gpos)
                    return
            self.opened.emit(r.path)
        self.viewport().update()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        k = e.key()
        n = len(self._rows)
        if not n:
            return super().keyPressEvent(e)
        if k == Qt.Key.Key_Down:
            self._cursor = min(n - 1, self._cursor + 1)
        elif k == Qt.Key.Key_Up:
            self._cursor = max(0, self._cursor - 1)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self._cursor >= 0:
            self.activate(self._cursor)
            return
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_Left) and self._cursor >= 0:
            r = self._rows[self._cursor]
            if r.kind == "dir" and r.open == (k == Qt.Key.Key_Left) and self.on_toggle:
                self.on_toggle(r.path)
            return
        else:
            return super().keyPressEvent(e)
        self.verticalScrollBar().setValue(
            min(
                max(self.verticalScrollBar().value(), (self._cursor + 1) * ROW_H + 6 - self.viewport().height()),
                self._cursor * ROW_H,
            )
        )
        self.viewport().update()


# ---------------------------------------------------------------- file tabs


class FTab(QWidget):
    """One open-file tab: the name in the mono face, a dot when unsaved, × to close."""

    activated = Signal(str)
    closed = Signal(str)

    def __init__(self, path: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.path = path
        self.on = False
        self.dirty = False
        self._hover = False
        self._x_hover = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(path)
        self.setAccessibleName(basename(path))
        self.setFixedHeight(30)
        self._font = theme.font_mono(12)
        bind(theme.theme.changed, self, lambda _t: self.update())

    def set_state(self, on: bool, dirty: bool) -> None:
        if (on, dirty) != (self.on, self.dirty):
            self.on, self.dirty = on, dirty
            self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = QFontMetrics(self._font)
        return QSize(12 + fm.horizontalAdvance(basename(self.path)) + (6 + 7 if self.dirty else 0) + 4 + 20 + 5, 30)

    def _x_rect(self) -> QRect:
        return QRect(self.width() - 25, 5, 20, 20)

    def paintEvent(self, _e) -> None:  # noqa: N802
        t = theme.tokens()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.on:
            p.setPen(theme.blend(t.hair, t.chrome))
            p.setBrush(theme.qcolor(t.canvas))
            p.drawRoundedRect(self.rect().adjusted(0, 0, -1, 2), theme.R2, theme.R2)
        p.setPen(theme.qcolor(t.text if (self.on or self._hover) else t.text_3))
        p.setFont(self._font)
        fm = QFontMetrics(self._font)
        name = basename(self.path)
        p.drawText(QRect(12, 0, fm.horizontalAdvance(name) + 2, 30), Qt.AlignmentFlag.AlignVCenter, name)
        x = 12 + fm.horizontalAdvance(name) + 6
        if self.dirty:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.qcolor(t.text_2))
            p.drawEllipse(QRect(x, 12, 7, 7))
        if self.on or self._hover:
            xr = self._x_rect()
            if self._x_hover:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.qcolor(t.fill_2))
                p.drawRoundedRect(xr, 4, 4)
            p.drawPixmap(xr.left() + 4, xr.top() + 4, icons.pixmap("x", t.text if self._x_hover else t.text_3, 11))

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = self._x_hover = False
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        xh = self._x_rect().contains(e.position().toPoint())
        if xh != self._x_hover:
            self._x_hover = xh
            self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.MiddleButton or (
            e.button() == Qt.MouseButton.LeftButton and self._x_rect().contains(e.position().toPoint())
        ):
            self.closed.emit(self.path)
        elif e.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.path)


class FileTabs(QWidget):
    """The strip of open files above the editor (``.ftabs``)."""

    activated = Signal(str)
    closed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("chrome")
        self.box = QHBoxLayout(self)
        self.box.setContentsMargins(6, 6, 6, 0)
        self.box.setSpacing(2)
        self.box.addStretch(1)
        self._tabs: Dict[str, FTab] = {}
        self.setFixedHeight(36)

    def sync(self, tabs: List[Tab], active: Optional[str]) -> None:
        paths = [t.path for t in tabs]
        for path in list(self._tabs):
            if path not in paths:
                w = self._tabs.pop(path)
                self.box.removeWidget(w)
                w.deleteLater()
        for i, t in enumerate(tabs):
            w = self._tabs.get(t.path)
            if w is None:
                w = FTab(t.path)
                w.activated.connect(self.activated)
                w.closed.connect(self.closed)
                self._tabs[t.path] = w
                self.box.insertWidget(i, w)
            w.set_state(t.path == active, t.dirty)
            w.updateGeometry()
        self.setVisible(bool(tabs))

    def tab(self, path: str) -> Optional[FTab]:
        return self._tabs.get(path)


# ---------------------------------------------------------------- document views


def _browser_qss(t: theme.Tokens, size: float) -> str:
    """A page surface on the canvas; the font is in the rule since the app's ``*`` rule beats setFont."""
    ui = theme._UI_FAMILY or "sans-serif"
    return f'QTextBrowser {{ background: {t.canvas}; color: {t.text}; border: none; padding: 0; font-family: "{ui}"; font-size: {size}px; }}'


def _top(w: QWidget) -> QWidget:
    """A block kept at the top of its page, the way ``.empty`` sits in the ``.files-editor`` column."""
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(w)
    lay.addStretch(1)
    return host


def empty_state(icon: str, text: str, hint: str = "", action: Optional[QWidget] = None) -> QWidget:
    """The ``.empty`` block at the top of a page: an icon, a line, a fainter hint, maybe a button."""
    e = EmptyState(icon, text, hint)
    if action is not None:
        e.layout().addWidget(action, 0, Qt.AlignmentFlag.AlignHCenter)
    return _top(e)


class MarkdownView(QTextBrowser):
    """A Markdown document as a page (``.doc-preview``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._on_link)
        self.document().setDocumentMargin(24)
        self.on_open_path: Optional[Callable[[str], None]] = None
        self._base = ""
        self._text = ""
        self._apply_theme()
        bind(theme.theme.changed, self, lambda _t: self._apply_theme())

    def _apply_theme(self) -> None:
        t = theme.tokens()
        self.setFont(theme.font_ui(theme.FS_BODY))
        self.setStyleSheet(_browser_qss(t, theme.FS_BODY))
        if self._text:
            self.set_markdown(self._text, self._base)

    def set_markdown(self, text: str, base_dir: str) -> None:
        self._text, self._base = text, base_dir
        t = theme.tokens()
        self.document().setBaseUrl(QUrl.fromLocalFile(base_dir.rstrip("/") + "/"))
        if text.strip():
            self.setHtml(f'<div style="line-height:170%">{render_html(text, base_dir=base_dir)}</div>')
        else:
            self.setHtml(f'<div style="color:{t.text_3}">This document is empty.</div>')

    def _on_link(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
        elif self.on_open_path:
            p = url.toLocalFile() if url.isLocalFile() else url.toString()
            self.on_open_path(
                p if p.startswith("/") or re.match(r"^[A-Za-z]:/", p) else f"{self._base.rstrip('/')}/{p}"
            )


class HtmlPreview(QWidget):
    """An HTML file without a web engine: Qt's own HTML rendering of the saved file (no scripts or
    stylesheets) under a bar with reload and "open in your browser"."""

    def __init__(self, path: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.path = path
        self._saved = ""
        self.on_save: Optional[Callable[[], None]] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        bar = Row(4, (12, 0, 6, 0))
        bar.setFixedHeight(30)
        bar.setObjectName("chrome")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.name = label(basename(path), tone="faint", size="micro", mono=True)
        bar.add(self.name, 1)
        self.note = label("Basic rendering · scripts and stylesheets are not applied", tone="faint", size="micro")
        bar.add(self.note)
        self.save_btn = button(
            "Save to refresh",
            size="sm",
            icon="save",
            on_click=lambda: self.on_save and self.on_save(),
            tooltip="Save the file to refresh the preview (Ctrl+S)",
        )
        self.save_btn.hide()
        bar.add(self.save_btn)
        self.reload_btn = IconButton("rotate-cw", "Reload", 12)
        self.reload_btn.clicked.connect(self.reload)
        bar.add(self.reload_btn)
        self.open_btn = IconButton("external-link", "Open in your browser", 12)
        self.open_btn.clicked.connect(lambda: store.call("shell.openPath", {"path": self.path}))
        bar.add(self.open_btn)
        lay.addWidget(bar)
        lay.addWidget(hairline())
        self.view = QTextBrowser()
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setOpenExternalLinks(True)
        self.view.document().setDocumentMargin(16)
        lay.addWidget(self.view, 1)
        self._apply_theme()
        bind(theme.theme.changed, self, lambda _t: self._apply_theme())

    def _apply_theme(self) -> None:
        self.view.setStyleSheet(_browser_qss(theme.tokens(), theme.FS_BODY))

    def set_saved(self, saved: str, dirty: bool) -> None:
        self.save_btn.setVisible(dirty)
        if saved != self._saved:
            self._saved = saved
            self.reload()

    def reload(self) -> None:
        self.view.document().setBaseUrl(QUrl.fromLocalFile(dirname(self.path).rstrip("/") + "/"))
        self.view.setHtml(self._saved)


class DocArea(QWidget):
    """The editor and, for documents, the page beside it or instead of it (``.doc-area``)."""

    def __init__(self, tab: Tab, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.path = tab.path
        self.cwd = cwd
        self.kind = doc_kind(tab.path)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.editor = CodeEditor(tab.path)
        self.editor.setPlainText(tab.doc)
        self.editor.document().setModified(False)
        self.editor.highlighter.rehighlight()
        lay.addWidget(self.editor, 1)
        self.line = hairline(vertical=True)
        lay.addWidget(self.line)
        self.md: Optional[MarkdownView] = None
        self.html: Optional[HtmlPreview] = None
        if self.kind == "md":
            self.md = MarkdownView()
            lay.addWidget(self.md, 1)
        elif self.kind == "html":
            self.html = HtmlPreview(tab.path)
            lay.addWidget(self.html, 1)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(250)
        self._render_timer.timeout.connect(self._render)
        self._pending_text = tab.doc
        self.set_view(doc_view(tab) if self.kind else "edit", tab)

    def set_view(self, view: str, tab: Tab) -> None:
        page = self.md or self.html
        self.editor.setVisible(view != "preview" or page is None)
        self.line.setVisible(view == "split" and page is not None)
        if page is not None:
            page.setVisible(view != "edit")
        self.update_page(tab)

    def update_page(self, tab: Tab) -> None:
        if self.md is not None and not self.md.isHidden():  # not isVisible: the area may not be shown yet
            self._pending_text = tab.doc
            self._render_timer.start()
        if self.html is not None:
            self.html.set_saved(tab.saved, tab.dirty)

    def _render(self) -> None:
        if self.md is not None:
            self.md.set_markdown(self._pending_text, dirname(self.path))


class ImageView(QScrollArea):
    """An image file, centred, with its size underneath (``.img-preview``)."""

    def __init__(self, tab: Tab, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(10)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img = QLabel()
        self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img.setAccessibleName(basename(tab.path))
        pm = QPixmap()
        data = (tab.data_url or "").split(",", 1)
        if len(data) == 2:
            pm.loadFromData(QByteArray(base64.b64decode(data[1])))
        self._pm = pm
        lay.addWidget(self.img)
        lay.addWidget(label(bytes_(tab.size), tone="faint"), 0, Qt.AlignmentFlag.AlignHCenter)
        self.setWidget(host)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if not self._pm.isNull():
            w = max(40, self.viewport().width() - 48)
            h = max(40, int(self.viewport().height() * 0.7))
            self.img.setPixmap(
                self._pm.scaled(
                    min(w, self._pm.width()),
                    min(h, self._pm.height()),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )


class SegButtons(Row):
    """A small segmented control of icon buttons (the ``Seg`` in ui/bits.tsx)."""

    changed = Signal(str)

    def __init__(self, options: List[Dict[str, str]], parent: Optional[QWidget] = None) -> None:
        super().__init__(0, (0, 0, 2, 0), parent)
        self._btns: Dict[str, IconButton] = {}
        for o in options:
            b = IconButton(o["icon"], o["title"], 13)
            b.clicked.connect(lambda _c=False, v=o["value"]: self.changed.emit(v))
            self._btns[o["value"]] = b
            self.add(b)

    def set_value(self, v: str) -> None:
        for k, b in self._btns.items():
            b.set_active(k == v)


# ---------------------------------------------------------------- the panel


class FilesPanel(QWidget):
    """The Files tab of the side panel for one session."""

    def __init__(self, session_id: str, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.session_id = session_id
        self.cwd = cwd.replace("\\", "/").rstrip("/") or cwd
        self.st = _per_cwd.setdefault(self.cwd, FilesState(expanded={self.cwd: True}))
        self._loading: set = set()
        self._errors: Dict[str, str] = {}
        self._history: Dict[str, List[Json]] = {}
        self._areas: Dict[str, QWidget] = {}
        self._line: Dict[str, Any] = {}
        self._filter = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- toolbar (.panel-toolbar)
        bar = Toolbar()
        self.tree_btn = icon_btn("panel-left", "Toggle file tree", 14)
        self.tree_btn.clicked.connect(self._toggle_tree)
        bar.add(self.tree_btn)
        self.path_lb = elide(lbl("", theme.FS_SMALL, tone="faint", mono=True))
        bar.add(self.path_lb, 1)
        self.mention_btn = icon_btn("at-sign", "Mention this file in the message", 13)
        self.mention_btn.clicked.connect(self._mention)
        bar.add(self.mention_btn)
        self.seg = Seg(
            [
                {"value": "preview", "icon": "book-open", "title": "Read"},
                {"value": "edit", "icon": "pencil-line", "title": "Edit"},
                {"value": "split", "icon": "columns-2", "title": "Side by side"},
            ]
        )
        self.seg.setAccessibleName("Document view")
        self.seg.changed.connect(self._set_view)
        bar.add(self.seg)
        self.save_btn = icon_btn("save", "Save (Ctrl+S)", 14)
        self.save_btn.clicked.connect(lambda: self._save_active())
        bar.add(self.save_btn)
        # New file / folder lives in the tree's context menu (the web has no button for it either).
        self.new_btn = icon_btn("plus", "New file or folder", 14)
        self.new_btn.clicked.connect(self._new_menu)
        self.new_btn.hide()
        bar.add(self.new_btn)
        self.hidden_btn = icon_btn("eye-off", "Show dotfiles", 14)
        self.hidden_btn.clicked.connect(self._toggle_hidden)
        bar.add(self.hidden_btn)
        self.refresh_btn = icon_btn("refresh-cw", "Refresh", 13)
        self.refresh_btn.clicked.connect(self.refresh)
        bar.add(self.refresh_btn)
        root.addWidget(bar)

        # ---- body: tree | editor
        body = QWidget()
        bl = QHBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        self.tree_wrap = QWidget()
        self.tree_wrap.setFixedWidth(TREE_W)
        tl = QVBoxLayout(self.tree_wrap)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(0)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter files")
        self.filter.setClearButtonEnabled(True)
        self.filter.setAccessibleName("Filter files")
        self.filter.setProperty("quiet", "true")
        self.filter.addAction(icons.icon("search", theme.tokens().text_3, 13), QLineEdit.ActionPosition.LeadingPosition)
        self.filter.textChanged.connect(self._on_filter)
        self.filter.setFixedHeight(30)
        self.filter.hide()
        tl.addWidget(self.filter)
        self.tree = FileTree()
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.tree.on_toggle = self._toggle_dir
        self.tree.opened.connect(lambda p: self.open_tab(p))
        self.tree.touches_clicked.connect(self._show_touches)
        tl.addWidget(self.tree, 1)
        bl.addWidget(self.tree_wrap)
        self.tree_line = Hairline(vertical=True)
        bl.addWidget(self.tree_line)

        self.editor_host = QWidget()
        self.editor_host.setObjectName("canvas")
        self.editor_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        el = QVBoxLayout(self.editor_host)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(0)
        self.tabs = FileTabs()
        self.tabs.activated.connect(self._activate)
        self.tabs.closed.connect(self.close_tab)
        self.tabs.hide()
        el.addWidget(self.tabs)
        self.stack = QStackedWidget()
        self.empty = _top(
            EmptyState("file", "Pick a file to view or edit it.", "File paths in Claude's replies open here too.")
        )
        self.stack.addWidget(self.empty)
        self.loading_w = _top(EmptyState("", "Opening…"))
        self.stack.addWidget(self.loading_w)
        self.error_state = EmptyState("", "")
        self.error_w = _top(self.error_state)
        self.stack.addWidget(self.error_w)
        el.addWidget(self.stack, 1)
        bl.addWidget(self.editor_host, 1)
        root.addWidget(body, 1)

        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(900)
        self._reload_timer.timeout.connect(self._reload_open_files)
        bind(store.file_open, self, self._on_file_open)
        bind(store.rt_changed, self, self._on_rt)
        bind(store.fs_changed, self, lambda _r: self._load_history())
        bind(store.sched_changed, self, lambda _r: self._load_history())
        bind(theme.theme.changed, self, lambda _t: self._render_toolbar())

        self._load_history()
        self._ensure_dir(self.cwd)
        for path, on in list(self.st.expanded.items()):
            if on and path != self.cwd and path in _dir_cache:
                self._ensure_dir(path)
        self.render()

    # ---------------------------------------------------------------- session
    def set_session(self, session: Json) -> None:
        """Follow the session: a different project swaps in that project's remembered state."""
        self.session_id = str(session.get("id") or self.session_id)
        cwd = str(session.get("cwd") or session.get("project") or self.cwd).replace("\\", "/").rstrip("/")
        if cwd and cwd != self.cwd:
            self.cwd = cwd
            self.st = _per_cwd.setdefault(cwd, FilesState(expanded={cwd: True}))
            for w in self._areas.values():
                self.stack.removeWidget(w)
                w.deleteLater()
            self._areas.clear()
            self._load_history()
            self._ensure_dir(cwd)
            self.render()

    # ---------------------------------------------------------------- tree data
    def _ensure_dir(self, path: str, force: bool = False) -> None:
        if path in self._loading:
            return
        if not force and path in _dir_cache:
            return
        self._loading.add(path)

        def ok(r: Any) -> None:
            self._loading.discard(path)
            r = r or {}
            _dir_cache[path] = list(r.get("entries") or [])
            if r.get("error"):
                self._errors[path] = str(r["error"])
            else:
                self._errors.pop(path, None)
            self.render_tree()

        def bad(e: Any) -> None:
            self._loading.discard(path)
            self._errors[path] = err_text(e)
            _dir_cache.setdefault(path, [])
            self.render_tree()

        self.render_tree()
        store.call("fs.list", {"dir": path}, quiet=True).then(ok).catch(bad)

    def _toggle_dir(self, path: str) -> None:
        on = not self.st.expanded.get(path, False)
        self.st.expanded[path] = on
        if on:
            self._ensure_dir(path)
        self.render_tree()

    def refresh(self) -> None:
        """Forget every listing and read the expanded folders again."""
        _dir_cache.clear()
        self._refetch_expanded()

    def _refetch_expanded(self) -> None:
        self._ensure_dir(self.cwd, force=True)
        for path, on in list(self.st.expanded.items()):
            if on and path != self.cwd:
                self._ensure_dir(path, force=True)

    def _on_filter(self, text: str) -> None:
        self._filter = text.strip().lower()
        self.render_tree()

    def _visible_rows(self) -> List[TreeRow]:
        rows: List[TreeRow] = []
        q = self._filter

        def walk(path: str, depth: int) -> bool:
            """Append the rows under an open folder; returns whether anything matched the filter."""
            matched = False
            err = self._errors.get(path)
            if err:
                rows.append(TreeRow(path, err, depth, "err"))
            entries = _dir_cache.get(path)
            if entries is None:
                if not err:
                    rows.append(TreeRow(path, "", depth, "loading"))
                return False
            for e in entries:
                name = str(e.get("name") or "")
                if not self.st.show_hidden and name.startswith("."):
                    continue
                epath = str(e.get("path") or f"{path}/{name}")
                is_dir = e.get("type") == "dir" or e.get("dir") is True
                if is_dir:
                    open_ = bool(self.st.expanded.get(epath)) or (bool(q) and epath in _dir_cache)
                    mark = len(rows)
                    rows.append(TreeRow(epath, name, depth, "dir", open=open_))
                    sub = walk(epath, depth + 1) if open_ else False
                    if q and not sub and q not in name.lower():
                        del rows[mark:]
                    else:
                        matched = matched or sub or (bool(q) and q in name.lower())
                else:
                    if q and q not in name.lower():
                        continue
                    matched = True
                    touches = self._history.get(_rel_key(epath, self.cwd)) or []
                    rows.append(
                        TreeRow(
                            epath,
                            name,
                            depth,
                            "file",
                            size=int(e.get("size") or 0),
                            touches=len({t.get("sessionId") for t in touches}),
                        )
                    )
            return matched

        walk(self.cwd, 0)
        return rows

    def render_tree(self) -> None:
        self.tree.active = self.st.active
        self.tree.set_rows(self._visible_rows())

    # ---------------------------------------------------------------- history marks
    def _load_history(self) -> None:
        cur = _history_cache.get(self.cwd)
        if cur and time.time() - cur["at"] < 15 and not cur.get("p"):
            self._history = cur["data"]
            self.render_tree()
            return
        cwd = self.cwd

        def ok(d: Any) -> None:
            _history_cache[cwd] = {"at": time.time(), "data": d if isinstance(d, dict) else {}}
            if self.cwd == cwd:
                self._history = _history_cache[cwd]["data"]
                self.render_tree()

        _history_cache[cwd] = {"at": time.time(), "data": (cur or {}).get("data") or {}, "p": True}
        store.call("project.fileHistory", {"cwd": cwd}, quiet=True).then(ok).catch(lambda _e: ok({}))

    def _show_touches(self, path: str, pos: QPoint) -> None:
        touches = self._history.get(_rel_key(path, self.cwd)) or []
        by_session: Dict[str, List[Json]] = {}
        for t in touches:
            by_session.setdefault(str(t.get("sessionId")), []).append(t)
        items: List[Json] = [{"heading": f"Changed in {plural(len(by_session), 'session')}"}]
        now = time.time() * 1000
        for sid, ts in by_session.items():
            latest = ts[0]
            add = sum(int(t.get("add") or 0) for t in ts)
            dele = sum(int(t.get("del") or 0) for t in ts)
            sub = f"{time_ago(latest.get('at'), now)} · {plural(len(ts), 'step')} · +{add} −{dele}{' · this session' if sid == store.active_id else ''}"
            items.append(
                {
                    "label": str(latest.get("title") or "Session"),
                    "hint": f"{latest.get('about') or ''}\n{sub}".strip(),
                    "icon": "arrow-up-right",
                    "on_select": lambda s=sid: self._go_session(s),
                }
            )
        show_menu_at(items, pos, self, 300)

    def _go_session(self, sid: str) -> None:
        store.open_session(sid)
        store.set_ui(panel=True, panel_tab="history")

    # ---------------------------------------------------------------- tabs
    def tab(self, path: str) -> Optional[Tab]:
        for t in self.st.tabs:
            if t.path == path:
                return t
        return None

    @property
    def active(self) -> Optional[Tab]:
        return self.tab(self.st.active) if self.st.active else None

    def open_tab(self, path: str, line: Optional[int] = None) -> None:
        """Open a file (fetching it once) and make it the active tab, optionally at a 1-based line."""
        path = _abs(path.replace("\\", "/"), self.cwd)
        existing = self.tab(path)
        if existing is None:
            existing = Tab(path, loading=True)
            self.st.tabs.append(existing)
        self.st.active = path
        self._line = {"line": line, "nonce": time.time()}
        if not existing.loading and not existing.error:
            self.render()
            self._goto_line()
            return
        existing.loading, existing.error = True, None
        self.render()

        def ok(r: Any) -> None:
            r = r or {}
            t = self.tab(path)
            if t is None:
                return
            t.doc = t.saved = str(r.get("text") or "")
            t.mtime = r.get("mtime")
            t.binary = bool(r.get("binary"))
            t.data_url = r.get("dataUrl")
            t.too_large = bool(r.get("tooLarge"))
            t.size = int(r.get("size") or 0)
            t.loading = False
            self.render()
            self._goto_line()

        def bad(e: Any) -> None:
            t = self.tab(path)
            if t is not None:
                t.loading, t.error = False, err_text(e)
                self.render()

        store.call("fs.read", {"path": path}, quiet=True).then(ok).catch(bad)

    def _activate(self, path: str) -> None:
        self.st.active = path
        self.render()

    def close_tab(self, path: str) -> None:
        t = self.tab(path)

        def do_close(_v: bool = False) -> None:
            self.st.tabs = [x for x in self.st.tabs if x.path != path]
            if self.st.active == path:
                self.st.active = self.st.tabs[-1].path if self.st.tabs else None
            w = self._areas.pop(path, None)
            if w is not None:
                self.stack.removeWidget(w)
                w.deleteLater()
            self.render()

        if t is not None and t.dirty:
            store.confirm_action(
                ConfirmSpec(
                    title=f"Discard unsaved changes to {basename(path)}?",
                    confirm="Discard",
                    danger=True,
                    on_confirm=do_close,
                )
            )
        else:
            do_close()

    def _set_view(self, view: str) -> None:
        t = self.active
        if t is None:
            return
        t.view = view
        if view == "split":
            self.st.show_tree = False
        self.render()

    def _toggle_tree(self) -> None:
        self.st.show_tree = not self.st.show_tree
        self.render()

    def _toggle_hidden(self) -> None:
        self.st.show_hidden = not self.st.show_hidden
        self.render()

    def _mention(self) -> None:
        t = self.active
        if t is not None:
            store.insert_into_composer(self.session_id, f"@{rel_path(t.path, self.cwd)} ")

    # ---------------------------------------------------------------- new file / folder
    def _target_dir(self) -> str:
        t = self.active
        if t is not None and t.path.startswith(self.cwd + "/"):
            return dirname(t.path)
        return self.cwd

    def _new_menu(self) -> None:
        show_menu(
            [
                {"label": "New file…", "icon": "file-plus", "on_select": lambda: self._new("file")},
                {"label": "New folder…", "icon": "folder", "on_select": lambda: self._new("dir")},
            ],
            self.new_btn,
            180,
            align_right=True,
        )

    def _new(self, kind: str) -> None:
        base = self._target_dir()
        name, ok = QInputDialog.getText(
            self,
            "New file" if kind == "file" else "New folder",
            f"Name, inside {rel_path(base, self.cwd) if base != self.cwd else basename(self.cwd)}:",
        )
        name = (name or "").strip().strip("/")
        if not ok or not name:
            return
        path = f"{base}/{name}"
        folder = dirname(path)

        def made(_r: Any) -> None:
            _dir_cache.pop(folder, None)
            self.st.expanded[folder] = True
            self._ensure_dir(folder, force=True)
            if kind == "file":
                self.open_tab(path)
            store.bump_fs()

        if kind == "file":
            store.call("fs.write", {"path": path, "text": "", "force": True}).then(made)
        else:
            store.call("fs.mkdir", {"path": path}).then(made)

    # ---------------------------------------------------------------- save
    def _save_active(self, force: bool = False) -> None:
        t = self.active
        if t is not None and not t.binary and not t.too_large and not t.loading:
            self.save(t, force)

    def save(self, tab: Tab, force: bool = False) -> None:
        """Write the tab's text; a file changed since it was read asks before being overwritten."""
        text = tab.doc

        def ok(r: Any) -> None:
            r = r or {}
            if r.get("conflict"):
                store.confirm_action(
                    ConfirmSpec(
                        title=f"{basename(tab.path)} changed on disk",
                        body="Someone (maybe Claude) changed this file after you opened it. Overwrite their version with yours?",
                        confirm="Overwrite",
                        danger=True,
                        on_confirm=lambda _v: self.save(tab, True),
                    )
                )
                return
            tab.saved = text
            tab.mtime = r.get("mtime", tab.mtime)
            store.toast(f"Saved {basename(tab.path)}", "success")
            store.bump_fs()
            self.render()

        store.call("fs.write", {"path": tab.path, "text": text, "expectedMtime": tab.mtime, "force": force}).then(ok)

    # ---------------------------------------------------------------- external changes
    def _on_file_open(self, d: Any) -> None:
        if not isinstance(d, dict) or not d.get("path"):
            return
        if store.active_id not in (None, self.session_id):
            return
        self.open_tab(str(d["path"]), d.get("line"))

    def _on_rt(self, sid: str) -> None:
        if sid == self.session_id:
            self._reload_timer.start()

    def _reload_open_files(self) -> None:
        """Pick up edits Claude makes to open files (only when there are no unsaved local edits)."""
        for t in list(self.st.tabs):
            if t.loading or t.binary or t.too_large or t.dirty:
                continue
            path = t.path

            def ok(r: Any, path: str = path) -> None:
                r = r or {}
                x = self.tab(path)
                text = r.get("text")
                if x is not None and not x.dirty and r.get("mtime") != x.mtime and isinstance(text, str):
                    x.doc = x.saved = text
                    x.mtime = r.get("mtime")
                    area = self._areas.get(path)
                    if isinstance(area, DocArea):
                        area.editor.set_text(text)
                        area.update_page(x)
                    self.render()

            store.call("fs.read", {"path": path}, quiet=True).then(ok).catch(lambda _e: None)
        self._refetch_expanded()

    # ---------------------------------------------------------------- render
    def _tree_menu(self, pos: QPoint) -> None:
        show_menu_at(
            [
                {"label": "New file…", "icon": "file-plus", "on_select": lambda: self._new("file")},
                {"label": "New folder…", "icon": "folder", "on_select": lambda: self._new("dir")},
            ],
            self.tree.viewport().mapToGlobal(pos),
            self,
            180,
        )

    def render(self) -> None:
        self.tree_wrap.setVisible(self.st.show_tree)
        self.tree_line.setVisible(self.st.show_tree)
        self.hidden_btn.set_icon_name("eye" if self.st.show_hidden else "eye-off")
        self.hidden_btn.setToolTip("Hide dotfiles" if self.st.show_hidden else "Show dotfiles")
        self.hidden_btn.setAccessibleName(self.hidden_btn.toolTip())
        self.tree_btn.set_active(self.st.show_tree)
        self.render_tree()
        self.tabs.sync(self.st.tabs, self.st.active)
        self._render_toolbar()
        t = self.active
        if t is None:
            self.stack.setCurrentWidget(self.empty)
            return
        if t.loading:
            self.stack.setCurrentWidget(self.loading_w)
            return
        if t.error:
            self.error_state.set_text(t.error)
            self.stack.setCurrentWidget(self.error_w)
            return
        w = self._areas.get(t.path)
        if w is None:
            w = self._make_area(t)
            self._areas[t.path] = w
            self.stack.addWidget(w)
        if isinstance(w, DocArea):
            w.set_view(doc_view(t) if w.kind else "edit", t)
        self.stack.setCurrentWidget(w)

    def _make_area(self, t: Tab) -> QWidget:
        if t.data_url:
            return ImageView(t)
        if t.binary or t.too_large:
            open_btn = button(
                "Open with system app",
                size="sm",
                icon="external-link",
                on_click=lambda: store.call("shell.openPath", {"path": t.path}),
            )
            return empty_state(
                "",
                f"{'This file is too large to open here' if t.too_large else 'Binary file'} · {bytes_(t.size)}",
                action=open_btn,
            )
        area = DocArea(t, self.cwd)
        area.editor.textChanged.connect(lambda p=t.path: self._on_edit(p))
        area.editor.save_requested.connect(lambda p=t.path: self._save_path(p))
        if area.md is not None:
            area.md.on_open_path = lambda p: self.open_tab(p)
        if area.html is not None:
            area.html.on_save = lambda p=t.path: self._save_path(p)
        return area

    def _on_edit(self, path: str) -> None:
        t = self.tab(path)
        area = self._areas.get(path)
        if t is None or not isinstance(area, DocArea):
            return
        t.doc = area.editor.toPlainText()
        self.tabs.sync(self.st.tabs, self.st.active)
        self._render_toolbar()
        area.update_page(t)

    def _save_path(self, path: str) -> None:
        t = self.tab(path)
        if t is not None:
            self.save(t)

    def _goto_line(self) -> None:
        line = self._line.get("line")
        area = self._areas.get(self.st.active or "")
        if line and isinstance(area, DocArea) and area.editor.isVisible():
            area.editor.goto_line(int(line))
            self._line = {}

    def _render_toolbar(self) -> None:
        t = self.active
        self.path_lb.setText(rel_path(t.path, self.cwd) if t else basename(self.cwd))
        self.path_lb.setToolTip(self.cwd)
        self.mention_btn.setVisible(t is not None)
        is_doc = t is not None and doc_kind(t.path) is not None and not t.binary and not t.loading
        self.seg.setVisible(is_doc)
        if is_doc and t is not None:
            self.seg.set_value(doc_view(t))
        self.save_btn.setVisible(t is not None and not t.binary)
        self.save_btn.setEnabled(t is not None and t.dirty)


if __name__ == "__main__":  # pragma: no cover - development window
    import sys

    from PySide6.QtWidgets import QApplication

    from ... import fonts
    from ...rpc import rpc

    app = QApplication(sys.argv)
    fonts.load()
    theme.theme.apply(True)
    store.init()
    rpc.connect_to(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5300/")
    panel = FilesPanel("dev", sys.argv[2] if len(sys.argv) > 2 else ".")
    panel.setObjectName("panel")
    panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    panel.resize(760, 640)
    panel.show()
    sys.exit(app.exec())
