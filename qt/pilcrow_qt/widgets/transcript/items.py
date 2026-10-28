"""The rows of a conversation: what the person said, what Claude said and thought, the notices in
between, and what a turn cost (the item components of web/src/components/Transcript.tsx, with
Markdown.tsx as the prose widget).

Every row keeps the ``rev`` it drew and ``refresh(rev)`` returns early when nothing changed, so a
streaming reply touches one row per frame. Rows carry their CSS margins as ``TOP``/``BOTTOM``;
the list that stacks them collapses adjacent margins the way block layout does.
"""

from __future__ import annotations

import html
import math
import random
import re
import time
from typing import Callable, List, Optional
from urllib.parse import quote, unquote

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt, QTimer, QUrl, QUrlQuery, Signal
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QFontMetricsF,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextTable,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ... import icons, markdown, theme
from ...lib.fmt import cost, duration, rel_path, tokens
from ...lib.transcript_model import (
    CommandOutputItem,
    CompactItem,
    NoticeItem,
    ResultItem,
    TextItem,
    ThinkingItem,
    UserItem,
)
from ...store import ConfirmSpec, store
from ..ui.bits import IconButton, WorkingMark
from ..ui.collapse import Collapse
from ..ui.signals import bind
from .ctx import ToolCtx

BUSY = ("running", "starting", "requires_action")
BUBBLE_LINE = markdown.px(theme.FS_BODY * 1.55)  # .msg-bubble line-height
HAN = re.compile(r"[㐀-鿿豈-﫿]")
MENTION = re.compile(r"(@[\w./~-]+[\w/-])")
# Words from the composing room, since the mark is a pilcrow: what an editor does while a page takes shape.
VERBS = [
    "Reading",
    "Drafting",
    "Composing",
    "Outlining",
    "Revising",
    "Proofing",
    "Collating",
    "Annotating",
    "Indexing",
    "Marking up",
    "Typesetting",
    "Kerning",
]


# ---------------------------------------------------------------- styling helpers


def pt(px: float) -> str:
    """A CSS size for a stylesheet: whole pixels, rounded half-up like ``theme.font_ui``, so what a
    row measures with and what it draws with are the same font (points would follow the X server's
    DPI and drift from the pixel-sized layout)."""
    return f"{int(px + 0.5)}px"


def style_label(lb: QLabel, px: float, color: str, weight: int = 400, extra: str = "") -> None:
    lb.setStyleSheet(f"QLabel {{ font-size: {pt(px)}; color: {color}; font-weight: {weight}; {extra} }}")


def icon_label(name: str, color: str, size: int, parent: Optional[QWidget] = None) -> QLabel:
    lb = QLabel(parent)
    lb.setFixedSize(size, size)
    lb.setPixmap(icons.pixmap(name, color, size))
    return lb


def set_icon(lb: QLabel, name: str, color: str, size: int) -> None:
    lb.setPixmap(icons.pixmap(name, color, size))


def lang_of(text: str) -> bool:
    """Chinese text (the first 400 characters tell): the web marks it zh-Hant-TW; here it is only noted."""
    return HAN.search(text[:400]) is not None


class Head(QWidget):
    """A flat clickable line (the web's ``<button class="…-head">``): pointer, hover tint, Enter/Space."""

    clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.hover = False

    def enterEvent(self, e) -> None:  # noqa: N802
        self.hover = True
        self.on_hover(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self.hover = False
        self.on_hover(False)
        super().leaveEvent(e)

    def on_hover(self, on: bool) -> None:
        pass

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)


class Row(QWidget):
    """A row of the conversation. ``TOP``/``BOTTOM`` are its block margins (transcript.css)."""

    TOP = 0
    BOTTOM = 0

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._rev = -1

    def refresh(self, rev: int) -> None:
        """Redraw when the item's revision moved on; subclasses do the work in ``_draw``."""
        if rev == self._rev:
            return
        self._rev = rev
        self._draw()

    def _draw(self) -> None:
        pass

    def find_text(self) -> str:
        return ""


def row_margins(w: QWidget) -> tuple:
    return int(getattr(w, "TOP", 0) or 0), int(getattr(w, "BOTTOM", 0) or 0)


# ---------------------------------------------------------------- markdown


