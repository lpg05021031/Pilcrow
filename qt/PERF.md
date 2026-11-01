# Pilcrow Qt client — performance: the baseline and the design that replaces it

This document is the record of what the Qt client costs today under extreme use, the reasons, and
the architecture that replaces the conversation view and the side panel's refresh model. The
numbers come from `qt/tests/bench` (see the end), run offscreen on the software rasteriser, which is
the machine the Qt client exists for: no GPU, a CPU painting every pixel.

## 1. How the client is measured

`python qt/tests/bench/run.py --heavy 600 --mixed 2000` boots the client against the mock server,
has the mock build two conversations on demand (`mock.generate`) and drives the window with real
input events the way Playwright drives the web client:

- **heavy** (600 turns, ~4,500 items): prose with headings, lists, code blocks and tables; 300-line
  command outputs; 48-line diffs; thinking blocks; sub-agents with their own nested steps; failed
  commands; "Explored" runs of quiet reads.
- **mixed** (2,000 turns, ~10,000 items): the plain shapes of `MOCK_LONG`.

Eighteen scenarios cover what a person does to a long conversation: open it cold, fling through it
with the wheel, drag the scrollbar, open and close folds, thinking rows and tool cards, sweep the
pointer over it, stream a long turn while following and while scrolled up, type in the composer,
resize the window, open every side-panel tab and drag the panel seam, stream with the panel open on
each tab, switch between the two conversations, cycle the view mode, switch theme, find, and sit
idle. The rig records, per scenario:

