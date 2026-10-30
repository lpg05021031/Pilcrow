"""The conversation as a scene, and the widget that shows it.

``TranscriptScene`` turns the transcript model into units and the units into groups and blocks,
incrementally: the units before the last user message are settled and cached, each update
recomputes the live turn and the unit before it, and only the blocks that changed are measured again.
Scenes are kept per session (an LRU of eight), so switching back to a conversation is one paint.

``Transcript`` is the widget the session view hosts: the canvas, the "Latest" pill, the find bar,
and the public methods the rest of the client uses (``set_empty``, ``open_find``,
``scroll_to_bottom``, ``reveal_item``, ``find_entries``, ``units``).
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

import shiboken6
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from .. import icons, theme
from ..lib.tools_meta import tool_meta
from ..lib.transcript_model import Item, TranscriptModel
from ..lib.units import Unit, group_turns, live_turn_folds
from ..store import store
from ..widgets.transcript.ctx import ToolCtx
from ..widgets.ui.signals import bind
from .blocks.fold import sync_unit_group, unit_group, unit_group_key
from .blocks.island import IslandBlock
from .blocks.misc import SkeletonBlock, WorkingBlock
from .scene import GRADE_EXACT, Block, Group, Scene
from .view import CanvasView

BUSY = ("running", "starting", "requires_action")
Json = Dict[str, Any]


def _boundary(items: List[Item]) -> int:
    """Index just after the last user message (or compaction / divider): items before it are settled."""
    for i in range(len(items) - 1, -1, -1):
        it = items[i]
        if it.kind == "user" or it.kind == "compact" or (it.kind == "notice" and getattr(it, "tag", None) == "divider"):
            return i
    return 0


class OrphanIsland(IslandBlock):
    MT = 10
    MB = 14

    def __init__(self, perm: Json, ctx: ToolCtx) -> None:
        self.perm, self.ctx = perm, ctx
        super().__init__(f"orphan:{perm.get('requestId')}", self._make)

    def _make(self) -> QWidget:
        from ..widgets.transcript.permission import PermissionCard

        return PermissionCard(self.perm, self.ctx, None, stale=False)


class EmptyIsland(IslandBlock):
    """The session view's empty card, shown through the scene; the view owns the widget."""

    def __init__(self, widget: QWidget) -> None:
        self._w = widget
        super().__init__("empty", lambda: widget, align="center", max_w=620, owned=False)

    @property
    def usable(self) -> bool:
        return shiboken6.isValid(self._w)


