"""Messages Pilcrow writes into a conversation, drawn in the tool cards' grammar (HELPERS.md §6.1).

A note to a lead — a helper finished, failed, was stopped or needs the person — is a card like any
tool's: a state dot in the status column, "Helper finished" as the verb, the helper's name as the
target, its figures at the right, Open on hover, and the handoff in an inset well under the head that
folds like a tool body. A brief or a message from the lead in a helper's conversation is the same card
with "From the lead" as the verb and the lead's title as the target; the standing instructions every
helper gets are folded under the brief, since they are for the model.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from ... import theme
from ...lib.tools_meta import HELPER_HEADER_PREFIX, HELPER_RING, HELPER_TONE
from ...lib.transcript_model import UserItem
from ...store import store
from .. import nodes as nd
from ..paint import Measurer, PaintState, rounded
from ..scene import Group, Target
from .base import MetaPart, NodeBlock, chevron, meta_width, paint_head_tint, paint_meta

NOTE_RE = re.compile(r"<helper-note\s+([^>]*)>\n?(.*?)\n?</helper-note>", re.S)
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
HEAD_H = 28
VERBS = {
    "done": "Helper finished",
    "failed": "Helper failed",
    "stopped": "Helper stopped",
    "needs_action": "Helper needs you",
    "skipped": "Helper skipped",
    "running": "Helper",
}


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


def _cwd_of(sid: str) -> str:
    rt = store.rt.get(sid)
    return str((rt.st.get("cwd") if rt else "") or "")


def _well(text: str, cwd: str) -> nd.Node:
    """Prose from another session, on the inset well a tool's result sits on."""
    return nd.Surface(nd.Markdown(text, compact=True, cwd=cwd), "inset", "light:hair", theme.R3, (12, 10, 12, 10))


# ---------------------------------------------------------------- the head, as a tool card draws it


