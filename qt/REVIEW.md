# The Qt client reviewed for speed: algorithms, data structures, design

A review of the client as a CPU-only program whose whole job is to stay smooth: no GPU, one
Python thread that owns the window, a conversation that can be twenty thousand messages long and
a model that writes a hundred kilobytes of code in a single tool call. The standard is the one a
chief engineer would hold a native client to: every cost on the main thread must be bounded by what
changed, not by what exists; a frame is 16 ms; a token is a few milliseconds; idle is zero.

Nothing here is argued from the code alone. Each finding names the measurement that shows it, taken
in this container (offscreen, one core shared with a mock server; the numbers are relative). The
ones that matter most are regressions of *shape*, not of constant factors: places where a cost grows
with the size of the conversation or of a message when it should not. Those are what break a
program at scale, and they are cheap to fix now and expensive to fix after the fact.

## 0. The budget

| Event | Budget | Why |
|---|---|---|
| A frame while scrolling or animating | 16 ms, all in | 60 Hz |
| A streaming token, from socket to pixels | 3 ms | tokens arrive at 50–100 a second; the rest of the frame is the user's |
| Opening a conversation | first paint under 200 ms; the rest in slices that never hold the window | the one thing every session does |
| Idle | 0 timers, 0 paints | a laptop on battery |
| Memory per block of history | tens of kilobytes at most, documents only for what is near | a day's work is thousands of blocks |

## 1. Verdict

The engine's spine is right and measurably so: heights in a Fenwick tree (find 3 µs, update 2 µs at
20 000 blocks), an anchor read before every mutation and restored after, three grades of height
(an estimate at 25 µs, exact at ~3 ms), a pixmap cache that makes a full viewport of 19 blocks paint
in 0.8 ms (4.3 ms from the documents), growers that keep a token flat at 5 ms in a 3000-line fence,
a clock that stops (2 s of idle: 0 timer events, 0 paints), 29 MB of memory for 1500 turns with
documents for 34 blocks of 6074. That is a design that scales, and most of it needs no change.

Three things do not scale and should be fixed, in this order:

1. **Streaming a tool's input re-parses the whole input for every delta** (quadratic): a 114 KB
   `Write` costs 9.7 s of main-thread CPU as it streams, 6.6 ms per delta by the end.
2. **Every flush does work proportional to the whole conversation**, not to what changed: a token
   costs 0.9 ms at 1500 turns and 2.8 ms at 6000, almost all of it list and dict building over
   every unit in `scene.sync` and `reconcile`.