class TranscriptScene(Scene):
    def __init__(self, sid: str, cwd: str) -> None:
        super().__init__()
        self.sid = sid
        self.cwd = cwd
        self.ctx: Optional[ToolCtx] = None
        self.fold_state: Dict[str, bool] = {}
        self.units: List[Unit] = []
        self.settled_units: List[Unit] = []
        self.settled_count = 0
        self._settled_last: Optional[Item] = None
        self.working = WorkingBlock(sid)
        self.skeleton = SkeletonBlock()
        self.empty: Optional[EmptyIsland] = None
        self.orphans: OrderedDict[str, OrphanIsland] = OrderedDict()
        self.stale = True
        self.saved: Optional[Tuple[str, int]] = None  # the remembered place, or None for the live end
        self.saved_following = True
        self.model_rev = -1
        self.shapes_stale = False
        self._find_cache: Dict[str, Tuple[int, str]] = {}

    # ---- the context
    def _ctx(self, rt, view: str) -> ToolCtx:
        pend: Dict[str, Json] = {}
        for p in rt.pending if rt else []:
            if p.get("toolUseId"):
                pend[str(p["toolUseId"])] = p
        old = self.ctx
        if (
            old is None
            or view != old.view
            or {k: v.get("requestId") for k, v in pend.items()}
            != {k: v.get("requestId") for k, v in old.pending.items()}
        ):
            if old is not None and view != old.view:
                self.shapes_stale = True
            self.ctx = ToolCtx(self.sid, self.cwd, pend, 0, view)
        return self.ctx

    # ---- sync
    def sync(self, model: TranscriptModel, rt, view: str) -> List[Block]:
        """Bring the scene in line with the model; returns the blocks whose content changed."""
        ctx = self._ctx(rt, view)
        items = model.items
        loaded = bool(rt and rt.loaded)
        loading = bool(rt and rt.loading) and not loaded
        running = bool(rt and rt.status in BUSY)
        # the settled prefix
        if self.settled_count > len(items) or (
            self.settled_count and items[self.settled_count - 1] is not self._settled_last
        ):
            self.settled_units, self.settled_count, self._settled_last = [], 0, None
        b = _boundary(items)
        if b > self.settled_count:
            self.settled_units.extend(group_turns(items[self.settled_count : b], False))
            self.settled_count = b
            self._settled_last = items[b - 1] if b else None
        tail = group_turns(items[b:], running) if b < len(items) else []
        units = self.settled_units + tail
        self.units = units
        changed: List[Block] = []
        # the root: skeleton, empty card, units, orphans, working line
        keys: List[str] = ["skeleton"]
        if loaded and not items and self.empty is not None and self.empty.usable:
            keys.append("empty")
        unit_keys = [unit_group_key(u) for u in units]
        unit_at = {k: i for i, k in enumerate(unit_keys)}
        keys.extend(unit_keys)
        orphan_perms = [
            p
            for p in (rt.pending if rt else [])
            if not p.get("toolUseId") or str(p.get("toolUseId")) not in model.tools
        ]
        orphan_keys = [f"orphan:{p.get('requestId')}" for p in orphan_perms]
        orphan_at = {k: i for i, k in enumerate(orphan_keys)}
        keys.extend(orphan_keys)
        keys.append("working")
        n_settled = len(self.settled_units)
        resync_from = max(0, n_settled - 1) if not self.shapes_stale else 0

        def make(i: int) -> Block:
            k = keys[i]
            if k == "skeleton":
                return self.skeleton
            if k == "empty":
                return self.empty  # type: ignore[return-value]
            if k == "working":
                return self.working
            if k.startswith("orphan:"):
                p = orphan_perms[orphan_at[k]]
                o = OrphanIsland(p, ctx)
                self.orphans[k] = o
                return o
            u = units[unit_at[k]]
            return unit_group(u, ctx, self.sid, self.fold_state)

        def update(blk: Block, i: int) -> List[Block]:
            k = keys[i]
            if k in ("skeleton", "empty", "working") or k.startswith("orphan:"):
                return []
            ui = unit_at[k]
            if ui < resync_from:
                return []
            return sync_unit_group(blk, units[ui], ctx)  # type: ignore[arg-type]

        from .blocks.fold import reconcile

        changed.extend(reconcile(self.root, keys, make, update, settled=1 + resync_from))
        self.shapes_stale = False
        # the skeleton and the working line
        if self.skeleton.shown != loading:
            self.skeleton.shown = loading
            changed.append(self.skeleton)
        turn_start = 0.0
        for it in reversed(items):
            if it.kind == "user":
                turn_start = it.ts or 0
                break
        if self.working.sync(turn_start, live_turn_folds(items)):
            changed.append(self.working)
        self.model_rev = model.rev
        self.stale = False
        return changed

    # ---- lookups
    def block_for_item(self, item_id: str) -> Optional[Block]:
        blk = self._lookup(item_id)
        if blk is None:
            # inside a fold that has not been built yet: build it, then look again
            i = self.unit_index_of(item_id)
            g = self.root.children[1 + i] if 0 <= i < len(self.units) and 1 + i < len(self.root.children) else None
            if isinstance(g, Group) and not g.shaped:
                g.ensure_shape(self)
                blk = self._lookup(item_id)
        return blk

    def _lookup(self, item_id: str) -> Optional[Block]:
        for prefix in ("text", "user", "thinking", "tool", "special", "notice", "result", "compact", "cmd"):
            blk = self.by_key.get(f"{prefix}:{item_id}")
            if blk is not None:
                return blk
        return None

    def unit_index_of(self, item_id: str) -> int:
        for i, u in enumerate(self.units):
            for it in u.work if u.kind == "work" else u.items:
                if it.id == item_id or getattr(it, "uuid", None) == item_id:
                    return i
        return -1

    def find_entries(self) -> List[Tuple[int, str, str]]:
        """(unit index, item id, text) for everything find can match; the texts are kept per item
        revision, so a search costs the walk, not the tool metadata."""
        out: List[Tuple[int, str, str]] = []
        cache = self._find_cache
        fresh: Dict[str, Tuple[int, str]] = {}
        for i, u in enumerate(self.units):
            for it in u.work if u.kind == "work" else u.items:
                hit = cache.get(it.id)
                if hit is None or hit[0] != it.rev:
                    hit = (it.rev, _find_text(it, self.cwd))
                fresh[it.id] = hit
                if hit[1]:
                    out.append((i, it.id, hit[1]))
        self._find_cache = fresh
        return out


def _find_text(it: Item, cwd: str) -> str:
    k = it.kind
    if k in ("user", "text", "command-output", "thinking", "notice"):
        return it.text
    if k == "tool":
        m = tool_meta(it.name, it.input, cwd)
        return f"{m.verb} {m.target}".strip()
    return ""


# ---------------------------------------------------------------- scenes per session

_scenes: OrderedDict[str, TranscriptScene] = OrderedDict()
SCENES_KEPT = 8


