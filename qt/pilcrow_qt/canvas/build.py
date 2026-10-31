"""Markdown → QTextDocument, built from markdown-it's token stream with a cursor.

The document used to be made by rendering the tokens to an HTML string, rewriting that string with
regular expressions and handing it to Qt's HTML importer with a stylesheet; half of every exact
measurement went into that round trip rather than into the layout. This module writes the same
blocks, lists, tables, images and character formats straight into the document: no HTML, no
stylesheet, no importer. What the painters read back is unchanged (paint.py): a code well is a
border-collapsed table, a quote is a block whose left margin is QUOTE_INDENT per level, an inline
code span is a run of fragments with the transparent-background marker, a path in a code span is an
anchor, a task list's box is an image resource.

Margins follow the rule Qt's importer applied to the HTML: a block's top margin is the largest of
its own element's and those of every enclosing element its element is the first child of; its
bottom margin the largest of its own and those of every enclosing element it is the last child of.
A pass over the tokens decides for every container whether it is the first or the last child of
its parent; the containers open at a block are boxes on a stack, and the block's margins are read
up the stack while the chain of first (or last) children holds.

The growers (grow.py) use :func:`insert_inline` for the words of a list item or a table cell.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextImageFormat,
    QTextLength,
    QTextListFormat,
    QTextTable,
    QTextTableCellFormat,
    QTextTableFormat,
)

from .. import markdown, theme
from ..markdown import Metrics

# ``.md-code``: the well's head is 30px (8 + 14 + 8) with the language in micro text; the code sits
# in padding 0 14px 13px (the head's right padding leaves room for the Copy).
HEAD_PAD = (14, 10, 8, 8)  # left, right, top, bottom
CODE_PAD = (14, 14, 0, 13)
HEAD_LINE = 14
LIST_STYLES = (
    QTextListFormat.Style.ListDisc,
    QTextListFormat.Style.ListCircle,
    QTextListFormat.Style.ListSquare,
)
LIST_INSET = 40  # what Qt's importer gave a frame inside a list item, per level (the ``ul`` margin)
TRANSPARENT = QBrush(QColor(0, 0, 0, 0))
SPECIFIED = (
    QTextCharFormat.FontPropertiesInheritanceBehavior.FontPropertiesSpecifiedOnly
)  # a font's own properties only
_ALIGN = {
    "left": Qt.AlignmentFlag.AlignLeft,
    "right": Qt.AlignmentFlag.AlignRight,
    "center": Qt.AlignmentFlag.AlignHCenter,
}

Run = Tuple[str, Optional[str]]


class _Box:
    """An open container: its margins, whether it is the first and the last child of its parent, and
    what it is."""

    __slots__ = ("kind", "mt", "mb", "first", "last", "data")

    def __init__(self, kind: str, mt: int, mb: int, first: bool, last: bool, data: Any = None) -> None:
        self.kind = kind
        self.mt = mt
        self.mb = mb
        self.first = first
        self.last = last
        self.data = data


class _ListBox:
    __slots__ = ("fmt", "textlist", "task", "depth")

    def __init__(self, fmt: QTextListFormat, task: bool, depth: int) -> None:
        self.fmt = fmt
        self.textlist = None
        self.task = task
        self.depth = depth


class Inline:
    """What the words of a paragraph, an item or a cell are written with: the font to start from, the
    colour of the words (None: the painter's), and what the item or cell around them asks for."""

    __slots__ = ("font", "fg", "squeezed", "box_first", "list_box", "spacing")

    def __init__(self, font: QFont, fg: Optional[str], squeezed: bool = False, spacing: float = 0.0) -> None:
        self.font = font
        self.fg = fg
        self.squeezed = squeezed
        self.box_first = False  # the first paragraph of a list item: a task's box goes in front
        self.list_box: Optional[_ListBox] = None
        self.spacing = spacing  # a heading's letter spacing


class Builder:
    def __init__(
        self,
        doc: QTextDocument,
        m: Metrics,
        t: theme.Tokens,
        fonts: Any,
        width: int,
        cwd: str = "",
        streaming: bool = False,
        line_height: Optional[int] = None,
    ) -> None:
        self.doc = doc
        self.m = m
        self.t = t
        self.fonts = fonts  # Measurer: .font(size, weight, mono, italic)
        self.width = width
        self.cwd = cwd
        self.streaming = streaming
        self.lh = line_height if line_height is not None else m.line
        self.cur = QTextCursor(doc)
        self.stack: List[_Box] = []
        self.first_unused = True  # the document's own first block, not yet written to
        self._edges: Dict[int, Tuple[bool, bool]] = {}  # open token index → (first child, last child)
        self.quote_depth = 0
        self.bullet_depth = 0
        self.list_depth = 0
        self.used_checks = False
        self.used_images = False
        self.wells: List[str] = []
        self.nested_wells = False
        self._cf_cache: Dict[Tuple, QTextCharFormat] = {}
        self._fonts_held: List[QFont] = []
        self._code_px = markdown.px(markdown.mono_size())
        self._code_line = markdown.px(markdown.mono_size() * 1.6)
        self._ch = markdown.font_metrics(self._code_px, mono=True).horizontalAdvance("0")
        self._pill_px = markdown.pill_pad_px(m)
        self._task_lists: set = set()

    # ------------------------------------------------------------ the walk
    def run(self, tokens: Sequence[Any]) -> None:
        self._task_lists = _task_lists(tokens)
        self._edges = _edges(tokens)
        i = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]
            ty = tok.type
            if ty == "paragraph_open":
                self._open("para", 0, 0 if tok.hidden else self.m.para, i, hidden=bool(tok.hidden))
                self._text_block(self.m.line if tok.hidden else self.lh)
            elif ty == "paragraph_close":
                self._close()
            elif ty == "heading_open":
                level = int(tok.tag[1]) if len(tok.tag) == 2 and tok.tag[1].isdigit() else 1
                size, line, top, bottom = self.m.heading(level)
                self._open("heading", top, bottom, i, (size, level))
                self._text_block(line)
            elif ty == "heading_close":
                self._close()
            elif ty == "inline":
                self._inline(tok.children or [], i, tokens)
            elif ty == "blockquote_open":
                self.quote_depth += 1
                self._open("quote", 0, self.m.para, i)
            elif ty == "blockquote_close":
                self._close()
                self.quote_depth -= 1
            elif ty in ("bullet_list_open", "ordered_list_open"):
                self._open_list(tok, i)
            elif ty in ("bullet_list_close", "ordered_list_close"):
                self._close()
                self.list_depth -= 1
                if ty == "bullet_list_close":
                    self.bullet_depth -= 1
            elif ty == "list_item_open":
                self._open("item", self.m.li_gap, self.m.li_gap, i, {"block": False})
            elif ty == "list_item_close":
                self._close()
            elif ty in ("fence", "code_block"):
                lang = (tok.info or "").strip().split(" ")[0] if ty == "fence" else ""
                self._well(tok.content.rstrip("\n"), lang)
            elif ty == "table_open":
                i = self._table(tokens, i)
                continue
            elif ty == "hr":
                self._hr(i)
            elif ty == "html_block":
                self._open("para", 0, self.m.para, i)
                self._text_block(self.lh)
                self.cur.insertText(tok.content.rstrip("\n"), self._cf(self._inline_ctx().font, None))
                self._close()
            i += 1
        self._finish()

    # ------------------------------------------------------------ boxes and blocks
    def _open(self, kind: str, mt: int, mb: int, i: int, data: Any = None, hidden: bool = False) -> None:
        first, last = self._edges.get(i, (True, True))
        # a tight list item's paragraph is no element of its own: the words belong to the item
        self.stack.append(_Box("hidden" if hidden else kind, mt, mb, first, last, data))

    def _close(self) -> None:
        if self.stack:
            self.stack.pop()

    def _margins(self) -> Tuple[int, int]:
        """The margins of a block written now: its element's, and up the chain of enclosing elements
        while it is their first (for the top) or last (for the bottom) child."""
        boxes = [b for b in self.stack if b.kind != "hidden"]
        if not boxes:
            return 0, 0
        own = boxes[-1]
        mt, mb = own.mt, own.mb
        k = len(boxes) - 1
        while k > 0 and boxes[k].first:
            k -= 1
            mt = max(mt, boxes[k].mt)
        k = len(boxes) - 1
        while k > 0 and boxes[k].last:
            k -= 1
            mb = max(mb, boxes[k].mb)
        return mt, mb

    def _block_fg(self) -> Optional[str]:
        """The colour of the block itself (a list's marker, an empty block): the innermost container's."""
        t = self.t
        for box in reversed(self.stack):
            if box.kind == "quote":
                return t.text_2
            if box.kind == "item":
                return t.text_3
            if box.kind == "heading" and box.data[1] >= 4:
                return t.text_2
        return None

    def _text_fg(self) -> Optional[str]:
        """The colour of the words: inside a list item the text colour, whatever else is around them
        (the marker alone is quiet); in a quote and in a small heading the quiet colour."""
        t = self.t
        quiet = False
        for box in reversed(self.stack):
            if box.kind == "item":
                return t.text
            if box.kind == "quote" or (box.kind == "heading" and box.data[1] >= 4):
                quiet = True
        return t.text_2 if quiet else None

    def _block_font(self) -> QFont:
        for box in reversed(self.stack):
            if box.kind == "heading":
                return self.fonts.font(box.data[0], 600)
        return self.fonts.font(self.m.base)

    def _new_block(self, bf: QTextBlockFormat, bcf: QTextCharFormat) -> None:
        """Write a block with ``bf`` and the margins its place in the elements gives it: the document's
        first block if nothing has used it, else a new one."""
        mt, mb = self._margins()
        bf.setTopMargin(mt)
        bf.setBottomMargin(mb)
        if self.first_unused:
            self.first_unused = False
            self.cur.setBlockFormat(bf)
            self.cur.setBlockCharFormat(bcf)
        else:
            self.cur.insertBlock(bf, bcf)

    def _text_block(self, line_h: int) -> None:
        """A block for words: the item's own block when it is the first in a list item, a block
        indented by the list depth after that, a quote's margin when inside a quote."""
        bf = QTextBlockFormat()
        bf.setLineHeight(line_h, QTextBlockFormat.LineHeightTypes.MinimumHeight.value)
        if self.quote_depth:
            bf.setLeftMargin(markdown.QUOTE_INDENT * self.quote_depth)
        item = self._innermost("item")
        lst = self._innermost("list")
        in_list_block = item is not None and lst is not None and not item.data["block"]
        if item is not None and not in_list_block:
            bf.setIndent(self.list_depth)
        if in_list_block and lst.data.task:
            bf.setTextIndent(-self.m.list_indent)
        heading = self._innermost("heading")
        bcf = self._cf(self._block_font(), self._block_fg(), spacing=_spacing(heading.data[0]) if heading else 0.0)
        self._new_block(bf, bcf)
        if in_list_block:
            item.data["block"] = True
            lb: _ListBox = lst.data
            if lb.textlist is None:
                lb.textlist = self.cur.createList(lb.fmt)
            else:
                lb.textlist.add(self.cur.block())

    def _innermost(self, kind: str) -> Optional[_Box]:
        for box in reversed(self.stack):
            if box.kind == kind:
                return box
        return None

    def _open_list(self, tok: Any, i: int) -> None:
        self.list_depth += 1
        fmt = QTextListFormat()
        fmt.setIndent(self.list_depth)
        task = False
        if tok.type == "bullet_list_open":
            self.bullet_depth += 1
            task = i in self._task_lists
            fmt.setStyle(QTextListFormat.Style.ListStyleUndefined if task else LIST_STYLES[(self.bullet_depth - 1) % 3])
        else:
            fmt.setStyle(QTextListFormat.Style.ListDecimal)
            start = tok.attrGet("start") if hasattr(tok, "attrGet") else None
            if start is not None:
                try:
                    fmt.setStart(int(start))
                except (TypeError, ValueError):
                    pass
        self._open("list", 0, self.m.para, i, _ListBox(fmt, task, self.list_depth))

    def _hr(self, i: int) -> None:
        mg = markdown.px(self.m.base * 1.4) - 9
        self._open("hr", mg, mg, i)
        bf = QTextBlockFormat()
        bf.setProperty(QTextFormat.Property.BlockTrailingHorizontalRulerWidth, QTextLength())
        bcf = QTextCharFormat()
        bcf.setFont(self.fonts.font(self.m.base), SPECIFIED)
        self._new_block(bf, bcf)
        self._close()

    def _finish(self) -> None:
        doc = self.doc
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

    # ------------------------------------------------------------ inline content
    def _inline_ctx(self) -> Inline:
        heading = self._innermost("heading")
        return Inline(self._block_font(), self._text_fg(), spacing=_spacing(heading.data[0]) if heading else 0.0)

    def _inline(self, children: Sequence[Any], i: int, tokens: Sequence[Any]) -> None:
        ctx = self._inline_ctx()
        item = self._innermost("item")
        lst = self._innermost("list")
        if item is not None and lst is not None and i >= 2 and tokens[i - 1].type == "paragraph_open":
            if tokens[i - 2].type == "list_item_open":
                ctx.box_first = True
                ctx.list_box = lst.data
        insert_inline(self.cur, children, ctx, self)

    def _cf(
        self,
        font: QFont,
        fg: Optional[str],
        italic: bool = False,
        strike: bool = False,
        href: Optional[str] = None,
        pill: bool = False,
        spacing: float = 0.0,
    ) -> QTextCharFormat:
        """A character format from a font and a colour (cached: a document has few distinct ones; the
        fonts are the measurer's, one object per size and weight, so their identity is their key)."""
        key = (id(font), fg, italic, strike, href, pill, spacing)
        cf = self._cf_cache.get(key)
        if cf is None:
            cf = QTextCharFormat()
            cf.setFont(font, SPECIFIED)
            if fg:
                cf.setForeground(QColor(fg))
            if italic:
                cf.setFontItalic(True)
            if strike:
                cf.setFontStrikeOut(True)
            if pill:
                cf.setBackground(TRANSPARENT)
            if href:
                cf.setAnchor(True)
                cf.setAnchorHref(href)
            if spacing:
                cf.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
                cf.setFontLetterSpacing(spacing)
            self._cf_cache[key] = cf
            self._fonts_held.append(font)  # the key is the font's identity: keep it alive
        return cf

    # ------------------------------------------------------------ code wells
    def _inset(self) -> int:
        """How far a frame sits in: a quote's indent per quote, Qt's list margin per list."""
        return markdown.QUOTE_INDENT * self.quote_depth + LIST_INSET * self.list_depth

    def _well(self, code: str, lang: str) -> None:
        m, t, fonts = self.m, self.t, self.fonts
        tf = QTextTableFormat()
        tf.setBorder(0)
        tf.setCellPadding(0)
        tf.setCellSpacing(0)
        tf.setBorderCollapse(True)
        tf.setTopMargin(markdown.px(m.base * 0.6))
        tf.setBottomMargin(m.base)
        inset = self._inset()
        if inset:
            tf.setLeftMargin(inset)
            tf.setWidth(QTextLength(QTextLength.Type.FixedLength, max(40, self.width - inset)))
            self.nested_wells = True
        else:
            tf.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
        tf.setColumnWidthConstraints([QTextLength()])
        table = self.cur.insertTable(2, 1, tf)
        text_fg = self.t.text_2 if self.quote_depth else None
        # the head: the language in micro text
        head = table.cellAt(0, 0)
        hf = head.format().toTableCellFormat()
        hf.setLeftPadding(HEAD_PAD[0])
        hf.setRightPadding(HEAD_PAD[1])
        hf.setTopPadding(HEAD_PAD[2])
        hf.setBottomPadding(HEAD_PAD[3])
        label = self._cf(fonts.font(theme.FS_MICRO), t.text_3)
        hf.merge(label)
        head.setFormat(hf)
        hc = head.firstCursorPosition()
        bf = QTextBlockFormat()
        bf.setLineHeight(HEAD_LINE, QTextBlockFormat.LineHeightTypes.MinimumHeight.value)
        hc.setBlockFormat(bf)  # (the cell's format is the first block's character format: not set again)
        hc.insertText(markdown.lang_label(lang), label)
        # the code: a block per line with a hanging indent, the colours from the highlighter
        body = table.cellAt(1, 0)
        cf = body.format().toTableCellFormat()
        cf.setLeftPadding(CODE_PAD[0])
        cf.setRightPadding(CODE_PAD[1])
        cf.setTopPadding(CODE_PAD[2])
        cf.setBottomPadding(CODE_PAD[3])
        plain = self._cf(fonts.font(self._code_px, mono=True), text_fg)
        cf.merge(plain)
        body.setFormat(cf)
        bc = body.firstCursorPosition()
        lines = code.split("\n")
        runs = markdown.styled_lines(code, lang, t.name)
        for k, (line, line_runs) in enumerate(zip(lines, runs)):
            lbf = self._code_line_format(line)
            if k == 0:
                bc.setBlockFormat(lbf)
            else:
                bc.insertBlock(lbf, plain)
            for text, style in line_runs:
                if text:
                    bc.insertText(text, self._code_cf(style, text_fg) if style else plain)
        self.cur.setPosition(table.lastPosition() + 1)
        self.first_unused = False
        self.wells.append(code)

    def _code_line_format(self, line: str) -> QTextBlockFormat:
        lead = len(line) - len(line.lstrip(" \t"))
        cols = (
            lead + (markdown.TAB_COLS - 1) * line[:lead].count("\t") + markdown.WRAP_INDENT if len(line) > lead else 0
        )
        ind = int(cols * self._ch + 0.5)
        bf = QTextBlockFormat()
        bf.setLeftMargin(ind)
        bf.setTextIndent(-ind)
        bf.setLineHeight(self._code_line, QTextBlockFormat.LineHeightTypes.MinimumHeight.value)
        return bf

    def _code_cf(self, style: str, fg: Optional[str]) -> QTextCharFormat:
        colour = fg
        italic = False
        for part in style.split(";"):
            if part.startswith("color:"):
                colour = part[6:]
            elif part == "font-style:italic":
                italic = True
        return self._cf(self.fonts.font(self._code_px, mono=True), colour, italic)

    # ------------------------------------------------------------ tables
    def _table(self, tokens: Sequence[Any], i: int) -> int:
        """The table whose ``table_open`` is at ``i``; returns the index after ``table_close``."""
        rows: List[List[Tuple[str, str, Any]]] = []  # (tag, align, inline children)
        row: Optional[List[Tuple[str, str, Any]]] = None
        tag, align = "td", "left"
        j = i + 1
        n = len(tokens)
        while j < n and tokens[j].type != "table_close":
            tok = tokens[j]
            ty = tok.type
            if ty == "tr_open":
                row = []
            elif ty == "tr_close":
                if row is not None:
                    rows.append(row)
                row = None
            elif ty in ("th_open", "td_open"):
                tag = "th" if ty == "th_open" else "td"
                style = tok.attrGet("style") if hasattr(tok, "attrGet") else None
                align = (style or "").replace("text-align:", "").strip() or "left"
            elif ty == "inline" and row is not None:
                row.append((tag, align, tok.children or []))
            j += 1
        if rows:
            self._insert_tables(rows)
        return j + 1

    def _insert_tables(self, rows: List[List[Tuple[str, str, Any]]]) -> None:
        m, t, fonts = self.m, self.t, self.fonts
        ncol = max(len(r) for r in rows)
        widths, mins, _maxs, _hards = plan_columns(rows, m, self.width)
        sq = markdown.squeezed(widths, mins)
        top, bottom = markdown.px(m.base * 0.4), m.base
        chunk = markdown.TABLE_CHUNK
        if self.streaming and len(rows) > 1 + chunk:
            groups = [rows[: 1 + chunk]] + [rows[k : k + chunk] for k in range(1 + chunk, len(rows), chunk)]
        else:
            groups = [rows]
        inset = self._inset()
        in_item = self._innermost("item") is not None
        text_fg = self._text_fg()
        block_fg = self._block_fg()
        last = len(groups) - 1
        # the formats a cell can take, made once: th or td, by alignment
        cell_fmt: Dict[bool, QTextTableCellFormat] = {}
        cell_font: Dict[bool, QFont] = {}
        cell_ctx_fg: Dict[bool, Optional[str]] = {}
        for head in (True, False):
            font = fonts.font(m.table, 600 if head else 400)
            cf = QTextTableCellFormat()
            cf.setLeftPadding(0)
            cf.setRightPadding(markdown.CELL_PAD)
            cf.setTopPadding(7)
            cf.setBottomPadding(7)
            fg = t.text_2 if head else block_fg
            cf.merge(self._cf(font, fg))
            cell_fmt[head] = cf
            cell_font[head] = font
            cell_ctx_fg[head] = text_fg if text_fg is not None else (t.text_2 if head else None)
        block_fmt: Dict[str, QTextBlockFormat] = {}
        for align, flag in _ALIGN.items():
            bf = QTextBlockFormat()
            bf.setAlignment(flag)
            if in_item:
                bf.setLineHeight(m.line, QTextBlockFormat.LineHeightTypes.MinimumHeight.value)
            block_fmt[align] = bf
        for k, group in enumerate(groups):
            tf = QTextTableFormat()
            tf.setBorder(0)
            tf.setCellPadding(0)
            tf.setCellSpacing(0)
            tf.setBorderCollapse(False)
            tf.setTopMargin(top if k == 0 else 0)
            tf.setBottomMargin(bottom if k == last else 0)
            if inset:
                tf.setLeftMargin(inset)
            tf.setWidth(QTextLength(QTextLength.Type.FixedLength, sum(widths)))
            tf.setColumnWidthConstraints([QTextLength(QTextLength.Type.FixedLength, w) for w in widths])
            table = self.cur.insertTable(len(group), ncol, tf)
            for r, row in enumerate(group):
                for c in range(ncol):
                    tag, align, kids = row[c] if c < len(row) else ("td", "left", [])
                    head = tag == "th"
                    cell = table.cellAt(r, c)
                    cell.setFormat(cell_fmt[head])
                    cc = cell.firstCursorPosition()
                    cc.setBlockFormat(block_fmt.get(align, block_fmt["left"]))
                    if kids:
                        insert_inline(cc, kids, Inline(cell_font[head], cell_ctx_fg[head], squeezed=bool(sq[c])), self)
            self.cur.setPosition(table.lastPosition() + 1)
        self.first_unused = False


