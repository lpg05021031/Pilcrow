"""What Claude wrote: one group per text item, one block per Markdown top-level block.

Streaming touches only the last block: a plain delta is appended to its document with a cursor,
anything with Markdown syntax re-renders that block alone, and the block is rendered once more from
its final source when the item finishes. Finished blocks are never touched again.

A code well in the block carries a Copy at its corner while the pointer is on it (``.md-copy``).
"""

from __future__ import annotations

import re
import time
from typing import Any, List, Optional, Tuple

from PySide6.QtCore import QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QTextCursor, QTextDocument, QTextTable

from ... import markdown, theme
from ...lib.transcript_model import TextItem
from ..paint import (
    FULL,
    Measurer,
    PaintState,
    doc_height,
    doc_pos_at,
    draw_doc,
    find_ranges,
    paint_decor,
    rounded,
    selection,
    table_rect,
)
from ..scene import GRADE_EXACT, GRADE_FAST, Block, Group, Target

PLAIN = re.compile(r"^[^*_`\[\]#>|~<&\\\n]*$")  # a delta that cannot change the markup
LINE_START_MARK = re.compile(r"(^|\n)\s*([-*+]|\d+[.)])\s")
COPY_W, COPY_H = 56, 20  # the Copy at a well's corner: room for "Copied" in micro text, 8px sides
COPIED_FOR = 1.2  # seconds the corner says "Copied"