| measure | what it tells |
|---|---|
| **stall** p50 / p95 / max, over 16 ms, over 50 ms | how long each turn of the event loop blocks the window (a stall over 16.7 ms drops a frame; over 50 ms is a "long task") |
| **layout** | `LayoutRequest` events inside the widget tree: one per QLayout activation |
| **resize** / **move** | content-widget resizes (each moves the scrollbar range) and child moves |
| **paint**, **paint_px** | paint events on the viewport and the area they covered (the rig's own grabs are excluded) |
| **widgets_made** | `ChildAdded` under the tree: what a refresh built from scratch |
| **scroll_sets** | how often the program changed the scroll position (each is a visible shift) |
| **fps**, **height_frames** | frames an animation actually produced |
| **flashes** | frames in which a band of ≥24 scanlines went blank and came back: flicker |
| **shift** (unexpected px, max jump) | the Qt analogue of cumulative layout shift, read off scanline hashes of consecutive grabs |
| **cpu_pct**, **rss** | CPU share of one core, resident memory |

Input is paced by the clock (120 wheel notches a second, a pointer at 720 px/s, 40 keys a second),
so a slower client receives the same input as a faster one and the stall numbers stay comparable.

## 2. Baseline (release 2.9.8)

`qt/tests/bench/baseline-2.9.8.json`, printed by `report.py`. The heavy conversation is 600 turns,
5,200 messages, 5.7 MB of JSON; the mixed one 2,000 turns, 10,500 messages, 4.1 MB. Offscreen,
software rasteriser, one core. (The table below is the first measurement; the file in the repository
was re-measured with the rig as it stands in §7, so the two columns of §7 are like for like. The
flicker count changed meaning in between — see §7 — which is why the baseline's zeros stay zeros.)

| scenario | measure | 2.9.8 |
|---|---|---|
| open cold | mixed 2,000 turns: rows on screen | 868 ms |
| open cold | heavy 600 turns: rows on screen | 680 ms |
| open cold | heavy: first paint of the live pages | 37 ms |
| view mode → verbose | to first paint (every drawn row rebuilt) | 15,684 ms |
| view mode → normal | to first paint | 2,047 ms |
| theme → light | to first paint (every widget re-polished) | 8,826 ms |
| fold toggle | layout passes per toggle | 122 |
| fold toggle | program scroll sets per toggle | 5.2 |
| fold toggle | event-loop stall, max | 69 ms |
| fold toggle | animation fps, worst | 56 |
| thinking toggle | layout passes per toggle | 53 |
| thinking toggle | animation fps, worst | 48 |
| tool card toggle | layout passes per toggle | 107 |
| tool card toggle | program scroll sets per toggle | 13 |
| tool card toggle | stall, max | 46 ms |
| tool card toggle | animation fps, worst | 28 |
| tool card toggle | row below: largest single-frame jump | 84 px |
| hover sweep | layout passes for 240 pointer moves | 1,348 |
| streaming, following | stall p95 / max | 18 ms / 5,701 ms |
| streaming, following | stalls over 16 ms in a 14 s turn | 19 |
| streaming, following | CPU, one core | 94 % |
| streaming, scrolled up | unexpected shift of what is on screen | 1,577 px over 30 frames |
| streaming, scrolled up | program scroll sets | 33 |
| streaming, scrolled up | stall p95 / max | 141 ms / 3,809 ms |
| typing in the composer | conversation layout passes | 0 |
| typing in the composer | stall p95 | 9.8 ms |
| window resize | step p95 (reflow of the column) | 148 ms |
| window resize | steps over 50 ms, of 26 | 10 |
| switch session | to first paint, p50 | 7,091 ms |
| switch session | widgets made per switch | 374 |
| switch session | RSS growth over 10 switches | 75 MB |
| panel tabs | Session tab: first event-loop turn | 683 ms |
| panel tabs | Timeline tab (300 steps): stall max | 1,286 ms |
| panel tabs | seam drag step p95 | 147 ms |
| panel open while streaming | Tasks tab: stall p95 / max | 174 ms / 576 ms |
| panel open while streaming | Tasks tab: layout passes in a 9 s turn | 1,928 |
| wheel fling, 120 notches/s | stall p95 / max | 12.7 ms / 21 ms |
| scrollbar drag, 81 steps | step p95 | 406 ms |
| scrollbar drag, 81 steps | steps over 16 ms | 61 |
| find | search over the whole conversation | 4,488 ms |
| find | widgets in the conversation afterwards | 22,556 |
| idle, nothing running | CPU | 3.6 % |
| idle, a turn that looks busy | CPU | 16.1 % |

Reading the table:

- **Nothing that touches the column is cheap.** A fold toggle is a hundred layout passes; a tool
  card opening pushes the scroll position around a dozen times; a pointer moving down the column
  relays it 1,348 times. The animations reach 50–60 fps only because the heavy work runs between
  frames; the toggles' stalls (47–69 ms) and the jumps of the row below (13–68 px) are the frames
  it drops.
- **Streaming stalls for seconds.** The 5.7 s and 3.8 s maxima are the moments a big result lands
  (a 300-line output, a sub-agent's children): the open card is rebuilt whole, synchronously, in
  Python. CPU sits at 94 % of a core for the whole turn.
- **Reading while Claude writes is not possible.** Scrolled up, the content moved by 1,577 px over
  the turn in 30 separate jumps, the largest 358 px: the compensation runs a frame late every time a
  page below takes its real height, and the follow logic still sets the scroll position 33 times.
- **Mode, theme and session changes rebuild the world.** Verbose view: 15.7 s. Theme: 8.8 s of
  re-polishing 2,700 layouts and 2,800 resizes. Switching to a conversation that is already loaded:
  7 s and 374 new widgets, because the session view is torn down and rebuilt, and 75 MB of growth
  over ten switches.
- **History is built a page at a time, synchronously.** Dragging the scrollbar through 600 turns
  freezes the window for 400 ms at 61 of 81 steps. Find draws every page (22,556 widgets) before it
  can count.
- **The side panel redoes its tabs while Claude works.** The Tasks tab, keyed on `rt.rev`, relays
  itself 1,928 times during one turn and stalls the window for up to 576 ms; the Session tab takes
  683 ms to build; the Timeline tab with 300 steps blocks for 1.3 s.
- **Idle is not idle.** A session that looks busy costs 16 % of a core to animate three indicators.

(An earlier run without `linkify-it-py` installed rendered all Markdown as plain text and measured
lower; the run above has the client's full requirements. The find scenario also surfaced a crash in
the find bar's teardown: its refresh timer fires after the highlight widget is deleted.)

## 3. Why

The conversation is a tree of QWidgets ten levels deep (Transcript → PageBox → UnitBox → WorkFold →
Steps → ToolCard → Collapse → Body → Surface → Row → QLabel), eight widgets per item, laid out by
QLayouts, inside a QScrollArea. Everything that goes wrong follows from that:

1. **Layout is synchronous and global.** A streamed token, a hovered chevron, a one-second timer
   writing "12s" into a label, a fold growing by one pixel: each changes a `sizeHint`, and Qt walks
   the invalidation up through every ancestor layout and relays the whole column. The baseline
   counts about a hundred layout passes per fold toggle and over a hundred a second while
   streaming. Each pass is milliseconds of C++ plus the Python `sizeHint` overrides on the way.
2. **Expand and collapse animate `maximumHeight`** (`ui/collapse.py`), so every animation frame is a
   full relayout, a scrollbar-range change and a scroll compensation. The target height is taken
   from `sizeHint()` before the body has been laid out at its real width, so the animation ends at
   the wrong height and snaps. The baseline shows ~8 distinct frames in a 200 ms close.
3. **Prose is a QTextBrowser per paragraph** that starts 1px tall, lays itself out, feeds its
   height back through `documentSizeChanged` → `setFixedHeight` → relayout, and on every streamed
   token rebuilds its whole document with `setHtml`. Cost per token grows with the message.
4. **Refresh rebuilds.** A tool head clears and recreates its icon and meta widgets on every rev;
   an open tool body is rebuilt whole on every chunk of output; `Steps`, `Stack` and
   `clear_layout` pull widgets out with `setParent(None)` and put them back. Deleted widgets linger
   until `deleteLater` runs, so there are frames with both old and new on screen.
5. **Hover changes layout.** Chevrons and "Open in Files" buttons are shown with `setVisible`, so a
   pointer moving down the column relays it continuously; several heads also call
   `setStyleSheet` on hover, which re-polishes fonts and invalidates geometry.
6. **Scroll position is kept right by timers** (follow at 24 % a frame, a compensation one frame
   late when a far page takes its real height, three 160–450 ms timers re-placing the view) because
   heights are unknown until laid out. Every correction is one frame of the wrong position.
7. **The side panel refreshes hidden tabs** every 500 ms while Claude streams, and the Tasks and
   Session tabs key their "did anything change" test on `rt.rev`, which moves on every token, so
   they tear down and rebuild all their sections twice a second during a turn. The sidebar rebuilds
   its whole list on every `sessions.changed` by reparenting every row.
8. **Opening a conversation parses and models the whole history on the GUI thread**, then builds
   widgets for the live pages synchronously; scrolling into history builds a page of eight units at
   a time, several hundred milliseconds each.

Patching these one by one keeps the ceiling where it is: any design in which rows are widgets pays
a relayout for every change and a Python widget tree for every row that comes into view.

## 4. The design

_The specification that implementers work from is `qt/DESIGN.md`: structures, algorithms,
invariants, the frame pipeline, each interaction, the side panel, migration and tests. This section
is the summary._

### 4.1 One canvas, a scene of blocks, no QLayout

The conversation becomes a single `TranscriptCanvas(QAbstractScrollArea)` that owns a **Scene**: an
ordered list of **blocks**. A block is a plain Python object with a key, the `rev` it was built from,
a height, `measure(width)`, `paint(painter, rect, state)` and `hit(point)`. Nothing in the scene is
a QWidget except the rare **islands** (a permission prompt, a question form, a plan review) that
are real widgets the canvas positions.

Blocks are grouped the way the conversation is: a **Group** block holds a child list of its own
(a work fold holds its steps; a tool card holds its body; an agent card holds its
children). Groups nest at most four deep. Every list, at every level, keeps its children's heights in
a **Fenwick tree**, so the y of block *i* is a prefix sum in O(log n), the block under a y is a binary
descent in O(log n), and one height change is O(log n). The whole conversation is positioned by
integer arithmetic; there is no layout pass.

### 4.2 Paint only what shows; keep what was painted

`paintEvent` finds the first block that intersects the exposed rectangle (one descent) and paints
blocks until it passes the bottom, translating by the scroll offset. Prose is drawn with
`QTextDocument.documentLayout().draw()` clipped to the exposed part; decorations the document
cannot draw (code wells, inline-code pills, quote rules, table hairlines, rails) are painted with
QPainter from the layout's geometry, as `items.py` does today. The viewport is opaque and paints its
own background.

Blocks that are finished, not hovered, not selected and not lit by find keep a **cached pixmap** of
themselves (keyed by block, rev, width, theme, device pixel ratio; an LRU of about 48 MB). A screen
of finished history is a dozen blits. When the scroll position changes, the viewport **scrolls its
pixels** (`QWidget::scroll`) and only the newly exposed strip is painted. A streamed token
invalidates the rectangle of the block it landed in and nothing else; a spinner invalidates its
14×14 pixels.

### 4.3 Streaming costs the paragraph, not the message

One Markdown top-level block (a paragraph, a list, a code fence) is one `ProseBlock` with one
`QTextDocument`. A streamed delta touches the last block only: when the delta is plain text it is
appended with a `QTextCursor`; when it carries Markdown syntax the block's document is re-rendered
(`setHtml` of that block alone, ~0.3 ms). Finished blocks are never touched again. Thinking rows
stream the same way; the collapsed "peek" is the same document painted through a clip, no second
layout.

Model updates arrive coalesced (one signal per frame per session, as today). Reconciliation is
**incremental**: the units before the last user message are settled and cached, so each update
recomputes only the live turn, diffs its blocks by key and `rev`, and updates one or two blocks.

### 4.4 Folds are one number

A fold is a Group whose visible height is `reveal × content height`. Opening or closing runs a
`clock.Tween` on `reveal` with the spring curves in `theme.py`; each frame updates one Fenwick
entry, fixes the scroll anchor and repaints from the fold down. The content keeps being painted
through the clip while it shrinks, as on the web. The chevron turns on the same clock. There is no
second height to get wrong: the content's height is the sum of its children's measured heights.

### 4.5 Scroll anchoring is exact, and there are no timers

Every layout change goes through one function that (1) picks the anchor — the bottom while the
reader follows, otherwise the block under the viewport's top edge and its offset — (2) applies the
height changes, (3) sets the scroll value so the anchor stays where it was, and (4) repaints. All in
the same frame, before anything is drawn. Following new output is a short eased animation of the
scroll value on the shared clock; "Latest" appears when the reader is more than a screen's distance
from the live end. The remembered position of a conversation is an anchor (block key, offset), so
coming back lands exactly, even after far blocks have taken their real heights.

### 4.6 Virtualisation per block, with a budget and a calibrated estimate

Blocks farther than three screens from the viewport are **cold**: no document, no pixmap, an
estimated height. The estimate is not a constant: it is computed from the text (characters per
line at the current width and font, code blocks by line count, fixed heights for heads and rows)
and **calibrated** against the blocks that have been measured, so the scrollbar's proportions are
right from the start and improve as the conversation is read. Cold blocks become warm through a
queue ordered by distance to the viewport, drained with a time budget (about 4 ms a frame) so a
fling stays smooth; each materialisation applies its height delta through the anchoring function,
so what the reader is looking at never moves. Measured heights are kept per width, so a block that
goes cold and comes back has the same height and nothing shifts. Documents and pixmaps of far
blocks are evicted by LRU; the Python blocks themselves are small and stay.

### 4.7 Interaction without widgets

Hit testing descends the groups to the block, and the block names a **target** (a fold head, a
chevron, a link, a path, a copy button, a "Show all" control). Hover is a pair of rectangles
repainted; the cursor follows the target. Click activates the target. Keyboard focus is an index
over the focusable targets on screen, drawn as a ring; Tab moves it, Enter and Space activate, Home,
End, PageUp and PageDown scroll. Text selection is a range of (block, position) pairs painted
through each block's document and copied as one text. Links use `anchorAt`; tooltips show after
the pointer rests. Find lights matches through each prose block's extra selections, as now, and the
current match is revealed through the anchoring function, not a pin timer.

### 4.8 Islands

The permission prompt, the live question form and the plan review stay the QWidgets they are. An
`IslandBlock` owns one, measures it at the column width, and the canvas calls `setGeometry` on it
whenever its y changes. They scroll with the pixels, keep native focus and input, and there are at
most one or two on screen. The "Latest" pill and the find bar are children of the canvas, not of the
viewport, so scrolling the pixels does not move them.

### 4.9 Sessions are scenes

Each session's Scene (blocks, fold state, anchor, caches) is retained while the app runs (an LRU of
eight). Switching sessions swaps the Scene the canvas draws: one repaint. A hidden session's Scene
is not reconciled on every update; it is marked stale and brought up to date when shown, which costs
the live turn only.

### 4.10 Opening without freezing

The history snapshot is modelled in slices (a few hundred messages per event-loop turn) behind the
loading skeleton, so the window stays responsive during the longest load; the first paint waits
only for the blocks of the last screen to be measured. The snapshot is one message parsed on the GUI
thread (`orjson` from requirements.txt, else the standard library; a parsing thread would wait on
the GIL all the same), so its parse is the floor of the longest turn: 5.6 ms per 1.1 MB with the
standard library, 38 ms for 600 heavy turns.

### 4.11 One clock

All breathing marks, spinners, rails, elapsed counters, the scroll easing and the composer's springs
and every other animation in the window share one timer (`canvas/clock.py`) that runs only while
something live is on screen, at 20 Hz for indicators and 60 Hz during an animation. The widget
indicators (the sidebar's row glyph, the status pill's dot, the working mark, spinners, the to-do
box, the dispatch bar) are `ui/bits.Indicator`s: on the clock only while shown and live, so a hidden
tab or a scrolled-away row costs nothing. (The widgets' other timers are not animations: debounces,
toasts, polling, the terminal's cursor blink.) A tick repaints the small rectangles that changed: a
live fold or the working line repaints its mark, and its whole line only when the elapsed figure
moves on a second; nothing is relaid out.

### 4.12 The side panel and the sidebar

- Only the **visible** tab refreshes; hidden tabs mark themselves stale and refresh when shown.
- The Tasks, Session and Timeline tabs **reconcile** rows by id (update in place, insert, remove)
  instead of tearing down their sections, and their change keys no longer include `rt.rev`.
- The Changes tab diffs the file list by path and updates file cards in place; file cards paint
  their hover actions instead of showing and hiding widgets.
- Panel disclosure (`Collapse`) measures its body at the real width before animating and runs on the
  shared clock. It animates `maximumHeight`, so the panel relays out each frame: the Tasks tab's
  workflow card measures 77 layout requests and 1,570 paints per toggle and a longest turn of 8 ms
  (bench `tasks_fold_toggle`), under a frame; the conversation's folds are one number instead (§4.4).
- The sidebar diffs its ordered list of rows and moves only the rows that moved; it no longer
  reparents every row on every `sessions.changed`.
- Nothing calls `setStyleSheet` or `setVisible` from a hover or a refresh. Colours and fonts are set
  with the painter, `setFont` and `setPalette`.

### 4.13 What stays

`lib/` (the transcript model, units, tool metadata, formatting), `markdown.py` (Markdown → HTML,
Pygments), `theme.py`, `icons.py`, `store.py`, `rpc.py`, the composer, the dialogs, the Files tree
and the terminal (both already painted), and the decoration painters in `items.py`. The public
surface of `Transcript` (`set_empty`, `open_find`, `scroll_to_bottom`, `reveal_item`,
`find_entries`, `units`, `set_scrolled_callback`) is kept so `session_view.py` and `find_bar.py`
change little; tests address blocks by item id instead of widgets by class.

## 5. Targets, and what is measured

Same rig, same scenarios, same machine class (software rasteriser), 600 heavy / 2,000 mixed turns
where the row says so (the 2.10.0 measures), else the 120 heavy / 200 mixed turns of
`test_perf_budget.py` on the current tree (a shared machine: the longest turns vary by ±50 % from
run to run, the medians and counts do not):

| scenario | target | measured |
|---|---|---|
| fold / thinking / tool toggle | 0 layout passes; ≤ 1 scroll adjustment; ≥ 55 fps; no flash; the row below moves only by the animated amount | 0–0.5 layouts; 58–62 fps; 0 flashes; stall max 10 ms. Scroll sets 0 (thinking) to 12 (a card at the live end: the view keeps the end in sight as the card grows, which is the easing, not a correction); the row below moves ≤ 23 px beyond the animation on heavy cards whose children exceed the 14 ms pre-measure budget |
| streaming (follow and scrolled up) | stall p95 ≤ 8 ms, max ≤ 24 ms; 0 layout passes; scrolled up: 0 px of unexpected shift | 120 turns: follow p95 3.5 ms, max 15–24 ms (one turn over 16 ms in a 3,700-turn run; 34–39 ms when the bench opens a conversation cold just before), 0 layouts, CPU 20 %; scrolled up: 0 px, p95 1.3 ms. 600 turns (2.10.0): p95 4.6 ms, max 21 ms |
| wheel fling | stall p95 ≤ 8 ms; ≤ 1 frame in 100 over 16 ms; blank fraction ≤ 0.05 | 120 turns: p95 6.7–8.6 ms, max 11–35 ms, 0 px unexpected shift (the blank fraction of 0.27 is the gaps between rows, which the canvas paints in the exact canvas colour; the widget transcript's rows were never exactly it) |
| scrollbar drag (a screen a step, 81 steps) | step p95 ≤ 33 ms; max ≤ 100 ms; ≤ 20 placeholder paints | p95 30–34 ms, max 51–124 ms (the one heavy row measured past the step's 8 ms deadline), 12–19 placeholder paints filled by the drain, 0 widgets. Every row is new on each step, so a step is a frame and a half or two; the budget is what it is |
| hover sweep, selection drag, typing | 0 layout passes in the conversation; 0 px of shift; a hover repaints its targets, a drag the rows it changed | 0 and 0; hover 7.4 kpx per pointer move (was 8.6 kpx with whole blocks), selection drag 24–26 kpx per move and p95 1 ms (was 203 kpx, the whole viewport, and 4.4 ms); typing stall p95 1.2 ms |
| open (120 heavy / 200 mixed; 600 / 2,000) | first paint ≤ 150 ms after the snapshot is modelled; the longest turn while modelling ≤ 50 ms at 120 / 200 turns, ≤ 75 ms at 600 / 2,000 (the snapshot is one message and its parse is the floor: 38 ms for 600 heavy turns with the standard library) | 120 / 200: longest turn 43–60 ms / 26–31 ms, first paint 7–11 ms; 600 / 2,000: 60 ms / 69 ms, first rows 126 / 424 ms, first paint 7 / 13 ms |
| switch session | ≤ 30 ms to first paint; 0 widgets made for a retained session | 57 ms (the sidebar's rows move, nothing is made or re-parented), 1 widget, 0 MB growth over 10 switches. 2.12: 16 ms, 0 widgets at 120/200 turns — the one widget was the closed side panel's tab, rebuilt for each session switched to; a closed panel now follows the session when the window is idle |
| window resize (1480 → 1000 → 1480 px in 26 steps) | step p95 ≤ 33 ms (two frames); no step over 50 ms; 0 layout passes in the conversation, 0 flashes | — (guarded since 2.12: p95 29 ms, max 29 ms, 0 / 0 / 0 at 120 turns) |
| find over the whole conversation | ≤ 100 ms to count the matches; 0 widgets | — (guarded since 2.12: 16 ms at 200 turns) |
| view mode cycle, theme switch | ≤ 100 ms to first paint | view mode 106 ms; theme 124–146 ms with the Changes panel open (was 3.5–4 s): the theme is a palette now, not a stylesheet — the switch swaps the application palette, re-applies the widgets' tints, polishes the ~100 native fields and scrollbars again and repaints; what remains is the Changes tab's hundred file rows laying out once each, and the conversation's own repaint (7 ms) |
| panel open on any tab while streaming | widgets made per second = 0 on hidden tabs; ≤ 1 refresh a second on the visible one | 0 widgets/s on every tab, stall p95 4–5 ms (the Tasks tab updates its rows in place; the Changes tab rebuilds a diff only when an edit landed) |
| idle with a busy-looking session (a real event loop) | ≤ 3 % of one core | 2.3–3.2 % (5.2 % before the indicators moved onto the clock, each on its own 40–60 ms timer); quiet 0.0–0.6 % |
| the side panel | a seam drag step p95 ≤ 45 ms; a tab's first show ≤ 66 ms; a Tasks fold's longest turn ≤ 16 ms | seam p95 39–43 ms, 0 layout passes in the conversation; tabs' first show 13–22 ms (the terminal 15 ms); the workflow card's `Collapse` 77 layout requests, 1,520 paints and a longest turn of 8–10 ms per toggle |
| Timeline with 300 steps | — | first turn 14 ms, stall max 21 ms, 1 widget (was 1,749 ms and 301 widgets): a list on the engine |
| a lead's helpers ticking (HELPERS.md: eight helpers, their state ten times a second) | the bar repaints alone: 0 paints of the conversation, the sidebar, the composer and the header; 0 layout passes outside the bar; ≤ 3 ms per update | lead idle: 1.9 ms per update (max 2.8), 0 / 0 / 0 / 0 paints, layout requests on the bar only, 0 stalls; the same while a reply streams: 1.8 ms per update, 0 stalls over 20 ms; the Changes tab in front updates its helper sections, behind another tab it does nothing. The bar has no QLayout and paints its own background; the store re-emits the sessions list only when a field the list shows changed (`tests/stress/s_helpers.py`; guarded by `tests/test_helpers.py`) |
| a workflow of 60 agents reporting ten times a second, the Tasks tab in front | the panel follows in place: no card rebuilt for a progress tick; ≤ 3 ms per update on the main thread; a hidden tab does nothing | 1.4 ms per update (max 7); 118 widgets made over the run (the 60 rows as their agents start, nothing else); stalls only when a batch of agents arrives (≤ 120 ms, the new rows laid out); the Files tab in front or the panel closed: 1.3 ms, 0 paints, 0 layouts (`tests/stress/s_workflow.py`; before: every tick rebuilt every card, 244 ms max, stalls to 369 ms) |
| a dispatch job of 40 repositories, its runs reporting ten times a second (DISPATCH.md) | the hidden page does nothing; the board touches its one card and no layout; the job view patches the rows that changed; the sidebar does not rebuild for a run's progress | board in front: 3.3 ms per update (was 5.0; paints halved, no card re-laid for a progress tick), the sidebar paints its badge when it changes (was 6 paints per update); the job view in front: 9.4 ms per update (was 11; some 19 rows rebuilt and painted per tick under this load); another page in front: 0.8 ms (was 10.3: both views refreshed while hidden). Found on the way: a rebuilt row kept the pixmap cached of its old state and asked for no repaint, so the table showed states long gone, and none of the engine's lists followed a theme switch (`tests/stress/s_dispatch.py`; guarded by `tests/test_dispatch.py`) |
| a lead's conversation: 960 messages with 60 helper notes and 180 helpers cards | fling with 0 stalls over 20 ms and no cold placeholders; a note measured under 1 ms | open 108 ms; fling top to bottom in 297 wheel steps, 0 stalls, 0 cold placeholders; a note measures in 0.5 ms; a pointer sweep over notes and cards 0 stalls |

**What is enforced.** `qt/tests/test_perf_budget.py` asserts these targets on every `pytest` run,
on a 120-turn heavy and a 200-turn mixed conversation, with headroom for a busy machine: costs at 1.5×
the target (stall p95 12 ms, stall max 36 ms streaming and 15 ms for a toggle, to-paint 150 ms, a
long task while modelling 75 ms, a resize step p95 50 ms, a switch 45 ms, find 150 ms), a 45 fps
floor for the 55 fps target, and every count that must be zero at zero (layout passes, flashes,
unexpected shift, widgets made). The bench runs in a process of its own. The normal run takes the quick set (opening, a fold, a streamed turn, a resize, the
switch, find, the theme, a pointer sweep; under a minute offscreen); `PILCROW_PERF=1` adds the card
toggle, typing, streaming while scrolled up, the fling, the scrollbar drag (p95 50 ms, max 150 ms,
30 placeholders), a selection drag (60 kpx a move), the view-mode cycle, the panel's tabs and seam
(a first show 100 ms, a seam step p95 70 ms), a Tasks fold (25 ms) and the idle busy look (4.5 %).

## 6. Running the benchmarks

```
pip install -r qt/requirements.txt
python qt/tests/bench/run.py --heavy 600 --mixed 2000 --out test/out/bench.json
python qt/tests/bench/run.py --only fold_toggle,streaming_follow      # a subset
```

`qt/tests/bench/rig.py` holds the instruments, `scenarios.py` the scenarios (one function each,
written against behaviour so they run on any implementation of the conversation view), `run.py`
the driver. The JSON it writes is what the tables above are made from.

## 7. 2.10.0 against 2.9.8, like for like

`report.py qt/tests/bench/baseline-2.9.8.json qt/tests/bench/canvas-2.10.0.json`. Both runs on the rig as
it stands: the flicker count requires the same content back in the same place (content moving past a
gap is motion, not flicker), the scrolled-up streaming scenario scrolls up after the turn has started
(sending takes the reader to their own message, as it should), and the canvas' placeholder paints are
counted. The 2.9.8 toggles were re-measured after a rig fix (its rows are clicked on the head widget).

| scenario | measure | 2.9.8 | canvas 2.10.0 |
|---|---|---|---|
| open_cold | mixed 2000 turns: rows on screen | 968 ms | 423 ms |
| open_cold | mixed: widgets under the content | 1076 | 0 |
| open_cold | heavy 600 turns: rows on screen | 632 ms | 255 ms |
| open_cold | heavy: first paint | 31.1 ms | 3.2 ms |
| open_cold | heavy: RSS added | 25.2 MB | 35 MB |
| view_mode_cycle | → verbose: to first paint | 15041 ms | 106 ms |
| view_mode_cycle | → verbose: widgets made | 126 | 0 |
| theme_toggle | → light: to first paint | 7540 ms | 146 ms |
| theme_toggle | → dark: to first paint | — | 124 ms |
| fold_toggle | layout passes per toggle | 118 | 0.5 |
| fold_toggle | scroll sets per toggle | 5 | 7.5 |
| fold_toggle | stall max | 41.35 ms | 10.22 ms |
| fold_toggle | animation fps (min) | 47.3 | 58.1 |
| fold_toggle | row below: max jump | 12 px | 23 px |
| fold_toggle | flashes | 0 | 0 |
| thinking_toggle | layout passes per toggle | 48.8 | 0 |
| thinking_toggle | animation fps (min) | 37.8 | 62.4 |
| tool_toggle | layout passes per toggle | 112 | 0.2 |
| tool_toggle | stall max | 75.72 ms | 6.83 ms |
| tool_toggle | animation fps (min) | 34 | 62.4 |
| hover_sweep | layout passes (240 moves) | 271 | 0 |
| hover_sweep | stall p95 | 1.46 ms | 0.82 ms |
| streaming_follow | stall p95 | 30.57 ms | 4.58 ms |
| streaming_follow | stall max | 3018.67 ms | 21.46 ms |
| streaming_follow | stalls over 16 ms | 25 | 7 |
| streaming_follow | layout passes per second | 20 | 0 |
| streaming_follow | CPU | 93.5 % | 29.8 % |
| streaming_follow | flashes (filmed turn) | 0 | 0 |
| streaming_scrolled_up | unexpected shift | 165 px | 0 px |
| streaming_scrolled_up | scroll sets | 0 | 0 |
| streaming_scrolled_up | stall p95 | 3592.27 ms | 3.13 ms |
| typing | transcript layout passes | 0 | 0 |
| typing | stall p95 | 5.7 ms | 1.51 ms |
| resize_window | step p95 | 190.84 ms | 42.69 ms |
| resize_window | steps over 50 ms | 10 | 0 |
| switch_session | to first paint p50 | 7805.32 ms | 57.26 ms |
| switch_session | widgets made per switch | 374 | 1 |
| switch_session | RSS growth over 10 switches | 90.7 MB | 0 MB |
| panel_tabs | tasks tab: first turn | 31.5 ms | 25.1 ms |
| panel_tabs | changes tab: first turn | 40.6 ms | 22.3 ms |
| panel_tabs | seam drag step p95 | 153.72 ms | 63.06 ms |
| panel_while_streaming | tasks: panel widgets made / s | 6 | 0 |
| panel_while_streaming | changes: panel widgets made / s | 6 | 0 |
| panel_while_streaming | info: panel widgets made / s | 6 | 0 |
| panel_while_streaming | tasks: stall p95 | 134.21 ms | 5.23 ms |
| scroll_wheel | fling up: stall p95 | 10.55 ms | 5.63 ms |
| scroll_wheel | fling up: stall max | 194.08 ms | 15.52 ms |
| scroll_wheel | fling up: px/s | 6906 | 7366 |
| scroll_wheel | fling up: blank fraction | 0 | 0.254 |
| scroll_drag | step p95 | 363.85 ms | 40.63 ms |
| scroll_drag | steps over 16 ms (of 81) | 58 | 80 |
| find | search over the whole conversation | 4077 ms | 42 ms |
| find | widgets after | 21682 | 0 |
| idle | CPU, quiet | 3.3 % | 3.3 % |
| idle | CPU, busy-looking | 9.1 % | 7.2 % |

What moved the numbers: the view is one painted viewport (no layout passes, no widgets made, 0 MB of
growth over ten switches); every change holds the anchor, so nothing on screen moves that the person
did not move (0 px scrolled up); folds and cards open on one animated number at 58–62 fps; the parts
of the history that are not on screen are neither built nor measured (a 2,000-turn conversation
opens at its end with its units and nothing inside them; verbose opens every fold on screen only);
find reads the model (42 ms, no widgets); streaming appends to the paragraph's document. Around the
conversation (phase 4): hidden panel tabs do nothing and catch up when shown, the Tasks tab updates
rows in place, the Timeline is a list on the engine, the sidebar moves only the rows that moved, the
composer and the session head restyle only what changed, the snapshot of an opening conversation is
modelled in slices (the 10 MB more of resident memory on opening is the scene: every unit of the
history has a block, a key and an estimate from the first frame, which is what makes the scrollbar
honest and the find instant). The theme switch, last at 3.5–4 s, is now 124–146 ms with the Changes
panel open: the theme moved from a stylesheet to palettes (`theme.py`: the application palette carries
the tokens, `tint()` gives a widget its own roles and is re-applied on a switch, the stylesheet is
static and names colours only as `palette(role)`; buttons, cards, chips, hairlines and sheets are
painted), so a switch is a palette change and a repaint, and no widget is re-polished except the
native fields and scrollbars whose rules read the palette.