def _spacing(size: int) -> float:
    """A heading's letter spacing (``-0.012em``), on the 1/64 px grid Qt lays text out on."""
    return -round(size * 0.012 * 64) / 64


def _edges(tokens: Sequence[Any]) -> Dict[int, Tuple[bool, bool]]:
    """For every container token and leaf block token, whether it is the first and the last child of
    its parent (what the margins of its blocks take in)."""
    out: Dict[int, Tuple[bool, bool]] = {}
    n = len(tokens)
    opens: List[int] = []  # the open containers, by index
    for i, tok in enumerate(tokens):
        if tok.nesting == -1:
            if opens:
                j = opens.pop()
                out[j] = (out.get(j, (False, False))[0], i + 1 >= n or tokens[i + 1].nesting == -1)
            continue
        if tok.type == "inline":
            continue
        first = i == 0 if not opens else i == opens[-1] + 1
        if tok.nesting == 1:
            out[i] = (first, False)
            opens.append(i)
        else:
            out[i] = (first, i + 1 >= n or tokens[i + 1].nesting == -1)
    return out


# ---------------------------------------------------------------- inline words


def insert_inline(cur: QTextCursor, children: Sequence[Any], ctx: Inline, b: Builder) -> None:
    """Write the inline tokens of a paragraph, an item or a cell at ``cur``: words, emphasis, strong,
    strikethrough, links, code spans as pills, images, breaks."""
    m, t = b.m, b.t
    font = ctx.font
    fg = ctx.fg
    spacing = ctx.spacing
    italic = False
    base_w = weight = font.weight()
    strike = 0
    href: Optional[str] = None
    link_fg: Optional[str] = None
    kids = list(children)
    k = 0
    if ctx.box_first and ctx.list_box is not None and kids and kids[0].type == "text":
        mt = markdown.TASK_RE.match(kids[0].content)
        if mt:
            kids[0].content = kids[0].content[mt.end() :]
            img = QTextImageFormat()
            img.setName(markdown.CHECK_ON if mt.group(1) != " " else markdown.CHECK_OFF)
            img.setWidth(m.list_indent)
            img.setHeight(markdown.CHECK_SIZE)
            img.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignMiddle)
            img.setFont(font, SPECIFIED)
            img.setForeground(QColor(t.text_3))
            cur.insertImage(img)
            b.used_checks = True

    def fmt() -> QTextCharFormat:
        f = (
            font
            if weight == base_w
            else b.fonts.font(font.pixelSize(), weight, font.family() == (theme._MONO_FAMILY or ""))
        )
        return b._cf(f, link_fg or fg, italic, strike > 0, href, False, spacing)

    cf = fmt()  # the format of the words as they stand; remade when the markup around them changes
    while k < len(kids):
        tok = kids[k]
        ty = tok.type
        k += 1
        if ty == "text":
            if tok.content:
                cur.insertText(tok.content, cf)
        elif ty == "softbreak":
            cur.insertText(" ", cf)
        elif ty == "hardbreak":
            cur.insertText("\u2028", cf)
        elif ty == "em_open":
            italic = True
            cf = fmt()
        elif ty == "em_close":
            italic = False
            cf = fmt()
        elif ty == "strong_open":
            weight = 620
            cf = fmt()
        elif ty == "strong_close":
            weight = base_w
            cf = fmt()
        elif ty == "s_open":
            strike += 1
            cf = fmt()
        elif ty == "s_close":
            strike = max(0, strike - 1)
            cf = fmt()
        elif ty == "link_open":
            href = tok.attrGet("href") if hasattr(tok, "attrGet") else None
            link_fg = t.info
            cf = fmt()
        elif ty == "link_close":
            href = None
            link_fg = None
            cf = fmt()
        elif ty == "code_inline":
            _pill(cur, tok.content, ctx, b, href, link_fg, weight, italic, strike > 0)
        elif ty == "image":
            src = (tok.attrGet("src") if hasattr(tok, "attrGet") else None) or ""
            img = QTextImageFormat()
            img.setName(_image_name(src, b.cwd))
            img.setFont(font if weight == base_w else b.fonts.font(font.pixelSize(), weight), SPECIFIED)
            if href:
                img.setAnchor(True)
                img.setAnchorHref(href)
                img.setForeground(QColor(link_fg or t.info))
            cur.insertImage(img)
            b.used_images = True
        elif ty in ("html_inline", "entity"):
            cur.insertText(tok.content, cf)


