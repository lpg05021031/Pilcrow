"""What blocks paint with: fonts and metrics, documents, static text, icons, the pixmap cache, and
the decorations QTextDocument cannot draw itself.

``Measurer`` is the one object blocks measure and paint through. It caches fonts, font metrics,
static texts and icons, builds Markdown and HTML documents laid out at a width, and carries the
estimator's metrics and calibration. ``PixmapCache`` keeps finished blocks as pixmaps under a byte
budget. ``draw_doc`` paints a document clipped to the exposed part; ``paint_decor`` draws the pill
behind inline code, the rule beside a quote, the well of a code block and the hairlines of a table
from the document's layout (ported from widgets/transcript/items.py MarkdownView).
"""

from __future__ import annotations

import html
import math
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QStaticText,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextOption,
    QTextTable,
    QTransform,
)

from .. import icons, markdown, theme
from .estimate import Calibration, Metrics

Rect = Tuple[int, int, int, int]
FULL = "full"


def rect_of(r: Rect) -> QRect:
    return QRect(r[0], r[1], r[2], r[3])


# ---------------------------------------------------------------- documents


def tidy_doc(doc: QTextDocument) -> None:
    """No margin above the first block or under the last; the empty paragraph QTextDocument leaves
    after a code block's table takes no room."""
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
            cur = QTextCursor(b)
            cf = QTextCharFormat()
            cf.setFontPointSize(1)
            cur.setBlockCharFormat(cf)
            bf = cur.blockFormat()
            bf.setTopMargin(0)
            bf.setBottomMargin(0)
            cur.setBlockFormat(bf)


def doc_height(doc: QTextDocument) -> int:
    return max(1, int(math.ceil(doc.size().height())))


def doc_pos_at(doc: QTextDocument, x: float, y: float) -> Optional[int]:
    """The text position under a point of a laid-out document, or None beside the words. A fixed
    line height leaves a strip of leading above each line that Qt's fuzzy hit test answers with a
    position far from it (the end of the document); a point whose answer lies on another line is
    tried a little lower, on the line it belongs to."""
    lay = doc.documentLayout()
    pos = -1
    for dy in (0, 3, 6, 9):
        pos = lay.hitTest(QPointF(x, y + dy), Qt.HitTestAccuracy.FuzzyHit)
        if pos < 0:
            return None
        r = lay.blockBoundingRect(doc.findBlock(pos))
        if r.top() - 3 <= y + dy <= r.bottom() + 4:
            return pos  # the position is on a line that holds the point
    return pos


def draw_doc(
    p: QPainter,
    doc: QTextDocument,
    x: int,
    y: int,
    clip: Optional[QRect],
    color: QColor,
    selections: Optional[List[Any]] = None,
    sel_bg: Optional[QColor] = None,
    sel_fg: Optional[QColor] = None,
) -> None:
    """Paint a laid-out document with its top-left at (x, y); only the lines inside ``clip`` are drawn."""
    p.save()
    p.translate(x, y)
    ctx = QAbstractTextDocumentLayout.PaintContext()
    pal = QPalette(ctx.palette)
    pal.setColor(QPalette.ColorRole.Text, color)
    if sel_bg is not None:
        pal.setColor(QPalette.ColorRole.Highlight, sel_bg)
    if sel_fg is not None:
        pal.setColor(QPalette.ColorRole.HighlightedText, sel_fg)
    ctx.palette = pal
    if clip is not None:
        ctx.clip = QRectF(clip.x() - x, clip.y() - y, clip.width(), clip.height())
    if selections:
        ctx.selections = selections
    doc.documentLayout().draw(p, ctx)
    p.restore()


def selection(doc: QTextDocument, a: int, b: int, bg: QColor, fg: Optional[QColor] = None) -> Any:
    """One highlighted range for ``draw_doc``."""
    sel = QAbstractTextDocumentLayout.Selection()
    end = max(0, doc.characterCount() - 1)  # a range may reach past the words (a source is longer)
    cur = QTextCursor(doc)
    cur.setPosition(min(max(0, a), end))
    cur.setPosition(min(max(0, b), end), QTextCursor.MoveMode.KeepAnchor)
    sel.cursor = cur
    fmt = QTextCharFormat()
    fmt.setBackground(bg)
    if fg is not None:
        fmt.setForeground(fg)
    sel.format = fmt
    return sel


