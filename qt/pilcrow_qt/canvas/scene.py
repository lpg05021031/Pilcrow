"""Blocks, groups and the scene: the positions of everything, without widgets or layouts.

A ``Block`` is one row of a list: it knows its key, the revision it was synced from, a visual
signature, and its height in one of three grades (estimated, fast-measured, exact). A ``Group`` is a
block with children whose heights live in a Fenwick tree; its own height is its head plus
``reveal × content``, which is how a fold opens and closes with one number. The ``Scene`` is the
root group plus the index by key, the anchor logic, the mutation log the view paints from, and the
warm/cold bookkeeping. Measurement is done by the blocks themselves through a ``Measurer`` object the
paint layer provides (fonts, documents); this module never imports Qt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Tuple

from .fenwick import Fenwick

if TYPE_CHECKING:  # pragma: no cover
    from .paint import Measurer

GRADE_ESTIMATE, GRADE_FAST, GRADE_EXACT = 0, 1, 2
Path = Tuple[int, ...]


class Target:
    """What the pointer is on inside a block: a kind (``head``, ``link``, ``button``…), an id the
    block understands, and the rect in block coordinates."""

    __slots__ = ("kind", "id", "rect", "data")

    def __init__(self, kind: str, id_: str, rect: Tuple[int, int, int, int], data: Any = None) -> None:
        self.kind = kind
        self.id = id_
        self.rect = rect  # x, y, w, h in block coordinates
        self.data = data

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Target) and other.kind == self.kind and other.id == self.id

    def __hash__(self) -> int:
        return hash((self.kind, self.id))

    def __repr__(self) -> str:
        return f"Target({self.kind}:{self.id})"


class Block:
    """One row. Subclasses implement measuring, painting and hit testing; this class is the layout
    contract. Heights are integers; ``h`` is what the layout uses right now."""

    KIND = "block"
    #: block margins (transcript.css): the gap between two siblings is max(upper.MB, lower.MT)
    MT = 0
    MB = 0
    __slots__ = (
        "key",
        "rev",
        "sig",
        "parent",
        "index",
        "h",
        "gap",
        "grade",
        "h_width",
        "warm",
        "last_seen",
        "indent",
        "scene",
    )

    def __init__(self, key: str) -> None:
        self.key = key
        self.rev = -1
        self.sig: Any = None
        self.parent: Optional[Group] = None
        self.index = -1
        self.h = 0
        self.gap = 0  # the room above this block inside its parent
        self.grade = GRADE_ESTIMATE
        self.h_width = 0
        self.warm = False
        self.last_seen = 0
        self.indent = 0
        self.scene: Optional[Scene] = None

    def mt(self) -> int:
        return self.MT

    def mb(self) -> int:
        return self.MB

    @property
    def outer(self) -> int:
        """What this block takes in its parent: its gap and its height."""
        return self.gap + self.h

    # ---- heights
    def estimate(self, width: int, m: Measurer) -> int:
        return 0

    def fast_measure(self, width: int, m: Measurer) -> int:
        return self.estimate(width, m)

    def measure(self, width: int, m: Measurer) -> int:
        """Exact height at ``width``; builds what it needs (documents) and marks the block warm."""
        return self.estimate(width, m)

    def cool(self) -> None:
        """Drop documents and pixmaps; keep the height."""
        self.warm = False

    # ---- painting and interaction (block coordinates: origin at the block's top-left)
    def paint(self, p: Any, x: int, y: int, w: int, st: Any) -> None:
        """Draw the block with its top-left at (x, y) and width ``w`` (viewport coordinates)."""

    def hit(self, x: int, y: int) -> Optional[Target]:
        return None

    def targets(self) -> List[Target]:
        return []

    def activate(self, target: Target, view: Any) -> None:
        pass

    def cursor_for(self, target: Target) -> Optional[Any]:
        return None

    def tooltip_for(self, target: Target) -> str:
        return ""

    def text(self) -> str:
        return ""

    def text_pos_at(self, x: int, y: int) -> Optional[int]:
        return None

    def selected_text(self, a: Optional[int], b: Optional[int]) -> str:
        t = self.text()
        if a is None or b is None:
            return t
        return t[min(a, b) : max(a, b)]

    def live(self) -> bool:
        return False

    def tick(self, now: float) -> Optional[Tuple[int, int, int, int]]:
        return None

    @property
    def static(self) -> bool:
        """Can the painted block be cached as a pixmap? Live or streaming blocks say no."""
        return not self.live()

    # ---- tree
    def y(self) -> int:
        """Top of this block's own area in scene coordinates (below its gap)."""
        y = self.gap
        b: Block = self
        while b.parent is not None:
            y += b.parent.child_y(b.index)
            if b.parent.parent is not None:
                y += b.parent.gap
            b = b.parent
        return y

    def path(self) -> Path:
        out: List[int] = []
        b: Block = self
        while b.parent is not None:
            out.append(b.index)
            b = b.parent
        return tuple(reversed(out))

    def set_height(self, h: int, grade: int, width: int) -> int:
        """Record a new height; propagates to the parents. Returns the delta."""
        h = max(0, int(h))
        self.grade = grade
        self.h_width = width
        d = h - self.h
        if d:
            if self.scene is not None:
                self.scene.note_dirty(self, 0)
            self.h = h
            if self.parent is not None:
                self.parent.child_height_changed(self.index, d)
        return d

    def set_gap(self, gap: int) -> None:
        gap = max(0, int(gap))
        d = gap - self.gap
        if d:
            if self.scene is not None and self.parent is not None:
                self.scene.note_dirty(self, -self.gap)
            self.gap = gap
            if self.parent is not None:
                self.parent.child_height_changed(self.index, d)

    def visible_in_tree(self) -> bool:
        b: Block = self
        while b.parent is not None:
            if b.parent.reveal <= 0.0:
                return False
            b = b.parent
        return True

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.key} h={self.h} g={self.grade}>"


