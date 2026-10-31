"""The viewport: paints the scene, scrolls its pixels, keeps the reader's place, and turns the
pointer and the keyboard into targets on blocks.

One ``QAbstractScrollArea``; nothing inside it but the islands. Every change to the scene goes
through ``begin()`` / ``end()``: the anchor is read before, the correction and the invalidation are
applied after, in the same event-loop turn. Painting descends the scene to the exposed blocks and
blits cached pixmaps for the finished ones. The prefetch queue warms blocks near the viewport and
cools the ones far away between frames, under a time budget.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPixmap, QTextCursor
from PySide6.QtWidgets import QAbstractScrollArea, QToolTip, QWidget

from .. import theme
from ..lib.fmt import resolve_path
from ..store import store
from . import a11y
from .anchor import Follow
from .blocks.island import IslandBlock
from .clock import Tween, clock
from .paint import FULL, Measurer, PaintState, rounded
from .scene import GRADE_EXACT, GRADE_FAST, Block, Group, Scene, Target

PAD_TOP = 22
PAD_BOTTOM = 44
NEAR_SCREENS = 3  # warm within this many viewports
FAR_SCREENS = 6  # cool beyond this many
PREFETCH_BUDGET = 0.004
FLING_BUDGET = 0.002
OPEN_BUDGET = 0.014  # measuring a fold's children before it opens, so the reveal runs on true heights
SCROLL_BUDGET = 0.008  # exact measuring on a person's scroll or a resize step; the rest follows in the drain
WARM_MAX = 600  # warm blocks kept before the far ones are cooled regardless of movement
TOOLTIP_MS = 600
TRIPLE_CLICK_MS = 0.4  # a press this soon after a double-click selects the block
FOCUS_KINDS = ("head", "button", "link", "toggle", "more", "chev", "image")  # what Tab lands on

_measurer: Optional[Measurer] = None


def measurer() -> Measurer:
    """One measurer per process: fonts, metrics and caches are shared by every canvas."""
    global _measurer
    if _measurer is None:
        _measurer = Measurer()
    return _measurer


def column_width(pane: int) -> int:
    """``--col: clamp(640px, min(61.8%, 100% - 48px), 1080px)``: the reading column of a pane."""
    return int(max(640, min(pane * 0.618, pane - 48, 1080)))


class Selection:
    __slots__ = ("a_key", "a_pos", "f_key", "f_pos")

    def __init__(self, a_key: str, a_pos: Optional[int]) -> None:
        self.a_key, self.a_pos = a_key, a_pos
        self.f_key, self.f_pos = a_key, a_pos

    @property
    def empty(self) -> bool:
        """Nothing selected: the same point twice. The same block with no positions is the whole block."""
        return self.a_key == self.f_key and self.a_pos == self.f_pos and self.a_pos is not None


class FollowEase:
    """Eases the scroll value towards the live end on the clock. Retargeting never restarts it: the
    value approaches whatever the target is now, by a fixed share of the distance per unit of time."""

    TAU = 0.06  # seconds to close 63 % of the distance

    def __init__(self, set_value: Callable[[int], None], get_value: Callable[[], int]) -> None:
        self._set = set_value
        self._get = get_value
        self.target: Optional[int] = None
        self._last = 0.0
        self.running = False

    def to(self, target: int) -> None:
        self.target = target
        if not self.running:
            self.running = True
            self._last = time.monotonic()
            clock.hold_fast()
            clock.subscribe(self)

    def stop(self) -> None:
        if self.running:
            self.running = False
            clock.release_fast()
            clock.unsubscribe(self)
        self.target = None

    def tick(self, now: float) -> None:
        if self.target is None:
            self.stop()
            return
        cur = self._get()
        d = self.target - cur
        if abs(d) < 1:
            self._set(self.target)
            self.stop()
            return
        dt = max(0.0, now - self._last)
        self._last = now
        k = 1.0 - math.exp(-dt / self.TAU)
        step = d * k
        if 0 < abs(step) < 1:
            step = 1 if d > 0 else -1
        self._set(int(round(cur + step)))


class CanvasView(QAbstractScrollArea):
    scrolled = Signal(bool)  # away from the top
    hovered_block = Signal(object)  # the block under the pointer (or None)

    def __init__(self, scene: Scene, parent: Optional[QWidget] = None, column: str = "reading") -> None:
        super().__init__(parent)
        self.scene = scene
        self.m = measurer()
        #: 'reading': the centred golden-section column of the conversation; 'fill': the whole width
        #: less the side gutters (a list in a panel)
        self.column = column
        self.gutter = 0 if column == "reading" else 8
        self.pad_top = PAD_TOP if column == "reading" else 6
        self.pad_bottom = PAD_BOTTOM if column == "reading" else 12
        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        vp = self.viewport()
        vp.setAccessibleName("Conversation" if column == "reading" else "List")
        a11y.install()
        vp.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        vp.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        vp.setAutoFillBackground(False)
        vp.setMouseTracking(True)
        self.verticalScrollBar().setSingleStep(20)

        self.col_x = 0
        self.col_w = 640
        self.follow = Follow()
        self._own = False  # the value is being set by the view, not the person
        self._silent = False  # ...and the picture is rigid with the anchor: no blit, no repaint
        self._committing = False
        self._depth = 0
        self._anchor: Optional[Tuple[str, int]] = None
        self._following = False
        self._max_before = 0
        self._upgrade_queue: List[Block] = []
        self._scanned_rev = -1
        self._drained: Any = None  # the state the last complete drain saw: nothing to do until it changes
        self._cooled_at = 0
        self._deadline: Optional[float] = None  # when set, the commit measures exactly only until then
        self._last_value = 0
        self._last_user_scroll = 0.0
        self._grew = False
        self.frame = 0
        self.cold_paints = 0  # placeholders painted where a block was not warm yet (a bench counter)
        self.hover: Optional[Tuple[Block, Target]] = None
        self.pressed: Optional[Tuple[Block, Target]] = None
        self.focus: Optional[Tuple[str, Target]] = None
        self.selection: Optional[Selection] = None
        self._selecting = False
        self._dbl_at = 0.0  # when the last double-click happened (a third click selects the block)
        self._dbl_pos = QPoint()
        self.needle = ""
        self.needle_key = ""
        self.needle_index = -1
        self._warm: Set[Block] = set()
        self._live: List[Block] = []
        self._islands: List[IslandBlock] = []
        self._tweens: Dict[str, Tween] = {}
        self.scroll_tween = Tween(self._scroll_frame)
        self.follow_ease = FollowEase(self._set_value, lambda: self.verticalScrollBar().value())
        self._prefetch = QTimer(self)
        self._prefetch.setSingleShot(True)
        self._prefetch.setInterval(0)
        self._prefetch.timeout.connect(self._drain)
        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.setInterval(TOOLTIP_MS)
        self._tip_timer.timeout.connect(self._show_tip)
        self._tip_pos = QPoint()
        self.on_latest: Optional[Callable[[bool], None]] = None  # show / hide the "Latest" pill
        self.on_user_scrolled: Optional[Callable[[], None]] = None
        vb = self.verticalScrollBar()
        vb.valueChanged.connect(self._on_value)
        self._relayout_width()

    # ---------------------------------------------------------------- geometry
    def scene_y(self, viewport_y: int) -> int:
        return viewport_y - self.pad_top + self.verticalScrollBar().value()

    def view_y(self, scene_y: int) -> int:
        return scene_y + self.pad_top - self.verticalScrollBar().value()

    def block_rect(self, b: Block) -> QRect:
        """The block's rect in viewport coordinates."""
        return QRect(self.col_x + b.indent, self.view_y(b.y()), self.col_w - b.indent, b.h)

    def _relayout_width(self) -> None:
        vw = self.viewport().width()
        if self.column == "fill":
            col = max(120, vw - 2 * self.gutter)
            self.col_x = self.gutter
        else:
            col = column_width(max(vw, 200))
            col = min(col, max(200, vw - 48))
            self.col_x = (vw - col) // 2
        self.col_w = col
        if self.scene.width != col:
            self.scene.width = col
            self._deadline = time.perf_counter() + SCROLL_BUDGET
            self.begin()
            try:
                self._remeasure_visible(force=True)
            finally:
                self.end()
                self._deadline = None
            self._prefetch.start()

    def _update_range(self) -> None:
        """The scroll range from the scene's height. A value the new range clamps is the view's own
        doing, not the person's: it must not change whether the view follows."""
        vb = self.verticalScrollBar()
        vh = self.viewport().height()
        total = self.pad_top + self.scene.total + self.pad_bottom
        was = self._own
        self._own = True
        try:
            vb.setPageStep(max(1, vh))
            vb.setRange(0, max(0, total - vh))
        finally:
            self._own = was

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self.begin()
        try:
            self._relayout_width()
            self._update_range()
        finally:
            self.end()
        self._place_islands()
        self.viewport().update()

    def viewportEvent(self, e) -> bool:  # noqa: N802
        if e.type() == QEvent.Type.Leave:
            self._set_hover(None)
        elif e.type() == QEvent.Type.ContextMenu:
            self._context_menu(e)
            return True
        return super().viewportEvent(e)

    def focusInEvent(self, e) -> None:  # noqa: N802
        super().focusInEvent(e)
        self._update_focus_rect()

    def focusOutEvent(self, e) -> None:  # noqa: N802
        super().focusOutEvent(e)
        self._update_focus_rect()

    def focusNextPrevChild(self, next_: bool) -> bool:  # noqa: N802
        """Tab moves over the targets on screen before it leaves the view."""
        if self.hasFocus() and self._focus_step(1 if next_ else -1):
            return True
        self._set_focus(None)
        return super().focusNextPrevChild(next_)

    # ---------------------------------------------------------------- the commit
    def begin(self) -> None:
        """Before a mutation: read the anchor from the layout as it is."""
        if self._depth == 0:
            vb = self.verticalScrollBar()
            self._max_before = vb.maximum()
            self._following = self.follow.following
            self._anchor = self.scene.pick_anchor(self.scene_y(0), self.viewport().height())
        self._depth += 1

    def end(self) -> None:
        """After the mutation: hold the anchor, then repaint only what moved relative to it."""
        self._depth -= 1
        if self._depth > 0 or self._committing:
            return
        self._committing = True
        try:
            self._commit()
        finally:
            self._committing = False
            self._anchor = None

    def _hold(self, anchor: Optional[Tuple[str, int]]) -> Optional[int]:
        """Put the scroll where the anchor sits where it was, without moving a pixel: the picture is
        rigid with the anchor. Returns the value the anchor wants (it may be beyond the range)."""
        self._silent = True
        try:
            self._update_range()
            if anchor is None:
                return None
            y = self.scene.resolve_anchor(anchor)
            if y is None:
                return None
            want = y + self.pad_top
            self._set_value(want)
            return want
        finally:
            self._silent = False

    def _commit(self) -> None:
        vb = self.verticalScrollBar()
        vp = self.viewport()
        before = vb.value()
        anchor = self._anchor
        # the layout changed under the anchor: put it back first, then measure what shows there
        # exactly (which moves things again), and hold it once more; twice over for the rare chain
        want = self._hold(anchor)
        for _ in range(3):
            if not self._remeasure_visible():
                break
            want = self._hold(anchor)
        dirty, rects = self.scene.take_dirty()
        # the anchor is held, so the picture moved only from the first change that is not above it
        a_y = None
        if anchor is not None and want is not None and vb.value() == want:
            a_b = self.scene.block(anchor[0])
            a_y = a_b.y() if a_b is not None else None
        full = False
        from_y: Optional[int] = None
        if a_y is None:
            full = bool(dirty) or vb.value() != before
        else:
            for x, off in dirty:
                if x.scene is not self.scene:
                    continue
                xy = x.y()
                if xy + x.h <= a_y:
                    continue  # above the anchor: it moved with it, and so did the scroll
                d = xy + off
                from_y = d if from_y is None else min(from_y, d)
        # following: ease to the end (this scroll blits, as a person's would)
        if vb.maximum() > self._max_before:
            self._grew = True
        if self._following:
            target = vb.maximum()
            if target != vb.value():
                if store.reduced_motion or abs(target - vb.value()) < 2 or not self.isVisible():
                    self._set_value(target)
                else:
                    self.follow_ease.to(target)
            else:
                self.follow_ease.stop()
        if self.on_latest is not None:
            self.on_latest(self.follow.shows_latest(vb.value(), vb.maximum(), vp.height(), self._grew))
        self._place_islands()
        if full:
            vp.update()
        elif from_y is not None:
            top = self.view_y(from_y)
            if top < vp.height():
                vp.update(QRect(0, max(0, top), vp.width(), vp.height() - max(0, top)))
        for x, y, w, h in rects:
            vp.update(QRect(self.col_x + x, self.view_y(y), w, h))
        self._prefetch.start()

    def mutate(self, fn: Callable[[], Any]) -> Any:
        self.begin()
        try:
            return fn()
        finally:
            self.end()

    def remeasure(self, b: Block) -> None:
        """A block changed its own content (a toggle inside it): measure it again, keep the anchor."""
        self.begin()
        try:
            self._exact(b)
            self.scene.note_block(b)
        finally:
            self.end()

    # ---------------------------------------------------------------- scrolling
    def _set_value(self, v: int) -> None:
        vb = self.verticalScrollBar()
        v = max(0, min(vb.maximum(), int(v)))
        if v == vb.value():
            return
        self._own = True
        try:
            vb.setValue(v)
        finally:
            self._own = False

    def _scroll_frame(self, v: float) -> None:
        self._set_value(int(round(v)))

    def _on_value(self, v: int) -> None:
        prev = self._last_value
        self._last_value = v
        vb = self.verticalScrollBar()
        if not self._own:
            self.scroll_tween.stop()
            self.follow_ease.stop()
            self.follow.on_user_scroll(v, prev, vb.maximum())
            self._last_user_scroll = time.monotonic()
            if self.follow.following:
                self._grew = False
            if self.on_user_scrolled is not None:
                self.on_user_scrolled()
        elif vb.maximum() - v <= 0 and self.follow.following:
            self._grew = False
        self.scrolled.emit(v > 4)
        if self._depth == 0 and not self._committing:
            # a commit at the new place: what came into view is measured, the pill and islands follow
            # (a person's scroll gets a deadline: the frame must not wait for a screen of documents)
            self._deadline = None if self._own else time.perf_counter() + SCROLL_BUDGET
            try:
                self.begin()
                self.end()
            finally:
                self._deadline = None
        if not self._silent:
            self._rehover()

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802
        if not self._silent:
            self.viewport().scroll(0, dy)

    def scroll_to_bottom(self, animate: bool = True) -> None:
        self.follow.on_jump_to_end()
        self._grew = False
        vb = self.verticalScrollBar()
        if animate and not store.reduced_motion and self.isVisible() and abs(vb.maximum() - vb.value()) > 2:
            self.follow_ease.to(vb.maximum())
        else:
            self.follow_ease.stop()
            self._set_value(vb.maximum())
        if self.on_latest is not None:
            self.on_latest(False)

    def at_bottom(self) -> bool:
        vb = self.verticalScrollBar()
        return vb.maximum() - vb.value() <= 60

    def scroll_to_block(self, b: Block, fraction: float = 0.38, only_if_hidden: bool = True) -> None:
        """Bring a block to ``fraction`` of the viewport height unless it is comfortably visible."""
        vb = self.verticalScrollBar()
        vh = self.viewport().height()
        r = self.block_rect(b)
        if only_if_hidden and 56 <= r.top() and r.bottom() <= vh - 24:
            return
        self.follow.following = False
        self.scroll_tween.stop()
        self.follow_ease.stop()
        self._set_value(vb.value() + r.top() - int(vh * fraction))

    def wheelEvent(self, e) -> None:  # noqa: N802
        self._last_user_scroll = time.monotonic()
        super().wheelEvent(e)

    def keyPressEvent(self, e) -> None:  # noqa: N802
        k = e.key()
        vb = self.verticalScrollBar()
        vh = self.viewport().height()
        if k == Qt.Key.Key_End or (k == Qt.Key.Key_Down and e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.scroll_to_bottom()
        elif k == Qt.Key.Key_Home or (k == Qt.Key.Key_Up and e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.follow.following = False
            vb.setValue(0)
        elif k == Qt.Key.Key_PageDown:
            vb.setValue(vb.value() + vh - 40)
        elif k == Qt.Key.Key_PageUp:
            vb.setValue(vb.value() - vh + 40)
        elif k == Qt.Key.Key_Down:
            vb.setValue(vb.value() + 40)
        elif k == Qt.Key.Key_Up:
            vb.setValue(vb.value() - 40)
        elif k == Qt.Key.Key_Escape and (self.selection is not None or self.focus is not None):
            self.clear_selection()
            self._set_focus(None)
        elif e.matches(QKeySequence.StandardKey.Copy) or (
            k == Qt.Key.Key_C and e.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.copy_selection()
        elif e.matches(QKeySequence.StandardKey.SelectAll):
            self.select_all()
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.focus is not None:
            self.activate_focus()
        else:
            super().keyPressEvent(e)
            return
        e.accept()

    # ---------------------------------------------------------------- warming
    def _exact(self, b: Block) -> int:
        """Measure a block exactly at the width it is painted at (the column less the block's indent);
        returns the height delta. ``h_width`` keeps the column width, which is what a width change
        compares against."""
        if isinstance(b, Group) and not b.shaped:
            self.shape(b)
        h = b.measure(self.col_w - b.indent, self.m)
        self._warm.add(b)
        b.warm = True
        return b.set_height(h, GRADE_EXACT, self.col_w)

    def estimate(self, b: Block) -> None:
        """A cold block's first height: its estimate, so it takes room before it is ever measured. For
        a group, every leaf that shows once it is open and has no height yet gets one."""
        if isinstance(b, Group) and b.children:
            for c in self._walk_open(b):
                if c.h == 0 and c.grade == 0 and not c.warm:
                    c.set_height(c.estimate(self.col_w - c.indent, self.m), 0, self.col_w)
            return
        b.warm = False
        b.set_height(b.estimate(self.col_w - b.indent, self.m), 0, self.col_w)

    def shape(self, g: Group) -> None:
        """Build a group's children the first time they are needed: they take their estimates, and
        the islands among them are hosted here."""
        if g.shaped:
            return
        g.ensure_shape(self.scene)
        self.estimate(g)
        for c in g.walk():
            if isinstance(c, IslandBlock):
                self.register_island(c)

    def _cold_visible(self, y0: int, y1: int) -> List[Block]:
        out: List[Block] = []
        for b, _y in self.scene.leaves_between(y0, y1):
            if not b.warm or b.h_width != self.col_w or b.grade < GRADE_EXACT:
                out.append(b)
        return out

    def _remeasure_visible(self, force: bool = False) -> bool:
        """Measure every cold block in the viewport exactly (until the deadline, when one is set: the
        rest paint as placeholders and the drain finishes them); returns whether anything was measured."""
        y0 = self.scene_y(0)
        y1 = y0 + self.viewport().height()
        did = False
        cold = self._cold_visible(y0, y1)
        deadline = self._deadline
        for b in cold:
            if isinstance(b, Group) and b.children and b.shaped and b.h_width == self.col_w and not force:
                continue
            if deadline is not None and did and time.perf_counter() > deadline:
                self._prefetch.start()
                break
            self._exact(b)
            did = True
        return did

    def _drain(self) -> None:
        """Between frames, in one commit: warm the blocks near the viewport, upgrade far estimates,
        cool the far warm. The anchor holds through it all, so nothing on screen moves."""
        if not self.isVisible() and not self.scene.root.children:
            return
        vh = max(1, self.viewport().height())
        y0 = self.scene_y(0)
        state = (y0, vh, self.col_w, self.scene.structure_rev, self.scene.height_changes, self.m.theme_name)
        if state == self._drained:
            return  # the last drain finished with nothing left, and nothing has changed since
        t0 = time.perf_counter()
        fling = time.monotonic() - self._last_user_scroll < 0.08
        budget = FLING_BUDGET if fling else PREFETCH_BUDGET
        y1 = y0 + vh
        near0, near1 = y0 - NEAR_SCREENS * vh, y1 + NEAR_SCREENS * vh
        more = False
        self.begin()
        try:
            # nearest first: alternate rings below and above the viewport
            below = [b for b, _y in self.scene.leaves_between(y1, near1) if not b.warm or b.h_width != self.col_w]
            above = [b for b, _y in self.scene.leaves_between(near0, y0) if not b.warm or b.h_width != self.col_w]
            above.reverse()
            for i in range(max(len(below), len(above))):
                for ring in (below, above):
                    if i < len(ring):
                        b = ring[i]
                        if isinstance(b, Group) and b.children and b.shaped and b.h_width == self.col_w:
                            continue
                        self._exact(b)
                if time.perf_counter() - t0 > budget:
                    more = True
                    break
            # then the estimates of cold blocks everywhere, a slice at a time
            if not more:
                if self.scene.fresh:
                    # what came since the last look, the top of the history first (popped from the end)
                    fresh = [
                        b
                        for b in self.scene.fresh
                        if b.grade == 0 and not b.warm and not (isinstance(b, Group) and b.children)
                    ]
                    self.scene.fresh = []
                    fresh.reverse()
                    self._upgrade_queue = fresh + self._upgrade_queue
                q = self._upgrade_queue
                while q:
                    b = q.pop()
                    if b.scene is not self.scene or b.grade != 0 or b.warm:
                        continue
                    b.set_height(b.fast_measure(self.col_w - b.indent, self.m), GRADE_FAST, self.col_w)
                    if time.perf_counter() - t0 > budget * 2:
                        more = bool(q)
                        break
        finally:
            self.end()
        # cool what is far, once the view has moved a screen or the warm set has grown
        if abs(y0 - self._cooled_at) >= vh or len(self._warm) > WARM_MAX:
            self._cooled_at = y0
            far0, far1 = y0 - FAR_SCREENS * vh, y1 + FAR_SCREENS * vh
            for b in list(self._warm):
                if b.scene is not self.scene:
                    self._warm.discard(b)
                    continue
                by = b.y()
                if by + b.h < far0 or by > far1:
                    b.cool()
                    self.m.pixmaps.drop_block(b.key)
                    self._warm.discard(b)
        if more:
            self._prefetch.start()
        else:
            self._drained = (
                self.scene_y(0),
                vh,
                self.col_w,
                self.scene.structure_rev,
                self.scene.height_changes,
                self.m.theme_name,
            )

    # ---------------------------------------------------------------- painting
    def paintEvent(self, e) -> None:  # noqa: N802
        self.frame += 1
        self.scene.frame = self.frame
        vp = self.viewport()
        t = self.m.tokens
        p = QPainter(vp)
        p.fillRect(e.rect(), QColor(t.canvas))
        st = PaintState(self.m)
        st.now = time.monotonic()
        st.pressed = self.pressed[1] if self.pressed is not None else None
        st.needle = self.needle
        scroll = self.verticalScrollBar().value()
        live: List[Block] = []
        y0 = e.rect().top() - self.pad_top + scroll
        y1 = e.rect().bottom() + 1 - self.pad_top + scroll
        hover_b = self.hover[0] if self.hover is not None else None
        cold_seen = False
        for b, by in self.scene.leaves_between(y0, y1):
            if b.h <= 0 or isinstance(b, IslandBlock):
                continue
            vy = by + self.pad_top - scroll
            x = self.col_x + b.indent
            w = self.col_w - b.indent
            rect = QRect(x, vy, w, b.h)
            if not b.warm and not isinstance(b, Group):
                # a cold block: faint bars where its lines will be; the prefetch fills it in
                cold_seen = True
                self.cold_paints += 1
                self._paint_placeholder(p, rect, t)
                continue
            if isinstance(b, Group):
                # a group paints its head and chrome; its children are leaves of their own
                clip = QRect(x, vy, w, b.h).intersected(e.rect())
                bst = st.for_block(self.hover[1] if hover_b is b else None, self._focus_for(b), None, clip)
                self._paint_block(p, b, rect, bst)
            else:
                sel = self._selection_for(b)
                is_hover = hover_b is b
                clip = rect.intersected(e.rect())
                if (
                    b.static
                    and sel is None
                    and not is_hover
                    and not self.needle
                    and b.h <= 2 * vp.height()
                    and self._focus_for(b) is None
                ):
                    self._paint_cached(p, b, rect, st)
                else:
                    bst = st.for_block(self.hover[1] if is_hover else None, self._focus_for(b), sel, clip)
                    if b.key == self.needle_key:
                        bst.needle_current = self.needle_index
                    self._paint_block(p, b, rect, bst)
            if b.live():
                live.append(b)
        p.end()
        self._live = live
        if live:
            clock.subscribe(self)
        else:
            clock.unsubscribe(self)
        if cold_seen:
            self._prefetch.start()

    @staticmethod
    def _paint_placeholder(p: QPainter, rect: QRect, t) -> None:
        c = QColor(theme.blend(t.fill_1, t.canvas))
        c.setAlphaF(0.7)
        y = rect.y() + 6
        while y < rect.bottom() - 8:
            p.fillRect(
                QRect(
                    rect.x() + 8,
                    y,
                    int(rect.width() * 0.38),
                    8,
                ),
                c,
            )
            y += 48

    def _paint_block(self, p: QPainter, b: Block, rect: QRect, st: PaintState) -> None:
        p.save()
        p.setClipRect(st.clip if st.clip is not None else rect)
        try:
            b.paint(p, rect.x(), rect.y(), rect.width(), st)
            if st.focus is not None and self.hasFocus():
                self._paint_focus_ring(p, rect, st)
        except Exception as ex:  # noqa: BLE001 - one row's painter must not blank the frame
            if not getattr(b, "_paint_failed", False):
                print(f"[canvas] {b.key} could not paint: {ex!r}")
                try:
                    b._paint_failed = True  # type: ignore[attr-defined]
                except AttributeError:
                    pass
            p.setPen(QColor(st.t.err))
            p.drawText(
                rect.adjusted(4, 0, 0, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                "could not draw this row",
            )
        p.restore()

    def _paint_focus_ring(self, p: QPainter, rect: QRect, st: PaintState) -> None:
        """The keyboard focus: a ring around the target (``:focus-visible``), outside its box."""
        rx, ry, rw, rh = st.focus.rect  # type: ignore[union-attr]
        if rw <= 0 or rh <= 0:
            rw, rh = rect.width(), min(rect.height(), 28)
            rx, ry = 0, 0
        ring = QRect(rect.x() + rx - 3, rect.y() + ry - 3, rw + 6, rh + 6)
        p.setClipRect(ring.adjusted(-2, -2, 2, 2))
        rounded(p, ring, None, QColor(st.t.accent), theme.R2, 1.5)

    def _paint_cached(self, p: QPainter, b: Block, rect: QRect, st: PaintState) -> None:
        dpr = self.viewport().devicePixelRatioF()
        key = (b.key, b.rev, b.sig, rect.width(), self.m.theme_name, dpr)
        pm = self.m.pixmaps.get(key)
        if pm is None:
            pm = QPixmap(int(rect.width() * dpr), int(max(1, rect.height()) * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(QColor(st.t.canvas))
            pp = QPainter(pm)
            bst = st.for_block(None, None, None, None)
            bst.caching = True
            try:
                b.paint(pp, 0, 0, rect.width(), bst)
            except Exception as ex:  # noqa: BLE001
                print(f"[canvas] {b.key} could not paint: {ex!r}")
            pp.end()
            self.m.pixmaps.put(key, pm)
        p.drawPixmap(rect.topLeft(), pm)

    def tick(self, now: float) -> None:
        """The clock: repaint the live blocks' rectangles."""
        vp = self.viewport()
        if not self._live:
            clock.unsubscribe(self)
            return
        for b in self._live:
            if b.scene is not self.scene:
                continue
            r = b.tick(now)
            if r is not None:
                br = self.block_rect(b)
                vp.update(QRect(br.x() + r[0], br.y() + r[1], min(r[2], br.width()), min(r[3], br.height())))
        if self.scroll_tween.running:
            pass

    # ---------------------------------------------------------------- islands
    def register_island(self, b: IslandBlock) -> None:
        b.host = self.viewport()
        if b not in self._islands:
            self._islands.append(b)

    def _place_islands(self) -> None:
        if not self._islands:
            return
        vh = self.viewport().height()
        for b in list(self._islands):
            if b.scene is not self.scene:
                b.drop_widget()
                self._islands.remove(b)
                continue
            if not b.alive:
                continue
            r = self.block_rect(b)
            visible = b.visible_in_tree() and r.bottom() >= -vh and r.top() <= 2 * vh
            b.place(r.x(), r.y(), r.width(), visible)

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
                    vp.update(self.block_rect(h[0]).adjusted(-8, 0, 8, 0))
                except RuntimeError:
                    pass
        cursor = hit[0].cursor_for(hit[1]) if hit is not None else None
        vp.setCursor(cursor if cursor is not None else Qt.CursorShape.ArrowCursor)
        self.hovered_block.emit(hit[0] if hit is not None else None)
        self._tip_timer.stop()
        QToolTip.hideText()
        if hit is not None and hit[0].tooltip_for(hit[1]):
            self._tip_timer.start()

    def _rehover(self) -> None:
        from PySide6.QtGui import QCursor

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

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        pos = e.position().toPoint()
        self._tip_pos = pos
        if self._selecting and self.selection is not None:
            self._extend_selection(pos)
            return
        self._set_hover(self.hit(pos))

    def mousePressEvent(self, e) -> None:  # noqa: N802
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

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
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

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
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
        from ..widgets.ui.menu import show_menu_at

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
        out: List[Tuple[Block, Target]] = []
        for b in self.visible_blocks():
            ts = [t for t in b.targets() if t.kind in FOCUS_KINDS]
            if not ts:
                t = b.hit(2, 2)
                if t is not None and t.kind in FOCUS_KINDS:
                    ts = [t]
            out.extend((b, t) for t in ts)
        return out

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

    def _update_focus_rect(self, *keys: Optional[Tuple[str, Target]]) -> None:
        vp = self.viewport()
        for f in (*keys, self.focus):
            if f is None:
                continue
            b = self.scene.block(f[0])
            if b is not None:
                vp.update(self.block_rect(b).adjusted(-8, -4, 8, 4))

    # ---------------------------------------------------------------- groups
    def toggle_group(self, g: Group, open_: bool, animate: bool = True) -> None:
        g.open = open_
        tw = self._tweens.get(g.key)
        if tw is not None:
            tw.stop()
        if open_ and not g.shaped:
            self.shape(g)
        if open_:
            # the children are measured before they show, so the reveal runs on their true heights;
            # a heavy body past the budget keeps its estimate and is measured as it comes into view
            self.begin()
            try:
                t0 = time.perf_counter()
                for c in self._walk_open(g):
                    if c.warm and c.grade >= GRADE_EXACT and c.h_width == self.col_w:
                        continue
                    if time.perf_counter() - t0 < OPEN_BUDGET:
                        self._exact(c)
                    elif c.grade < GRADE_FAST and not c.warm:
                        c.set_height(c.fast_measure(self.col_w - c.indent, self.m), GRADE_FAST, self.col_w)
            finally:
                self.end()
        if not animate or store.reduced_motion or not self.isVisible():
            self.begin()
            try:
                g.set_reveal(1.0 if open_ else 0.0)
            finally:
                self.end()
            return
        target = 1.0 if open_ else 0.0
        content = max(1, g.content_h())
        ms = min(420, 240 + content // 5) if open_ else min(260, 160 + content // 10)
        curve = theme.spring_soft() if open_ else theme.ease_exit()

        def frame(v: float, g=g) -> None:
            self.begin()
            try:
                g.set_reveal(v)
            finally:
                self.end()

        def done(g=g) -> None:
            self._tweens.pop(g.key, None)

        tw = Tween(frame, done)
        self._tweens[g.key] = tw
        tw.start(g.reveal, target, ms, curve)

    def _walk_open(self, g: Group) -> Any:
        """The leaves of a group that show once it is open: closed groups inside stay closed, and a
        group not built yet counts as one leaf (it carries its own estimate until it is needed)."""
        for c in g.children:
            if isinstance(c, Group) and c.children:
                if c.open:
                    yield from self._walk_open(c)
                continue
            yield c

    def open_path(self, b: Block) -> None:
        """Open every closed group above a block, without animation."""
        g = b.parent
        while g is not None:
            if g.parent is not None and not g.open:
                g.open = True
                g.set_reveal(1.0)
            g = g.parent

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
            s.f_key, s.f_pos = b.key, p1
            self.viewport().update()

    def clear_selection(self) -> None:
        if self.selection is not None:
            self.selection = None
            self.viewport().update()

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
        from urllib.parse import parse_qs, unquote, urlparse

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

    # ---------------------------------------------------------------- theme
    def on_theme(self) -> None:
        self.m.refresh()
        for b in list(self._warm):
            b.cool()
        self._warm.clear()
        self.begin()
        try:
            self._remeasure_visible(force=True)
        finally:
            self.end()
        self.viewport().update()
        self._prefetch.start()

    # ---------------------------------------------------------------- state for tests and the find bar
    def visible_blocks(self) -> List[Block]:
        y0 = self.scene_y(0)
        y1 = y0 + self.viewport().height()
        return [b for b, _y in self.scene.leaves_between(y0, y1) if b.h > 0]

    def first_visible_key(self) -> Optional[str]:
        a = self.scene.leaf_at(self.scene_y(0))
        return a[0].key if a else None

    def place(self) -> Optional[Tuple[str, int]]:
        """The reader's place: the block under the top edge and the edge's offset into it."""
        return self.scene.pick_anchor(self.scene_y(0))

    def restore(self, place: Tuple[str, int]) -> bool:
        """Scroll so a remembered place is back under the top edge; False when the block is gone."""
        y = self.scene.resolve_anchor(place)
        if y is None:
            return False
        self.follow.following = False
        self._grew = True  # what lies below the place is unseen: the "Latest" pill offers the way down
        self.scroll_tween.stop()
        self.follow_ease.stop()
        self._set_value(y + self.pad_top)
        return True