class MarkdownView(QTextBrowser):
    """Prose from Markdown (Markdown.tsx): a frameless read-only text browser that takes the height of
    its document. While a reply streams only the block being written is rendered again; finished
    blocks keep their HTML. Links open outside; a code span that looks like a path opens in Files.

    Lines are set at the web's pixel heights (``.md``: 14.5px on 24px lines) and the decorations
    QTextDocument cannot draw from HTML are painted under the text: the pill behind inline code, the
    rule beside a blockquote, the rounded well of a code block, the hairlines of a table."""

    def __init__(
        self,
        cwd: str = "",
        compact: bool = False,
        quiet: bool = False,
        parent: Optional[QWidget] = None,
        line_height: Optional[int] = None,
    ) -> None:
        super().__init__(parent)
        self.cwd = cwd
        self._compact = compact
        self._quiet = quiet
        self._m = markdown.metrics(compact or quiet)
        self._line = line_height or self._m.line
        self._blocks: List[str] = []
        self._html: List[str] = []
        self._open: List[bool] = []
        self._text = ""
        self._raw: Optional[str] = None  # HTML given straight (the person's message), not Markdown
        self.setReadOnly(True)
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.viewport().setAutoFillBackground(False)
        self.document().setDocumentMargin(0)
        self.document().setIndentWidth(self._m.list_indent)
        self.setFixedHeight(1)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)
        self.anchorClicked.connect(self._on_anchor)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._on_theme())

    def _paint(self) -> None:
        t = theme.tokens()
        m = self._m
        color = t.text_2 if self._quiet else t.text
        font = theme.font_ui(m.base)
        self.document().setDefaultFont(font)
        # A fixed line box in QTextDocument sits its text at the bottom; the web centres it. Pulling
        # the viewport up by the half-leading puts the glyphs where the browser puts them.
        natural = QFontMetrics(font).height()
        self._lead = max(0, (self._line - natural) // 2)
        self.setViewportMargins(0, -self._lead, 0, 0)
        self.setStyleSheet(
            f"MarkdownView {{ font-size: {pt(m.base)}; color: {color}; background: transparent; border: none; padding: 0; "
            f"selection-background-color: {theme.blend(t.selection, t.canvas).name()}; }}"
        )
        css = markdown.stylesheet(t, m)
        if self._line != m.line:
            css += f" p {{ line-height: {self._line}px; }}"
        self.document().setDefaultStyleSheet(css)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Dark, theme.blend(t.hair, t.canvas))  # what <hr> is drawn with
        pal.setColor(QPalette.ColorRole.Text, QColor(color))
        self.setPalette(pal)

    def _on_theme(self) -> None:
        self._paint()
        self._html = []
        self._blocks = []
        if self._raw is not None:
            self.set_html(self._raw, force=True)
        else:
            self.set_text(self._text, False, force=True)

    def _fit(self, size) -> None:
        h = max(1, int(math.ceil(size.height())))
        if h != self.height():
            self.setFixedHeight(h)
            self.updateGeometry()

    def set_text(self, text: str, streaming: bool = False, force: bool = False) -> None:
        if text == self._text and not force and not streaming:
            return
        self._text = text
        self._raw = None
        blocks = markdown.split_blocks(text) if text else []
        out: List[str] = []
        opens: List[bool] = []
        n = len(blocks)
        for i, b in enumerate(blocks):
            open_ = streaming and i == n - 1
            cached = i < len(self._blocks) and self._blocks[i] == b and not self._open[i] and not open_
            out.append(self._html[i] if cached else self._render(b, open_))
            opens.append(open_)
        self._blocks, self._html, self._open = blocks, out, opens
        self._set_html("".join(out))

    def set_html(self, body: str, force: bool = False) -> None:
        """Rich text that is not Markdown (the person's own words with their mentions)."""
        if body == self._raw and not force:
            return
        self._raw = body
        self._text = ""
        self._blocks, self._html, self._open = [], [], []
        self._set_html(body)

    def _set_html(self, body: str) -> None:
        doc = self.document()
        doc.setHtml(body)
        self._tidy(doc)
        doc.documentLayout().documentSizeChanged.emit(doc.size())

    @staticmethod
    def _tidy(doc: QTextDocument) -> None:
        """What the CSS says with ``:first-child``/``:last-child``: no margin above the first block or
        under the last; and the empty paragraph QTextDocument leaves after a code block's table takes
        no room (the table's own bottom margin is the gap)."""
        first, last = doc.firstBlock(), doc.lastBlock()
        if first.blockFormat().topMargin():
            cur = QTextCursor(first)
            bf = cur.blockFormat()
            bf.setTopMargin(0)
            cur.setBlockFormat(bf)
        if last.blockFormat().bottomMargin():
            cur = QTextCursor(last)
            bf = cur.blockFormat()
            bf.setBottomMargin(0)
            cur.setBlockFormat(bf)
        for frame in doc.rootFrame().childFrames():
            b = doc.findBlock(frame.lastPosition() + 1)
            if b.isValid() and not b.text() and b != last and b.charFormat().fontPointSize() != 1:
                # an empty line is as tall as its font whatever its line height says: make the font tiny
                cur = QTextCursor(b)
                cf = QTextCharFormat()
                cf.setFontPointSize(1)
                cur.setBlockCharFormat(cf)
                bf = cur.blockFormat()
                bf.setTopMargin(0)
                bf.setBottomMargin(0)
                cur.setBlockFormat(bf)

    def _render(self, block: str, streaming: bool) -> str:
        try:
            out = markdown.render_html(block, streaming=streaming, compact=self._compact or self._quiet)
        except Exception:  # noqa: BLE001 - a Markdown failure must not take the row down
            out = f"<p>{html.escape(block)}</p>"
        return _link_paths(out)

    def measure(self, body: str, width: int) -> tuple:
        """(ideal width, height) of a rich-text body laid out like this view, for sizing a bubble."""
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        doc.setDefaultFont(self.document().defaultFont())
        doc.setDefaultStyleSheet(self.document().defaultStyleSheet())
        doc.setHtml(body)
        self._tidy(doc)
        doc.setTextWidth(width)
        h = int(math.ceil(doc.size().height()))  # lays the text out
        lines = 0
        b = doc.firstBlock()
        while b.isValid() and lines < 2:
            lines += b.layout().lineCount() if b.layout() is not None else 1
            b = b.next()
        # a block that wraps is as wide as it may be (the web's max-width), a one-liner hugs its words
        w = width if lines > 1 else min(width, int(math.ceil(doc.idealWidth())))
        return w, h

    def _on_anchor(self, url: QUrl) -> None:
        if url.scheme() == "pilcrow-path":
            q = QUrlQuery(url)
            path = unquote(q.queryItemValue("p"))
            line = q.queryItemValue("l")
            store.open_file(resolve_path(path, self.cwd), int(line) if line.isdigit() else None)
            return
        if url.scheme() in ("http", "https", "mailto"):
            store.call("shell.openExternal", {"url": url.toString()}).catch(
                lambda _e: store.toast("Could not open the link", "error")
            )

    def plain_text(self) -> str:
        return self._text

    def wheelEvent(self, e) -> None:  # noqa: N802
        e.ignore()  # the conversation scrolls, never the row

    def set_find(self, needle: str, current: int = -1) -> bool:
        """Light every occurrence of ``needle`` (the find wash) and the ``current``-th one in full
        (accent on white), as the web's ::highlight(find) and ::highlight(find-current). An empty
        needle clears them. Returns whether anything was lit."""
        sels = []
        if needle:
            t = theme.tokens()
            # Inline code carries word joiners (U+2060) so it never wraps; the search sees the text
            # without them, and ``pos`` maps a clean offset back to a document position.
            raw = self.document().toPlainText()
            pos = [i for i, ch in enumerate(raw) if ch != "\u2060"]
            text = "".join(raw[i] for i in pos).lower()
            wash = QTextCharFormat()
            wash.setBackground(theme.blend("rgba(217, 119, 87, 0.28)", t.canvas))
            full = QTextCharFormat()
            full.setBackground(QColor(t.accent))
            full.setForeground(QColor("#ffffff"))
            n = needle.lower()
            i = text.find(n)
            k = 0
            while i >= 0 and len(sels) < 2000:
                cur = QTextCursor(self.document())
                cur.setPosition(pos[i])
                cur.setPosition(pos[i + len(n) - 1] + 1, QTextCursor.MoveMode.KeepAnchor)
                sel = QTextEdit.ExtraSelection()
                sel.cursor = cur
                sel.format = full if k == current else wash
                sels.append(sel)
                k += 1
                i = text.find(n, i + len(n))
        self.setExtraSelections(sels)
        return bool(sels)

    def createMimeDataFromSelection(self):  # noqa: N802
        """Copied text loses the no-break spaces that pad the code pills."""
        md = super().createMimeDataFromSelection()
        if md is not None and md.hasText():
            md.setText(md.text().replace("\u202f", "").replace("\u2060", ""))
        return md

    # ---- decorations under the text
    def paintEvent(self, e) -> None:  # noqa: N802
        self._paint_decor(e.rect())
        super().paintEvent(e)

    def _paint_decor(self, rect) -> None:
        doc = self.document()
        lay = doc.documentLayout()
        t = theme.tokens()
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        pill = theme.blend(t.fill_2, t.canvas)
        quote = theme.blend(t.hair_2, t.canvas)
        inset = QColor(t.inset)
        hair, hair2 = theme.blend(t.hair, t.canvas), theme.blend(t.hair_2, t.canvas)
        view = QRectF(rect)
        for frame in doc.rootFrame().childFrames():
            if not isinstance(frame, QTextTable):
                continue
            fr = self._table_rect(doc, lay, frame)
            if not fr.intersects(view):
                continue
            if frame.format().borderCollapse():
                # .md-code: the inset well, with a hairline only on a light page
                p.setBrush(inset)
                p.setPen(Qt.PenStyle.NoPen if t.dark else QPen(hair, 1))
                p.drawRoundedRect(fr.adjusted(0.5, 0.5, -0.5, -0.5) if not t.dark else fr, theme.R3, theme.R3)
                p.setPen(Qt.PenStyle.NoPen)
            else:
                self._table_rules(p, doc, lay, frame, fr, hair, hair2)  # .md table
        pos = lay.hitTest(QPointF(0, view.top()), Qt.HitTestAccuracy.FuzzyHit)
        b = doc.findBlock(pos) if pos >= 0 else doc.firstBlock()
        while b.isValid():
            br = lay.blockBoundingRect(b)
            if br.top() > view.bottom():
                break
            if br.bottom() >= view.top():
                bf = b.blockFormat()
                if bf.leftMargin() == markdown.QUOTE_INDENT and b.textList() is None:
                    h = self._block_height(b, br)
                    nxt = b.next()
                    if nxt.isValid() and nxt.blockFormat().leftMargin() == markdown.QUOTE_INDENT:
                        h += bf.bottomMargin()  # one rule down a quote of several paragraphs
                    p.fillRect(QRectF(br.left(), br.top(), 2, h), quote)
                self._pills(p, b, br, pill)
            b = b.next()
        p.end()

    @staticmethod
    def _pills(p: QPainter, b, br, color) -> None:
        tl = b.layout()
        if tl is None or tl.lineCount() == 0:
            return
        it = b.begin()
        while not it.atEnd():
            fr = it.fragment()
            it += 1
            bg = fr.charFormat().background()
            if bg.style() == Qt.BrushStyle.NoBrush or bg.color().alpha() != 0:
                continue
            fm = QFontMetricsF(fr.charFormat().font())
            asc, desc = fm.ascent(), fm.descent()
            start = fr.position() - b.position()
            end = start + fr.length()
            line = tl.lineForTextPosition(start)
            while line.isValid() and line.textStart() < end:
                a = max(start, line.textStart())
                z = min(end, line.textStart() + line.textLength())
                x1 = line.cursorToX(a)[0]
                x2 = line.cursorToX(z)[0]
                base = br.top() + line.y() + line.ascent()
                p.setBrush(color)
                p.drawRoundedRect(QRectF(br.left() + x1, base - asc - 2, x2 - x1, asc + desc + 4), 5, 5)
                if line.lineNumber() + 1 >= tl.lineCount():
                    break
                line = tl.lineAt(line.lineNumber() + 1)

    @staticmethod
    def _block_height(b, br) -> float:
        """A block's drawn height: its bounding rect stops at the glyphs, not at its fixed line boxes."""
        tl = b.layout()
        n = tl.lineCount() if tl is not None else 0
        if not n:
            return br.height()
        last = tl.lineAt(n - 1)
        return max(br.height(), last.y() + last.height())

    def _table_rect(self, doc, lay, table: QTextTable) -> QRectF:
        """The box a table occupies. The layout reports a table's rect from its first cell's content and
        with its margins folded in, so the box is read off the cells: the first one's padding above its
        first line, the frame's bottom margin under the block that follows."""
        first = table.cellAt(0, 0)
        cf = first.format().toTableCellFormat()
        top_block = lay.blockBoundingRect(first.firstCursorPosition().block())
        top = top_block.top() - cf.topPadding()
        left = top_block.left() - cf.leftPadding()
        fr = lay.frameBoundingRect(table)
        after = doc.findBlock(table.lastPosition() + 1)
        if after.isValid() and after.position() > table.lastPosition():
            bottom = lay.blockBoundingRect(after).top() - table.format().bottomMargin()
        else:
            bottom = top + fr.height()
        return QRectF(left, top, fr.width(), max(0.0, bottom - top))

    def _table_rules(self, p: QPainter, doc, lay, table: QTextTable, fr: QRectF, hair, hair2) -> None:
        """``.md th, .md td``: a hairline under every row (a stronger one under the header)."""
        rows = table.rows()
        for r in range(rows):
            if r + 1 < rows:
                nxt = table.cellAt(r + 1, 0)
                pad = nxt.format().toTableCellFormat().topPadding()
                bottom = lay.blockBoundingRect(nxt.firstCursorPosition().block()).top() - pad
            else:
                bottom = fr.bottom()
            p.fillRect(QRectF(fr.left(), bottom - 1, fr.width(), 1), hair2 if r == 0 else hair)