class Group(Block):
    """A block with children. ``reveal`` is 1 open, 0 closed; the children's heights sit in ``fen``."""

    KIND = "group"
    #: how far the children sit in from the group's left edge
    CHILD_INDENT = 0
    __slots__ = (
        "children",
        "fen",
        "open",
        "reveal",
        "head_h",
        "pad_top",
        "pad_bottom",
        "shaped",
        "by_key",
        "first_gap",
    )

    def __init__(self, key: str, head_h: int = 0, pad_top: int = 0, pad_bottom: int = 0, open_: bool = True) -> None:
        super().__init__(key)
        self.children: List[Block] = []
        self.fen = Fenwick()
        self.open = open_
        self.reveal = 1.0 if open_ else 0.0
        self.head_h = head_h
        self.pad_top = pad_top
        self.pad_bottom = pad_bottom
        self.shaped = False  # children built
        self.by_key: Dict[str, Block] = {}
        self.first_gap = 0  # the gap above the first child
        self.h = self._own_height()

    # ---- heights: a group's height comes from its children (and its chrome), not from a measurer
    def estimate(self, width: int, m: Measurer) -> int:
        return self.h

    def fast_measure(self, width: int, m: Measurer) -> int:
        return self.h

    def measure(self, width: int, m: Measurer) -> int:
        self.warm = True
        return self.h

    # ---- margins: a headless group shows its children's margins to its siblings
    def mt(self) -> int:
        if self.head_h == 0 and self.children:
            return self.children[0].mt()
        return self.MT

    def mb(self) -> int:
        if self.head_h == 0 and self.children:
            return self.children[-1].mb()
        return self.MB

    def gap_for(self, i: int) -> int:
        if i <= 0:
            return self.first_gap
        return max(self.children[i - 1].mb(), self.children[i].mt())

    def refresh_gaps(self, from_i: int = 0) -> None:
        """Recompute the gaps of the children from ``from_i`` on, then this group's standing in its parent."""
        for i in range(max(0, from_i), len(self.children)):
            self.children[i].set_gap(self.gap_for(i))
        if self.parent is not None and self.head_h == 0:
            # our first/last margins may have changed: our own gap and the next sibling's
            self.parent.refresh_gaps_at(self.index)

    def refresh_gaps_at(self, i: int) -> None:
        for j in (i, i + 1):
            if 0 <= j < len(self.children):
                self.children[j].set_gap(self.gap_for(j))
        if self.parent is not None and self.head_h == 0:
            self.parent.refresh_gaps_at(self.index)

    # ---- geometry
    def content_h(self) -> int:
        return self.fen.total()

    def _shown(self) -> int:
        inner = self.content_h()
        if self.reveal >= 1.0:
            return inner
        if self.reveal <= 0.0:
            return 0
        return int(round(inner * self.reveal))

    def _own_height(self) -> int:
        shown = self._shown()
        pads = (self.pad_top + self.pad_bottom) if shown or self.reveal > 0 else 0
        return self.head_h + pads + shown

    def content_top(self) -> int:
        return self.head_h + self.pad_top

    def child_y(self, i: int) -> int:
        """Top of child ``i``'s outer box (its gap included) in this group's coordinates."""
        return self.content_top() + self.fen.prefix(i)

    def child_at(self, y_local: int) -> int:
        """Index of the child under a local y (clamped)."""
        if not self.children:
            return -1
        return self.fen.find(y_local - self.content_top())

    def child_height_changed(self, i: int, delta: int) -> None:
        self.fen.add(i, delta)
        self._refresh_height()

    def _refresh_height(self, note_at: Optional[int] = None) -> None:
        """Recompute this group's height after its content changed and propagate the delta up.
        ``note_at`` is the offset into this group from which the picture may have moved (a child that
        changed notes itself; a change of the group's own chrome or shape notes here)."""
        if note_at is not None and self.scene is not None:
            self.scene.note_dirty(self, note_at)
        h = self._own_height()
        if h != self.h:
            d = h - self.h
            self.h = h
            if self.parent is not None:
                self.parent.child_height_changed(self.index, d)

    def set_reveal(self, r: float) -> None:
        r = max(0.0, min(1.0, float(r)))
        if r != self.reveal:
            was = self._shown()
            self.reveal = r
            # the picture changes from the clip edge on (the children above it stay where they are)
            at = self.head_h + (self.pad_top + min(was, self._shown()) if was and r > 0.0 else 0)
            self._refresh_height(at)

    def set_open(self, open_: bool, animate: bool = False) -> None:
        self.open = open_
        if not animate:
            self.set_reveal(1.0 if open_ else 0.0)

    def set_chrome(
        self, head_h: Optional[int] = None, pad_top: Optional[int] = None, pad_bottom: Optional[int] = None
    ) -> None:
        if head_h is not None:
            self.head_h = head_h
        if pad_top is not None:
            self.pad_top = pad_top
        if pad_bottom is not None:
            self.pad_bottom = pad_bottom
        self._refresh_height(0)

    # ---- children
    def _adopt(self, b: Block, i: int) -> None:
        b.parent = self
        b.index = i
        b.scene = self.scene
        b.indent = self.indent + self.CHILD_INDENT
        self.by_key[b.key] = b
        if isinstance(b, Group):
            b._set_scene(self.scene)

    def _set_scene(self, scene: Optional[Scene]) -> None:
        self.scene = scene
        for c in self.children:
            c.scene = scene
            c.indent = self.indent + self.CHILD_INDENT
            if isinstance(c, Group):
                c._set_scene(scene)

    def append(self, b: Block) -> None:
        i = len(self.children)
        at = self.content_top() + self.fen.total()
        self.children.append(b)
        self._adopt(b, i)
        b.gap = self.gap_for(i)
        self.fen.append(b.h + b.gap)
        if self.scene is not None:
            self.scene.register(b)
        self._refresh_height(at)
        if self.parent is not None and self.head_h == 0 and i == 0:
            self.parent.refresh_gaps_at(self.index)

    def extend(self, blocks: List[Block]) -> None:
        for b in blocks:
            self.append(b)

    def truncate(self, k: int) -> List[Block]:
        """Drop children from ``k`` on; returns them (the scene unregisters them)."""
        if k >= len(self.children):
            return []
        at = self.child_y(max(0, k))
        gone = self.children[k:]
        del self.children[k:]
        for b in gone:
            self.by_key.pop(b.key, None)
            b.parent = None
            if self.scene is not None:
                self.scene.unregister(b)
        self.fen.truncate(k)
        self._refresh_height(at)
        if self.parent is not None and self.head_h == 0:
            self.parent.refresh_gaps_at(self.index)
        return gone

    def replace_children(self, blocks: List[Block]) -> None:
        self.truncate(0)
        self.extend(blocks)

    def insert(self, i: int, b: Block) -> None:
        at = self.child_y(min(i, len(self.children)))
        self.children.insert(i, b)
        for j in range(i, len(self.children)):
            self.children[j].index = j
        self._adopt(b, i)
        b.gap = self.gap_for(i)
        self.fen.insert(i, b.h + b.gap)
        if self.scene is not None:
            self.scene.register(b)
        self._refresh_height(at)
        self.refresh_gaps(i + 1)

    def remove(self, i: int) -> Block:
        at = self.child_y(i)
        b = self.children.pop(i)
        for j in range(i, len(self.children)):
            self.children[j].index = j
        self.by_key.pop(b.key, None)
        b.parent = None
        if self.scene is not None:
            self.scene.unregister(b)
        self.fen.remove(i)
        self._refresh_height(at)
        self.refresh_gaps(i)
        return b

    def walk(self) -> Iterator[Block]:
        for c in self.children:
            yield c
            if isinstance(c, Group):
                yield from c.walk()

    def leaves_between(self, y0: int, y1: int, base: int = 0) -> Iterator[Tuple[Block, int]]:
        """(leaf block, scene y) for the leaves of this open group intersecting [y0, y1) (scene coords)."""
        if not self.children or self.reveal <= 0.0:
            return
        top = base + self.content_top()
        i = self.fen.find(y0 - top)
        n = len(self.children)
        while 0 <= i < n:
            c = self.children[i]
            cy = top + self.fen.prefix(i)
            if cy >= y1:
                break
            if c.h > 0:
                if isinstance(c, Group) and c.children:
                    yield c, cy + c.gap
                    yield from c.leaves_between(y0, y1, cy + c.gap)
                else:
                    yield c, cy + c.gap
            i += 1

    def ensure_shape(self, scene: Scene) -> None:
        """Build the children the first time the group is needed (overridden by shaped groups)."""
        self.shaped = True


