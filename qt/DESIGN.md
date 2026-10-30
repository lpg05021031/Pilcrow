# The canvas engine — final design

The conversation view, and every long list in the Qt client that has to stay smooth, is rebuilt on
one engine: a **scene of blocks** positioned by **Fenwick trees**, painted by **QPainter** into a
single `QAbstractScrollArea` viewport, with **exact scroll anchoring**, **per-block virtualisation**
with **calibrated height estimates**, **pixmap-cached painting**, **pixel scrolling**, and **one
clock**. There is no QLayout and no QWidget inside the scene; the only widgets are a handful of
**islands** (interactive forms) the engine positions.

`qt/PERF.md` has the measured baseline this replaces and the targets it must meet. This document is
the specification: structures, algorithms, invariants, the frame pipeline, the per-interaction
behaviour, the side panel, the migration, and the tests.

## 0. Principles

1. **Positions are arithmetic, never layout.** Every y on screen is a prefix sum over heights that
   the engine owns. No height is ever read back from a widget; nothing invalidates upward.
2. **A change costs what it touches.** A token costs one paragraph; a fold frame costs one Fenwick
   update and one repaint; a hover costs two rectangles; a session switch costs one paint.
3. **Nothing on screen moves unless the person moved it or an animation is moving it.** Every
   layout mutation is applied together with its anchor correction, in the same event-loop turn,
   before anything is painted.
4. **Knowledge of a height has three grades** — estimated, fast-measured, exact — and the scrollbar
   is honest at every grade. Upgrading a height never shifts what is on screen.
5. **Python runs between frames, not inside them.** Work that is not needed for the next frame
   (materialising far blocks, re-rendering after a theme change) runs from a budgeted queue that
   yields to input.
6. **Pure where possible.** Fenwick, scene reconciliation, anchoring, estimation and fold shapes are
   plain Python with an injected measurer, unit-tested without a QApplication.

## 1. Module layout

```
qt/pilcrow_qt/canvas/
  fenwick.py      Fenwick (binary indexed) tree over int heights: prefix, update, find_by_prefix
  clock.py        the shared animation clock
  estimate.py     height estimation (formula + fast measure) and its calibration
  anchor.py       anchor choice and correction, follow state, the scroll easing
  scene.py        Block, Group, Scene, incremental reconciliation, warm/cold, prefetch order
  paint.py        PaintState, the pixmap cache, decoration painters (ported from items.py)
  canvas.py       CanvasView(QAbstractScrollArea): events, hit testing, hover, focus, selection,
                  islands, overlays, the paint loop, pixel scrolling
  blocks/
    prose.py      ProseBlock: one Markdown top-level block, one QTextDocument, streaming append
    user.py       UserBlock (bubble, attachments, actions), CommandChip
    thinking.py   ThinkingBlock (head, peek, body)
    tool.py       ToolBlock (head), ToolBodyGroup, the body painters per tool
    fold.py       FoldGroup (work fold head), ChapterGroup, ExploreGroup, StepsGroup
    misc.py       NoticeBlock, DividerBlock, ResultBlock, WorkingBlock, SkeletonBlock
    island.py     IslandBlock (a real QWidget placed by the engine)
  transcript.py   TranscriptScene: items → units → groups and blocks; the Transcript widget API
  lists.py        RowScene: a flat scene for the Timeline, Tasks and other panel lists
```

`fenwick.py`, `estimate.py`, `anchor.py`, `scene.py` import nothing from Qt. `paint.py` and the
blocks import QtGui only. `canvas.py` is the one QtWidgets file.

## 2. Data structures

### 2.1 Fenwick tree

`Fenwick(n)` over integer heights with `add(i, delta)`, `prefix(i)` (sum of heights before index
*i*), `total()`, `find(y)` (the largest *i* with `prefix(i) ≤ y`, by binary descent), `insert(i, h)`
and `remove(i)` (rebuild of the suffix, O(n) but rare: blocks are appended at the end of a live
turn, and the live turn is small), and `set(i, h)`. All O(log n) except insert/remove in the middle.
n is the number of children of one group: ≤ ~20,000 at the top level of a long conversation, a few
dozen inside a fold.

### 2.2 Block