def find_ranges(doc: QTextDocument, needle: str, limit: int = 2000) -> List[Tuple[int, int]]:
    """Document positions of every occurrence of ``needle`` (case-insensitive), skipping the word
    joiners inline code carries so that a match may span them."""
    if not needle:
        return []
    raw = doc.toPlainText()
    pos = [i for i, ch in enumerate(raw) if ch != "⁠"]
    text = "".join(raw[i] for i in pos).lower()
    n = needle.lower()
    out: List[Tuple[int, int]] = []
    i = text.find(n)
    while i >= 0 and len(out) < limit:
        out.append((pos[i], pos[i + len(n) - 1] + 1))
        i = text.find(n, i + len(n))
    return out


# ---------------------------------------------------------------- decorations (items.py MarkdownView)


def _block_height(b: Any, br: QRectF) -> float:
    tl = b.layout()
    n = tl.lineCount() if tl is not None else 0
    if not n:
        return br.height()
    last = tl.lineAt(n - 1)
    return max(br.height(), last.y() + last.height())


_PAD_CHARS = markdown.PILL_PAD + markdown.NO_BREAK + " "


def _pills(p: QPainter, b: Any, br: QRectF, color: QColor) -> None:
    """The pill under each inline code span of a block: one rounded rectangle per line it covers.
    A span is the run of consecutive fragments carrying the transparent-background marker (its pads
    are fragments of their own, at a smaller size)."""
    tl = b.layout()
    if tl is None or tl.lineCount() == 0:
        return
    text = b.text()
    runs: List[Tuple[int, int, float, float]] = []  # start, end (block positions), ascent, descent
    it = b.begin()
    while not it.atEnd():
        fr = it.fragment()
        it += 1
        bg = fr.charFormat().background()
        if bg.style() == Qt.BrushStyle.NoBrush or bg.color().alpha() != 0:
            continue
        fm = QFontMetricsF(fr.charFormat().font())
        start = fr.position() - b.position()
        end = start + fr.length()
        if runs and runs[-1][1] == start:
            s0, _e, asc, desc = runs[-1]
            runs[-1] = (s0, end, max(asc, fm.ascent()), max(desc, fm.descent()))
        else:
            runs.append((start, end, fm.ascent(), fm.descent()))
    for start, end, asc, desc in runs:
        line = tl.lineForTextPosition(start)
        while line.isValid() and line.textStart() < end:
            a = max(start, line.textStart())
            z = min(end, line.textStart() + line.textLength())
            # a span that wrapped leaves its padding alone on a line: no pill under nothing
            if text[a:z].strip(_PAD_CHARS):
                x1 = line.cursorToX(a)[0]
                x2 = line.cursorToX(z)[0]
                base = br.top() + line.y() + line.ascent()
                p.setBrush(color)
                p.drawRoundedRect(QRectF(br.left() + x1, base - asc - 2, x2 - x1, asc + desc + 4), 5, 5)
            if line.lineNumber() + 1 >= tl.lineCount():
                break
            line = tl.lineAt(line.lineNumber() + 1)


def table_rect(doc: QTextDocument, lay: Any, table: QTextTable) -> QRectF:
    """The rectangle a table (a prose table or a code well) covers, in document coordinates. The
    layout's frame rectangle for a table starts at the first cell's content (inside its padding) and
    is as tall as the table with both its margins; the cell's padding and the margins are taken off."""
    first = table.cellAt(0, 0)
    cf = first.format().toTableCellFormat()
    top_block = lay.blockBoundingRect(first.firstCursorPosition().block())
    top = top_block.top() - cf.topPadding()
    left = top_block.left() - cf.leftPadding()
    fr = lay.frameBoundingRect(table)
    tf = table.format()
    height = fr.height() - tf.topMargin() - tf.bottomMargin()
    return QRectF(left, top, fr.width(), max(0.0, height))