_CODE_RE = re.compile(r"<code( style=\"[^\"]*\")>([^<]*)</code>")


def _link_paths(body: str) -> str:
    """Inline code spans that look like file paths become links (Markdown.tsx marks them ``md-path``)."""

    def repl(m: re.Match) -> str:
        ref = markdown.as_path_ref(html.unescape(m.group(2)))
        if not ref:
            return m.group(0)
        line = f"&l={ref['line']}" if ref.get("line") else ""
        return f'<a href="pilcrow-path://open?p={quote(ref["path"])}{line}" style="text-decoration:none;color:inherit" title="Open in Files">{m.group(0)}</a>'

    return _CODE_RE.sub(repl, body)


def resolve_path(p: str, cwd: str) -> str:
    if not p or p.startswith("/") or p.startswith("~/") or re.match(r"^[A-Za-z]:[\\/]", p):
        return p
    base = (cwd or "").rstrip("/")
    return f"{base}/{p[2:] if p.startswith('./') else p}" if base else p


# ---------------------------------------------------------------- the person's message


class CopyButton(IconButton):
    """Copies a text; the icon turns into a check for a moment (bits.tsx CopyButton)."""

    def __init__(self, text_of: Callable[[], str], parent: Optional[QWidget] = None) -> None:
        super().__init__("copy", "Copy", 13, parent=parent)
        self._text_of = text_of
        self.clicked.connect(self._copy)

    def _copy(self) -> None:
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText(self._text_of())
        self.set_icon_name("check")
        self._color = theme.tokens().ok
        self._paint_icon()
        QTimer.singleShot(1200, self._reset)

    def _reset(self) -> None:
        self._color = ""
        self.set_icon_name("copy")