```python
class Block:
    __slots__ = (
        "key",        # stable identity: item id, unit key, or "<item>:<part>"
        "rev",        # the item rev this block was last synced from
        "sig",        # visual signature: what paint depends on; equal → no repaint
        "parent",     # the Group that holds it (None for the root)
        "index",      # position in the parent's children (kept in sync by the parent)
        "h",          # current height used for layout
        "grade",      # 0 estimated · 1 fast-measured · 2 exact
        "h_width",    # the width the height was measured for
        "warm",       # documents / static text are materialised
        "last_seen",  # frame counter of the last paint (for cooling)
        "indent",     # nesting indent in px (agent children)
        "focusables", # tuple of target ids, in tab order
    )
    def estimate(self, ctx) -> int
    def fast_measure(self, width, ctx) -> int
    def measure(self, width, ctx) -> int         # exact; materialises what it needs
    def cool(self) -> None                        # drop documents, pixmaps; keep h
    def paint(self, p, rect, st) -> None
    def hit(self, pt) -> Optional[Target]         # pt in block coordinates
    def activate(self, target, view) -> None
    def cursor_for(self, target) -> Qt.CursorShape
    def tooltip_for(self, target) -> str
    def text(self) -> str                         # plain words, for find / copy / tests
    def text_pos_at(self, pt) -> Optional[int]    # for selection; None where there is no text
    def selected_text(self, a, b) -> str          # a..b positions, or whole when a is None
    def live(self) -> bool                        # wants clock ticks
    def tick(self, now) -> Optional[QRect]        # rect to repaint on a tick, or None
```

`sig` is a tuple the block builds from the item (status, text length, result presence, error flag,
elapsed bucket…). The reconciler compares `rev` first (cheap), then `sig` (so a rev bump that
changed nothing visible costs nothing).

### 2.3 Group

```python
class Group(Block):
    __slots__ = ("children", "fen", "open", "reveal", "pad_top", "pad_bottom", "head_h", "content_h")
```

A Group's height is `head_h + pad_top + round(reveal * content_h) + pad_bottom` where
`content_h = fen.total()`; `reveal` is 1.0 open, 0.0 closed, in between while animating. Its
children are painted translated by `head_h + pad_top`, clipped to the group's rect, so a closing
group clips its content from the bottom, as the web's height transition does. Groups that are
always open (an ItemsUnit, a tool body group with no head) have `head_h = 0`, `reveal = 1`.

Kinds of group: `UnitGroup` (one unit of the conversation: a stretch of plain items or a work
fold), `FoldGroup` (the work fold with its head), `ChapterGroup`, `ExploreGroup`, `StepsGroup`,
`ToolBodyGroup` (a tool card's body, incl. an agent's children as nested blocks), `IslandGroup`.
Depth is at most four: root → UnitGroup/FoldGroup → ChapterGroup/StepsGroup → ToolBodyGroup →
(agent children) blocks.

### 2.4 Scene

```python
class Scene:
    root: Group                   # children are UnitGroups in conversation order
    by_key: Dict[str, Block]      # every block, warm or cold
    settled_units: List[Unit]     # units before the last user boundary (immutable)
    settled_items: int            # how many items they cover
    anchor: Anchor                # see §4
    following: bool
    needle: str                   # the find query, applied to blocks as they warm
    width: int                    # column width heights are valid for
    theme: str                    # token set name heights/pixmaps are valid for
    frame: int                    # paint counter
```

One Scene per session, retained in `store`-adjacent state for the life of the app (LRU of eight
sessions; a dropped Scene is rebuilt from the model when the session is shown again).

## 3. The frame pipeline

Everything that changes the picture goes through four stages in one event-loop turn:

1. **Mutate** — a coalesced model update (`store.rt_changed`, one per 16 ms per session), an input
   event, a clock tick or an animation frame produces a `Mutation` list: blocks added, removed,
   resynced (`rev`/`sig` changed), heights changed, reveal changed.
2. **Commit** — `begin()` reads the anchor from the pre-mutation layout; the Fenwick trees are
   updated along each affected path as the mutation runs; `end()` puts the anchor back (a *silent*
   scroll set: no pixels move), measures exactly whatever is in the viewport now, and puts the
   anchor back once more. The commit is reentrancy-safe: a scroll set inside it never starts another.