3. **A document is built through an HTML round trip** (tokens → HTML string → five regex passes →
   Qt's HTML parser → CSS → layout): 3.2 ms for a rich block of which perhaps 1.2 ms is the layout
   itself. The grades hide this from the eye, but it is the ceiling on how much the drain can warm
   per frame, and it is where the only remaining constant-factor win of 2× lies.

Everything else is a constant factor within budget, or a rare path, and is listed for completeness.

## 2. Findings, worst first

### R1 · A tool input that streams is parsed from the start on every delta — high, quadratic

**Fixed.** The scan that finds what is open in the JSON is kept as state on the tool item (`PartialScanner`) and advanced by each delta; the repair looks at the tail of the text only; and the parse runs once per flush of the view (`TranscriptModel.refresh_inputs`, called by the store before it emits) and at the block's end, not per delta. After: the same 114 KB `Write` costs 0.16 s of main-thread time over its whole stream where it cost 9.7 s; a delta is 4–7 µs, a flush's parse 0.02 → 0.20 ms as the input grows.

**Evidence.** `tests` on a 114 KB `Write` input fed to `TranscriptModel.push` as 2871 deltas of
40 bytes: 9.7 s in total, 0.35 ms per delta in the first tenth, 6.65 ms in the last. Alone,
`parse_partial_json` on 57 KB of unfinished JSON takes 3.4 ms; on the finished 115 KB, 0.18 ms
(the `json.loads` fast path).

**Cause.** `_on_stream` handles `input_json_delta` by appending to `item.input_json` and calling
`parse_partial_json(item.input_json)` every time. The parser walks every character of the whole
string in Python (`for c in src`) to find the open strings and brackets, runs three regexes over
the whole string, repairs it and parses it. Cost per delta is O(length so far); over a stream it is
O(n²). A big edit or a generated file is exactly the input that streams long, and the client spends
seconds of its one thread on it while the window is live. (The web client does the same; it has a
JIT and a second thread for painting, which is why it was never noticed there.)

**Fix.** Two independent halves, both small:

- Keep the scanner's state on the tool item (`in_string`, `escaped`, the bracket stack) and advance
  it over the delta only: O(delta) per token. The repair then needs only the tail of the string
  (the last key or separator), not a scan.
- Do not parse per delta at all. `item.input` is read by the view at most once per flush (16 ms):
  make it lazy (the parse happens when `input` is read and the raw string has changed since) or
  mark the item dirty and parse in `_flush_dirty`. `json.loads` of the repaired 115 KB is 0.2 ms;
  at 60 flushes a second that is 1 % of a core, where it is now 100 %.

**Expected.** Per delta: 6.6 ms → under 0.05 ms. Per flush: 0.2 ms at 100 KB, flat.

### R2 · A flush does work proportional to the whole conversation — medium-high, O(n) per token

**Fixed.** The settled prefix's keys and the index of every settled unit are kept across flushes and only ever extended; `reconcile` no longer builds a list of every child's key or walks the settled prefix, it compares from the settled index on (one check behind it that the contract held). The scene also remembers how many settled units its children were last synced against (`_synced_settled`): units that settled since (the turn that was live, or one a loading slice had cut in two) get one more sync, and a scene from the cache that meets the conversation loaded again (new items, the same user uuids, so the unit keys agree) starts from zero and syncs every unit, which `tests/test_scene_sync.py` pins down. After: `scene.sync` with nothing changed is 0.08 ms at 1500 turns and 0.25 ms at 6000 (was 0.59 and 3.46); a token through the flush is 0.29 and 0.62 ms (was 0.92 and 2.83). The remainder is two list concatenations of pointers, microseconds per thousand.

**Evidence.** `scene.sync` with nothing changed: 0.59 ms at 1500 turns (4501 items), 3.46 ms at 6000
(18 001 items). A token through `rt_changed` (model push, sync, measure, commit): 0.92 ms → 2.83 ms.
Profiled at 6000 turns, 30 flushes: `reconcile` 47 ms of its own time (1.6 ms each), `sync` 42 ms
(1.4 ms each); the measuring and painting of the token itself is under 0.3 ms.

**Cause.** `sync` rebuilds, every flush, the full `keys` list of the root's children, the
`unit_at` dict over every unit, and `reconcile` builds `have = [c.key for c in group.children]`
over every child and then loops over every key to decide that the first `settled` of them need
nothing. The settled *units* are cached (`settled_units`, `_settled_keys`), so the grouping is
already O(tail); the *lists and dicts* around them are not. At 6000 turns this is 3 ms a token,
60 times a second: a fifth of a core on bookkeeping. At 20 000 it is a dropped frame per token.

**Fix.** Make the settled prefix a persistent structure and let the tail be the only thing touched:

- `reconcile(group, keys, make, update, settled)`: do not build `have`; compare `keys[i]` with
  `group.children[i].key` from `settled` on, stop at the first difference, and loop only from
  `settled` (the prefix is by contract identical: that is what "settled" means).
- `sync`: keep `keys` and `unit_at` as members extended when `settled_count` grows, and slice the
  tail onto them (two short lists) instead of rebuilding them; `orphan_at` likewise.
- `_boundary` is already O(turn) (it scans back to the last user message); keep it so.

**Expected.** A flush independent of history: ~0.3 ms at any size.

### R3 · Documents are built through an HTML round trip — medium, constant factor 2–3×

**Fixed, and the estimate corrected.** `canvas/build.py` writes the document from markdown-it's tokens with a cursor: paragraphs, headings, quotes, lists with quiet markers and task boxes, code wells, tables with planned columns and streaming chunks, pills with their pads and joiners, links, images, rules; the margins follow the same first-child/last-child rule Qt's importer applied, decided in one pass over the tokens. Over a corpus of forty constructs it is pixel-identical to the renderer in height, words, block formats, lists and character formats (`tests/test_build.py`), and it differs where the renderer was wrong: a path in a code span is a link again (lost since the pill got its pads), an indented code block is a well, code in a list item keeps the text colour. The growers write an item's or a cell's words through it too, so no HTML is parsed anywhere in the conversation. Measured fairly, with both documents laid out: a rich block 2.92 → 2.56 ms (−13 %), a paragraph 0.43 → 0.36, a 60-line fence 8.4 → 6.7 (−20 %), a 40-item list 6.4 → 4.4 (−30 %), a 30-row table 6.8 → 5.4 (−20 %). Not the 2× estimated above: of the rich block's 2.6 ms, markdown-it's parse is 0.5, the build 0.9 and Qt's layout 1.1, and the layout was never the round trip's to save. The HTML renderer stays as `markdown_doc_html`, the reference the builder is tested against and what stands in should it ever fail.

**Evidence.** A 725-character rich block (heading, paragraph, list, table, fence, quote; 469 px):
`markdown_doc` 3.23 ms = markdown-it parse 0.54 + its HTML render 0.05 + the post-processing regex
passes 0.43 + `setHtml` 0.71 + layout 0.80 + glue 0.7. A 540-character plain paragraph: 0.32 ms by
the same road, 0.10 ms by `setPlainText` and a layout. The estimate for the same block costs 0.025 ms.

**Cause.** The renderer produces an HTML string, five regular-expression passes rewrite it (fences,
inline code, headings, tables, images) and Qt's HTML importer parses it and the stylesheet into
formats, which is the part Qt does slowly. Half of every exact measurement is spent getting the
content into the document rather than laying it out.

**Why it matters anyway.** The three grades mean the eye rarely waits for an exact height, but the
drain warms blocks under a time budget (8 ms a frame, 2 ms during a fling), so this cost decides how
many blocks are exact when a fling stops, how many placeholders flash, and how long a cold open of a
long session takes to settle (192 ms to the first paint of 1500 turns is mostly this).

**Fix.** Build the document from markdown-it's token stream with a `QTextCursor` (block formats,
char formats, lists, tables and images set directly), as the growers already do for a streaming
fence, list and table: no HTML string, no regex passes, no HTML parser, no stylesheet parse. The
decorations the view paints (pills, wells, rules) already key on formats, not on HTML. Measured in
the growers, cursor construction of a 60-line fence is well under a millisecond.