class UserMessage(Row):
    TOP, BOTTOM = 26, 8

    def __init__(self, item: UserItem, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.item = item
        self.ctx = ctx
        self._queued = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        if item.command:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addStretch(1)
            self.chip = QLabel(f"/{item.command.name}")
            self.chip.setObjectName("cmd-chip")
            self.chip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            self.chip.setFixedHeight(26)
            row.addWidget(self.chip)
            if item.command.args:
                self.args = QLabel(item.command.args)
                row.addWidget(self.args)
            lay.addLayout(row)
            self.bubble = None
            self._paint()
            bind(theme.theme.changed, self, lambda _t: self._paint())
            return
        self.bubble = QWidget()
        self.bubble.setObjectName("bubble")
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bl = QVBoxLayout(self.bubble)
        bl.setContentsMargins(14, 9, 14, 9)
        bl.setSpacing(0)
        if item.images or item.files:
            bl.addWidget(self._attachments())
        # ``.msg-bubble``: 14.5px on 1.55 lines, like the prose but tighter
        self.text = MarkdownView(line_height=BUBBLE_LINE)
        self.text.setVisible(bool(item.text))
        bl.addWidget(self.text)
        lay.addWidget(self.bubble, 0, Qt.AlignmentFlag.AlignRight)
        # the actions keep their room (the web fades them in), so a hover never moves the page
        self.actions = QWidget()
        self.actions.setFixedHeight(28)  # .msg-actions: margin-top 4px over 24px icon buttons
        al = QHBoxLayout(self.actions)
        al.setContentsMargins(0, 4, 0, 0)
        al.setSpacing(2)
        self.copy_btn = CopyButton(lambda: self.item.text)
        self.edit_btn = IconButton("pencil-line", "Edit and resend in a new branch of this conversation", 13)
        self.edit_btn.clicked.connect(self._edit)
        self.rewind_btn = IconButton("history", "Restore files to how they were before this message", 13)
        self.rewind_btn.clicked.connect(self._rewind)
        for b in (self.copy_btn, self.edit_btn, self.rewind_btn):
            b.setFixedSize(24, 24)  # .icon-btn.sm
            al.addWidget(b)
            b.setVisible(False)
        lay.addWidget(self.actions, 0, Qt.AlignmentFlag.AlignRight)
        self.queued_note = QLabel("Queued — Claude reads this after the current step")
        self.queued_note.setVisible(False)
        lay.addWidget(self.queued_note, 0, Qt.AlignmentFlag.AlignRight)
        self.refresh(item.rev)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _attachments(self) -> QWidget:
        box = QWidget()
        hl = QHBoxLayout(box)
        hl.setContentsMargins(0, 2, 0, 8)
        hl.setSpacing(6)
        hl.addStretch(1)
        for src in self.item.images:
            lb = QLabel()
            pm = _pixmap_of(src)
            if not pm.isNull():
                lb.setPixmap(
                    pm.scaled(220, 160, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
            lb.setCursor(Qt.CursorShape.PointingHandCursor)
            lb.setToolTip("Attached image")
            lb.mouseReleaseEvent = lambda _e, s=src: store.open_lightbox(s)  # type: ignore[method-assign]
            hl.addWidget(lb)
        for f in self.item.files:
            chip = QWidget()
            chip.setObjectName("file-chip")
            chip.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            chip.setFixedHeight(26)
            cl = QHBoxLayout(chip)
            cl.setContentsMargins(10, 0, 10, 0)
            cl.setSpacing(6)
            cl.addWidget(icon_label("paperclip", theme.tokens().text_2, 12))
            cl.addWidget(QLabel(f))
            hl.addWidget(chip)
        return box

    def resizeEvent(self, e) -> None:  # noqa: N802
        self._size_bubble()
        super().resizeEvent(e)

    def _size_bubble(self) -> None:
        """The bubble hugs its text up to 84% of the column. Measured here because a box layout
        would size a right-aligned wrapped label for the whole column's width, then clip it."""
        if self.bubble is None or self.width() < 40:
            return
        pad_w, pad_h = 28, 18
        avail = max(80, int(self.width() * 0.84)) - pad_w
        text_w, text_h = self.text.measure(render_mentions(self.item.text), avail)
        attach = self.bubble.layout().itemAt(0).widget() if self.bubble.layout().count() > 1 else None
        w = text_w
        h = text_h if self.item.text else 0
        if attach is not None:
            w = max(w, attach.sizeHint().width())
            h += attach.sizeHint().height()
        self.text.setFixedWidth(max(1, text_w if self.item.text else 1))
        self.text.setFixedHeight(max(1, text_h if self.item.text else 1))
        self.bubble.setFixedSize(w + pad_w, h + pad_h)

    def _draw(self) -> None:
        if self.bubble is None:
            return
        self.text.set_html(render_mentions(self.item.text))
        self.text.setVisible(bool(self.item.text))
        self._size_bubble()
        self.sync_state()

    def sync_state(self) -> None:
        """Queued and busy come from the runtime, not the item: re-read them on every render."""
        if self.bubble is None:
            return
        r = store.rt.get(self.ctx.session_id)
        queued = bool(r and self.item.uuid and self.item.uuid in (r.st.get("queued") or []))
        busy = bool(r and r.status in BUSY)
        if queued != self._queued:
            self._queued = queued
            self._paint()
        self.actions.setVisible(not queued)
        self.queued_note.setVisible(queued)
        self.rewind_btn.setVisible(self.hover_on() and bool(self.item.uuid) and not busy)

    def hover_on(self) -> bool:
        return self.underMouse()

    def enterEvent(self, e) -> None:  # noqa: N802
        if self.bubble is not None:
            self.copy_btn.setVisible(True)
            self.edit_btn.setVisible(True)
            self.sync_state()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        if self.bubble is not None:
            for b in (self.copy_btn, self.edit_btn, self.rewind_btn):
                b.setVisible(False)
        super().leaveEvent(e)

    def _paint(self) -> None:
        t = theme.tokens()
        bg = theme.blend(t.fill_2, t.canvas).name() if t.dark else t.raised_2
        if self.bubble is None:
            self.chip.setStyleSheet(
                f"#cmd-chip {{ background: {bg}; border-radius: 13px; padding: 0 10px; font-family: '{theme._MONO_FAMILY or 'monospace'}'; font-size: {pt(12.5)}; }}"
            )
            if hasattr(self, "args"):
                self.args.setStyleSheet(
                    f"QLabel {{ color: {t.text_2}; font-family: '{theme._MONO_FAMILY or 'monospace'}'; font-size: {pt(theme.MONO_SIZE)}; }}"
                )
            return
        self.bubble.setStyleSheet(
            f"#bubble {{ background: {bg}; border-radius: 16px; }} #file-chip {{ background: {theme.blend(t.fill_2, bg).name()}; border-radius: {theme.R2}px; font-size: {pt(theme.FS_SMALL)}; }}"
        )
        self.text.setStyleSheet(
            f"MarkdownView {{ font-size: {pt(theme.FS_BODY)}; color: {t.text_3 if self._queued else t.text}; "
            f"background: transparent; border: none; padding: 0; "
            f"selection-background-color: {theme.blend(t.selection, bg).name()}; }}"
        )
        style_label(self.queued_note, theme.FS_MICRO, t.text_3, extra="margin-top: 6px;")

    def find_text(self) -> str:
        return self.item.text

    # ---- actions (Transcript.tsx UserMessage)
    def _edit(self) -> None:
        sid, item = self.ctx.session_id, self.item
        if not item.prev_uuid:
            store.insert_into_composer(sid, item.text, True)
            return
        store.fork_session(sid, item.prev_uuid).then(lambda r: store.insert_into_composer(r["id"], item.text, True))

    def _rewind(self) -> None:
        sid, item, cwd = self.ctx.session_id, self.item, self.ctx.cwd

        def dry(r: dict) -> None:
            if not r.get("canRewind"):
                store.toast(
                    r.get("error") or "There is no saved copy of the files from before this message.", "warning"
                )
                return
            files = [rel_path(f, cwd) for f in (r.get("filesChanged") or [])]
            if not files:
                store.toast("No files changed after this message.", "info")
                return
            n = len(files)

            def confirm(branch: bool) -> None:
                def done(d: dict) -> None:
                    if not d.get("canRewind"):
                        store.toast(d.get("error") or "Could not restore the files.", "error")
                        return
                    store.bump_fs()
                    skipped = int(d.get("skippedLinks") or 0)
                    note = f" ({skipped} linked file{'s' if skipped > 1 else ''} skipped)" if skipped else ""
                    store.toast(f"Restored {n} file{'s' if n > 1 else ''}{note}", "success")
                    if branch and item.prev_uuid:
                        store.fork_session(sid, item.prev_uuid).then(
                            lambda f: store.insert_into_composer(f["id"], item.text, True)
                        )

                store.call("session.rewind", {"id": sid, "messageId": item.uuid, "dryRun": False}).then(done)

            store.confirm_action(
                ConfirmSpec(
                    title="Restore files?",
                    body=f"Put {'this file' if n == 1 else f'these {n} files'} back the way {'it was' if n == 1 else 'they were'} before this message. The conversation stays as it is.",
                    list=files,
                    confirm="Restore files",
                    danger=True,
                    checkbox={
                        "label": "Also start a new branch of the conversation from before this message",
                        "value": False,
                    }
                    if item.prev_uuid
                    else None,
                    on_confirm=confirm,
                )
            )

        store.call("session.rewind", {"id": sid, "messageId": item.uuid, "dryRun": True}).then(dry)


def _pixmap_of(src: str) -> QPixmap:
    pm = QPixmap()
    if src.startswith("data:"):
        try:
            head, data = src.split(",", 1)
            pm.loadFromData(QByteArray.fromBase64(data.encode("ascii")))
        except ValueError:
            pass
    else:
        pm.load(src)
    return pm


def render_mentions(text: str) -> str:
    """The person's words as rich text, ``@path`` mentions in mono and the info colour, whitespace kept."""
    t = theme.tokens()
    mono = theme._MONO_FAMILY or "monospace"
    out = []
    for i, part in enumerate(MENTION.split(text)):
        esc = html.escape(part)
        if i % 2 == 1 and len(part) > 1:
            out.append(
                f"<span style=\"font-family:'{mono}';font-size:{int(theme.FS_BODY * 0.88 + 0.5)}px;color:{t.info}\">{esc}</span>"
            )
        else:
            out.append(esc)
    return f'<p style="white-space:pre-wrap;margin:0">{"".join(out)}</p>'


# ---------------------------------------------------------------- Claude's prose


class AssistantText(Row):
    TOP, BOTTOM = 10, 10

    def __init__(self, item: TextItem, ctx: ToolCtx, quiet: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.item = item
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.md = MarkdownView(ctx.cwd, quiet=quiet)
        lay.addWidget(self.md)
        self.refresh(item.rev)

    def _draw(self) -> None:
        self.md.set_text(self.item.text, bool(self.item.streaming))

    def find_text(self) -> str:
        return self.item.text


# ---------------------------------------------------------------- thinking


class Thinking(Row):
    TOP, BOTTOM = 8, 8

    def __init__(self, item: ThinkingItem, view: str = "normal", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.item = item
        self._open: Optional[bool] = None
        self._view = view
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = Head()
        self.head.setFixedHeight(26)
        self.head.setAccessibleName("Thinking")
        hl = QHBoxLayout(self.head)
        # .thinking-head: margin-left -8px, padding 0 9px 0 7px, gap 7px; a layout has no negative
        # margin, so the icon sits on the text edge (1px right of the web's) and the hover tint starts there
        hl.setContentsMargins(0, 0, 9, 0)
        hl.setSpacing(7)
        self.ico = icon_label("sparkles", theme.tokens().think, 13)
        hl.addWidget(self.ico)
        self.title = QLabel()
        hl.addWidget(self.title)
        self.chev = icon_label("chevron-right", theme.tokens().text_3, 13)
        hl.addWidget(self.chev)
        hl.addStretch(1)
        self.head.clicked.connect(self.toggle)
        self.head.on_hover = lambda _on: self._paint()  # type: ignore[method-assign]
        lay.addWidget(self.head, 0, Qt.AlignmentFlag.AlignLeft)
        self.peek = QLabel()
        self.peek.setWordWrap(True)
        self.peek.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)
        self.peek.setMaximumHeight(int(4.9 * 13.5 * 1.65))
        self.peek.setVisible(False)
        lay.addWidget(self.peek)
        self.body = Collapse(self._build_body, False)
        lay.addWidget(self.body)
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._draw_title)
        self.refresh(item.rev)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    @property
    def is_open(self) -> bool:
        return self._open if self._open is not None else self._view in ("thinking", "verbose")

    def _build_body(self) -> QWidget:
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.text.setContentsMargins(14, 2, 0, 2)
        self.text.setText(self.item.text)
        self._paint_text()
        return self.text

    def toggle(self) -> None:
        self._open = not self.is_open
        self._sync_open()

    def _sync_open(self) -> None:
        open_ = self.is_open and bool(self.item.text)
        self.body.set_open(open_)
        self.peek.setVisible(bool(self.item.streaming and not self.is_open and self.item.text))
        set_icon(self.chev, "chevron-down" if self.is_open else "chevron-right", theme.tokens().text_3, 13)

    def _draw(self) -> None:
        it = self.item
        self.setVisible(bool(it.text or it.streaming or it.redacted))
        self._draw_title()
        if it.streaming and not self._tick.isActive():
            self._tick.start()
        elif not it.streaming:
            self._tick.stop()
        self.peek.setText(it.text[-320:])
        if self.body.body() is not None:
            self.text.setText(it.text)
        self._sync_open()

    def _draw_title(self) -> None:
        it = self.item
        if it.streaming:
            self.title.setText("Thinking")
        elif it.redacted:
            self.title.setText("Reasoned privately")
        else:
            secs = max(1, int(round(((it.end_ts or time.time() * 1000) - it.start_ts) / 1000)))
            self.title.setText(f"Thought for {secs}s")

    def _paint(self) -> None:
        t = theme.tokens()
        set_icon(self.ico, "sparkles", t.think, 13)
        set_icon(self.chev, "chevron-down" if self.is_open else "chevron-right", t.text_3, 13)
        style_label(
            self.title,
            theme.FS_UI,
            t.text_2 if (self.head.hover or self.item.streaming) else t.text_3,
            500 if self.item.streaming else 400,
        )
        style_label(self.peek, 13.5, t.text_3, extra="line-height: 165%; margin-left: 15px;")
        self.head.setStyleSheet(
            f"Head {{ background: {theme.blend(t.fill_1, t.canvas).name() if self.head.hover else 'transparent'}; border-radius: {theme.R2}px; }}"
        )
        self.head.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._paint_text()

    def _paint_text(self) -> None:
        if self.body.body() is None:
            return
        t = theme.tokens()
        self.text.setStyleSheet(
            f"QLabel {{ font-size: {pt(13.5)}; color: {t.text_2}; line-height: 165%; border-left: 1px solid {theme.blend(t.hair_2, t.canvas).name()}; margin: 4px 0 6px 1px; }}"
        )

    def find_text(self) -> str:
        return self.item.text


# ---------------------------------------------------------------- notices, dividers, footers


class Divider(Row):
    """A centred line of micro text with hairlines either side (``.divider``: interrupts, resets, compactions)."""

    TOP, BOTTOM = 18, 18

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        from ..ui.bits import hairline

        self.left, self.right = hairline(), hairline()
        self.label = QLabel(text)
        lay.addWidget(self.left, 1)
        lay.addWidget(self.label)
        lay.addWidget(self.right, 1)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def set_text(self, text: str) -> None:
        self.label.setText(text)

    def _paint(self) -> None:
        style_label(self.label, theme.FS_MICRO, theme.tokens().text_3)

    def find_text(self) -> str:
        return self.label.text()


class Notice(Row):
    TOP, BOTTOM = 10, 10

    def __init__(self, item: NoticeItem, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.item = item
        self._open = False
        self._mode = ""
        self.inner: Optional[QWidget] = None
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.refresh(item.rev)
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _draw(self) -> None:
        it = self.item
        mode = (
            "divider"
            if it.tag in ("divider", "interrupt")
            else it.tag
            if it.tag in ("task", "retry-ok", "cli-info")
            else "notice"
        )
        if mode != self._mode:
            if self.inner is not None:
                self.lay.removeWidget(self.inner)
                self.inner.deleteLater()
            self._mode = mode
            self.inner = self._build(mode)
            self.lay.addWidget(self.inner)
        self._fill()
        self._paint()

    def _build(self, mode: str) -> QWidget:
        if mode == "divider":
            self.TOP, self.BOTTOM = 18, 18
            return Divider(self.item.text)
        box = QWidget()
        box.setObjectName("notice")
        box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hl = QHBoxLayout(box)
        if mode == "notice":
            self.TOP, self.BOTTOM = 10, 10
            hl.setContentsMargins(14, 10, 14, 10)
            hl.setSpacing(10)
        else:
            self.TOP, self.BOTTOM = (10, 10) if mode == "task" else (6, 6)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(8)
        self.ico = icon_label("info", theme.tokens().text_3, 14 if mode == "notice" else 12)
        hl.addWidget(self.ico, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(self.text)
        self.md: Optional[MarkdownView] = None
        if mode == "cli-info":
            self.md = MarkdownView(compact=True, quiet=True)
            self.md.setVisible(False)
            col.addWidget(self.md)
        self.detail: Optional[QPlainTextEdit] = None
        if mode == "notice":
            self.detail = QPlainTextEdit()
            self.detail.setReadOnly(True)
            self.detail.setMaximumHeight(220)
            self.detail.setVisible(False)
            col.addWidget(self.detail)
        self.link = QPushButton()
        self.link.setProperty("kind", "link")
        self.link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.link.clicked.connect(self._toggle)
        self.link.setVisible(False)
        col.addWidget(self.link, 0, Qt.AlignmentFlag.AlignLeft)
        hl.addLayout(col, 1)
        return box

    def _fill(self) -> None:
        it = self.item
        if self._mode == "divider":
            self.inner.set_text(it.text)  # type: ignore[union-attr]
            return
        if self._mode == "cli-info":
            first = re.split(r"(?<=\.)\s", it.text)[0]
            self.text.setText(first)
            self.text.setVisible(not self._open)
            if self.md is not None:
                self.md.setVisible(self._open)
                if self._open:
                    self.md.set_text(it.text)
            self.link.setText("Less" if self._open else "More")
            self.link.setVisible(len(it.text) > len(first))
            self.link.setAccessibleName(self.link.text())
            return
        count = f" ×{it.count}" if it.count and it.count > 1 and self._mode == "notice" else ""
        self.text.setText(
            html.escape(it.text) + (f'<span style="color:{theme.tokens().text_3}">{count}</span>' if count else "")
        )
        if self.detail is not None:
            self.detail.setPlainText(it.detail or "")
            self.detail.setVisible(bool(it.detail) and self._open)
            self.link.setText("Hide details" if self._open else "Details")
            self.link.setAccessibleName(self.link.text())
            self.link.setVisible(bool(it.detail))

    def _toggle(self) -> None:
        self._open = not self._open
        self._fill()

    def _paint(self) -> None:
        t = theme.tokens()
        it = self.item
        if self._mode == "divider" or self.inner is None:
            return
        if self._mode == "notice":
            bg = t.err_wash if it.level == "error" else t.accent_wash if it.level == "warning" else t.fill_1
            ico = "circle-alert" if it.level == "error" else "triangle-alert" if it.level == "warning" else "info"
            color = t.err if it.level == "error" else t.accent_text if it.level == "warning" else t.text_3
            self.inner.setStyleSheet(
                f"#notice {{ background: {theme.blend(bg, t.canvas).name()}; border-radius: {theme.R3}px; }}"
            )
            set_icon(self.ico, ico, color, 14)
            style_label(self.text, theme.FS_UI, t.text, extra="line-height: 155%;")
            self.link.setStyleSheet(
                f"QPushButton {{ font-size: {pt(theme.FS_SMALL)}; color: {t.text_3}; margin-top: 2px; }} QPushButton:hover {{ color: {t.text}; }}"
            )
            if self.detail is not None:
                self.detail.setStyleSheet(
                    f"QPlainTextEdit {{ background: {t.inset}; border: none; border-radius: {theme.R2}px; padding: 8px 10px; font-size: {pt(12)}; color: {t.text_2}; margin: 8px 0 2px 0; }}"
                )
            return
        if self._mode == "task":
            warn = it.level == "warning"
            set_icon(self.ico, "circle-alert" if warn else "circle-check", t.accent_text if warn else t.ok, 12)
        elif self._mode == "retry-ok":
            set_icon(self.ico, "info", t.text_3, 12)
        else:
            set_icon(self.ico, "info", t.accent_text if it.level == "warning" else t.text_3, 12)
        self.inner.setStyleSheet("")
        style_label(self.text, theme.FS_SMALL, t.text_3, extra="line-height: 155%;")
        self.link.setStyleSheet(
            f"QPushButton {{ font-size: {pt(theme.FS_SMALL)}; color: {t.text_2}; margin-left: 6px; }} QPushButton:hover {{ color: {t.text}; }}"
        )

    def find_text(self) -> str:
        return self.item.text


class Compact(Row):
    TOP, BOTTOM = 18, 18

    def __init__(self, item: CompactItem, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.item = item
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.divider = Divider("")
        lay.addWidget(self.divider)
        self.refresh(item.rev)

    def _draw(self) -> None:
        it = self.item
        text = "Conversation compacted" + ("" if it.trigger == "manual" else " automatically")
        if it.pre_tokens:
            text += f" · {tokens(it.pre_tokens)} → {tokens(it.post_tokens) if it.post_tokens else 'summary'}"
        self.divider.set_text(text)

    def find_text(self) -> str:
        return self.divider.label.text()


class CommandOutput(Row):
    TOP, BOTTOM = 10, 10

    def __init__(self, item: CommandOutputItem, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.item = item
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.box = QWidget()
        self.box.setObjectName("cmd-output")
        self.box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bl = QVBoxLayout(self.box)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.ico = icon_label("square-terminal", theme.tokens().text_3, 12)
        head.addWidget(self.ico)
        self.title = QLabel("Command output")
        head.addWidget(self.title)
        head.addStretch(1)
        bl.addLayout(head)
        self.md = MarkdownView(ctx.cwd, compact=True)
        bl.addWidget(self.md)
        lay.addWidget(self.box)
        self.refresh(item.rev)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _draw(self) -> None:
        self.md.set_text(self.item.text)

    def _paint(self) -> None:
        t = theme.tokens()
        self.box.setStyleSheet(
            f"#cmd-output {{ background: {theme.blend(t.fill_1, t.canvas).name()}; border-radius: {theme.R3}px; }}"
        )
        set_icon(self.ico, "square-terminal", t.text_3, 12)
        style_label(self.title, theme.FS_MICRO, t.text_3, 600)

    def find_text(self) -> str:
        return self.item.text


class ResultFooter(Row):
    """✱ duration · tokens · cost, with "Fork from here" on hover; a failed turn is an error notice."""

    TOP, BOTTOM = 8, 20

    def __init__(self, item: ResultItem, ctx: ToolCtx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.item = item
        self.ctx = ctx
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.lay = lay
        self.inner: Optional[QWidget] = None
        self._ok: Optional[bool] = None
        self.refresh(item.rev)
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _draw(self) -> None:
        it = self.item
        if self._ok != it.ok:
            if self.inner is not None:
                self.lay.removeWidget(self.inner)
                self.inner.deleteLater()
            self._ok = it.ok
            self.inner = self._build_ok() if it.ok else self._build_err()
            self.lay.addWidget(self.inner)
        self._fill()
        self._paint()

    def _build_ok(self) -> QWidget:
        self.TOP, self.BOTTOM = 8, 20
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        self.mark = QLabel("✱")
        hl.addWidget(self.mark)
        self.dur = QLabel()
        hl.addWidget(self.dur)
        self.detail = QLabel()
        hl.addWidget(self.detail)
        self.fork = QPushButton(" Fork from here")
        self.fork.setProperty("kind", "link")
        self.fork.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fork.setToolTip("Start a new session that continues from this point")
        self.fork.setAccessibleName("Fork from here")
        self.fork.clicked.connect(lambda: store.fork_session(self.ctx.session_id, self.item.chain_uuid))
        hl.addWidget(self.fork)
        hl.addStretch(1)
        self.detail.setVisible(False)
        self.fork.setVisible(False)
        return w

    def _build_err(self) -> QWidget:
        self.TOP, self.BOTTOM = 10, 10
        it = self.item
        text = (
            "Stopped: reached the turn limit"
            if it.subtype == "error_max_turns"
            else "Stopped: reached the budget limit"
            if it.subtype == "error_max_budget_usd"
            else it.text or "The turn ended with an error"
        )
        n = NoticeItem(
            id=it.id, level="error", text=text, detail="\n".join(it.errors) if it.errors else None, rev=it.rev
        )
        self.notice = Notice(n)
        return self.notice

    def _fill(self) -> None:
        it = self.item
        if not it.ok:
            return
        self.dur.setText(duration(it.duration_ms))
        parts = []
        if it.output_tokens:
            parts.append(f"{tokens(it.output_tokens)} tokens")
        if it.cost_usd:
            parts.append(cost(it.cost_usd))
        self.detail.setText(" · ".join(parts))
        self.fork.setEnabled(bool(it.chain_uuid))
        tip = duration(it.duration_ms)
        if it.output_tokens:
            tip += f" · {tokens(it.output_tokens)} tokens out"
        if it.input_tokens:
            tip += f" · {tokens(it.input_tokens)} in"
        if it.cost_usd:
            tip += f" · {cost(it.cost_usd)}"
        self.setToolTip(tip)

    def enterEvent(self, e) -> None:  # noqa: N802
        if self.item.ok:
            self.detail.setVisible(bool(self.detail.text()))
            self.fork.setVisible(bool(self.item.chain_uuid))
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        if self.item.ok:
            self.detail.setVisible(False)
            self.fork.setVisible(False)
        super().leaveEvent(e)

    def _paint(self) -> None:
        if not self.item.ok or self.inner is None:
            return
        t = theme.tokens()
        style_label(self.mark, 12, t.accent, extra="margin-right: -2px;")
        style_label(self.dur, theme.FS_MICRO, t.text_3)
        style_label(self.detail, theme.FS_MICRO, t.text_3)
        self.fork.setIcon(icons.icon("git-fork", t.text_3, 12, hover=t.text))
        self.fork.setIconSize(QSize(12, 12))
        self.fork.setStyleSheet(
            f"QPushButton {{ font-size: {pt(theme.FS_MICRO)}; color: {t.text_3}; text-decoration: none; }} QPushButton:hover {{ color: {t.text}; }}"
        )


# ---------------------------------------------------------------- the working line


class WorkingLine(Row):
    """The breathing mark, a verb from the composing room, elapsed time and thinking tokens — shown
    while a turn runs and no fold on the page is carrying the mark."""

    TOP, BOTTOM = 10, 4

    def __init__(self, session_id: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session_id
        self.verb = random.choice(VERBS)
        self.start_ts = 0.0
        self.quiet = False
        self.setFixedHeight(30)
        hl = QHBoxLayout(self)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)
        self.mark = WorkingMark(15)
        hl.addWidget(self.mark)
        self.text = QLabel()
        hl.addWidget(self.text)
        self.meta = QLabel()
        hl.addWidget(self.meta)
        hl.addStretch(1)
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._fill)
        self.setVisible(False)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def update_state(self, start_ts: float, quiet: bool) -> None:
        self.start_ts, self.quiet = start_ts, quiet
        self._fill()

    def _fill(self) -> None:
        r = store.rt.get(self.sid)
        status = r.status if r else ""
        compacting = bool(r and r.st.get("compacting"))
        show = status in ("running", "starting") and (not self.quiet or compacting)
        if not show:
            self.setVisible(False)
            self._tick.stop()
            return
        self.setVisible(True)
        if not self._tick.isActive():
            self._tick.start()
        self.text.setText(
            "Compacting the conversation"
            if compacting
            else "Starting Claude Code"
            if status == "starting"
            else f"{self.verb}…"
        )
        elapsed = time.time() * 1000 - self.start_ts if self.start_ts else 0
        meta = duration(elapsed) if elapsed > 1000 else ""
        if r and r.thinking_tokens:
            meta += f"{' · ' if meta else ''}{tokens(r.thinking_tokens)} thinking tokens"
        self.meta.setText(meta)

    def _paint(self) -> None:
        t = theme.tokens()
        style_label(self.text, theme.FS_UI, t.text_3, 500)  # .working: text-3, the verb at 500
        style_label(self.meta, theme.FS_SMALL, t.text_3)


# ---------------------------------------------------------------- the empty-conversation skeleton


class LoadingSkeleton(QWidget):
    """Three faint bars where the first rows will be (``.transcript-loading``)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(10)
        self.bars: List[QWidget] = []
        for short in (False, True, False):
            b = QWidget()
            b.setObjectName("sk")
            b.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            b.setFixedHeight(14)
            b.setMaximumWidth(260 if short else 16777215)
            lay.addWidget(b)
            self.bars.append(b)
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _paint(self) -> None:
        t = theme.tokens()
        self.setStyleSheet(
            f"#sk {{ background: {theme.blend(t.fill_1, t.canvas).name()}; border-radius: {theme.R2}px; }}"
        )