3. **Invalidate** — every height change was noted as `(block, offset)`. A change whose block ends
   above the anchor moved with the anchor and the scroll together, so it changed nothing on screen;
   the first change at or below the anchor gives the scene y from which the viewport is repainted.
   Rects noted for in-place changes (a live row, a toggle inside a body) are repainted as they are.
   A person's scroll, and the easing towards the end, blit with `viewport().scroll(0, dy)` so only
   the exposed strip is painted.
4. **Paint** — `paintEvent` descends to the first block intersecting each exposed rectangle and
   paints forward until past its bottom. It never mutates: a block that is still cold (only after a
   fling that outran the prefetch) is painted as faint placeholder bars and the prefetch fills it
   in; `CanvasView.cold_paints` counts how often that happened (zero in the benchmarks).

Between frames, the **prefetch drain** — one commit — warms blocks within three screens of the
viewport, upgrades the estimates of cold blocks everywhere (one slice per drain, the top of the
history first), and cools blocks beyond six screens once the view has moved a screen or the warm set
has grown past 600, under a time budget (4 ms per drain, 2 ms while a fling is in progress). A
drain that finishes with nothing left records the state it saw (scroll, viewport, width, structure
revision, height changes, theme) and the next one returns at once unless that state has changed.

Per-frame budget at 60 Hz (16.7 ms): reconcile ≤ 1 ms, commit ≤ 0.2 ms, paint ≤ 4 ms, prefetch
≤ 4 ms, leaving half the frame to Qt and the OS.

## 4. Scroll anchoring and following

### 4.1 The anchor

The anchor is `(key, offset)`: the first leaf in the viewport whose height is **exact** (so the
offset of the top edge into it means what it shows), else the leaf under the top edge, and
`offset = scroll − y`. Preferring an exact leaf is what keeps the reading still when the person
scrolls up into cold history: the cold block at the top is measured and grows *upwards*, away from
the lines they were reading. `pick_anchor()` costs one Fenwick descent per level plus a short walk.
It is read at the start of every commit, following or not; it is never stored across mutations
except as the session's remembered position.

### 4.2 Correction

After the heights are applied, `scroll_new = y(key) + offset + PAD_TOP`, clamped, set without
blitting (the picture is rigid with the anchor, so there is nothing to move). If the block is gone,
or the value had to be clamped, the whole viewport is repainted. Mutations entirely below the anchor
leave `y(key)` unchanged, so nothing happens — which is what the reader wants when Claude writes
below. Mutations entirely above it change the scroll value and nothing else: no paint at all.

### 4.3 Following

`following` is true when the reader is within 60 px of the end (after their own scroll, a send, End,
or the "Latest" pill) and false the moment they scroll up by more than one line. While following,
the anchor is still held (so a height upgrade far above never nudges the view), and then the
**target** of the scroll easing is set to the maximum; the easing (on the clock) moves the actual
value by `d × (1 − e^(−dt/τ))`, τ = 60 ms, snapping when `|d| < 1` or when `reduced_motion`.
Retargeting never restarts the easing. Every own scroll set runs a small commit of its own, so what
the easing brings into view is measured before it is painted. "Latest" shows when `following` is false and new content arrived
more than one viewport below.

### 4.4 Remembered position

Leaving a conversation stores `(anchor key, offset)` or `BOTTOM`. Returning resolves the key through
`by_key` and sets the scroll exactly; since heights are kept per width and far blocks keep their
measured heights, the landing is exact, not approximate, and needs no re-placing timers.

### 4.5 Things that no longer exist

The follow step timer, the compensation one frame late, the restore / pin / land timers, and the
estimate-to-real snap of a page coming into view.

## 5. Heights: three grades, one scrollbar

- **Grade 0, estimate** (`estimate.py`): from the text and the fonts, no Qt layout. Prose: lines =
  Σ over paragraphs of `ceil(width_of(text) / column)` using `averageCharWidth` for Latin and the
  em width for CJK, times the prose line height, plus paragraph margins; code fences: line count ×
  mono line height + the well's padding; headings scaled; heads and rows: their fixed heights. Each
  block kind keeps a **calibration factor**, the exponential moving average of `exact / estimate`
  over the blocks that have been measured (α = 0.1), applied to its estimates. Cost: ~5 µs.
