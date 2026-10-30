"""What Claude wrote: one group per text item, one block per Markdown top-level block.

Streaming touches only the last block: a plain delta is appended to its document with a cursor,
anything with Markdown syntax re-renders that block alone, and the block is rendered once more from
its final source when the item finishes. Finished blocks are never touched again.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QTextCursor, QTextDocument

from ... import markdown, theme
from ...lib.transcript_model import TextItem
from ..paint import FULL, Measurer, PaintState, doc_height, draw_doc, find_ranges, paint_decor, selection
from ..scene import GRADE_EXACT, GRADE_FAST, Block, Group, Target

PLAIN = re.compile(r"^[^*_`\[\]#>|~<&\\\n]*$")  # a delta that cannot change the markup
LINE_START_MARK = re.compile(r"(^|\n)\s*([-*+]|\d+[.)])\s")


class ProseBlock(Block):
    """One Markdown top-level block (a paragraph, a list, a fence…) as a document."""

    KIND = "prose"
    __slots__ = ("src", "streaming", "compact", "quiet", "cwd", "para", "doc", "_doc_src", "line_h")

    def __init__(
        self,
        key: str,
        src: str,
        compact: bool = False,
        quiet: bool = False,
        cwd: str = "",
        para: int = 0,
        line_h: Optional[int] = None,
    ) -> None:
        super().__init__(key)
        self.src = src
        self.streaming = False
        self.compact, self.quiet, self.cwd, self.para, self.line_h = compact, quiet, cwd, para, line_h
        self.doc: Optional[QTextDocument] = None
        self._doc_src = ""

    def mt(self) -> int:
        return self.para  # the space above a paragraph that follows another (0.75em; none above the first)

    # ---- content
    def set_src(self, src: str, streaming: bool) -> bool:
        """New source for this block; returns whether anything changed."""
        if src == self.src and streaming == self.streaming:
            return False
        self.src, self.streaming = src, streaming
        return True

    def can_append(self, old: str, new: str) -> bool:
        if self.doc is None or not new.startswith(old) or self._doc_src != old:
            return False
        delta = new[len(old) :]
        if not PLAIN.match(delta):
            return False
        # inside a fence, a list or a table the markup of the whole block can change
        return not (old.count("```") % 2 or LINE_START_MARK.search(old) or "|" in old)

    def append_delta(self, delta: str, m: Measurer, width: int) -> int:
        assert self.doc is not None
        cur = QTextCursor(self.doc)
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.insertText(delta)
        self._doc_src = self.src
        return doc_height(self.doc)

    # ---- heights
    def estimate(self, width: int, m: Measurer) -> int:
        return m.calibration.apply(self.KIND, m.estimate_markdown(self.src, width, self.compact or self.quiet))

    def fast_measure(self, width: int, m: Measurer) -> int:
        if self.src.lstrip().startswith(("```", "~~~", "|", "#")):
            return self.estimate(width, m)
        mm = markdown.metrics(self.compact or self.quiet)
        plain = re.sub(r"[*_`]", "", self.src)
        return m.fast_height(plain, width, m.font(mm.base), self.line_h or mm.line)

    def measure(self, width: int, m: Measurer) -> int:
        if self.doc is not None and self._doc_src != self.src and int(self.doc.textWidth()) == width:
            if self.streaming and self.can_append(self._doc_src, self.src):
                # a plain delta: typed into the document, no re-render
                self.append_delta(self.src[len(self._doc_src) :], m, width)
            else:
                self.doc = None
        if self.doc is None or self._doc_src != self.src or int(self.doc.textWidth()) != width:
            self.doc = m.markdown_doc(self.src, width, self.compact, self.quiet, self.streaming, self.cwd, self.line_h)
            self._doc_src = self.src
        self.warm = True
        h = doc_height(self.doc) if self.src.strip() else 0
        if self.grade < GRADE_EXACT and self.h:
            m.calibration.observe(self.KIND, max(1, self.h), h)
        return h

    def cool(self) -> None:
        self.doc = None
        self._doc_src = ""
        self.warm = False

    # ---- paint
    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        if self.doc is None:
            return
        top = y
        clip = QRect(x, top, w, self.h)
        if st.clip is not None:
            clip = clip.intersected(st.clip)
        paint_decor(p, self.doc, x, top, clip, st.t)
        color = QColor(st.t.text_2 if self.quiet else st.t.text)
        sels: List[Any] = []
        if st.needle:
            for k, (a, b) in enumerate(find_ranges(self.doc, st.needle)):
                if k == st.needle_current:
                    sels.append(selection(self.doc, a, b, QColor(st.t.accent), QColor("#ffffff")))
                else:
                    sels.append(selection(self.doc, a, b, theme.blend("rgba(217, 119, 87, 0.28)", st.t.canvas)))
        if st.selection is not None:
            bg = theme.blend(st.t.selection, st.t.canvas)
            if st.selection == FULL:
                sels.append(selection(self.doc, 0, self.doc.characterCount(), bg))
            else:
                a, b = st.selection
                sels.append(selection(self.doc, a, b, bg))
        draw_doc(p, self.doc, x, top, clip, color, sels or None)

    # ---- interaction
    def hit(self, x: int, y: int) -> Optional[Target]:
        if self.doc is None:
            return None
        href = self.doc.documentLayout().anchorAt(_pt(x, y))
        if href:
            return Target("link", href, (0, 0, 0, 0), href)
        return None

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor if target.kind == "link" else Qt.CursorShape.IBeamCursor

    def activate(self, target: Target, view: Any) -> None:
        if target.kind == "link":
            view.open_link(str(target.data), self.cwd)

    def text(self) -> str:
        return self.src

    def plain(self) -> str:
        return self.doc.toPlainText().replace(" ", "").replace("⁠", "") if self.doc is not None else self.src

    def text_pos_at(self, x: int, y: int) -> Optional[int]:
        if self.doc is None:
            return None
        pos = self.doc.documentLayout().hitTest(_pt(x, y), Qt.HitTestAccuracy.FuzzyHit)
        return pos if pos >= 0 else None

    def selected_text(self, a: Optional[int], b: Optional[int]) -> str:
        if self.doc is None:
            return self.src
        if a is None or b is None:
            return self.plain()
        cur = QTextCursor(self.doc)
        cur.setPosition(min(a, b))
        cur.setPosition(max(a, b), QTextCursor.MoveMode.KeepAnchor)
        return cur.selectedText().replace(" ", "\n").replace(" ", "").replace("⁠", "")

    @property
    def static(self) -> bool:
        return not self.streaming


def _pt(x: int, y: int):
    from PySide6.QtCore import QPointF

    return QPointF(x, y)


class TextGroup(Group):
    """A text item: its Markdown split into top-level blocks, one ProseBlock each, with the prose
    paragraph margin between them."""

    KIND = "text"
    __slots__ = ("item", "compact", "quiet", "cwd", "_srcs")

    def __init__(self, item: TextItem, cwd: str = "", quiet: bool = False, compact: bool = False) -> None:
        super().__init__(f"text:{item.id}", head_h=0, pad_top=0, pad_bottom=0, open_=True)
        self.item = item
        self.cwd, self.quiet, self.compact = cwd, quiet, compact
        self._srcs: List[str] = []
        self.shaped = True
        self.sync(item)

    def gap_px(self) -> int:
        return markdown.metrics(self.compact or self.quiet).para

    def sync(self, item: TextItem) -> List[Block]:
        """Bring the blocks in line with the item's text; returns the blocks whose content changed.
        Blocks keep their identity: the one being written is updated in place (its document takes
        the delta), blocks past the new count go, new ones are appended."""
        self.item = item
        self.rev = item.rev
        srcs = markdown.split_blocks(item.text) if item.text else []
        streaming = bool(item.streaming)
        changed: List[Block] = []
        n = len(srcs)
        if n < len(self.children):
            self.truncate(n)
        for i in range(n):
            last = i == n - 1
            if i < len(self.children):
                b = self.children[i]
                if isinstance(b, ProseBlock) and b.set_src(srcs[i], streaming and last):
                    changed.append(b)
            else:
                b = ProseBlock(
                    f"{self.key}:{i}", srcs[i], self.compact, self.quiet, self.cwd, self.gap_px() if i else 0
                )
                b.streaming = streaming and last
                self.append(b)
                changed.append(b)
        self._srcs = srcs
        self.sig = (n, streaming)
        return changed

    def text(self) -> str:
        return self.item.text

    @property
    def static(self) -> bool:
        return not self.item.streaming


_ = (GRADE_FAST, Tuple)