**Expected.** Exact measurement 3.2 ms → ~1.4 ms for a rich block; a paragraph 0.32 → 0.12 ms. The
drain warms twice the blocks per frame; the open of a long session settles in half the time. (What
came of it is above: 13–30 %, the layout being the larger half of what remains.)

### R4 · Opening a long conversation costs 0.1 ms per message, in slices — medium

**Evidence.** The harness pushes every message and syncs once: 539 ms for 4501 items, 1567 ms for
18 001 (0.09–0.12 ms per item). In the client the push comes in slices of 300 with a sync after
each, so a 20 000-message session is about 2 s of CPU in 67 turns of the event loop, with the
skeleton shown throughout (F3). None of it holds the window; all of it heats the laptop.

**Cause.** Half is the model (a dict per message turned into dataclasses, the stream events replayed),
half is `sync` after each slice, which per R2 is O(items so far): the open is O(n²/300).

**Fix.** R2 makes each slice's sync O(slice). The model's half is inherent to replaying the history
and is fine; if it ever matters, the server can send the settled history as items rather than as the
raw stream, and the client can skip replaying stream events for a turn that is finished.

**Expected.** 2 s → under 1 s for 20 000 messages, all of it in the model.

### R5 · Fenwick insert, remove and truncate rebuild the whole tree — low, rare

**Evidence.** `Fenwick(20 000)`: `find` 3 µs, `set` 2 µs, `append` 11 µs (all O(log n)), but
`insert` in the middle 3.1 ms, and `truncate` and `remove` the same, because they call `_build()`.

**Cause.** The simple implementation. Today a root with 6000 children truncates only when the live
turn changes shape (a flat becomes a fold), so this is a millisecond a few times per turn, not per
token; inside a fold the trees are small.

