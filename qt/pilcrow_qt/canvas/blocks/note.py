"""A note Pilcrow wrote into a lead's conversation (HELPERS.md §5.4): a helper finished, failed or was
stopped. Drawn apart from the person's messages — left-aligned, a rule in the state's colour, the helper's
name and numbers in the head, its handoff as quiet prose — with Open at the right."""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QTextDocument

from ... import theme
from ...lib.transcript_model import UserItem
from ...store import store
from ..paint import Measurer, PaintState, doc_height, draw_doc, rounded
from ..scene import Block, Target

NOTE_RE = re.compile(r"<helper-note\s+([^>]*)>\n?(.*?)\n?</helper-note>", re.S)
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
HEAD_H = 24
PAD = 12
GAP = 6
TAIL_H = 18
OPEN_W = 40
TONE = {"done": "ok", "failed": "err", "needs_action": "warn", "stopped": "text_3", "skipped": "text_4"}


def parse_notes(text: str) -> Tuple[List[Dict[str, str]], str]:
    """The ``<helper-note>`` blocks of a note (their attributes and body) and the text around them."""
    parts: List[Dict[str, str]] = []
    tail: List[str] = []
    last = 0
    for m in NOTE_RE.finditer(text):
        before = text[last : m.start()].strip()
        if before:
            tail.append(before)
        attrs = dict(ATTR_RE.findall(m.group(1)))
        attrs["body"] = m.group(2).strip()
        parts.append(attrs)
        last = m.end()
    rest = text[last:].strip()
    if rest:
        tail.append(rest)
    return parts, " ".join(tail)