def _table_rules(p: QPainter, lay: Any, table: QTextTable, fr: QRectF, hair: QColor, hair2: QColor) -> None:
    rows = table.rows()
    for r in range(rows):
        if r + 1 < rows:
            nxt = table.cellAt(r + 1, 0)
            pad = nxt.format().toTableCellFormat().topPadding()
            bottom = lay.blockBoundingRect(nxt.firstCursorPosition().block()).top() - pad
        else:
            bottom = fr.bottom()
        p.fillRect(QRectF(fr.left(), bottom - 1, fr.width(), 1), hair2 if r == 0 else hair)


def paint_decor(p: QPainter, doc: QTextDocument, x: int, y: int, clip: Optional[QRect], t: theme.Tokens) -> None:
    """The decorations under the text of a Markdown document whose top-left is at (x, y)."""
    lay = doc.documentLayout()
    p.save()
    p.translate(x, y)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    pill = theme.blend(t.fill_2, t.canvas)
    quote = theme.blend(t.hair_2, t.canvas)
    inset = QColor(t.inset)
    hair, hair2 = theme.blend(t.hair, t.canvas), theme.blend(t.hair_2, t.canvas)
    if clip is not None:
        view = QRectF(clip.x() - x, clip.y() - y, clip.width(), clip.height())
    else:
        view = QRectF(0, 0, doc.size().width(), doc.size().height())
    for frame in doc.rootFrame().childFrames():
        if not isinstance(frame, QTextTable):
            continue
        fr = table_rect(doc, lay, frame)
        if not fr.intersects(view):
            continue
        if frame.format().borderCollapse():
            p.setBrush(inset)
            p.setPen(Qt.PenStyle.NoPen if t.dark else QPen(hair, 1))
            p.drawRoundedRect(fr.adjusted(0.5, 0.5, -0.5, -0.5) if not t.dark else fr, theme.R3, theme.R3)
            p.setPen(Qt.PenStyle.NoPen)
        else:
            _table_rules(p, lay, frame, fr, hair, hair2)
    pos = lay.hitTest(QPointF(0, view.top()), Qt.HitTestAccuracy.FuzzyHit)
    b = doc.findBlock(pos) if pos >= 0 else doc.firstBlock()
    while b.isValid():
        br = lay.blockBoundingRect(b)
        if br.top() > view.bottom():
            break
        if br.bottom() >= view.top():
            bf = b.blockFormat()
            if bf.leftMargin() == markdown.QUOTE_INDENT and b.textList() is None:
                h = _block_height(b, br)
                nxt = b.next()
                if nxt.isValid() and nxt.blockFormat().leftMargin() == markdown.QUOTE_INDENT:
                    h += bf.bottomMargin()
                p.fillRect(QRectF(br.left(), br.top(), 2, h), quote)
            _pills(p, b, br, pill)
        b = b.next()
    p.restore()


# ---------------------------------------------------------------- shapes


def rounded(
    p: QPainter,
    rect: QRect,
    fill: Optional[QColor],
    border: Optional[QColor],
    radius: float,
    width: float = 1.0,
) -> None:
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(rect)
    if border is not None:
        p.setPen(QPen(border, width))
        r = r.adjusted(width / 2, width / 2, -width / 2, -width / 2)
    else:
        p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill if fill is not None else Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(r, radius, radius)


# ---------------------------------------------------------------- the pixmap cache


