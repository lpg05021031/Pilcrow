"""The view's input: what the pointer and the keyboard are on (hits, hover, clicks), the focus ring
over targets, the selection and copy, the context menu and links. ``ViewInput`` is a part of
``CanvasView`` (a mixin with no state of its own): it reads and sets the view's ``hover``,
``pressed``, ``focus`` and ``selection`` and repaints through its viewport; the commit, the
painting and the scrolling stay in view.py.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QAccessible, QCursor, QGuiApplication, QTextCursor
from PySide6.QtWidgets import QToolTip

from ..lib.fmt import resolve_path
from ..store import store
from ..widgets.ui.menu import show_menu_at
from . import a11y
from .paint import FULL
from .scene import Block, Group, Target

TOOLTIP_MS = 600
TRIPLE_CLICK_MS = 0.4  # a press this soon after a double-click selects the block
FOCUS_KINDS = ("head", "button", "link", "toggle", "more", "chev", "image")  # what Tab lands on


class Selection:
    __slots__ = ("a_key", "a_pos", "f_key", "f_pos")

    def __init__(self, a_key: str, a_pos: Optional[int]) -> None:
        self.a_key, self.a_pos = a_key, a_pos
        self.f_key, self.f_pos = a_key, a_pos

    @property
    def empty(self) -> bool:
        """Nothing selected: the same point twice. The same block with no positions is the whole block."""
        return self.a_key == self.f_key and self.a_pos == self.f_pos and self.a_pos is not None


class ViewInput:
    """Hits, hover, clicks, focus, selection, the context menu and links, for ``CanvasView``."""

    # ---------------------------------------------------------------- hits, hover, clicks
    def hit(self, pos: QPoint) -> Optional[Tuple[Block, Target]]:
        y = self.scene_y(pos.y())
        found = self.scene.leaf_at(y)
        if found is None:
            return None
        b, by = found
        if b.h <= 0 or y < by or y >= by + b.h:
            return None
        lx = pos.x() - (self.col_x + b.indent)
        ly = y - by
        if lx < -8 or lx > self.col_w - b.indent + 8:
            return None
        t = b.hit(max(0, lx), ly)
        if t is None:
            return None
        return b, t

    def _set_hover(self, hit: Optional[Tuple[Block, Target]]) -> None:
        old = self.hover
        if old is None and hit is None:
            return
        if old is not None and hit is not None and old[0] is hit[0] and old[1] == hit[1]:
            return
        self.hover = hit
        vp = self.viewport()
        for h in (old, hit):
            if h is not None:
                try:
                    r = self._hover_rect(*h)
                except RuntimeError:
                    continue
                if not r.isEmpty():
                    vp.update(r)
        cursor = hit[0].cursor_for(hit[1]) if hit is not None else None
        vp.setCursor(cursor if cursor is not None else Qt.CursorShape.ArrowCursor)
        self.hovered_block.emit(hit[0] if hit is not None else None)
        self._tip_timer.stop()
        QToolTip.hideText()
        if hit is not None and hit[0].tooltip_for(hit[1]):
            self._tip_timer.start()

    def _hover_rect(self, b: Block, t: Target) -> QRect:
        """What a hover on ``t`` changes, in viewport coordinates: the block's ``hover_rect``, with
        the 8px either side a head's tint reaches past the column."""
        x, y, w, h = b.hover_rect(t)
        if w <= 0 or h <= 0:
            return QRect()
        br = self.block_rect(b)
        return QRect(br.x() + x - 8, br.y() + y, w + 16, h)

    def _rehover(self) -> None:

        gp = self.viewport().mapFromGlobal(QCursor.pos()) if self.underMouse() else None
        if gp is not None and self.viewport().rect().contains(gp):
            self._set_hover(self.hit(gp))
        elif self.hover is not None:
            self._set_hover(None)

    def _show_tip(self) -> None:
        if self.hover is None:
            return
        b, t = self.hover
        tip = b.tooltip_for(t)
        if tip:
            QToolTip.showText(self.viewport().mapToGlobal(self._tip_pos), tip, self.viewport())

    def mouseMoveEvent(self, e) -> None:
        pos = e.position().toPoint()
        self._tip_pos = pos
        if self._selecting and self.selection is not None:
            self._extend_selection(pos)
            return
        self._set_hover(self.hit(pos))

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(e)
            return
        self.setFocus()
        pos = e.position().toPoint()
        if time.monotonic() - self._dbl_at < TRIPLE_CLICK_MS and (pos - self._dbl_pos).manhattanLength() < 6:
            # the third click: the whole block
            found = self.scene.leaf_at(self.scene_y(pos.y()))
            if found is not None and found[0].text():
                self.selection = Selection(found[0].key, None)
                self.viewport().update(self.block_rect(found[0]))
            self._dbl_at = 0.0
            return
        hit = self.hit(pos)
        self.pressed = hit
        if hit is not None and hit[1].kind in ("button", "head", "toggle", "link", "image", "chev", "more"):
            self.viewport().update(self.block_rect(hit[0]))
            return
        # text: start a selection (on the block under the pointer, whether or not it named a target)
        self.clear_selection()
        found = self.scene.leaf_at(self.scene_y(pos.y())) if hit is None else None
        b = hit[0] if hit is not None else found[0] if found is not None else None
        if b is not None and b.h > 0:
            lx = pos.x() - (self.col_x + b.indent)
            ly = self.scene_y(pos.y()) - b.y()
            p0 = b.text_pos_at(lx, ly) if -8 <= lx <= self.col_w - b.indent + 8 else None
            if p0 is not None:  # a press beside the words selects nothing until it is dragged
                self.selection = Selection(b.key, p0)
                self._selecting = True

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(e)
            return
        pressed = self.pressed
        self.pressed = None
        if self._selecting:
            self._selecting = False
            if self.selection is not None and self.selection.empty:
                self.clear_selection()
            return
        pos = e.position().toPoint()
        hit = self.hit(pos)
        if pressed is not None:
            self.viewport().update(self.block_rect(pressed[0]))
        if pressed is not None and hit is not None and pressed[0] is hit[0] and pressed[1] == hit[1]:
            self.activate(hit[0], hit[1])

    def mouseDoubleClickEvent(self, e) -> None:
        pos = e.position().toPoint()
        hit = self.hit(pos)
        if hit is not None and hit[1].kind not in ("text", "row"):
            return  # a button or a head: the two clicks were two activations
        found = self.scene.leaf_at(self.scene_y(pos.y()))
        if found is None:
            return
        b = found[0]
        self._dbl_at, self._dbl_pos = time.monotonic(), pos
        lx = pos.x() - (self.col_x + b.indent)
        ly = self.scene_y(pos.y()) - b.y()
        word = self.word_at(b, lx, ly)
        if word is not None:
            self.selection = Selection(b.key, word[0])
            self.selection.f_pos = word[1]
        elif b.text():
            self.selection = Selection(b.key, None)  # the whole block
        else:
            return
        self.viewport().update(self.block_rect(b))

    @staticmethod
    def word_at(b: Block, x: int, y: int) -> Optional[Tuple[int, int]]:
        """The word under a point of a block that draws a document: (start, end) positions."""
        pos = b.text_pos_at(x, y)
        doc = getattr(b, "doc", None)
        if pos is None or doc is None:
            return None
        cur = QTextCursor(doc)
        cur.setPosition(pos)
        cur.select(QTextCursor.SelectionType.WordUnderCursor)
        a, z = cur.selectionStart(), cur.selectionEnd()
        return (a, z) if z > a else None

    # ---------------------------------------------------------------- the context menu
    def _context_menu(self, e) -> None:

        show_menu_at(self.context_items(e.pos()), e.globalPos(), self, 230)

    def context_items(self, pos: QPoint) -> List[Dict[str, Any]]:
        """The right-click menu at a viewport point: copy, select all, the link or the file there."""
        hit = self.hit(pos)
        found = self.scene.leaf_at(self.scene_y(pos.y()))
        b = found[0] if found is not None else None
        items: List[Dict[str, Any]] = []
        has_sel = self.selection is not None and not self.selection.empty
        items.append(
            {
                "label": "Copy",
                "icon": "copy",
                "kbd": "Ctrl+C",
                "disabled": not has_sel,
                "on_select": self.copy_selection,
            }
        )
        if b is not None and b.text():
            text = b.selected_text(None, None)
            items.append({"label": "Copy this message", "icon": "copy", "on_select": lambda t=text: self.copy_text(t)})
        items.append({"label": "Select all", "icon": "text-select", "kbd": "Ctrl+A", "on_select": self.select_all})
        if hit is not None and hit[1].kind == "link":
            href = str(hit[1].data or hit[1].id)
            cwd = getattr(hit[0], "cwd", "")
            items.append({"separator": True})
            items.append(
                {"label": "Open link", "icon": "external-link", "on_select": lambda h=href, c=cwd: self.open_link(h, c)}
            )
            items.append({"label": "Copy link", "icon": "link", "on_select": lambda h=href: self.copy_text(h)})
        path = getattr(b, "file", None) if b is not None else None
        if not path and b is not None and b.parent is not None:
            path = getattr(b.parent, "file", None)
        if path:
            items.append({"separator": True})
            items.append(
                {"label": "Open file", "icon": "arrow-up-right", "on_select": lambda p=path: store.open_file(p)}
            )
            items.append(
                {"label": "Copy path", "icon": "copy", "mono": True, "on_select": lambda p=path: self.copy_text(p)}
            )
        return items

    def select_all(self) -> None:
        first = self.scene.leaf_at(0)
        last = self.scene.leaf_at(max(0, self.scene.total - 1))
        if first is None or last is None:
            return
        self.selection = Selection(first[0].key, None)
        self.selection.f_key, self.selection.f_pos = last[0].key, None
        self.viewport().update()

    def activate(self, b: Block, t: Target) -> None:
        self.begin()
        try:
            b.activate(t, self)
        finally:
            self.end()
        self._rehover()

    def activate_focus(self) -> None:
        if self.focus is None:
            return
        b = self.scene.block(self.focus[0])
        if b is not None:
            self.activate(b, self.focus[1])

    def _focus_for(self, b: Block) -> Optional[Target]:
        return self.focus[1] if (self.focus is not None and self.focus[0] == b.key) else None

    def focusables(self) -> List[Tuple[Block, Target]]:
        """The targets Tab visits, in reading order over the blocks on screen: a block's own targets
        (buttons, links, toggles), else its head when it has one."""
        return [(b, t) for b in self.visible_blocks() for t in self.block_targets(b)]

    def block_targets(self, b: Block) -> List[Target]:
        """What Tab visits in one block: its own targets (buttons, links, toggles), else its head."""
        ts = [t for t in b.targets() if t.kind in FOCUS_KINDS]
        if not ts:
            head = self.head_target(b)
            ts = [head] if head is not None else []
        return ts

    @staticmethod
    def head_target(b: Block) -> Optional[Target]:
        """What a press on the block's first line does (its head, a toggle), if anything."""
        t = b.hit(2, 2)
        return t if t is not None and t.kind in FOCUS_KINDS else None

    def _focus_step(self, d: int) -> bool:
        """Move the focus to the next (d=1) or previous (d=-1) target on screen; False at the ends."""
        ring = self.focusables()
        if not ring:
            return False
        cur = self.focus
        i = -1
        if cur is not None:
            for k, (b, t) in enumerate(ring):
                if b.key == cur[0] and t == cur[1]:
                    i = k
                    break
        if i < 0:
            if d < 0:
                return False
            nxt = 0
        else:
            nxt = i + d
            if nxt < 0 or nxt >= len(ring):
                return False
        b, t = ring[nxt]
        self._set_focus((b.key, t))
        self.scroll_to_block(b)
        return True

    def _set_focus(self, f: Optional[Tuple[str, Target]]) -> None:
        old = self.focus
        if old == f:
            return
        self.focus = f
        self._update_focus_rect(old)
        if f is not None:
            a11y.announce(self, f[0], QAccessible.Event.Focus, f[1])

    def _update_focus_rect(self, *keys: Optional[Tuple[str, Target]]) -> None:
        vp = self.viewport()
        for f in (*keys, self.focus):
            if f is None:
                continue
            b = self.scene.block(f[0])
            if b is not None:
                vp.update(self.block_rect(b).adjusted(-8, -4, 8, 4))

    # ---------------------------------------------------------------- selection and copy
    def _order(self, a: Block, b: Block) -> Tuple[Block, Block]:
        return (a, b) if a.y() <= b.y() else (b, a)

    def _selection_for(self, b: Block) -> Optional[Any]:
        s = self.selection
        if s is None or s.empty:
            return None
        a_b = self.scene.block(s.a_key)
        f_b = self.scene.block(s.f_key)
        if a_b is None or f_b is None:
            return None
        if a_b is b and f_b is b:
            if s.a_pos is None or s.f_pos is None:
                return FULL
            return (min(s.a_pos, s.f_pos), max(s.a_pos, s.f_pos))
        first, last = self._order(a_b, f_b)
        if b is first:
            pos = s.a_pos if a_b is first else s.f_pos
            return (pos, 10**9) if pos is not None else FULL
        if b is last:
            pos = s.a_pos if a_b is last else s.f_pos
            return (0, pos) if pos is not None else FULL
        by = b.y()
        if first.y() < by < last.y():
            return FULL
        return None

    def _extend_selection(self, pos: QPoint) -> None:
        s = self.selection
        if s is None:
            return
        hit = self.hit(pos)
        found = self.scene.leaf_at(self.scene_y(pos.y())) if hit is None else None
        b = hit[0] if hit is not None else found[0] if found is not None else None
        if b is None:
            return
        lx = pos.x() - (self.col_x + b.indent)
        ly = self.scene_y(pos.y()) - b.y()
        # over the words (a target's or not) the focus is a position; beside them, the whole block
        p1 = b.text_pos_at(lx, ly) if -8 <= lx <= self.col_w - b.indent + 8 else None
        if b.key != s.f_key or p1 != s.f_pos:
            old = s.f_key
            s.f_key, s.f_pos = b.key, p1
            # only the rows between the old edge and the new one changed (the anchor's row too when
            # the edge crossed it, which lies between them)
            self._update_span(old, b.key)

    def clear_selection(self) -> None:
        s = self.selection
        if s is not None:
            self.selection = None
            self._update_span(s.a_key, s.f_key)

    def _update_span(self, a_key: str, b_key: str) -> None:
        """Repaint the rows from one block to another, both included."""
        a, b = self.scene.block(a_key), self.scene.block(b_key)
        if a is None or b is None:
            self.viewport().update()
            return
        first, last = self._order(a, b)
        y0 = self.view_y(first.y())
        self.viewport().update(QRect(0, y0, self.viewport().width(), self.view_y(last.y() + last.h) - y0))

    def selected_text(self) -> str:
        s = self.selection
        if s is None or s.empty:
            return ""
        a_b = self.scene.block(s.a_key)
        f_b = self.scene.block(s.f_key)
        if a_b is None or f_b is None:
            return ""
        if a_b is f_b:
            return (
                a_b.selected_text(s.a_pos, s.f_pos)
                if (s.a_pos is not None and s.f_pos is not None)
                else a_b.selected_text(None, None)
            )
        first, last = self._order(a_b, f_b)
        parts: List[str] = []
        for b, _y in self.scene.leaves_between(first.y(), last.y() + max(1, last.h)):
            if isinstance(b, Group) and b.children:
                continue
            sel = self._selection_for(b)
            if sel is None:
                continue
            if sel == FULL:
                t = b.selected_text(None, None)
            else:
                t = (
                    b.selected_text(sel[0], sel[1] if sel[1] < 10**9 else None)
                    if sel[1] < 10**9
                    else b.selected_text(sel[0], len(b.text()) if b.text() else None)
                )
            if t:
                parts.append(t)
        return "\n\n".join(parts)

    def copy_selection(self) -> None:
        text = self.selected_text()
        if text:
            self.copy_text(text)

    def copy_text(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)

    # ---------------------------------------------------------------- links
    def open_link(self, href: str, cwd: str) -> None:

        if href.startswith("pilcrow-path://"):
            q = parse_qs(urlparse(href).query)
            path = unquote(q.get("p", [""])[0])
            line = q.get("l", [""])[0]
            store.open_file(resolve_path(path, cwd), int(line) if line.isdigit() else None)
            return
        if href.startswith(("http://", "https://", "mailto:")):
            store.call("shell.openExternal", {"url": href}).catch(
                lambda _e: store.toast("Could not open the link", "error")
            )