**Fix.** `truncate(k)` is O(1): node `i` of a Fenwick tree covers values below `i`, so dropping the
suffix of `_vals` and of `_tree` is the whole operation. `insert`/`remove` at `i` need the tree
rebuilt from `i` on only (nodes below `i` cover nothing at or after it).

### R6 · Find re-searches every visible document on every paint — low

**Evidence/cause.** While the find bar holds a needle, `ProseBlock.paint` calls
`find_ranges(self.doc, needle)` on each paint, and the needle also disqualifies the block from the
pixmap cache, so scrolling with the bar open draws every visible block from its document and
searches it each frame. At 19 visible blocks this is a few milliseconds a frame: within budget, but
work done 60 times for an answer that changes once.

**Fix.** Cache the ranges per `(doc revision, needle)` on the block; paint the highlight over the
cached pixmap instead of bypassing the cache.

### R7 · Select all over a long conversation builds documents for the cold blocks it crosses — low

**Evidence.** Select all and `selected_text` over 1500 turns: 26 ms for 535 000 characters, and the
number of documents went from 34 to 61. Acceptable; noted because a 20 000-message selection would
be 300 ms and a few hundred documents the drain then has to cool.

**Fix.** Blocks whose text is their source (prose, thoughts, user messages) can answer
`selected_text(None, None)` from the source without a document; only partial selections need one.

### R8 · A large message is decoded on the main thread — low, bounded by the server

**Evidence.** `json.loads` of a 2.2 MB tool result: 6.4 ms; the model's push of it: under 0.1 ms. A
20 MB result would be a 60 ms hitch.

**Cause.** `QWebSocket` delivers text frames on the GUI thread and `rpc._on_message` decodes there.
A worker thread would not help: CPython's `json` holds the GIL for the whole decode.

**Fix.** The server should cap what it sends (it already truncates what it shows). On the client,
`orjson` decodes 3–5× faster and is a one-line swap behind a fallback; not installed here.

### R9 · Cold start: 190 ms of imports before the first frame — info