class PixmapCache:
    """Finished blocks as pixmaps, least recently painted first out, under a byte budget."""

    def __init__(self, budget: int = 48 * 1024 * 1024) -> None:
        self.budget = budget
        self.bytes = 0
        self._items: OrderedDict[Tuple, Tuple[QPixmap, int]] = OrderedDict()
        self._by_block: Dict[str, Tuple] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: Tuple) -> Optional[QPixmap]:
        hit = self._items.get(key)
        if hit is None:
            self.misses += 1
            return None
        self._items.move_to_end(key)
        self.hits += 1
        return hit[0]

    def put(self, key: Tuple, pm: QPixmap) -> None:
        old = self._by_block.get(key[0])
        if old is not None and old != key:
            self._drop(old)
        size = pm.width() * pm.height() * 4
        if size > self.budget // 4:
            return  # too big to be worth keeping
        self._items[key] = (pm, size)
        self._by_block[key[0]] = key
        self.bytes += size
        while self.bytes > self.budget and self._items:
            k, (_pm, sz) = self._items.popitem(last=False)
            self._by_block.pop(k[0], None)
            self.bytes -= sz

    def _drop(self, key: Tuple) -> None:
        hit = self._items.pop(key, None)
        if hit is not None:
            self.bytes -= hit[1]
        self._by_block.pop(key[0], None)

    def drop_block(self, block_key: str) -> None:
        k = self._by_block.get(block_key)
        if k is not None:
            self._drop(k)

    def clear(self) -> None:
        self._items.clear()
        self._by_block.clear()
        self.bytes = 0


# ---------------------------------------------------------------- the measurer