def scene_for(sid: str, cwd: str) -> TranscriptScene:
    sc = _scenes.get(sid)
    if sc is None or sc.cwd != cwd:
        sc = TranscriptScene(sid, cwd)
        _scenes[sid] = sc
    _scenes.move_to_end(sid)
    while len(_scenes) > SCENES_KEPT:
        _scenes.popitem(last=False)
    return sc


def forget_scenes() -> None:
    _scenes.clear()


# ---------------------------------------------------------------- the widget


class Jump(QPushButton):
    """The "Latest" pill over the bottom of the view while the reader is up in the history."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(" Latest", parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Latest")
        self.setFixedHeight(30)
        self.setIconSize(QSize(14, 14))
        self._paint()
        bind(theme.theme.changed, self, lambda _t: self._paint())

    def _paint(self) -> None:
        t = theme.tokens()
        self.setIcon(icons.icon("arrow-down", t.text, 14))
        self.setStyleSheet(
            f"QPushButton {{ background: {t.raised}; color: {t.text}; border: 1px solid {theme.blend(t.hair_2, t.raised).name()}; border-radius: 15px; "
            f"padding: 0 12px 0 10px; font-size: {int(theme.FS_SMALL + 0.5)}px; font-weight: 550; }} QPushButton:hover {{ background: {t.raised_2}; }}"
        )


class Transcript(QWidget):
    """The conversation of one session."""

    find_requested = Signal()

    def __init__(self, session_id: str, cwd: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.sid = session_id
        self.cwd = cwd
        self.scene = scene_for(session_id, cwd)
        self.view = CanvasView(self.scene, self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.view)
        self.jump = Jump(self)
        self.jump.hide()
        self.jump.clicked.connect(self.scroll_to_bottom)
        self.find_bar: Optional[QWidget] = None
        self._scrolled_cb: Optional[Callable[[bool], None]] = None
        self._empty_widget: Optional[QWidget] = None
        self._placed = False
        self.view.on_latest = self._on_latest
        self.view.on_user_scrolled = self._remember
        self.view.scrolled.connect(lambda on: self._scrolled_cb(on) if self._scrolled_cb else None)
        for o in list(self.scene.orphans.values()):
            self.view.register_island(o)
        for blk in self.scene.root.walk():
            if isinstance(blk, IslandBlock):
                self.view.register_island(blk)
        bind(store.rt_changed, self, self._on_rt)
        bind(store.sent, self, lambda sid: sid == self.sid and self.scroll_to_bottom())
        bind(store.ui_changed, self, lambda _u: self._on_ui())
        bind(theme.theme.changed, self, lambda _t: self.view.on_theme())
        self._sync()
        QTimer.singleShot(0, self._initial_place)

    # ---- public (the API session_view.py and the find bar use)
    def viewport(self) -> QWidget:
        return self.view.viewport()

    def verticalScrollBar(self):  # noqa: N802
        return self.view.verticalScrollBar()

    def set_empty(self, widget: QWidget) -> None:
        self._empty_widget = widget
        if self.scene.empty is None or self.scene.empty._w is not widget:
            if self.scene.empty is not None:
                self.scene.empty.widget = None  # the old view's card went with it
            self.scene.empty = EmptyIsland(widget)
            self.view.register_island(self.scene.empty)
        self._sync()

    def set_scrolled_callback(self, fn: Optional[Callable[[bool], None]]) -> None:
        self._scrolled_cb = fn

    def units(self) -> List[Unit]:
        return self.scene.units

    def find_entries(self) -> List[Tuple[int, str, str]]:
        return self.scene.find_entries()

    def first_visible_unit(self) -> int:
        key = self.view.first_visible_key()
        if not key:
            return 0
        blk = self.scene.block(key)
        while blk is not None and blk.parent is not None and blk.parent is not self.scene.root:
            blk = blk.parent
        if blk is None:
            return 0
        for i, u in enumerate(self.scene.units):
            if unit_group_key(u) == blk.key:
                return i
        return 0

    def scroll_to_bottom(self) -> None:
        self.view.scroll_to_bottom()
        self.scene.saved = None
        self.scene.saved_following = True

    def reveal_item(self, item_id: str, needle: str = "", k: int = -1) -> Optional[Block]:
        """Open the folds around an item and bring it into view (with a needle: the paragraph holding
        its k-th match); the block, or None when it is not here."""
        blk = self.locate(item_id, needle, k)
        if blk is None:
            return None
        self.view.scroll_to_block(blk)
        return blk

    def locate(self, item_id: str, needle: str = "", k: int = -1) -> Optional[Block]:
        """The block of an item, built and measured, its folds opened; for a text with a needle, the
        paragraph that holds the k-th match."""
        blk = self.scene.block_for_item(item_id)
        if blk is None:
            return None
        v = self.view
        v.begin()
        try:
            v.open_path(blk)
            if not blk.warm or blk.h_width != v.col_w:
                v._exact(blk)
            if needle and k >= 0 and isinstance(blk, Group) and blk.children:
                from .paint import find_ranges

                for c in blk.children:
                    if not c.warm or c.h_width != v.col_w:
                        v._exact(c)
                    doc = getattr(c, "doc", None)
                    n = len(find_ranges(doc, needle)) if doc is not None else 0
                    if k < n:
                        blk = c
                        break
                    k -= n
        finally:
            v.end()
        return blk

    def block_rect(self, blk: Block):
        return self.view.block_rect(blk)

    def set_needle(self, needle: str, current_item_id: str = "", k: int = -1) -> None:
        """Light every match of ``needle`` on screen, the current one (the k-th in its item) in full."""
        self.view.needle = needle
        blk = self.locate(current_item_id, needle, k) if current_item_id else None
        self.view.needle_key = blk.key if blk is not None else ""
        self.view.needle_index = self._needle_index(blk, current_item_id, needle, k) if blk is not None else -1
        self.view.viewport().update()

    def _needle_index(self, blk: Block, item_id: str, needle: str, k: int) -> int:
        """The match's index within the block that paints it (a text's k-th match counted across
        its paragraphs becomes the j-th of one paragraph)."""
        from .paint import find_ranges

        g = self.scene.block_for_item(item_id)
        if g is blk or not isinstance(g, Group):
            return k
        for c in g.children:
            if c is blk:
                return k
            doc = getattr(c, "doc", None)
            k -= len(find_ranges(doc, needle)) if doc is not None else 0
        return k

    def open_find(self) -> None:
        store.set_find_open(True)
        self.find_requested.emit()
        if self.find_bar is None:
            from .find_bar import FindBar

            bar = FindBar(self, on_close=self._close_find, parent=self)
            self.find_bar = bar
            self._place_find()
            bar.show()
            bar.raise_()
        self.find_bar.setFocus()

    def _close_find(self) -> None:
        bar = self.find_bar
        self.find_bar = None
        if bar is not None:
            bar.hide()
            bar.deleteLater()
        self.set_needle("")
        store.set_find_open(False)
        self.view.setFocus()

    # ---- the model
    def _on_rt(self, sid: str) -> None:
        if sid == self.sid:
            self._sync()

    def _on_ui(self) -> None:
        if self.scene.ctx is not None and store.ui.view != self.scene.ctx.view:
            self._sync()

    def _sync(self) -> None:
        r = store.rt.get(self.sid)
        m = store.get_model(self.sid)
        v = self.view
        v.begin()
        try:
            changed = self.scene.sync(m, r, store.ui.view)
            for blk in changed:
                if isinstance(blk, IslandBlock):
                    v.register_island(blk)
                if blk.scene is not self.scene:
                    continue
                if isinstance(blk, Group):
                    v.estimate(blk)  # its height is its children's; the leaves that show get estimates
                elif blk.warm:
                    v._exact(blk)
                else:
                    v.estimate(blk)
        finally:
            v.end()
        if r is not None and r.loaded and not self._placed:
            QTimer.singleShot(0, self._initial_place)

    def _initial_place(self) -> None:
        r = store.rt.get(self.sid)
        if not (r and r.loaded) or self._placed:
            return
        self._placed = True
        saved = self.scene.saved
        if saved is not None and not self.scene.saved_following and self.view.restore(saved):
            self._on_latest(True)
            return
        self.view.follow.following = True
        self.view.scroll_to_bottom(animate=False)

    def _remember(self) -> None:
        v = self.view
        if v.follow.following:
            self.scene.saved = None
            self.scene.saved_following = True
        else:
            self.scene.saved = v.place()
            self.scene.saved_following = False

    # ---- overlays
    def _on_latest(self, show: bool) -> None:
        if show:
            self._place_jump()
            self.jump.show()
            self.jump.raise_()
        else:
            self.jump.hide()

    def _place_jump(self) -> None:
        vp = self.view.viewport()
        self.jump.adjustSize()
        self.jump.move((vp.width() - self.jump.width()) // 2, vp.height() - 18 - self.jump.height())

    def _place_find(self) -> None:
        bar = self.find_bar
        if bar is None:
            return
        vp = self.view.viewport()
        bar.setFixedWidth(min(380, max(120, vp.width() - 36)))
        bar.move(vp.width() - 18 - bar.width(), 10)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._place_jump()
        self._place_find()

    def hideEvent(self, e) -> None:  # noqa: N802
        self._remember()
        super().hideEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        self.view.keyPressEvent(e)


_ = (GRADE_EXACT, time)