**Evidence.** `python -X importtime` of the main window: 189 ms cumulative, of which `shiboken6`
72 ms and `PySide6.QtCore/QtGui/QtWidgets` ~30 ms (not ours), `pilcrow_qt` 18 ms, `json` 12 ms,
`re` 9 ms, `inspect` 9 ms (pulled by shiboken's feature support).

**Fix.** Nothing large is ours. Pygments (compiles a lexer's state machine, ~30 ms a language) is
already lazy per language; markdown-it is imported by `markdown.py` at startup and could be deferred
to the first prose block for a few milliseconds. Not worth more than that.

### R10 · The pixmap cache's budget is in bytes, not screens — info

At 1× a 900×1100 viewport is 4 MB, so 48 MB is twelve screens of cached blocks; at 2× it is six and
at 3× fewer than three, and a block taller than a quarter of the budget is never cached. Twelve
screens is generous and six is enough; three is where a fling at 3× would start redrawing from
documents (4.3 ms a frame instead of 0.8: still within budget). If a 3× display ever matters, size
the budget as *k* screens at the current ratio.

### R11 · Small things that are fine but are O(n) — info

- `item.text += delta` and `item.input_json += delta` copy the string each token: a 50 KB reply is
  ~25 ms of copying over its whole life. Fine.
- `TranscriptModel._seen` (message uuids) grows without bound: ~100 bytes a message. Fine.
- `Scene.dirty_from` and `_commit` call `Block.y()` (O(depth · log n)) per dirty block: dirty blocks
  are few. Fine.
- `_selection_for` is evaluated per visible block per paint while a selection exists, three `y()`
  each: 19 blocks, microseconds. Fine.

## 3. What is right, with its numbers

| Mechanism | Measured | Judgement |
|---|---|---|
| Fenwick heights, `find`/`set`/`append` | 3 / 2 / 11 µs at 20 000 | the right structure; keep |
| Anchor read before and restored after every mutation | pixel-identical pictures through streaming, resize, folds (AUDIT §3) | the invariant that makes everything else safe |
| Three grades of height, estimate first | estimate 25 µs vs exact ~3 ms; 1500 turns open with 34 documents | the design that hides R3 from the eye |
| Pixmap cache (48 MB, LRU by bytes, keyed on width/theme/dpr) | full viewport 0.8 ms cached, 4.3 ms from documents | right; see R10 for the only caveat |
| Growers for streaming fences, lists, tables, paragraphs | 5.0 → 5.7 ms a token over 3000 lines; 4–5 ms for lists and tables | the hardest problem, solved |
| Flush coalescing (16 ms timer) | at most 60 syncs a second whatever the token rate | right |
| Clock that stops; no polling anywhere | 2 s idle: 0 timer events, 0 paints | right |
| Dirty rectangles from the anchor down; `update(rect)` not `update()` | a token repaints its block | right |
| Memory: `__slots__` blocks, documents only near the viewport, far blocks cooled | +29 MB for 1500 turns, +56 MB for 6000 | right |
| Caches: fonts, metrics, 4000 `QStaticText`s, 4 MB of highlighted source, one lexer per language | hits are dictionary lookups | right |
| Deadline in `_remeasure_visible`, budgets in `_drain` (8 ms, 2 ms in a fling) | placeholders instead of stalls | right |
| Theme as a palette swap | 124–146 ms for 600 turns and an open panel | acceptable; widgets, not the engine |

## 3b. Smoothness, measured after the fixes

`tests/stress/s_smooth.py` records the times at which the view paints during the motions a person
makes, and which widgets of the real window repaint while a reply streams. A late frame is one more
than 25 ms after the one before.

| Motion | Frames | Pacing |
|---|---|---|
| A fling of 60 notches through 1500 turns | 61 frames over 995 ms | median 16.8 ms, p90 17.2, worst 19.6, 0 late; 0 placeholders, 0 stale |
| A fold of 600 turns opened | 16 frames over 240 ms | median 15.9, worst 16.5, 0 late; the anchor kept |
| The same fold closed | 11 frames over 160 ms | median 15.9, worst 16.9, 0 late |
| Zoom in / out (Ctrl+= / Ctrl+-) | one frame each | the call 7–10 ms, the frame 10 ms after it; the block under the top edge stays at the same pixel |
| Theme switch | one frame | 15 ms |
| The real window: a reply streaming (~100 words) | 13 viewport paints | 6 of them the streaming block's line only; **7 are the whole viewport, and those flushes also repaint the whole shell** (sidebar, panel, cards): see below |

**Open, found by the probe.** While a reply streams with the view following it, every flush that
moves the scroll repaints the whole viewport, not the strip that moved (the view's pixel scrolling
blits only for a person's scroll), and the same flushes repaint the whole shell: `Shell.place()`
ends with `self.update()` (the ground's shadow) and `place()` runs from `_on_ui`, which the stream
reaches; a full repaint of the shell is a paint event for every card, tab and file row under it,
166 under the panel during one short reply. Neither shows as flicker (Qt double-buffers), but both
are work the conversation does to widgets that did not change, and the standard here is that it
does none. Next: blit on a following scroll as on a person's; have `place()` repaint only the ground
around the cards, and only when a seam moved.

## 4. On threads, and why the answer is still "no"

Everything runs on the GUI thread. A reviewer's reflex is to move parsing and layout to a worker.
Here it would buy little: CPython holds the GIL through `json.loads`, through Pygments, through
markdown-it, and the Qt layout of a `QTextDocument` is a short C++ call between Python calls.
What a second thread can give is isolation from *I/O* and from *C++ work that releases the GIL*,
and there is almost none of either on the hot path. The right model for this program is the one it
has: one thread, every cost bounded by what changed (R1, R2), the heavy constant made cheaper (R3),
and the remaining heavy work sliced under a deadline (the drain, the open). If a day comes when the
layout itself is the bottleneck, the lever is a process, not a thread: a measuring sidecar that
returns heights, which the three-grade design would absorb without redesign.

## 5. Order of work

R1 first: it is quadratic, it is on the one thread, and it is an afternoon (state on the item, a
lazy `input`). R2 next: an hour of making two lists persistent, and the client becomes
history-size-independent per token, which is the property that lets it live for a day. R3 is the
one real project (a token-to-cursor builder), worth doing when the next round of work touches the
renderer; the growers are its template. R4 follows from R2. R5–R8 are small and can ride along
with whatever touches their files; R9–R11 are noted so nobody rediscovers them.