class Measurer:
    """Fonts, metrics, documents, static texts and icons for one theme and device pixel ratio."""

    def __init__(self) -> None:
        self.calibration = Calibration()
        self.pixmaps = PixmapCache()
        self._fonts: Dict[Tuple, QFont] = {}
        self._fms: Dict[Tuple, QFontMetricsF] = {}
        self._static: OrderedDict[Tuple, QStaticText] = OrderedDict()
        self._checks: Dict[Tuple, QPixmap] = {}
        self.refresh()

    def refresh(self) -> None:
        """After a theme change or a font-scale change: new fonts, new metrics, empty caches."""
        self.tokens = theme.tokens()
        self.theme_name = self.tokens.name
        app = QGuiApplication.instance()
        self.dpr = float(app.devicePixelRatio()) if app is not None else 1.0
        self._fonts.clear()
        self._fms.clear()
        self._static.clear()
        self._checks.clear()
        self.pixmaps.clear()
        self.prose = markdown.metrics(False)
        self.compact = markdown.metrics(True)
        ui = self.fm(self.font(theme.FS_BODY))
        mono = self.fm(self.font(theme.MONO_SIZE, mono=True))
        self.metrics = Metrics(
            avg_w=ui.averageCharWidth(),
            em_w=ui.horizontalAdvance("中"),
            mono_w=mono.horizontalAdvance("0"),
            line=self.prose.line,
            mono_line=markdown.px(theme.MONO_SIZE * 1.6),
            para=self.prose.para,
            code_pad=30 + 13 + 8,
        )
        self.metrics_compact = Metrics(
            avg_w=self.fm(self.font(theme.FS_UI)).averageCharWidth(),
            em_w=self.fm(self.font(theme.FS_UI)).horizontalAdvance("中"),
            mono_w=self.metrics.mono_w,
            line=self.compact.line,
            mono_line=self.metrics.mono_line,
            para=self.compact.para,
            code_pad=self.metrics.code_pad,
        )

    # ---- fonts
    def font(self, size: float, weight: int = 400, mono: bool = False, italic: bool = False) -> QFont:
        key = (round(size, 2), int(weight), mono, italic)
        f = self._fonts.get(key)
        if f is None:
            f = theme.font_mono(size, weight) if mono else theme.font_ui(size, weight, italic)
            self._fonts[key] = f
        return f

    def fm(self, font: QFont) -> QFontMetricsF:
        key = (font.family(), font.pixelSize(), font.weight(), font.italic())
        m = self._fms.get(key)
        if m is None:
            m = QFontMetricsF(font)
            self._fms[key] = m
        return m

    def text_w(self, text: str, font: QFont) -> float:
        return self.fm(font).horizontalAdvance(text)

    def elide(self, text: str, font: QFont, width: int) -> str:
        return self.fm(font).elidedText(text, Qt.TextElideMode.ElideRight, max(8, width))

    def static(self, text: str, font: QFont) -> QStaticText:
        key = (text, font.family(), font.pixelSize(), font.weight(), font.italic())
        st = self._static.get(key)
        if st is None:
            st = QStaticText(text)
            st.setTextFormat(Qt.TextFormat.PlainText)
            opt = QTextOption()
            opt.setWrapMode(QTextOption.WrapMode.NoWrap)
            st.setTextOption(opt)
            st.prepare(QTransform(), font)
            self._static[key] = st
            if len(self._static) > 4000:
                self._static.popitem(last=False)
        else:
            self._static.move_to_end(key)
        return st

    def draw_text(self, p: QPainter, x: float, y: float, text: str, font: QFont, color: QColor) -> QSize:
        """Draw a single line with its top-left at (x, y); returns the size it took."""
        st = self.static(text, font)
        p.setFont(font)
        p.setPen(color)
        p.drawStaticText(QPointF(x, y), st)
        return st.size().toSize()

    def line_h(self, font: QFont) -> int:
        return int(math.ceil(self.fm(font).height()))

    # ---- icons
    def icon(self, name: str, color: Any, size: int) -> QPixmap:
        c = self.tokens.color(color) if isinstance(color, str) and not color.startswith("#") else color
        return icons.pixmap(name, c, size)

    def color(self, name: Any) -> QColor:
        if isinstance(name, QColor):
            return name
        if isinstance(name, str) and (name.startswith("#") or name.startswith("rgb")):
            return theme.qcolor(name)
        return self.tokens.color(name)

    # ---- heights without documents
    def fast_height(self, text: str, width: int, font: QFont, line_h: Optional[int] = None) -> int:
        """Lines Qt would break ``text`` into at ``width`` (its real line breaking, no document)."""
        if not text:
            return 0
        fm = self.fm(font)
        br = fm.boundingRect(QRectF(0, 0, max(40, width), 1e7), Qt.TextFlag.TextWordWrap, text)
        lines = max(1, int(round(br.height() / max(1.0, fm.height()))))
        return lines * (line_h or self.line_h(font))

    # ---- documents
    def markdown_doc(
        self,
        src: str,
        width: int,
        compact: bool = False,
        quiet: bool = False,
        streaming: bool = False,
        cwd: str = "",
        line_height: Optional[int] = None,
        size: Optional[float] = None,
    ) -> QTextDocument:
        """A laid-out document for one Markdown source (a top-level block or a whole text). ``size``
        picks a style of its own (a thought at 13.5px) over the prose and compact ones."""
        m = markdown.metrics_for(size) if size else markdown.metrics(compact or quiet)
        try:
            body = markdown.render_html(
                src, streaming=streaming, base_dir=cwd or None, compact=compact or quiet, width=width, m=m
            )
        except Exception:  # noqa: BLE001 - a Markdown failure must not take the row down
            body = f"<p>{html.escape(src)}</p>"
        body = link_paths(body)
        doc = self._doc(self.font(m.base), width)
        doc.setIndentWidth(m.list_indent)
        if markdown.CHECK_ON in body:
            for name, on in ((markdown.CHECK_ON, True), (markdown.CHECK_OFF, False)):
                doc.addResource(
                    QTextDocument.ResourceType.ImageResource, QUrl(name), self.check_pixmap(on, m.list_indent)
                )
        css = markdown.stylesheet(self.tokens, m)
        if line_height is not None and line_height != m.line:
            css += f" p {{ line-height: {line_height}px; }}"
        doc.setDefaultStyleSheet(css)
        doc.setHtml(body)
        tidy_doc(doc)
        doc.setTextWidth(width)
        return doc

    def html_doc(self, body: str, width: int, font: Optional[QFont] = None, css: str = "") -> QTextDocument:
        doc = self._doc(font or self.font(theme.FS_UI), width)
        if css:
            doc.setDefaultStyleSheet(css)
        doc.setHtml(body)
        tidy_doc(doc)
        doc.setTextWidth(width)
        return doc

    def plain_doc(self, text: str, width: int, font: QFont) -> QTextDocument:
        doc = self._doc(font, width)
        doc.setPlainText(text)
        doc.setTextWidth(width)
        return doc

    def _doc(self, font: QFont, width: int) -> QTextDocument:
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        doc.setDefaultFont(font)
        doc.setUseDesignMetrics(False)
        return doc

    def check_pixmap(self, on: bool, width: int) -> QPixmap:
        """A task list's box, drawn in the marker's place: ``width`` is the list indent the image
        spans (the box at its left, the gap to the words after it)."""
        key = ("check", on, width, self.theme_name)
        pm = self._checks.get(key)
        if pm is None:
            size = markdown.CHECK_SIZE
            dpr = self.dpr
            pm = QPixmap(int(width * dpr), int(size * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            t = self.tokens
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            rr = QRectF(0.75, 0.75, size - 1.5, size - 1.5)
            if on:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.blend(t.fill_3, t.canvas))
                p.drawRoundedRect(rr, 4, 4)
                p.drawPixmap(2, 2, icons.pixmap("check", QColor(t.text_2), size - 4))
            else:
                p.setPen(QPen(theme.blend(t.hair_3, t.canvas), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rr, 4, 4)
            p.end()
            self._checks[key] = pm
        return pm

    # ---- estimation
    def estimate_markdown(self, src: str, width: int, compact: bool = False) -> int:
        from .estimate import estimate_markdown

        return estimate_markdown(src, width, self.metrics_compact if compact else self.metrics)


def link_paths(body: str) -> str:
    """Inline code spans that look like file paths become links (``pilcrow-path://``)."""
    import re
    from urllib.parse import quote

    code_re = re.compile(r"<code( style=\"[^\"]*\")>([^<]*)</code>")

    def repl(m: re.Match[str]) -> str:
        ref = markdown.as_path_ref(html.unescape(m.group(2)))
        if not ref:
            return m.group(0)
        line = f"&l={ref['line']}" if ref.get("line") else ""
        return (
            f'<a href="pilcrow-path://open?p={quote(ref["path"])}{line}" style="text-decoration:none;color:inherit">'
            f"{m.group(0)}</a>"
        )

    return code_re.sub(repl, body)


# ---------------------------------------------------------------- paint state


class PaintState:
    """What a block needs to know while it paints: the tokens and measurer, the pointer and focus
    targets if they are on this block, the selection and find state, the exposed clip, the time."""

    __slots__ = (
        "m",
        "t",
        "hover",
        "focus",
        "pressed",
        "selection",
        "needle",
        "needle_current",
        "clip",
        "now",
        "caching",
    )

    def __init__(self, m: Measurer) -> None:
        self.m = m
        self.t = m.tokens
        self.hover: Optional[Any] = None
        self.focus: Optional[Any] = None
        self.pressed: Optional[Any] = None
        self.selection: Optional[Any] = None  # (a, b) positions, FULL, or None
        self.needle = ""
        self.needle_current = -1  # which occurrence in this block is the current match (-1 none)
        self.clip: Optional[QRect] = None
        self.now = 0.0
        self.caching = False  # painting into the pixmap cache: no hover, no selection, no clip

    def for_block(
        self, hover: Optional[Any], focus: Optional[Any], selection: Optional[Any], clip: Optional[QRect]
    ) -> PaintState:
        st = PaintState(self.m)
        st.hover, st.focus, st.pressed = hover, focus, self.pressed
        st.selection = selection
        st.needle, st.needle_current = self.needle, self.needle_current
        st.clip = clip
        st.now = self.now
        return st


__all__ = [
    "FULL",
    "Measurer",
    "PaintState",
    "PixmapCache",
    "Rect",
    "doc_height",
    "doc_pos_at",
    "draw_doc",
    "find_ranges",
    "link_paths",
    "paint_decor",
    "rect_of",
    "rounded",
    "selection",
    "table_rect",
    "tidy_doc",
]

_ = (Iterable,)