def paint_card_head(
    p: QPainter,
    x: int,
    y: int,
    w: int,
    head_h: int,
    st: PaintState,
    *,
    verb: str,
    target: str,
    meta: List[MetaPart],
    reveal: float,
    hover: bool,
    hover_open: bool,
    rects: Dict[str, QRect],
    dot: Optional[str] = None,
    icon: Optional[str] = None,
) -> None:
    """A tool card's head: the status column (a state dot or an icon), the verb in text-3, the target,
    the chevron, an open arrow on hover, the figures right-aligned. ``rects`` receives the arrow's
    rectangle in block coordinates for hit testing."""
    t = st.t
    if hover:
        paint_head_tint(p, QRect(x - 6, y, w + 6, head_h), st)
    icon_y = y + (head_h - 14) // 2
    if dot is not None:
        color = QColor(getattr(t, HELPER_TONE.get(dot, "text_4")))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(x + 2 + 2.5, icon_y + 2.5, 9, 9)
        if dot in HELPER_RING:
            p.setPen(QPen(color, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
        p.drawEllipse(r)
    elif icon:
        p.drawPixmap(x + 2, icon_y, st.m.icon(icon, QColor(t.text_2), 14))
    cx = x + 28
    fv = st.m.font(theme.FS_UI)
    if verb:
        st.m.draw_text(p, cx, y + (head_h - st.m.line_h(fv)) / 2, verb, fv, QColor(t.text_3))
        cx += st.m.text_w(verb, fv) + 7
    meta_w = meta_width(st, meta)
    chev_w = 13 + 7
    open_w = 13 + 7
    avail = max(40, (x + w) - cx - meta_w - chev_w - open_w - 8)
    shown = st.m.elide(target, fv, int(avail))
    st.m.draw_text(p, cx, y + (head_h - st.m.line_h(fv)) / 2, shown, fv, QColor(t.text))
    cx += st.m.text_w(shown, fv) + 7
    chevron(
        p, int(cx), y + (head_h - 13) // 2, 13, reveal, QColor(t.text_3), st, 1.0 if (hover or reveal > 0) else 0.45
    )
    cx += chev_w
    rects["open"] = QRect(int(cx) - x - 4, (head_h - 22) // 2, 22, 22)
    if hover or hover_open:
        if hover_open:
            rounded(
                p, QRect(int(cx) - 4, y + (head_h - 22) // 2, 22, 22), theme.blend(t.fill_2, t.canvas), None, theme.R2
            )
        p.drawPixmap(
            int(cx), y + (head_h - 13) // 2, st.m.icon("arrow-up-right", QColor(t.text if hover_open else t.text_3), 13)
        )
    paint_meta(p, x + w, y, head_h, st, meta)


class _Card(Group):
    """A card with a tool-shaped head over one body block; the head folds the body."""

    CHILD_INDENT = 28
    MT = 12
    MB = 12
    __slots__ = ("sid", "body", "user_open", "_rects")

    def __init__(self, key: str, sid: str, has_body: bool) -> None:
        super().__init__(key, head_h=HEAD_H, pad_top=4 if has_body else 0, pad_bottom=10 if has_body else 0, open_=True)
        self.sid = sid
        self.user_open: Optional[bool] = None
        self._rects: Dict[str, QRect] = {}
        self.body = NodeBlock(f"{key}:body", self._build, (0, 0, 0, 0), est_h=60)
        self.shaped = True
        if has_body:
            self.append(self.body)

    # what a subclass says
    def _build(self, m: Measurer) -> nd.Node:  # pragma: no cover - abstract
        raise NotImplementedError

    def head_text(self) -> str:
        h = self.head()
        return f"{h['verb']} {h['target']}"

    def head(self) -> Dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    def open_target(self) -> Optional[str]:
        return None

    def open_tip(self) -> str:
        return "Open"

    # ---- paint and hits
    def paint(self, p: QPainter, x: int, y: int, w: int, st: PaintState) -> None:
        hover = st.hover is not None and st.hover.kind in ("head", "button")
        hover_open = st.lit("button", "open")
        paint_card_head(
            p,
            x,
            y,
            w,
            self.head_h,
            st,
            reveal=self.reveal,
            hover=hover,
            hover_open=hover_open,
            rects=self._rects,
            **self.head(),
        )

    def hit(self, x: int, y: int) -> Optional[Target]:
        if y >= self.head_h:
            return None
        r = self._rects.get("open")
        if r is not None and r.contains(x, y) and self.open_target():
            return Target("button", "open", (r.x(), r.y(), r.width(), r.height()))
        return Target("head", "card", (0, 0, self.h_width or 0, self.head_h))

    def targets(self) -> List[Target]:
        """The head, then the arrow that opens the session (where the head last painted it)."""
        out = [Target("head", "card", (0, 0, self.h_width or 0, self.head_h))]
        r = self._rects.get("open")
        if r is not None and self.open_target():
            out.append(Target("button", "open", (r.x(), r.y(), r.width(), r.height())))
        return out

    def hover_rect(self, target: Target) -> Tuple[int, int, int, int]:
        return (0, 0, self.h_width or 0, self.head_h)  # the open arrow lights the whole head

    def cursor_for(self, target: Target) -> Optional[Any]:
        return Qt.CursorShape.PointingHandCursor

    def tooltip_for(self, target: Target) -> str:
        return self.open_tip() if target.kind == "button" else ""

    def activate(self, target: Target, view: Any) -> None:
        if target.kind == "button" and target.id == "open":
            sid = self.open_target()
            if sid:
                store.open_session(sid)
            return
        if target.kind == "head" and self.children:
            self.user_open = not self.open
            view.toggle_group(self, self.user_open)

    def live(self) -> bool:
        return False


# ---------------------------------------------------------------- a helper's note to its lead


class NoteCard(_Card):
    KIND = "note-card"
    __slots__ = ("part",)

    def __init__(self, key: str, part: Dict[str, str], sid: str) -> None:
        self.part = part
        super().__init__(key, sid, has_body=bool((part.get("body") or "").strip()))

    def _build(self, m: Measurer) -> nd.Node:
        return _well((self.part.get("body") or "").strip(), _cwd_of(self.sid))

    def head(self) -> Dict[str, Any]:
        part = self.part
        state = str(part.get("state") or "")
        meta: List[MetaPart] = []
        t = theme.tokens()
        for key in ("elapsed", "cost"):
            if part.get(key):
                meta.append(("text", str(part[key]), t.text_3))
        files = str(part.get("files") or "")
        if files and files != "0":
            if str(part.get("add") or "0") != "0":
                meta.append(("mono", f"+{part.get('add')}", t.ok))
            if str(part.get("del") or "0") != "0":
                meta.append(("mono", f"−{part.get('del')}", t.err))
        return {
            "dot": state or "stopped",
            "verb": VERBS.get(state, "Helper"),
            "target": str(part.get("name") or ""),
            "meta": meta,
        }

    def open_target(self) -> Optional[str]:
        rt = store.rt.get(self.sid)
        helpers = (rt.st.get("helpers") if rt else None) or []
        name = self.part.get("name")
        return next(
            (str(h.get("id")) for h in helpers if isinstance(h, dict) and h.get("name") == name and h.get("id")), None
        )

    def open_tip(self) -> str:
        return "Open the helper's session"

    def text(self) -> str:
        body = (self.part.get("body") or "").strip()
        return self.head_text() + (f"\n{body}" if body else "")


class NoteBlock(Group):
    """One note message: a card per helper it speaks of (a note coalesces what happened within a
    couple of seconds). The text around the cards is for the model and is not drawn."""

    KIND = "note"
    __slots__ = ("item", "sid", "cards")

    def __init__(self, item: UserItem, sid: str) -> None:
        super().__init__(f"note:{item.id}", head_h=0, pad_top=0, pad_bottom=0, open_=True)
        self.item = item
        self.sid = sid
        self.cards: List[NoteCard] = []
        self.shaped = True
        self.sig = None
        self.sync(item)

    def sync(self, item: UserItem) -> bool:
        changed = item.rev != self.sig
        self.item = item
        self.sig = item.rev
        self.rev = item.rev
        if not changed:
            return False
        parts, _tail = parse_notes(item.text)
        if not parts:
            parts = [{"name": "note", "state": "", "body": item.text}]
        while self.children:
            self.remove(len(self.children) - 1)
        self.cards = [NoteCard(f"note:{item.id}:{k}", part, self.sid) for k, part in enumerate(parts)]
        for c in self.cards:
            self.append(c)
        return True

    def text(self) -> str:
        return "\n\n".join(c.text() for c in self.cards)

    def live(self) -> bool:
        return False


# ---------------------------------------------------------------- the lead's words in a helper's conversation


class FromLeadBlock(_Card):
    """A brief or a message the lead sent (``_from: lead``), drawn as a card rather than as the
    person's bubble: it is not the person speaking. The standing instructions a brief opens with
    fold under it."""

    KIND = "from-lead"
    __slots__ = ("item", "state")

    def __init__(self, item: UserItem, sid: str) -> None:
        self.item = item
        self.state: Dict[str, Any] = {}
        super().__init__(f"from:{item.id}", sid, has_body=bool(item.text.strip()))
        self.body.on("toggle:hdr", self._toggle_hdr)
        self.sig = item.rev
        self.rev = item.rev

    def sync(self, item: UserItem) -> bool:
        changed = item.rev != self.sig
        self.item = item
        self.sig = item.rev
        self.rev = item.rev
        if changed:
            self.body.rebuild()
        return changed

    def _split(self) -> Tuple[str, str]:
        text = self.item.text
        if text.startswith(HELPER_HEADER_PREFIX):
            head, _sep, rest = text.partition("\n\n")
            return head.strip(), rest.strip()
        return "", text

    def _build(self, m: Measurer) -> nd.Node:
        header, brief = self._split()
        col = nd.vstack(spacing=8)
        if brief:
            col.add(_well(brief, _cwd_of(self.sid)))
        if header:
            col.add(
                nd.Toggle("Standing instructions for a helper", "hdr", nd.note(header), bool(self.state.get("hdr")))
            )
        return col

    def _toggle_hdr(self, _t: Target, view: Any) -> None:
        self.state["hdr"] = not self.state.get("hdr")
        self.body.rebuild()
        view.remeasure(self.body)

    def _lead(self) -> Dict[str, Any]:
        rt = store.rt.get(self.sid)
        h = (rt.st.get("helper") if rt else None) or {}
        return h if isinstance(h, dict) else {}

    def head(self) -> Dict[str, Any]:
        title = str(self._lead().get("leadTitle") or "the lead session")
        return {"icon": "corner-down-right", "verb": "From the lead", "target": title, "meta": []}

    def open_target(self) -> Optional[str]:
        lead = self._lead().get("leadId")
        return str(lead) if lead else None

    def open_tip(self) -> str:
        return "Open the lead session"

    def text(self) -> str:
        return self.item.text