def _pill(
    cur: QTextCursor,
    code: str,
    ctx: Inline,
    b: Builder,
    href: Optional[str],
    fg: Optional[str],
    weight: int,
    italic: bool,
    strike: bool,
) -> None:
    """An inline code span: mono at 0.86em on a pill the view paints (the transparent background is
    its marker), a pad character at either end at the size that makes it the pill's side padding,
    joiners between the characters so the span stays whole (not in a squeezed table column), and a
    link when the span is a path. It keeps the slant, weight, colour and link of the words around it."""
    m = b.m
    link = href
    if link is None and ("/" in code or "." in code):
        ref = markdown.as_path_ref(code)
        if ref:
            line = f"&l={ref['line']}" if ref.get("line") else ""
            link = f"pilcrow-path://open?p={quote(ref['path'])}{line}"
    pad = b._cf(b.fonts.font(b._pill_px, weight, True), fg, italic, strike, link, True, ctx.spacing)
    text = b._cf(b.fonts.font(m.code, weight, True), fg, italic, strike, link, True, ctx.spacing)
    cur.insertText(markdown.PILL_PAD, pad)
    cur.insertText(code if ctx.squeezed else markdown.NO_BREAK.join(code), text)
    cur.insertText(markdown.PILL_PAD, pad)


def _image_name(src: str, cwd: str) -> str:
    if not cwd or src.startswith(("http:", "https:", "data:", "/", "pilcrow-check:")):
        return src
    return markdown._join(cwd, src)


