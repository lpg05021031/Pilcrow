"""Thinking: a quiet head ("Thinking", "Thought for 4s", "Reasoned privately"), a peek at the last
lines while it streams and is closed, and the thought itself behind the head, with a rule beside it.

While a thought streams it is plain text (cheap to grow); once written it is set as quiet Markdown at
its own size, so its lists, code spans and paragraphs read the way the reply's do."""

from __future__ import annotations

import time
from typing import Any, Optional, Tuple

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QTextDocument

from ... import theme
from ...lib.transcript_model import ThinkingItem
from ..paint import FULL, Measurer, PaintState, doc_height, doc_pos_at, draw_doc, paint_decor, selection
from ..scene import Block, Group, Target
from .base import chevron, paint_head_tint

HEAD_H = 26
PEEK_LINES = 4.9
BODY_SIZE = 13.5
BODY_LINE = int(BODY_SIZE * 1.65 + 0.5)


class ThinkingBody(Block):
    KIND = "thinking-body"
    __slots__ = ("owner", "doc", "_src", "_md")

    def __init__(self, owner: ThinkingBlock) -> None:
        super().__init__(f"{owner.key}:body")
        self.owner = owner
        self.doc: Optional[QTextDocument] = None
        self._src = ""
        self._md = False  # the document is Markdown (a finished thought), not plain text

    def estimate(self, width: int, m: Measurer) -> int:
        return m.fast_height(self.owner.item.text, width - 15, m.font(BODY_SIZE), BODY_LINE) + 8

    def measure(self, width: int, m: Measurer) -> int:
        text = self.owner.item.text
        if not text:
            self.doc = None
            return 0
        md = not self.owner.item.streaming
        if self.doc is None or self._src != text or self._md != md or int(self.doc.textWidth()) != width - 15:
            if md:
                self.doc = m.markdown_doc(text, width - 15, quiet=True, size=BODY_SIZE)
            else:
                self.doc = m.plain_doc(text, width - 15, m.font(BODY_SIZE))
                _set_line_height(self.doc, BODY_LINE)
            self._src, self._md = text, md
        self.warm = True
        return doc_height(self.doc) + 8

    def cool(self) -> None:
        self.doc = None
        self._src = ""
        self.warm = False

    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        if self.doc is None:
            return
        p.fillRect(QRect(x + 1, y + 2, 1, self.h - 6), theme.blend(st.t.hair_2, st.t.canvas))
        clip = QRect(x + 15, y + 2, w - 15, self.h - 4)
        if st.clip is not None:
            clip = clip.intersected(st.clip)
        if self._md:
            paint_decor(p, self.doc, x + 15, y + 2, clip, st.t)
        sels = None
        if st.selection is not None:
            bg = theme.blend(st.t.selection, st.t.canvas)
            a, b = (0, self.doc.characterCount()) if st.selection == FULL else st.selection
            sels = [selection(self.doc, a, b, bg)]
        draw_doc(p, self.doc, x + 15, y + 2, clip, QColor(st.t.text_2), sels)

    def text(self) -> str:
        return self.owner.item.text

    def text_pos_at(self, x: int, y: int) -> Optional[int]:
        if self.doc is None:
            return None
        return doc_pos_at(self.doc, x - 15, y - 2)

    def selected_text(self, a: Optional[int], b: Optional[int]) -> str:
        if self.doc is None or not self._md:
            text = self.owner.item.text
            return text if a is None or b is None else text[min(a, b) : max(a, b)]
        if a is None or b is None:
            return _clean(self.doc.toPlainText())
        from PySide6.QtGui import QTextCursor

        end = self.doc.characterCount() - 1
        cur = QTextCursor(self.doc)
        cur.setPosition(min(min(a, b), end))
        cur.setPosition(min(max(a, b), end), QTextCursor.MoveMode.KeepAnchor)
        return _clean(cur.selectedText())

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.IBeamCursor

    def hit(self, x: int, y: int) -> Optional[Target]:
        return Target("text", "body", (0, 0, self.h_width, self.h))

    @property
    def static(self) -> bool:
        return not self.owner.item.streaming