class NoteBlock(Block):
    KIND = "note"
    __slots__ = ("item", "sid", "parts", "tail", "docs", "rects")

    def __init__(self, item: UserItem, sid: str) -> None:
        super().__init__(f"note:{item.id}")
        self.item = item
        self.sid = sid
        self.parts, self.tail = parse_notes(item.text)
        self.docs: List[Optional[QTextDocument]] = []
        self.rects: List[QRect] = []
        self.sig = None
        self.sync(item)

    def sync(self, item: UserItem) -> bool:
        changed = item.rev != self.sig
        self.item = item
        self.sig = item.rev
        self.rev = item.rev
        if changed:
            self.parts, self.tail = parse_notes(item.text)
            self.docs = []
        return changed

    # ---- heights
    @staticmethod
    def _width(width: int) -> int:
        return max(120, int(width * 0.84))

    def estimate(self, width: int, m: Measurer) -> int:
        inner = self._width(width) - 2 * PAD
        f = m.font(theme.FS_SMALL)
        lh = max(1, m.line_h(f))
        total = 0
        for p in self.parts:
            body = p.get("body") or ""
            lines = max(1, int(m.fast_height(body, inner, f, lh) / lh)) if body else 0
            total += HEAD_H + (lines * lh + 6 if body else 0) + 8 + GAP
        if not self.parts:
            total += HEAD_H + GAP
        return total + (TAIL_H if self.tail else 0) + 2

    fast_measure = estimate

    def measure(self, width: int, m: Measurer) -> int:
        self.warm = True
        w = self._width(width)
        inner = w - 2 * PAD
        f = m.font(theme.FS_SMALL)
        lh = max(1, m.line_h(f))
        self.docs = []
        self.rects = []
        y = 0
        for p in self.parts or [{"body": self.item.text}]:
            body = p.get("body") or ""
            doc = (
                m.html_doc(
                    f'<p style="white-space:pre-wrap;margin:0;line-height:{lh}px">{html.escape(body)}</p>', inner, f
                )
                if body
                else None
            )
            bh = doc_height(doc) if doc is not None else 0
            h = HEAD_H + (bh + 6 if doc is not None else 0) + 8
            self.rects.append(QRect(0, y, w, h))
            self.docs.append(doc)
            y += h + GAP
        if self.tail:
            y += TAIL_H
        return y + 2

    def cool(self) -> None:
        self.docs = []
        self.warm = False

    # ---- paint
    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        t = st.t
        f_head = st.m.font(theme.FS_SMALL, 600)
        f_meta = st.m.font(theme.FS_MICRO)
        bg = theme.blend(t.fill_1, t.canvas)
        parts = self.parts or [{"name": "note", "state": "", "body": self.item.text}]
        for k, rect in enumerate(self.rects):
            part = parts[k] if k < len(parts) else {}
            r = rect.translated(x, y)
            rounded(p, r, bg, None, theme.R2)
            tone = TONE.get(str(part.get("state") or ""), "text_3")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(getattr(t, tone)))
            p.drawRoundedRect(r.x(), r.y() + 6, 2, r.height() - 12, 1, 1)
            cx = r.x() + PAD
            name = str(part.get("name") or "")
            st.m.draw_text(p, cx, r.y() + (HEAD_H - st.m.line_h(f_head)) / 2, name, f_head, QColor(t.text))
            cx += st.m.text_w(name, f_head) + 8
            bits = [str(part.get("state") or "").replace("_", " ")]
            for key in ("elapsed", "cost"):
                if part.get(key):
                    bits.append(str(part[key]))
            files = str(part.get("files") or "")
            if files and files != "0":
                bits.append(f"{files} files +{part.get('add', '0')} −{part.get('del', '0')}")
            meta = " · ".join(b for b in bits if b)
            ob = self._open_rect(k, x, y)
            st.m.draw_text(p, cx, r.y() + (HEAD_H - st.m.line_h(f_meta)) / 2, meta, f_meta, QColor(t.text_3))
            on = st.hover is not None and st.hover.kind == "button" and st.hover.id == f"open{k}"
            if on:
                rounded(p, ob, theme.blend(t.fill_2, bg.name()), None, theme.R2)
            st.m.draw_text(
                p,
                ob.x() + 8,
                ob.y() + (ob.height() - st.m.line_h(f_meta)) / 2,
                "Open",
                f_meta,
                QColor(t.text if on else t.text_2),
            )
            doc = self.docs[k] if k < len(self.docs) else None
            if doc is not None:
                clip = QRect(r.x() + PAD, r.y() + HEAD_H, r.width() - 2 * PAD, r.height() - HEAD_H - 8)
                if st.clip is not None:
                    clip = clip.intersected(st.clip)
                draw_doc(p, doc, r.x() + PAD, r.y() + HEAD_H, clip, QColor(t.text_2), None)
        if self.tail:
            ty = y + ((self.rects[-1].bottom() + 1 + GAP) if self.rects else 0)
            st.m.draw_text(p, x + 2, ty + 2, self.tail, f_meta, QColor(t.text_3))

    def _open_rect(self, k: int, x: int, y: int) -> QRect:
        r = self.rects[k].translated(x, y)
        return QRect(r.right() - 8 - OPEN_W, r.y() + 2, OPEN_W, HEAD_H - 4)

    # ---- interaction
    def hit(self, x: int, y: int) -> Optional[Target]:
        for k in range(len(self.rects)):
            ob = self._open_rect(k, 0, 0)
            if ob.contains(x, y):
                return Target("button", f"open{k}", (ob.x(), ob.y(), ob.width(), ob.height()))
        return Target("row", "row", (0, 0, self.h_width or 0, self.h))

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor if target.kind == "button" else None

    def tooltip_for(self, target: Target) -> str:
        return "Open the helper's session" if target.kind == "button" else ""

    def activate(self, target: Target, _view: Any) -> None:
        if target.kind != "button" or not self.parts:
            return
        k = int(str(target.id)[4:] or 0)
        name = self.parts[k].get("name") if k < len(self.parts) else None
        rt = store.rt.get(self.sid)
        helpers = (rt.st.get("helpers") if rt else None) or []
        hid = next((h.get("id") for h in helpers if isinstance(h, dict) and h.get("name") == name), None)
        if hid:
            store.open_session(str(hid))
        else:
            store.toast(f"{name} is no longer a helper of this session", "info")

    def text(self) -> str:
        return self.item.text