# ---------------------------------------------------------------- tables: what the words want


def inline_widths(
    children: Sequence[Any], m: Metrics, head: bool = False, hard: bool = True
) -> Tuple[float, float, float]:
    """(the width a cell's words want on one line, the least they can be given, the least Qt will
    lay them out in), from the inline tokens: the token-stream twin of ``markdown.cell_widths``.
    The last is measured only when asked (a table that fits its room never needs it)."""
    fm_t = markdown.font_metrics(m.table, weight=600 if head else 400)
    fm_c = markdown.font_metrics(m.code, mono=True)
    adv = markdown.advance
    pad_w = adv(markdown.font_metrics(markdown.pill_pad_px(m), mono=True), markdown.PILL_PAD)
    max_w = 0.0
    min_w = 0.0
    hard_w = 0.0
    prose: List[str] = []

    def flush() -> None:
        nonlocal max_w, min_w, hard_w
        if prose:
            w, u, h = markdown.text_widths_plain("".join(prose), fm_t, hard)
            max_w += w
            min_w = max(min_w, u)
            hard_w = max(hard_w, h)
            prose.clear()

    for tok in children:
        ty = tok.type
        if ty == "text":
            prose.append(tok.content)
        elif ty == "softbreak":
            prose.append(" ")
        elif ty == "code_inline":
            flush()
            cw = adv(fm_c, tok.content) + 2 * pad_w
            max_w += cw
            min_w = max(min_w, cw)
            if hard:
                hard_w = max(hard_w, max((adv(fm_c, ch) for ch in set(tok.content) if not ch.isspace()), default=0.0))
    flush()
    # prose never squeezes below a few characters a line (CJK would otherwise go one character a line)
    min_w = max(min_w, min(max_w, adv(fm_t, "中") * markdown.MIN_CELL_CHARS))
    return max_w, min_w, hard_w