class ThinkingBlock(Group):
    """The head and, under it, the thought."""

    KIND = "thinking"
    __slots__ = ("item", "view_mode", "user_open", "peek_doc", "_peek_src", "_peek_w")

    def __init__(self, item: ThinkingItem, view_mode: str = "normal") -> None:
        super().__init__(f"thinking:{item.id}", head_h=HEAD_H, pad_top=4, pad_bottom=6, open_=False)
        self.item = item
        self.view_mode = view_mode
        self.user_open: Optional[bool] = None
        self.peek_doc: Optional[QTextDocument] = None
        self._peek_src = ""
        self._peek_w = 0
        self.shaped = True
        self.append(ThinkingBody(self))
        self.set_open(self.wants_open)
        self.sync(item, view_mode)

    @property
    def wants_open(self) -> bool:
        if self.user_open is not None:
            return self.user_open
        return self.view_mode in ("thinking", "verbose")

    @property
    def peeking(self) -> bool:
        return bool(self.item.streaming and not self.open and self.item.text)

    def sync(self, item: ThinkingItem, view_mode: str) -> bool:
        changed = item.rev != self.rev or view_mode != self.view_mode
        self.item = item
        self.view_mode = view_mode
        self.rev = item.rev
        self.sig = (item.rev, item.streaming, item.redacted, view_mode)
        return changed

    def title(self, now: float) -> str:
        it = self.item
        if it.streaming:
            return "Thinking"
        if it.redacted:
            return "Reasoned privately"
        secs = max(1, int(round(((it.end_ts or now * 1000) - it.start_ts) / 1000)))
        return f"Thought for {secs}s"

    # ---- heights: the head carries the peek while streaming and closed
    def peek_height(self, width: int, m: Measurer) -> int:
        if not self.peeking:
            return 0
        text = self.item.text[-320:]
        if self.peek_doc is None or self._peek_src != text or self._peek_w != width:
            self.peek_doc = m.plain_doc(text, max(40, width - 15), m.font(BODY_SIZE))
            _set_line_height(self.peek_doc, BODY_LINE)
            self._peek_src, self._peek_w = text, width
        return min(doc_height(self.peek_doc), int(PEEK_LINES * BODY_LINE)) + 6

    def measure(self, width: int, m: Measurer) -> int:
        self.warm = True
        self.set_chrome(head_h=HEAD_H + self.peek_height(width, m))
        return self.h

    def estimate(self, width: int, m: Measurer) -> int:
        return self.h

    def cool(self) -> None:
        self.peek_doc = None
        self._peek_src = ""
        self.warm = False

    # ---- paint
    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        t = st.t
        hover = st.hover is not None and st.hover.kind == "head"
        head = QRect(x, y, w, HEAD_H)
        f = st.m.font(theme.FS_UI, 500 if self.item.streaming else 400)
        title = self.title(time.time())
        tw = int(st.m.text_w(title, f))
        if hover:
            paint_head_tint(p, QRect(x - 6, y, 13 + 7 + tw + 7 + 13 + 9 + 12, HEAD_H), st)
        p.drawPixmap(x, y + (HEAD_H - 13) // 2, st.m.icon("sparkles", QColor(t.think), 13))
        color = QColor(t.text_2 if (hover or self.item.streaming) else t.text_3)
        st.m.draw_text(p, x + 20, y + (HEAD_H - st.m.line_h(f)) / 2, title, f, color)
        chevron(
            p,
            x + 20 + tw + 7,
            y + (HEAD_H - 13) // 2,
            13,
            self.reveal,
            QColor(t.text_3),
            st,
            1.0 if (hover or self.open) else 0.55,
        )
        if self.peeking and self.peek_doc is not None and self.head_h > HEAD_H:
            ph = self.head_h - HEAD_H - 6
            clip = QRect(x + 15, y + HEAD_H + 2, w - 15, ph)
            if st.clip is not None:
                clip = clip.intersected(st.clip)
            # the tail of the thought: bottom-aligned inside the peek box
            full = doc_height(self.peek_doc)
            off = max(0, full - ph)
            draw_doc(p, self.peek_doc, x + 15, y + HEAD_H + 2 - off, clip, QColor(t.text_3))
        _ = head

    def hit(self, x: int, y: int) -> Optional[Target]:
        if y < HEAD_H:
            return Target("head", "thinking", (0, 0, self.h_width or 0, HEAD_H))
        return None

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor if target.kind == "head" else None

    def activate(self, target: Target, view: Any) -> None:
        if target.kind == "head":
            self.user_open = not self.open
            view.toggle_group(self, self.user_open)

    def text(self) -> str:
        return self.item.text

    def live(self) -> bool:
        return bool(self.item.streaming)

    def tick(self, now: float) -> Optional[Tuple[int, int, int, int]]:
        return (0, 0, 100000, HEAD_H) if self.item.streaming else None

    @property
    def static(self) -> bool:
        return not self.item.streaming


def _clean(text: str) -> str:
    """Document text as words: paragraph separators as newlines, the padding and joiners of inline
    code gone."""
    return text.replace("\u2029", "\n").replace("\u00a0", "").replace("\u202f", "").replace("\u2060", "")


def _set_line_height(doc: QTextDocument, px: int) -> None:
    from PySide6.QtGui import QTextBlockFormat, QTextCursor

    cur = QTextCursor(doc)
    cur.select(QTextCursor.SelectionType.Document)
    bf = QTextBlockFormat()
    bf.setLineHeight(px, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
    cur.mergeBlockFormat(bf)
