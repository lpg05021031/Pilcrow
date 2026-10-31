"""A block that is being written grows in place.

A reply streams a token at a time, and a block of Markdown that is re-rendered from its source on
every flush costs as much as it is long: a list of a thousand items, a table of five hundred rows or
a fence of a thousand lines costs hundreds of milliseconds a token. The growers here take the block's
document and the delta instead, and touch only what the delta touches:

- a **paragraph** takes a plain delta by cursor (anything that could change the markup re-renders);
- a **fence** is built without a table (a table re-lays all its rows on any change): a head block and
  one block per line, marked for the painter to draw the well around; a line in progress takes its
  characters, a finished line takes its colours and its hanging indent, a new line takes a block;
- a **list** (flat, one marker, no blank lines, no tasks) takes words into its last item and, when
  the item is done, sets it as inline Markdown; a new item takes a block in the same list;
- a **table** takes a finished row as a row, its cells as inline Markdown, into the last of its
  chunk tables (TABLE_CHUNK rows each, no space between), widening the columns when a row needs it.

Anything else — a nested list, a blank line, a fence inside a quote — returns False from ``grow`` and
the block is rendered from its source as before; once the block is written it is rendered once more
from its final source, and that render and the grown document have the same height, so nothing moves.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from PySide6.QtGui import (
    QColor,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextLength,
    QTextListFormat,
    QTextTable,
    QTextTableFormat,
)

from .. import markdown, theme
from .paint import Measurer, link_paths

# A code well built from blocks: the head carries 1, each line 2 (QTextDocument has no other place
# for a mark the painter can read back).
WELL_PROP = QTextFormat.Property.UserProperty + 7
WELL_HEAD, WELL_CODE, WELL_SPACE = 1, 2, 3
HEAD_PAD_TOP, HEAD_PAD_BOTTOM, HEAD_LINE = 8, 8, 14  # ``.md-code-head``: 30px, the label in micro text
CODE_PAD_BOTTOM = 13  # ``.md pre { padding: 0 14px 13px }``
PAD_LEFT, PAD_RIGHT = 14, 14

FENCE_RE = re.compile(r"^(`{3,}|~{3,})([^\n]*)\n")
ITEM_RE = re.compile(r"^(?P<mk>[-*+]|\d{1,9}[.)])[ \t]+(?P<body>.*)$")
MARKER_START_RE = re.compile(r"^(?:[-*+]|\d{1,9}[.)]?)$")  # a line that may still become an item's marker
TASK_RE = re.compile(r"^\[[ xX]\]\s")
PLAIN = re.compile(r"^[^*_`\[\]#>|~<&\\\n]*$")  # a delta that cannot change a paragraph's markup
LINE_START_MARK = re.compile(r"(^|\n)\s*([-*+]|\d+[.)])\s")
SEP_RE = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
ORDERED = (
    QTextListFormat.Style.ListDecimal,
    QTextListFormat.Style.ListLowerAlpha,
    QTextListFormat.Style.ListUpperAlpha,
    QTextListFormat.Style.ListLowerRoman,
    QTextListFormat.Style.ListUpperRoman,
)


class Grower:
    """The document of one streaming block and how it takes a delta."""

    kind = ""

    def __init__(
        self, src: str, m: Measurer, width: int, compact: bool, quiet: bool, cwd: str, line_h: Optional[int]
    ) -> None:
        self.m = m
        self.width = width
        self.compact, self.quiet, self.cwd, self.line_h = compact, quiet, cwd, line_h
        self.mm = markdown.metrics(compact or quiet)
        self.t = theme.tokens()
        self.done_ = False  # finished: the block keeps this document
        self.doc: QTextDocument = self.build(src)

    @staticmethod
    def accepts(src: str) -> bool:
        return False

    def build(self, src: str) -> QTextDocument:
        raise NotImplementedError

    def render(self, src: str) -> QTextDocument:
        """The block rendered from a source, as the full render does it."""
        return self.m.markdown_doc(src, self.width, self.compact, self.quiet, True, self.cwd, self.line_h)

    def grow(self, old: str, new: str) -> bool:
        """Take the delta from ``old`` to ``new`` into the document; False when the shape changed."""
        raise NotImplementedError

    @staticmethod
    def continues(src: str, delta: str) -> bool:
        """Does the block whose source is ``src`` stay one top-level block when ``delta`` follows it?
        Decided from the delta's lines alone, so a reply's blocks are not parsed again for a token."""
        return False

    def finish(self) -> bool:
        """The block is written: take in what was still on its way (the line, item or row in
        progress), so the document is the render's and the block keeps it. False when the render is
        needed after all."""
        return True

    def code(self) -> Optional[str]:
        """The code of a well being written (what Copy copies), or None."""
        return None


# ---------------------------------------------------------------- paragraphs


class ParaGrower(Grower):
    kind = "para"

    @staticmethod
    def accepts(src: str) -> bool:
        head = src.lstrip()
        if head.startswith(("```", "~~~", "|", "#", ">", "<")):
            return False
        return not (LINE_START_MARK.search(src) or "|" in src)

    def build(self, src: str) -> QTextDocument:
        return self.render(src)

    @staticmethod
    def continues(src: str, delta: str) -> bool:
        # a new line could start a heading, a list, a fence; a long paragraph is cut into pieces
        return "\n" not in delta and len(src) + len(delta) <= markdown.PARA_LIMIT

    def grow(self, old: str, new: str) -> bool:
        delta = new[len(old) :]
        if not PLAIN.match(delta):
            return False
        cur = QTextCursor(self.doc)
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.insertText(delta)
        return True


# ---------------------------------------------------------------- fences


class FenceGrower(Grower):
    kind = "fence"

    @staticmethod
    def accepts(src: str) -> bool:
        return FENCE_RE.match(src) is not None

    @staticmethod
    def continues(src: str, delta: str) -> bool:
        mt = FENCE_RE.match(src)
        if mt is None:
            return False
        body = src[mt.end() :] + delta
        n = len(mt.group(1))
        close = re.search(r"(?m)^ {0,3}" + re.escape(mt.group(1)[0]) + "{" + str(n) + r",}[ \t]*$", body)
        return close is None  # closed: whatever follows is the parser's to place

    def build(self, src: str) -> QTextDocument:
        mt = FENCE_RE.match(src)
        assert mt is not None
        self.marker = mt.group(1)
        self.lang = (mt.group(2) or "").strip().split(" ")[0]
        self.closed = False
        self.done: List[str] = []  # the finished lines, trailing blank ones included
        self.shown = 0  # how many of them have a block (trailing blank lines wait for words)
        self.tail = ""  # the line in progress
        self.visible = 0  # how much of it is in its block (a possible closing marker is held back)
        self.placeholder = True  # the one empty block a well shows before it has a line
        self.indented = False  # the line in progress has its hanging indent set
        m, mm = self.m, self.mm
        self.code_px = markdown.px(markdown.mono_size())
        self.code_line = markdown.px(markdown.mono_size() * 1.6)
        self.ch = markdown.font_metrics(self.code_px, mono=True).horizontalAdvance("0")
        self.plain = QTextCharFormat()
        self.plain.setFont(m.font(self.code_px, mono=True))
        self._fmts: Dict[str, QTextCharFormat] = {}
        doc = m.blank_doc(width=self.width, font=m.font(mm.base))
        cur = QTextCursor(doc)
        # Qt counts no margin above the first block or under the last: the table's margins above
        # and below the well are blocks of their own, of a fixed height and a font too small to show
        cur.setBlockFormat(self._spacer(markdown.px(mm.base * 0.6)))
        cur.setBlockCharFormat(self._tiny())
        head = QTextBlockFormat()
        head.setTopMargin(HEAD_PAD_TOP)
        head.setBottomMargin(HEAD_PAD_BOTTOM)
        head.setLeftMargin(PAD_LEFT)
        head.setRightMargin(10)
        head.setLineHeight(HEAD_LINE, QTextBlockFormat.LineHeightTypes.MinimumHeight.value)
        head.setProperty(WELL_PROP, WELL_HEAD)
        label = QTextCharFormat()
        label.setFont(m.font(theme.FS_MICRO))
        label.setForeground(QColor(self.t.text_3))
        cur.insertBlock(head, label)
        cur.insertText(markdown.lang_label(self.lang), label)
        cur.insertBlock(self._line_format(""), self.plain)
        cur.insertBlock(self._spacer(CODE_PAD_BOTTOM + mm.base), self._tiny())
        self.doc = doc
        if not self._feed(src[mt.end() :]):
            raise ValueError("not one fence")
        doc.setTextWidth(self.width)
        return doc

    # ---- formats
    @staticmethod
    def _spacer(height: int) -> QTextBlockFormat:
        bf = QTextBlockFormat()
        bf.setLineHeight(height, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
        bf.setProperty(WELL_PROP, WELL_SPACE)
        return bf

    @staticmethod
    def _tiny() -> QTextCharFormat:
        cf = QTextCharFormat()
        cf.setFontPointSize(1)
        return cf

    def _line_format(self, line: str) -> QTextBlockFormat:
        lead = len(line) - len(line.lstrip(" \t"))
        cols = (
            lead + (markdown.TAB_COLS - 1) * line[:lead].count("\t") + markdown.WRAP_INDENT if len(line) > lead else 0
        )
        ind = int(cols * self.ch + 0.5)
        bf = QTextBlockFormat()
        bf.setLeftMargin(PAD_LEFT + ind)
        bf.setTextIndent(-ind)
        bf.setRightMargin(PAD_RIGHT)
        bf.setTopMargin(0)
        bf.setBottomMargin(0)
        bf.setLineHeight(self.code_line, QTextBlockFormat.LineHeightTypes.MinimumHeight.value)
        bf.setProperty(WELL_PROP, WELL_CODE)
        return bf

    def _fmt(self, style: str) -> QTextCharFormat:
        f = self._fmts.get(style)
        if f is None:
            f = QTextCharFormat(self.plain)
            for part in style.split(";"):
                if part.startswith("color:"):
                    f.setForeground(QColor(part[6:]))
                elif part == "font-style:italic":
                    f.setFontItalic(True)
            self._fmts[style] = f
        return f

    def _last_line(self):
        """The block of the line in progress: the one before the trailing spacer."""
        return self.doc.lastBlock().previous()

    # ---- growth
    def _feed(self, text: str) -> bool:
        pieces = text.split("\n")
        for k, piece in enumerate(pieces):
            if k and not self._end_line():
                return False
            if piece:
                if self.closed:
                    if piece.strip():
                        return False
                    continue
                self._append(piece)
        return True

    def _open_block(self) -> None:
        """A block for the line in progress: the placeholder while the well is empty, else a new
        block after the last line (trailing blank lines get theirs first, now that words follow)."""
        if self.placeholder:
            self.placeholder = False
            return
        cur = QTextCursor(self._last_line())
        cur.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        while self.shown < len(self.done):
            cur.insertBlock(self._line_format(""), self.plain)
            self.shown += 1
        cur.insertBlock(self._line_format(""), self.plain)

    def _maybe_close(self, line: str) -> bool:
        """Could this line still turn out to be the closing fence? (Its characters are held back
        until it is complete: a render shows no closing marker.)"""
        lead = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        return (
            lead <= 3 and bool(stripped) and set(stripped) == {self.marker[0]} and not line.lstrip(" ").startswith("\t")
        )

    def _append(self, piece: str) -> None:
        self.tail += piece
        if self._maybe_close(self.tail):
            return  # held: nothing of it is shown yet
        text = self.tail[self.visible :]
        if not self.visible:
            self._open_block()
        cur = QTextCursor(self._last_line())
        cur.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        cur.insertText(text, self.plain)
        self.visible = len(self.tail)
        if not self.indented and self.tail.strip():
            # the line's indentation is known once its first word is: the wrap takes it from now on
            self.indented = True
            cur.setBlockFormat(self._line_format(self.tail))

    def _end_line(self) -> bool:
        """The line in progress is complete: the closing fence ends the well, a blank line waits for
        words to follow it (the render shows no trailing blank lines), any other line gets its
        colours."""
        if self.closed:
            return True
        line = self.tail
        if self._maybe_close(line):
            if len(line.strip()) >= len(self.marker):
                self.closed = True
                self.tail = ""
                return True
            # too short to close: a line of the marker's characters, shown after all
            held, self.tail = line, ""
            self._append(held)
        if not line:
            self.done.append("")
            return True
        self._colour(self._last_line(), line)
        self.done.append(line)
        self.shown += 1
        self.tail = ""
        self.visible = 0
        self.indented = False
        return True

    def _colour(self, block, line: str) -> None:
        if not self.lang or not line.strip():
            return
        base = block.position()
        cur = QTextCursor(self.doc)
        for a, b, style in markdown.token_runs(line, self.lang, self.t.name):
            cur.setPosition(base + a)
            cur.setPosition(base + b, QTextCursor.MoveMode.KeepAnchor)
            cur.setCharFormat(self._fmt(style))

    def grow(self, old: str, new: str) -> bool:
        return self._feed(new[len(old) :])

    def finish(self) -> bool:
        if self.closed or not self.tail:
            return True
        if self._maybe_close(self.tail) and len(self.tail.strip()) >= len(self.marker):
            self.closed = True
            self.tail = ""
            return True
        self._colour(self._last_line(), self.tail)
        return True

    def code(self) -> Optional[str]:
        parts = list(self.done)
        if self.tail or not parts:
            parts.append(self.tail)
        return "\n".join(parts)


# ---------------------------------------------------------------- lists


def _family(marker: str) -> str:
    return marker if marker in "-*+" else "ordered" + marker[-1]


class ListGrower(Grower):
    kind = "list"

    @staticmethod
    def accepts(src: str) -> bool:
        lines = src.split("\n")
        if not lines or not ITEM_RE.match(lines[0]):
            return False
        family = _family(ITEM_RE.match(lines[0]).group("mk"))  # type: ignore[union-attr]
        for i, line in enumerate(lines):
            mt = ITEM_RE.match(line)
            if mt is None:
                # only the last line may still be a marker on its way (or nothing yet)
                return i == len(lines) - 1 and (line == "" or MARKER_START_RE.match(line) is not None)
            if _family(mt.group("mk")) != family or TASK_RE.match(mt.group("body")):
                return False
        return True

    @staticmethod
    def continues(src: str, delta: str) -> bool:
        first = ITEM_RE.match(src.split("\n", 1)[0])
        if first is None:
            return False
        family = _family(first.group("mk"))
        lines = (src.rsplit("\n", 1)[-1] + delta).split("\n")
        for i, line in enumerate(lines):
            mt = ITEM_RE.match(line)
            if mt is None:
                if i == len(lines) - 1 and (line == "" or MARKER_START_RE.match(line)):
                    continue
                return False
            if _family(mt.group("mk")) != family or TASK_RE.match(mt.group("body")):
                return False
        return True

    def build(self, src: str) -> QTextDocument:
        lines = src.split("\n")
        self.items: List[str] = []
        self.tail: Optional[str] = None  # the item in progress (its whole line), or None
        self.pending = ""  # the start of a line that is not an item yet
        last = lines[-1]
        if ITEM_RE.match(last):
            self.items = lines[:-1]
            self.tail = last
            shown = lines
        else:
            self.items = lines[:-1]
            self.pending = last
            shown = lines[:-1]
        self.text_fmt = QTextCharFormat()
        self.text_fmt.setForeground(QColor(self.t.text))
        doc = self.render("\n".join(shown))
        self._rechunk(doc)
        return doc

    @staticmethod
    def _rechunk(doc: QTextDocument) -> None:
        """The one list of the render as lists of LIST_CHUNK items (a block joins a list at the cost
        of the list's length): the items keep their formats, an ordered list numbers on."""
        lst = doc.lastBlock().textList()
        if lst is None or lst.count() <= markdown.LIST_CHUNK:
            return
        blocks = [lst.item(i) for i in range(lst.count())]
        base = lst.format().start() if lst.format().start() > 0 else 1
        for start in range(markdown.LIST_CHUNK, len(blocks), markdown.LIST_CHUNK):
            lf = QTextListFormat(lst.format())
            if lf.style() in ORDERED:
                lf.setStart(base + start)
            cur = QTextCursor(blocks[start])
            last = blocks[-1]
            cur.setPosition(last.position() + max(0, last.length() - 1), QTextCursor.MoveMode.KeepAnchor)
            cur.createList(lf)

    def grow(self, old: str, new: str) -> bool:
        pieces = new[len(old) :].split("\n")
        for k, piece in enumerate(pieces):
            if k and not self._end_line():
                return False
            if piece and not self._append(piece):
                return False
        return True

    def _append(self, piece: str) -> bool:
        if self.tail is not None:
            self.tail += piece
            cur = QTextCursor(self.doc)
            cur.movePosition(QTextCursor.MoveOperation.End)
            cur.insertText(piece, self.text_fmt)
            return True
        self.pending += piece
        mt = ITEM_RE.match(self.pending)
        if mt is None:
            return self.pending == "" or MARKER_START_RE.match(self.pending) is not None
        if TASK_RE.match(mt.group("body")):
            return False
        first = ITEM_RE.match(self.items[0]) if self.items else None
        if first is not None and _family(first.group("mk")) != _family(mt.group("mk")):
            return False
        self.tail, self.pending = self.pending, ""
        cur = QTextCursor(self.doc)
        cur.movePosition(QTextCursor.MoveOperation.End)
        # the items keep li's 0.22em above and below (the render's first and last have none, which
        # the new item takes over from the one before it)
        prev = cur.blockFormat()
        prev.setBottomMargin(self.mm.li_gap)
        cur.setBlockFormat(prev)
        lst = cur.block().textList()
        if lst is not None and lst.count() >= markdown.LIST_CHUNK:
            # a block joins a list at the cost of the list's length: the next LIST_CHUNK items get a
            # list of their own, numbered on from this one
            lf = lst.format()
            if lf.style() in ORDERED:
                lf.setStart(lf.start() + lst.count() if lf.start() > 0 else lst.count() + 1)
            bf = cur.blockFormat()
            bf.setObjectIndex(-1)
            cur.insertBlock(bf, cur.blockCharFormat())
            cur.createList(lf)
        else:
            cur.insertBlock()
        bf = cur.blockFormat()
        bf.setTopMargin(self.mm.li_gap)
        bf.setBottomMargin(0)
        cur.setBlockFormat(bf)
        cur.insertText(mt.group("body"), self.text_fmt)
        return True

    def finish(self) -> bool:
        if self.pending:
            return False  # a marker on its way with no item behind it: the render decides
        return self.tail is None or self._end_line()

    def _end_line(self) -> bool:
        if self.tail is None:
            return False  # a blank line, or a line that is not an item: the list changed shape
        mt = ITEM_RE.match(self.tail)
        if mt is None:
            return False
        block = self.doc.lastBlock()
        bf, bcf = block.blockFormat(), block.charFormat()
        cur = QTextCursor(block)
        cur.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        cur.insertHtml(link_paths(markdown.render_inline(mt.group("body"), self.mm)))
        # an HTML fragment brings a block format of its own: the item keeps the list's
        cur.setBlockFormat(bf)
        cur.setBlockCharFormat(bcf)
        self.items.append(self.tail)
        self.tail = None
        return True


# ---------------------------------------------------------------- tables


class TableGrower(Grower):
    kind = "table"

    @staticmethod
    def accepts(src: str) -> bool:
        lines = src.split("\n")
        if len(lines) < 3 or not lines[0].lstrip().startswith("|") or not SEP_RE.match(lines[1]):
            return False
        return all(ln.lstrip().startswith("|") for ln in lines[2:-1])

    @staticmethod
    def continues(src: str, delta: str) -> bool:
        lines = (src.rsplit("\n", 1)[-1] + delta).split("\n")
        return all(ln.lstrip().startswith("|") or (i == len(lines) - 1 and ln == "") for i, ln in enumerate(lines))

    def build(self, src: str) -> QTextDocument:
        lines = src.split("\n")
        self.tail = lines[-1]
        done = lines[:-1]
        self.ncol = len(markdown.table_cells(done[0]))
        self.aligns = [self._align(c) for c in markdown.table_cells(done[1])][: self.ncol]
        self.aligns += ["left"] * (self.ncol - len(self.aligns))
        rows: List[List[markdown.Cell]] = []
        for i, line in enumerate(done):
            if i == 1:
                continue
            rows.append(self._cells(line, head=i == 0))
        self.widths, self.mins, self.maxs, self.hards = markdown.table_plan(rows, self.mm, self.width)
        self.squeezed = markdown.squeezed(self.widths, self.mins)
        return self.render("\n".join(done))

    @staticmethod
    def _align(sep: str) -> str:
        s = sep.strip()
        if s.startswith(":") and s.endswith(":"):
            return "center"
        if s.endswith(":"):
            return "right"
        return "left"

    def _cells(self, line: str, head: bool = False) -> List[markdown.Cell]:
        raw = markdown.table_cells(line)[: self.ncol]
        raw += [""] * (self.ncol - len(raw))
        tag = "th" if head else "td"
        sq = getattr(self, "squeezed", ())
        cells: List[markdown.Cell] = []
        for c, text in enumerate(raw):
            inner = link_paths(markdown.render_inline(text, self.mm, colour=False))
            if c < len(sq) and sq[c]:
                inner = inner.replace(markdown.NO_BREAK, "")  # a squeezed column lets its code spans break
            cells.append((tag, self.aligns[c], inner))
        return cells

    def _tables(self) -> List[QTextTable]:
        return [f for f in self.doc.rootFrame().childFrames() if isinstance(f, QTextTable)]

    def grow(self, old: str, new: str) -> bool:
        pieces = new[len(old) :].split("\n")
        for k, piece in enumerate(pieces):
            if k and not self._end_line():
                return False
            self.tail += piece
        return True

    def finish(self) -> bool:
        return not self.tail or self._end_line()

    def _end_line(self) -> bool:
        line, self.tail = self.tail, ""
        if not line.lstrip().startswith("|"):
            return False
        cells = self._cells(line)
        tables = self._tables()
        if not tables:
            return False
        mins, maxs, hards = list(self.mins), list(self.maxs), list(self.hards)
        for c, (_tag, _align, inner) in enumerate(cells):
            mx, mn, hd = markdown.cell_widths(inner, self.mm)
            maxs[c] = max(maxs[c], mx + markdown.CELL_PAD)
            mins[c] = max(mins[c], mn + markdown.CELL_PAD)
            hards[c] = max(hards[c], hd + markdown.CELL_PAD)
        if (mins, maxs, hards) != (self.mins, self.maxs, self.hards):
            self.mins, self.maxs, self.hards = mins, maxs, hards
            widths = markdown.column_widths(mins, maxs, float(self.width), hards)
            if markdown.squeezed(widths, mins) != self.squeezed:
                return False  # a column has just run out of room: the cells of the rows so far are re-rendered
            if widths != self.widths:
                self.widths = widths
                for table in tables:
                    self._set_widths(table)
        last = tables[-1]
        body_rows = last.rows() - (1 if len(tables) == 1 else 0)
        if body_rows >= markdown.TABLE_CHUNK:
            prev = last
            last = self._new_chunk(prev)
            r = 0
        else:
            prev = last
            last.appendRows(1)
            r = last.rows() - 1
        for c, (_tag, _align, inner) in enumerate(cells):
            cell = last.cellAt(r, c)
            # the cell above: in this table, or the last row of the table before
            model = last.cellAt(r - 1, c) if r > 0 else prev.cellAt(prev.rows() - 1, c)
            cell.setFormat(model.format())
            cur = cell.firstCursorPosition()
            if inner:
                cur.insertHtml(f'<span style="font-size:{self.mm.table}px">{inner}</span>')
            # the HTML fragment brings a block format of its own: the cell takes the one above's
            cur.setBlockFormat(model.firstCursorPosition().blockFormat())
            cur.setBlockCharFormat(model.firstCursorPosition().blockCharFormat())
        return True

    def _set_widths(self, table: QTextTable) -> None:
        tf = table.format()
        tf.setColumnWidthConstraints([QTextLength(QTextLength.Type.FixedLength, w) for w in self.widths])
        tf.setWidth(QTextLength(QTextLength.Type.FixedLength, sum(self.widths)))
        table.setFormat(tf)

    def _new_chunk(self, prev: QTextTable) -> QTextTable:
        """A table of its own for the next TABLE_CHUNK rows, with no space between it and the last."""
        pf = prev.format()
        bottom = pf.bottomMargin()
        pf.setBottomMargin(0)
        prev.setFormat(pf)
        tf = QTextTableFormat(pf)
        tf.setTopMargin(0)
        tf.setBottomMargin(bottom)
        cur = QTextCursor(self.doc)
        cur.setPosition(prev.lastPosition() + 1)
        return cur.insertTable(1, self.ncol, tf)


# ---------------------------------------------------------------- the choice


GROWERS: Tuple[type, ...] = (FenceGrower, ListGrower, TableGrower, ParaGrower)


def continues(src: str, delta: str) -> bool:
    """Does the last block of a streaming text stay one block when ``delta`` follows it? True only
    when a grower takes the block's shape and says so from the delta's lines; otherwise the tail of
    the text is parsed again."""
    for cls in GROWERS:
        if cls.accepts(src):
            return bool(cls.continues(src, delta))
    return False


def start(
    src: str, m: Measurer, width: int, compact: bool, quiet: bool, cwd: str, line_h: Optional[int]
) -> Optional[Grower]:
    """A grower for a block being written, with its document built, or None when the block has a
    shape none of them takes (it is rendered from its source each time, as before)."""
    for cls in GROWERS:
        if cls.accepts(src):
            try:
                return cls(src, m, width, compact, quiet, cwd, line_h)
            except Exception:  # noqa: BLE001 - a grower that cannot build falls back to the render
                return None
    return None