class Scene:
    """The root group, the index by key, and what changed since the view last painted."""

    def __init__(self) -> None:
        self.root = Group("root")
        self.root.scene = self
        self.by_key: Dict[str, Block] = {}
        self.width = 0
        self.frame = 0
        #: block → the offset into it from which the picture may have moved (its own height, a gap
        #: above it, a child added or removed, a reveal); the view turns these into repaints
        self.dirty: Dict[Block, int] = {}
        self.dirty_rects: List[Tuple[int, int, int, int]] = []  # scene-coordinate rects to repaint
        self.height_changes = 0
        self.structure_rev = 0  # bumped when blocks come or go

    # ---- registry
    def register(self, b: Block) -> None:
        self.structure_rev += 1
        b.scene = self
        self.by_key[b.key] = b
        if isinstance(b, Group):
            b._set_scene(self)
            for c in b.walk():
                self.by_key[c.key] = c

    def unregister(self, b: Block) -> None:
        self.structure_rev += 1
        self.by_key.pop(b.key, None)
        b.scene = None
        if isinstance(b, Group):
            for c in b.walk():
                self.by_key.pop(c.key, None)
                c.scene = None

    def block(self, key: str) -> Optional[Block]:
        return self.by_key.get(key)

    # ---- geometry
    @property
    def total(self) -> int:
        return self.root.h

    def y_of(self, b: Block) -> int:
        return b.y()

    def leaf_at(self, y: int) -> Optional[Tuple[Block, int]]:
        """The leaf block under scene y (and its y); None when the scene is empty."""
        g: Group = self.root
        base = 0
        while True:
            if not g.children or g.reveal <= 0.0:
                return (g, base) if g is not self.root else None
            i = g.child_at(y - base)
            if i < 0:
                return None
            c = g.children[i]
            cy = base + g.child_y(i) + c.gap
            if isinstance(c, Group) and c.children and c.reveal > 0.0:
                # the head of an open group counts as its own leaf
                if y < cy + c.content_top() or y >= cy + c.h - c.pad_bottom:
                    return c, cy
                g, base = c, cy
                continue
            return c, cy

    def leaves_between(self, y0: int, y1: int) -> Iterator[Tuple[Block, int]]:
        return self.root.leaves_between(y0, y1, 0)

    # ---- dirtiness
    def note_dirty(self, b: Block, offset: int) -> None:
        """From ``offset`` into block ``b`` on, the picture may have moved. A block hidden in a closed
        group changes nothing on screen (its group's height does not depend on it) and is not noted."""
        self.height_changes += 1
        if not b.visible_in_tree():
            return
        cur = self.dirty.get(b)
        if cur is None or offset < cur:
            self.dirty[b] = offset

    @property
    def dirty_from(self) -> Optional[int]:
        """The lowest scene y from which the picture may have moved (None when nothing changed)."""
        ys = [b.y() + off for b, off in self.dirty.items() if b.scene is self]
        return min(ys) if ys else None

    def note_rect(self, b: Block, x: int, y: int, w: int, h: int) -> None:
        by = b.y()
        self.dirty_rects.append((x, by + y, w, h))

    def note_block(self, b: Block) -> None:
        self.dirty_rects.append((0, b.y(), self.width or 1, b.h))

    def take_dirty(self) -> Tuple[List[Tuple[Block, int]], List[Tuple[int, int, int, int]]]:
        d, r = list(self.dirty.items()), self.dirty_rects
        self.dirty, self.dirty_rects = {}, []
        return d, r

    # ---- anchors
    def pick_anchor(self, scroll: int, height: int = 0) -> Optional[Tuple[str, int]]:
        """The block the picture is held to while the layout changes: the first leaf in the viewport
        whose height is exact (its offset from the top edge means what it shows), else the leaf under
        the top edge. Returns (key, offset of the top edge into it)."""
        first: Optional[Tuple[Block, int]] = None
        if height > 0:
            for b, by in self.leaves_between(scroll, scroll + height):
                if first is None:
                    first = (b, by)
                if b.warm and b.grade >= GRADE_EXACT and b.h_width == self.width:
                    return b.key, scroll - by
        if first is None:
            first = self.leaf_at(scroll)
            if first is None:
                return None
        return first[0].key, scroll - first[1]

    def resolve_anchor(self, anchor: Tuple[str, int]) -> Optional[int]:
        """The scroll value that puts the anchor back where it was, or None when it is gone."""
        b = self.by_key.get(anchor[0])
        if b is None or not b.visible_in_tree():
            return None
        return b.y() + anchor[1]