class ProseBlock(Block):
    """One Markdown top-level block (a paragraph, a list, a fence…) as a document."""

    KIND = "prose"
    __slots__ = (
        "src",
        "streaming",
        "compact",
        "quiet",
        "cwd",
        "para",
        "doc",
        "_doc_src",
        "line_h",
        "_wells",
        "_copied",
    )

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
        self._wells: Optional[List[Tuple[QRectF, str]]] = None  # (rect, code) of each code well
        self._copied: Tuple[int, float] = (-1, 0.0)  # which well said "Copied", until when

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
        self._wells = None
        self.warm = True
        h = doc_height(self.doc) if self.src.strip() else 0
        if self.grade < GRADE_EXACT and self.h:
            m.calibration.observe(self.KIND, max(1, self.h), h)
        return h

    def cool(self) -> None:
        self.doc = None
        self._doc_src = ""
        self._wells = None
        self.warm = False

    # ---- code wells
    def wells(self) -> List[Tuple[QRectF, str]]:
        """Each code well's rectangle (block coordinates) and the code it holds."""
        if self._wells is None:
            out: List[Tuple[QRectF, str]] = []
            if self.doc is not None:
                codes = markdown.fence_sources(self.src)
                lay = self.doc.documentLayout()
                for frame in self.doc.rootFrame().childFrames():
                    if isinstance(frame, QTextTable) and frame.format().borderCollapse():
                        code = codes[len(out)] if len(out) < len(codes) else ""
                        out.append((table_rect(self.doc, lay, frame), code))
            self._wells = out
        return self._wells

    @staticmethod
    def copy_rect(well: QRectF) -> QRect:
        """Where a well's Copy sits: the right of its 30px head."""
        return QRect(int(well.right()) - 6 - COPY_W, int(well.top()) + 5, COPY_W, COPY_H)

    def _paint_copies(self, p: QPainter, x: int, y: int, st: PaintState) -> None:
        hov = st.hover
        shown = -1
        if hov is not None and isinstance(hov.id, str) and hov.id.startswith(("well:", "copy:")):
            shown = int(hov.id.split(":")[1])
        said, until = self._copied
        for i, (r, code) in enumerate(self.wells()):
            copied = i == said and time.monotonic() < until
            if not code or (i != shown and not copied):
                continue
            cr = self.copy_rect(r).translated(x, y)
            on = hov is not None and hov.kind == "button" and hov.id == f"copy:{i}"
            if on and not copied:
                rounded(p, cr, theme.blend(st.t.fill_2, st.t.canvas), None, theme.R1)
            f = st.m.font(theme.FS_MICRO)
            label = "Copied" if copied else "Copy"
            color = QColor(st.t.ok if copied else st.t.text if on else st.t.text_3)
            tw = st.m.text_w(label, f)
            st.m.draw_text(p, cr.x() + (COPY_W - tw) / 2, cr.y() + (COPY_H - st.m.line_h(f)) / 2, label, f, color)

    def _copy(self, i: int, view: Any) -> None:
        wells = self.wells()
        if i >= len(wells):
            return
        view.copy_text(wells[i][1])
        self._copied = (i, time.monotonic() + COPIED_FOR)
        self._repaint(view)
        QTimer.singleShot(int(COPIED_FOR * 1000) + 60, lambda: self._repaint(view))

    def _repaint(self, view: Any) -> None:
        try:
            view.viewport().update(view.block_rect(self))
        except RuntimeError:
            pass  # the view is gone

    # ---- paint
    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        if self.doc is None:
            return
        top = y
        clip = QRect(x, top, w, self.h)
        if st.clip is not None:
            clip = clip.intersected(st.clip)
        paint_decor(p, self.doc, x, top, clip, st.t)
        if not st.caching:
            self._paint_copies(p, x, top, st)
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
        for i, (r, code) in enumerate(self.wells()):
            if not code or not r.contains(x, y):
                continue
            cr = self.copy_rect(r)
            if cr.contains(x, y):
                return Target("button", f"copy:{i}", (cr.x(), cr.y(), cr.width(), cr.height()))
            return Target("text", f"well:{i}", (int(r.x()), int(r.y()), int(r.width()), int(r.height())))
        return None

    def targets(self) -> List[Target]:
        out: List[Target] = []
        for i, (r, code) in enumerate(self.wells()):
            if code:
                cr = self.copy_rect(r)
                out.append(Target("button", f"copy:{i}", (cr.x(), cr.y(), cr.width(), cr.height())))
        return out

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor if target.kind in ("link", "button") else Qt.CursorShape.IBeamCursor

    def tooltip_for(self, target: Target) -> str:
        return "Copy the code" if target.kind == "button" else ""

    def activate(self, target: Target, view: Any) -> None:
        if target.kind == "link":
            view.open_link(str(target.data), self.cwd)
        elif target.kind == "button" and isinstance(target.id, str) and target.id.startswith("copy:"):
            self._copy(int(target.id[5:]), view)

    def text(self) -> str:
        return self.src

    def plain(self) -> str:
        return self.doc.toPlainText().replace(" ", "").replace("⁠", "") if self.doc is not None else self.src

    def text_pos_at(self, x: int, y: int) -> Optional[int]:
        if self.doc is None:
            return None
        return doc_pos_at(self.doc, x, y)

    def selected_text(self, a: Optional[int], b: Optional[int]) -> str:
        if self.doc is None:
            return self.src
        if a is None or b is None:
            return self.plain()
        end = self.doc.characterCount() - 1  # the selection may reach past the words (the source is longer)
        cur = QTextCursor(self.doc)
        cur.setPosition(min(min(a, b), end))
        cur.setPosition(min(max(a, b), end), QTextCursor.MoveMode.KeepAnchor)
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
    __slots__ = ("item", "compact", "quiet", "cwd", "_srcs", "_est")

    def __init__(self, item: TextItem, cwd: str = "", quiet: bool = False, compact: bool = False) -> None:
        self._est = 0  # read by content_h() while Group.__init__ sets the first height
        super().__init__(f"text:{item.id}", head_h=0, pad_top=0, pad_bottom=0, open_=True)
        self.item = item
        self.cwd, self.quiet, self.compact = cwd, quiet, compact
        self._srcs: List[str] = []
        # a finished text is split into its blocks when it first comes near the screen (the parse is
        # the costly part of opening a long conversation); a text being written is split at once
        self.shaped = False
        self.sync(item)

    def gap_px(self) -> int:
        return markdown.metrics(self.compact or self.quiet).para

    # ---- before the split: one estimate for the whole text
    def ensure_shape(self, scene: Any) -> None:
        if self.shaped:
            return
        self.shaped = True
        self._split(self.item)

    def content_h(self) -> int:
        return self.fen.total() if self.shaped else self._est

    def estimate(self, width: int, m: Measurer) -> int:
        if self.shaped:
            return self.h
        text = self.item.text
        compact = self.compact or self.quiet
        self._est = m.calibration.apply("prose", m.estimate_markdown(text, width, compact)) if text.strip() else 0
        return self._est

    fast_measure = estimate

    def sync(self, item: TextItem) -> List[Block]:
        """Bring the blocks in line with the item's text; returns the blocks whose content changed."""
        self.item = item
        self.rev = item.rev
        if not self.shaped:
            if not item.streaming:
                self.sig = (None, False)
                return [self]
            self.shaped = True
        return self._split(item)

    def _split(self, item: TextItem) -> List[Block]:
        """Blocks keep their identity: the one being written is updated in place (its document takes
        the delta), blocks past the new count go, new ones are appended."""
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