- **Grade 1, fast measure**: `QFontMetricsF.boundingRect(QRectF(0, 0, width, 1e6), TextWordWrap,
  plain_text).height()` per paragraph — Qt's real line breaking, in C++, no document. Within a few
  pixels of the exact height for prose. Cost: ~50 µs per block. The prefetch queue upgrades every
  cold block to grade 1 in idle time, so a 2,000-turn conversation has a near-exact scrollbar
  within about a second of opening, without a document anywhere.
- **Grade 2, exact**: the block's documents laid out at the width. Cost: 0.2–1 ms per prose block,
  less for heads. Applied to visible blocks and the prefetch window.

Every block has a height from the moment it exists: a block the reconciler makes is estimated at
once (for a group, the leaves that show once it is open), so the scrollbar is honest from the first
frame and a block streaming in at the end never spends a frame at zero height. A tool body's
estimate reads the item (lines of output up to the tail it shows, lines of a diff, files found,
steps of a todo), so it is close before the body is ever built. A fold that opens measures its
children exactly under a 14 ms budget before the reveal starts, so the animation runs on true
heights; past the budget the rest take the fast grade and are measured as they come into view.

A person's scroll and a resize step measure exactly under an 8 ms deadline: what fits is painted
exactly, the rest shows as placeholder bars for a frame or two while the drain finishes it. A
commit from the model (new output, a toggle) has no deadline: what it shows must be right.

Shapes are lazy twice over: a work fold builds its inside (chapters, steps, cards) the first time it
is opened, revealed by a search, or scrolled into view while open, and carries a guess (a row per
step) until then; a finished text splits into its paragraphs the first time it comes near the
screen, and is one estimate until then. Opening a 600-turn conversation builds its units and
nothing inside them; switching every fold open (verbose) costs the units that are on screen.

A height upgrade is a mutation like any other: it goes through the anchor correction, so the
reader never sees it. Heights are cached per `(key, rev, width, font scale)`; a block that is
cooled keeps its grade-2 height and shows the same height when warmed again.

## 6. Painting

### 6.1 The loop

```
for rect in event.region().rects():
    y0 = rect.top() + scroll
    i = root.fen.find(y0); y = root.fen.prefix(i)
    while i < len(root.children) and y < rect.bottom() + scroll + 1:
        paint_block(root.children[i], QRect(x, y - scroll, width, h))
        y += h; i += 1
```

`paint_block` for a Group paints its head, sets a clip to the group's rect, and recurses into its
children with the same descent on its own Fenwick tree. A cold visible block is warmed first
(exact measure; if the height changes, the mutation is queued for the next commit, with anchoring).

### 6.2 Text

Prose, thinking and code are `QTextDocument`s drawn with `documentLayout().draw(painter, ctx)`
where `ctx.clip` is the exposed part of the block, `ctx.palette` carries the selection colours and
`ctx.selections` the find and selection highlights. The document's `textWidth` is the column width.
Heads (verb · target · meta) are `QStaticText`s cached on the block — glyph layout computed once,
drawn in one call. Icons are `icons.pixmap` (already cached).

The decorations QTextDocument cannot draw (the inline-code pill, the rule beside a quote, the code
well, table hairlines, the rail of a chapter, the breathing mark) are painted from the layout's
geometry by the functions ported from `items.py` (`_pills`, `_table_rect`, `_table_rules`,
`_block_height`). The padding hacks for QTextBrowser (U+202F pads, negative viewport margins) go:
the pill and the first-line leading are drawn by the painter at the right place.

### 6.3 The pixmap cache

A block that is **static** (not streaming, not live, not hovered, not focused, not selected, not
lit by find) and **shorter than twice the viewport** is painted once into a `QPixmap` at the
device pixel ratio and blitted afterwards. Key: `(key, rev, sig, width, theme, dpr, font_scale)`.
LRU by bytes, 48 MB budget; a block's entry is dropped when its `rev`/`sig` changes or it cools.
The pixmap is opaque (the canvas background is uniform), so the blit is a memcpy per scanline.
Blocks that are not static paint directly; text drawn from a laid-out document costs ~0.1 ms per
screenful of lines in the raster engine.

### 6.4 Pixel scrolling