Cell = Tuple[str, str, Any]  # tag (th/td), alignment, inline children


def plan_columns(rows: Sequence[Sequence[Cell]], m: Metrics, width: int) -> markdown.Plan:
    """Column widths for rows of token cells (see ``markdown.column_widths``), with the least and the
    most each column wants and the floor it cannot go under: the token twin of ``markdown.table_plan``.
    The floors (each column's widest character) are measured only when the table does not fit its
    room even at its least, which is when they decide anything."""
    ncol = max((len(r) for r in rows), default=0)
    for with_hard in (False, True):
        mins = [0.0] * ncol
        maxs = [0.0] * ncol
        hards = [0.0] * ncol
        for row in rows:
            for c, (tag, _al, kids) in enumerate(row):
                mx, mn, hd = inline_widths(kids, m, head=tag == "th", hard=with_hard)
                maxs[c] = max(maxs[c], mx + markdown.CELL_PAD)
                # a heading stays on one line: a column is never narrower than its name
                mins[c] = max(mins[c], (mx if tag == "th" else mn) + markdown.CELL_PAD)
                hards[c] = max(hards[c], hd + markdown.CELL_PAD)
        room = float(width) if width > 0 else sum(maxs)
        if with_hard or sum(math.ceil(mn) for mn in mins) <= int(room):
            return markdown.column_widths(mins, maxs, room, hards), mins, maxs, hards
    raise AssertionError("unreachable")


# ---------------------------------------------------------------- lists: which are task lists


def _task_lists(tokens: Sequence[Any]) -> set:
    """Indexes of the ``bullet_list_open`` tokens whose every item is a task (``- [ ]``)."""
    out: set = set()
    n = len(tokens)
    for i, tok in enumerate(tokens):
        if tok.type != "bullet_list_open":
            continue
        items = tasks = 0
        j = i + 1
        while j < n and not (tokens[j].type == "bullet_list_close" and tokens[j].level == tok.level):
            tj = tokens[j]
            if tj.type == "list_item_open" and tj.level == tok.level + 1:
                items += 1
                if (
                    j + 2 < n
                    and tokens[j + 1].type == "paragraph_open"
                    and tokens[j + 2].type == "inline"
                    and markdown.TASK_RE.match(tokens[j + 2].content or "")
                ):
                    tasks += 1
            j += 1
        if items and items == tasks:
            out.add(i)
    return out


_ = (QTextTable,)