`scrollContentsBy(dx, dy)` calls `viewport().scroll(0, dy)`: Qt moves the pixels and posts a paint
for the exposed strip only; child widgets (the islands) move with the pixels. The "Latest" pill and
the find bar are children of the view, not of the viewport, and stay put.

### 6.5 Dirty rectangles

A streamed token: `update(block_rect)`. A tick on a spinner: `update(14×14)`. A fold frame:
`update(fold_top … bottom)`. A hover: `update(old_target_rect ∪ new_target_rect)`. A resize or a
theme change: `update()`. Qt merges the region.

### 6.6 Robustness

Each `paint_block` call is guarded: an exception in one block's painter logs once and paints a faint
"could not draw this row" marker in its rect; the rest of the frame is unaffected. (In PySide6 an
exception that escapes `paintEvent` aborts the whole paint: a blank frame.)

## 7. Reconciliation

### 7.1 Units, incrementally

`TranscriptScene.sync(model, running)`:

1. `B` = index after the last user boundary (user message, compaction, divider) in `model.items`.
   Items before `B` are settled: the model only appends, and settles items in place by bumping
   `rev`, so `settled_units` (computed with `group_turns` once per new boundary) stay valid; their
   blocks are resynced by `rev` when a settle touches them (a turn's `end_ts`, a tool's final
   status) — a handful of blocks per turn end.
2. `tail_units = group_turns(items[B:], running)`: the live turn, usually under fifty items.
3. For each tail unit, `UnitGroup.sync(unit, ctx)`: children are diffed by key in order (common
   prefix kept; the first differing key truncates and rebuilds from there — the live turn is small);
   each kept block with a moved `rev` is resynced (`sig` compared; height re-measured only if the
   block says its text or structure changed).
4. Removed unit keys are deleted from the root; new ones appended.
5. The mutation list goes to the commit stage.

Cost per update: O(live turn), typically under 0.5 ms.

### 7.2 Shapes

The shape of a work fold (chapters, "Explored" runs, steps) uses the pure helpers that exist today
(`chapters_of`, `group_items`, `is_quiet`, `tally`, `work_summary`). A **cold** UnitGroup does not
compute its shape: its height is the fold head's fixed height when closed, or the sum of its items'
estimates when open. The shape is computed when the group warms, and marked stale (not recomputed)
when the view mode changes, so a view-mode switch costs O(warm groups) — about ten milliseconds —
and the scroll position is anchored through it.

### 7.3 Streaming into a paragraph

`ProseBlock` holds the Markdown source of one top-level block and its document. On a delta:

- if the block is not inside a fence/list/table and the delta contains none of `` * _ ` [ ] # > | ~ < & \n ``
  (and no `-` or digit at a line start), the text is appended with a `QTextCursor` at the end,
  using the format of the last character — O(delta);
- otherwise the block's HTML is re-rendered (`markdown.render_html` of that block only, ~0.3 ms)
  and set with `setHtml`;
- when the text item finishes streaming, the block is re-rendered once from its final source so the
  markup is exact whatever the append heuristics did.

Only the last ProseBlock of a streaming text item is ever touched; the earlier blocks of the same
item keep their documents and pixmaps. The thinking "peek" (the last lines of a streaming thought
under a closed head) is the same document painted through a clip, bottom-aligned.

## 8. Interaction

### 8.1 Hit testing and hover

`CanvasView.hit(pos)`: root descent to the UnitGroup, recursive descent to the leaf, then the
block's `hit(local)` returns a `Target(kind, id, rect)` or None. Hover state is `(block key,
target id)`; on change, the old and new target rects are invalidated, the cursor set from
`cursor_for`, the tooltip timer armed (`QToolTip.showText` after 600 ms at rest). Chevrons and
"Open in Files" buttons occupy reserved space and are painted at alpha 0 when not hovered: no
geometry depends on hover.

### 8.2 Activation and focus

Click → `block.activate(target, view)`: toggles a group (`view.toggle(group)`), opens a file,
copies, shows all lines, follows a link, forks… Keyboard: Tab / Shift+Tab move the focus index over
the `focusables` of visible blocks (the ring is painted by the block), Enter / Space activate, Home /
End / PageUp / PageDown / arrows scroll, Escape clears the selection, Ctrl+F opens find, Ctrl+C
copies. Focus survives scrolling (it is a key, not a widget).

### 8.3 Selection

`Selection(anchor=(path, pos), focus=(path, pos))`. On press the view records the text position
under the pointer (`text_pos_at`); on drag it updates the focus and invalidates the blocks whose
selection changed. `PaintState.selection_for(block)` gives `(a, b)`, `FULL` or `None`; text blocks
pass it to the document's draw context, non-text blocks paint a wash when `FULL`. Double-click
selects a word (`QTextCursor.select(WordUnderCursor)`), triple-click a block. Copy joins
`selected_text` of the blocks between anchor and focus with blank lines, stripping the pill pads and
word joiners (as `createMimeDataFromSelection` does today). The context menu offers Copy, Select
all, Copy link / path.

### 8.4 Groups opening and closing

`view.toggle(group)`: `group.open = not open`; if `reduced_motion`, `reveal` jumps; otherwise a
`QVariantAnimation` on `reveal` (spring curves from `theme.py`, 240–420 ms by content height, the
`ease_exit` curve when closing) whose each frame is a mutation: one Fenwick update on each level up
to the root, anchor correction, `update(from group's y)`. The chevron angle is `90° × reveal`.
Fold state is stored per key in the Scene (replacing `turn_open` / `chapter_open`).

### 8.5 Find

The find bar searches `scene.text_entries()` (block key, text) — plain Python over the blocks, cold
or warm, since `text()` reads the item. Stepping to a match: open the groups on its path without
animation, mark the match as the scene's current needle, warm the block (exact height), and set the
anchor so the block lands at 38 % of the viewport — one commit, no timers. Highlights: warm text
blocks apply the needle as extra selections when they warm or when the needle changes; the current
match is lit in full, others washed, as today.

### 8.6 Islands

`IslandBlock(widget)`: a real QWidget (the permission prompt, the live question form, the plan
review, the empty-conversation card). `measure(width)`: `widget.setFixedWidth(width);
widget.layout().activate(); widget.sizeHint().height()`. After every commit and scroll the view
calls `setGeometry(x, y − scroll, width, h)` on islands within a screen of the viewport and hides
the others. They keep native focus, input methods and shortcuts; `bind` guards them as today.

### 8.7 Theme, view mode, font scale, resize

- **Theme**: `paint.cache.clear()`; warm documents re-render (their HTML carries token colours) in
  the prefetch queue, visible ones first; the first frame after the switch paints with the new
  background and the re-rendered visible blocks: ~30 ms for a screen, not 8.8 s.
- **View mode**: fold defaults and quiet grouping change: shapes are marked stale (§7.2), the
  anchor is kept, warm groups reshape on their next paint.
- **Font scale** (a new feature the engine makes cheap, Ctrl+= / Ctrl+− / Ctrl+0): the theme's
  prose and mono sizes scale; heights are stale at the new `font_scale` key; visible blocks are
  measured now under anchoring, the rest by the prefetch queue.
- **Width change** (window or panel resize, sidebar toggle, composer growing): same as font scale
  for the width key. During a drag, visible blocks are re-measured every frame (≈ 20 × 0.5 ms), the
  rest are left at their previous-width heights (grade kept, width mismatched) until idle; the
  anchor holds the reader's place.

### 8.8 Sessions

Each `SessionView` keeps its `TranscriptCanvas`, whose Scene is retained; `MainWindow` keeps the
last eight `SessionView`s in its `QStackedWidget` and switches with `setCurrentWidget`. A hidden
Scene is not synced on every update: `rt_changed` for a hidden session sets `stale = True`; the
Scene syncs when shown (O(live turn)). Switching costs one paint.

### 8.9 Opening

`store._load_session` pushes the snapshot into the model in slices of 300 messages per event-loop
turn (a zero timer chain), under the loading skeleton; the Scene is built when the model is complete
(blocks are small: ~16,000 in a few hundred milliseconds, spread over slices as well). The first
paint needs only the last screen's blocks exact. `orjson` is used for `json.loads` when installed.

## 9. The clock

`clock.py`: one `QTimer`, 33 ms while only indicators are live (breathing marks, spinners, rails,
elapsed counters), 16 ms while an animation runs (reveal, scroll easing, resizer handle), stopped
when nothing subscribes. Subscribers are weak references with a `tick(now)` that returns the rect to
repaint. The canvas subscribes once and fans out to its live blocks on screen; off-screen live blocks
do not tick. An elapsed counter repaints its meta rectangle; its width is reserved for "99m 59s".

## 10. The side panel and the sidebar

### 10.1 Refresh discipline

`Refresher` gains `visible_only=True`: a hidden tab marks itself stale and refreshes in `showEvent`.
Change keys never include `rt.rev`; they are built from the data the tab shows (the tasks list, the
usage dict, the steps). `TasksPanel`, `InfoPanel` and `HistoryPanel.render` reconcile their rows by
id: update in place, insert new, remove gone; sections are created once.

### 10.2 Lists on the engine

The Timeline (300 rows: 1.3 s today) and the Tasks tab's running/finished lists are `RowScene`s on
the same engine: flat scenes of row blocks (title, sub, counts, rail dot) with the selected row's
detail as an island. The Changes tab keeps `DiffGrid` (already painted) but caches per-line advances
and relays only when wrap mode or the column layout changes, not on every pixel of a seam drag; its
file cards paint their hover actions instead of showing and hiding widgets; `DiffView.set_files`
diffs by path and updates cards in place.

### 10.3 Panel disclosure

`Collapse` is reimplemented on the clock: it measures its body at the real width before animating
(so the target height is right), animates a clip height instead of `maximumHeight` where the body is
a painted widget, and falls back to the current behaviour for widget bodies in the dialogs where
trees are tiny.

### 10.4 Sidebar

`Sidebar.rebuild` diffs the target order against the current order and moves only the rows whose
index changed (`QBoxLayout.insertWidget` at the new index), updates rows in place, and never
reparents a row that stays. Its hover state is painted.

### 10.5 Hygiene, app-wide

No `setStyleSheet` and no `setVisible` from hover or refresh paths in the session view, the
sidebar and the panels: colours and fonts via the painter, `setFont`, `setPalette`. All indicators on
the clock. These are small, local changes measured by the `hover_sweep`, `panel_tabs` and `idle`
scenarios.

## 11. Invariants (asserted in debug builds and tests)

- I1 Every y comes from a Fenwick prefix; `Group.h == head_h + pads + round(reveal × fen.total())`.
- I2 A mutation and its anchor correction are applied in the same commit, before any paint.
- I3 `measure(width)` is a pure function of (block content, theme fonts, width, font scale); the
  cache is keyed by exactly those.
- I4 No document or pixmap exists for a block farther than six screens from the viewport.
- I5 No QLayout and no QWidget inside the scene except `IslandBlock` widgets; no `setStyleSheet`
  anywhere in `canvas/`.
- I6 The only timers are the clock and the animations; nothing re-places the scroll position later.
- I7 Painting never raises out of `paintEvent`.

## 12. Costs, estimated against the baseline

Back-of-envelope from before the work; the measurements are in `PERF.md` §5 and §7:

| interaction | 2.9.8 | engine |
|---|---|---|
| fold toggle | 122 layout passes, 69 ms stall, 5 scroll sets | 0 layouts, ≤ 2 ms per frame, 0 sets |
| tool card toggle | 107 layouts, 13 scroll sets, 84 px jump | 0, 0, animated amount only |
| streaming, big result lands | 5.7 s stall | ≤ 2 ms (blocks built, exact measure of the visible ones) |
| streaming, scrolled up | 1,577 px shift | 0 px |
| wheel fling | p95 12.7 ms | ≤ 3 ms (blits + exposed strip) |
| scrollbar drag step | p95 406 ms | ≤ 8 ms (visible blocks warmed, ≤ 20 × 0.5 ms) |
| hover sweep, 240 moves | 1,348 layouts | 0 layouts, 2 rects per move |
| view mode → verbose | 15.7 s | ~10 ms |
| theme switch | 8.8 s | ~30 ms |
| session switch | 7.1 s, 374 widgets | one paint |
| open 2,000 turns | 868 ms rows, synchronous | ≤ 150 ms to first paint; modelling in slices |
| find over the conversation | 4.5 s, 22,556 widgets | ~20 ms, 0 widgets |
| idle, busy-looking | 16 % | ≤ 2 % |
| Tasks tab while streaming | 1,928 layouts, 576 ms stall | 0 while hidden; one reconcile a second when shown |

## 13. Migration

Phases 1 and 2 have landed (2.10.0): `canvas/` with Fenwick, Scene, anchor, estimate, clock, paint,
nodes, the prose / user / thinking / notice / result / working blocks, streaming append, the pixmap
cache, pixel scrolling, follow and "Latest", the remembered place, islands for the empty card; the
work folds (`FoldGroup`, `ChapterGroup`, `Explore`, `Steps`, `Flat`), `ToolBlock` with every body
painter (Bash, Read, Edit/Write diff, Notebook, Grep/Glob, Todo, Task, Agent with nested children,
Web, Workflow, generic/MCP, errors with fixes), the permission / question / plan islands, orphans.
`canvas.transcript.Transcript` replaced `widgets.transcript.view.Transcript` behind the same public
methods; the old package is gone but for the real widgets the canvas still hosts (`permission.py`,
`bodies/ask.py`, `bodies/plan.py`) and the pure code/diff helpers (`bodies/code.py`,
`bodies/common.py`); the fold helpers moved to `lib/folds.py`. Sessions keep their scene (eight)
and their view (eight) when switched. What the implementation refined over the plan: the anchor
prefers an exact leaf (§4.1); the hold is silent and the invalidation is per changed block (§3);
every block has a height from birth, and a fold measures its children under a budget before it
opens (§5); the drain is one commit and remembers when there is nothing to do.

Phase 3 — people: context menu, keyboard focus and shortcuts, accessibility (`QAccessibleInterface`
exposing visible blocks), font scale. (Selection and copy, tooltips and find highlighting are in.)

Phase 4 — the rest of the window: Refresher discipline and row reconciliation, `RowScene` for the
Timeline and Tasks lists, `DiffGrid` caching, `Collapse` on the clock, the sidebar diff, hover and
stylesheet hygiene, sliced loading, the perf budget tests in `qt/tests/test_perf_budget.py` and in
the release script. The view-mode and theme switches still re-sync every unit and re-polish every
widget in the window; both belong here.

## 14. Tests

- **Pure** (`test_canvas_core.py`): the Fenwick tree against a list under random updates; group
  heights, positions and reveal; leaf walks; the anchor surviving growth above it and the removal of
  its block; the follow thresholds; estimates and calibration; sibling margins collapsing.
- **Blocks, offscreen, no server** (`test_blocks.py`, `test_tool_blocks.py`): prose line boxes,
  pills, quotes and code wells as painted; find lights; the person's bubble; the column width; every
  tool body (head words, numbered code, diffs, ANSI output, exit codes and tails, todo, search,
  nested agents and their reports, MCP), the sync fast path, the question and plan forms and the
  permission card answering through `store.respond`, and a screenshot of every kind painted by a
  real `CanvasView` with `cold_paints == 0`.
- **Behaviour, against the mock** (`test_transcript.py`): rows and closed folds of a conversation,
  verbose opening every fold, a fold opening to an "Explored" line or to cards, a live turn carrying
  the mark and the view ending at the end, a long conversation opening at its end with most of the
  history cold, scrolling up stopping the follow and new output showing "Latest" without moving
  the view, the remembered place in a new view, find.
- **Budgets** (phase 4): `test_perf_budget.py` will run a short version of the bench (120 heavy
  turns) and assert the targets in `qt/PERF.md` §5 with headroom; `npm run release` runs it.

## 15. Rejected alternatives, briefly

Patching the widget tree (batched updates, no animation) keeps every change a relayout and every
page a Python widget build. `QListView` with delegates lays out every item's `sizeHint` for
non-uniform rows, has no fold animation and no cross-row selection. `QGraphicsView` is the viable
fallback, but `QGraphicsWidget` layouts cost what QLayouts cost and plain items add a BSP index a
one-dimensional list does not need. QML with the software backend is excluded by the project rules
and is not faster for text on a CPU. Threads do not help a PySide6 painter: Python holds the GIL;
the engine is single-threaded and cooperative by design, and a background HTML pre-renderer is a
possible later addition behind `sys.setswitchinterval(0.001)`, not part of this design.
