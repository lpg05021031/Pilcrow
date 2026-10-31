# The Qt client under attack: what stalls, flickers or breaks the picture

An adversarial review of the client as a whole, done with the strictest reading of three words:
**跑版** (the layout gives way), **閃爍** (the picture changes when nothing was asked of it) and
**卡頓** (the main thread misses frames). Nothing here is argued from the code alone: every
finding was provoked with a script and measured, and the scripts are in `tests/stress/` so the
numbers can be taken again after a fix.

The machine is this container, offscreen, one core shared with the mock server; the numbers are
relative. "Stall" is a gap of more than 20 ms between ticks of a 1 ms timer on the main thread (a
dropped frame at 60 Hz); "placeholder" is a block painted as faint bars because its document was
not ready in time.

## 1. How it was done

`tests/stress/core.py` hosts a transcript over a model in the store and feeds it the way the server
does (model push, dirty flush every 16 ms), with a probe counting stalls, paints and Markdown
renders. The scripts:

| Script | What it provokes |
|---|---|
| `s_stream.py` | A reply streamed token by token as one 48 KB paragraph, Chinese prose, a 1000-item list, a 500-row table, a 300- or 3000-line fence, a 40 KB thought, a realistic mixed reply; the same while the reader is scrolled up |
| `s_turn.py` | A live turn that already holds 50, 400 or 1500 tool steps and keeps streaming |
| `s_scroll.py` | A fling through 1500 turns; a fold of 1200 steps opened; a 60-step resize storm; theme and text-scale switches; a cold open of a 1500-turn session |
| `s_islands.py` | A permission card (a real widget in the conversation) through scrolls; a running agent card on the clock |
| `s_layout.py` | Inputs that can only break the layout: unbreakable code spans, 200-character words, a 2000 px image, a 20-column table, tabs, carriage returns, emoji, nested quotes and fences |
| `s_hidpi.py` | Device pixel ratios 1.25 and 1.5: cached against direct paints, hairlines |
| `s_misc.py` | A pointer sweep over tool heads and prose; the sidebar with 500 sessions; a 3000-line paste in the composer |
| `s_app.py`, `s_panel.py`, `s_open.py` | The whole window against the mock server: a reply streaming into the real session view, the panel and the sidebar toggled, a window resize storm, find, and the opening of a long session frame by frame |

## 2. Findings, worst first

### F1 · Streaming a long list, table or code block re-renders the whole block every flush — high

**Fixed.** A block being written grows in place (`canvas/grow.py`, DESIGN.md §7.3): a fence takes a line, a list an item, a table a row, as cursor edits into the document it has; the block is rendered from its source only when its shape changes, and when it is done it keeps the document it grew (the render's height, so nothing moves and nothing is rendered again). The reply is not parsed again for a token either: a grower says from the delta's lines whether its block stays one block. After, through the real path (model, flush, scene, paint): a token costs 4 ms in a 1000-item list and 5 ms in a 500-row table or a 300-line fence, flat from the first token to the last and with no stall over 20 ms in the list, where it was 222, 181 and 25 ms and climbing with hundreds of stalls.

| Shape | Per-token cost, first 10 % → last 10 % | Stalls > 20 ms | Worst |
|---|---|---|---|
| List, 1000 items | 6 ms → 222 ms | 796 | 284 ms |
| Table, 500 rows | 5 ms → 181 ms | 363 | 300 ms |
| Fence, 300 lines | 6 ms → 25 ms | 68 | 100 ms |
| Fence, 1200 lines (extrapolated and spot-checked) | → 360 ms | every flush | — |

A paragraph being written takes a plain delta by cursor (cheap), but inside a fence, a list or a
table `ProseBlock.can_append` refuses, and every flush renders the block from its source: a
markdown-it parse, Pygments over the whole fence, the table's column measuring, `setHtml` of
everything, the layout. The cost is linear in the block, so the block is quadratic. At 60 items or
60 lines it is invisible; at 200 the frame rate halves; at 500 the window freezes for the reply.
Replies of that shape are not exotic: a file list, a long table of results, a generated module.

Fix: grow the block instead of remaking it. A fence gains a line: highlight only the new line
(Pygments on the tail, whole-block once on close) and append one `<p>` with `QTextCursor.insertHtml`;
a list gains an item, a table a row: the same append. Until then, a stopgap: a render that took
*t* ms is not repeated for 4 *t* ms (the live block grows in steps, the thread stays free).

### F2 · One giant paragraph: the whole paragraph is laid out again per token — medium

**Fixed.** A paragraph past `PARA_LIMIT` (6000 characters) is kept in pieces cut at whitespace where the inline markup is balanced; the cuts depend only on the text before them, so a paragraph being written is cut where its finished self will be. The later pieces are continuations with no space above, and a token costs the layout of one piece.

48 KB in one paragraph (no blank line): 3.9 ms → 15 ms per token, 178 stalls, worst 57 ms. 21 KB of
Chinese: 3.9 → 7.3 ms. The cursor insert is cheap; QTextDocument re-lays the block it touched, which
is the whole paragraph. Rare in practice (a paragraph seldom passes 5 KB) and inherent to
QTextDocument; the only real fix is to keep a paragraph past a size in two blocks that paint as one
(no margin, no indent between them).

### F3 · Opening a long conversation shows its beginning for 0.4 s, then jumps to the end — medium

**Fixed.** The slices still go into the scene as they come, but the view paints the skeleton alone and offers no scrolling until the conversation is whole, then opens at its end in one step. After: of the frames sampled every 20 ms while 1500 turns load, none shows a word of the conversation before it is whole (`s_open.py`).

1500 turns through the mock: the first slice paints at 283 ms with the view at the top; fourteen
more frames show turn 1 and the slices piling up under it; at 745 ms the whole is loaded and the
view jumps to the end. It is by design (the view holds the top while slices arrive so the easing is
not retargeted), and it reads as a flash on every open of a long session. Fix: keep the skeleton
until `loaded`, or follow the growing end silently (no easing) while slices arrive.

### F4 · The first open of the side panel stalls 240 ms — medium

**Fixed.** One Pygments lexer per language instead of a lookup per file (70 ms of the stall), the file cards built twelve to an event-loop turn so the first ones paint before the rest exist, and the panel itself built hidden while the window is idle after a session opens, so the first toggle is a flip.

**Then the fill itself.** With a hundred edits the cards still came in at 31–70 ms a turn for twenty turns. Two causes: the cards were built by count (five a turn, 3 ms each) rather than by time, and every card added laid the whole list out twice, once in its stale height (every card squeezed) and once when the scroll host caught up: 50 000 move and resize events for a hundred cards. Now the cards are built for 4 ms a turn, the list is pinned to the height of its cards (`QLayout.SetMinAndMaxSize`: 3 000 events, none on a card already placed), the prebuilt panel is laid out and painted once off screen, and the lexers the session's edits need are compiled one per idle turn instead of by the first card (35 ms). After: the first open is 5 ms, a hundred cards fill in at 11–13 ms a turn (the first two 19 and 24), and the window never misses two frames in a row.

`store.set_ui(panel=True)` the first time in a session: 33 ms to the call's return and a 240 ms
stall while `RightPanel` and its tabs are built. Later toggles cost 8 ms; the sidebar 6–11 ms. Fix:
build the panel at idle once a session is open (hidden), or build each tab when it is first shown.

### F5 · Typing in the composer with a long draft costs 54 ms a keystroke — medium

**Fixed.** The field no longer sets the document's width on every key (which laid the whole draft out twice), fixes the line height on the blocks a change brought in rather than scanning them all, and reads its text once per change instead of seven times.

After a 3000-line paste (149 ms, acceptable) every keystroke takes 54 ms: `_on_text_changed`
re-applies the line height to the whole document, detects the popup, autosizes and refreshes the
whole composer. A pasted log is a common draft. Fix: set the line height once as the document's
default block format, debounce the detection, and autosize from the document's size signal.

### F6 · An image wider than the column is clipped — medium

**Fixed.** Once a document is laid out, an image wider than the room its line has (the column, less a quote's or a list's indent) is scaled down to that room, the web's `max-width: 100%` (`paint.fit_doc`); an image that fits, and a task list's box, are left alone. After: a 2000 px image in a 600 px column is 600 × 18, in a quote 586 wide, nothing past the column (`s_layout.py`).

`![big](...)` of 2000 px in a 600 px column: 1400 px cut off on the right (the web has
`max-width: 100%`). Fix: after `setHtml`, clamp every `QTextImageFormat` width to the column and
scale its height.

### F7 · A fence inside a blockquote overflows the column by the quote's indent — low

**Fixed.** The same pass gives a well nested in a quote or a list item the room left by its indent as a fixed width (Qt took its `100%` of the frame, not of the room). After: a fence in a quote, in a quote in a quote, in a list item, in a nested list and in a quote in a list all end at the column's edge; the top-level well still spans the column. (The note below that a fence in a list item sized correctly was wrong: it overflowed by the list's indent, 40 px, as well.)

The well is a `width="100%"` table; inside a quote that is 100 % of the frame, not of the room left
by the 14 px margin, so the well runs 14 px past the right edge and is clipped (the light theme loses
its right border). A fence in a list item sizes correctly. Fix: emit a pixel width for fences nested
in quotes (the render knows the width and the nesting from the tokens).

### F8 · Tabs in code: an 80 px tab stop, and a hanging indent that assumes four columns — low

**Fixed.** Every document's tab stop is four columns of the code face (`markdown.TAB_COLS`, set on every document the measurer makes, a grower's included), and the hanging indent counts a tab as four, so a continuation lands two columns past the code it belongs to. After: text after one tab sits at 4.00 columns (31 px, not 80), after two at 8.00; a plain mono document (command output) measures its tabs in its own face.

Tab-indented code (Go, Makefiles): Qt's default tab stop puts text after one tab at 80 px (about
eleven columns) while the wrapped continuation is indented as if a tab were four columns, so
continuations land short of the code they belong to. Fix: set the document's tab stop distance to
four (or eight, the browser's) mono advances and compute the hanging indent from the same number.

### F9 · Carriage-return progress output shows every intermediate state — low

**Fixed.** Command output is shown as a terminal would have shown it before its colours are read (`lib/ansi.py overwrite`): a carriage return goes back to the start of the line and what follows writes over it, `ESC[K`/`ESC[1K`/`ESC[2K` erase, `ESC[nA`/`ESC[nB` move a line up or down, `ESC[nG`/`ESC[nC`/`ESC[nD` move the column, and the colour of every character survives the rewrite (`\r\n` is a line end). Text with none of those controls comes back as it came (a 20 000-line log in 8 ms); a line nothing writes over is kept whole, so a log with one progress bar pays for the bar. After: the `npm install` bar reads `progress 100%`, and `ESC[2K ESC[1A line` three times over shows one `line`.

`npm install`-style output (`\r progress 10% \r progress 20% …`) renders as
"progress 0%progress 10%progress 20%…" on one line: the `\r` is dropped and nothing is overwritten.
Fix: in the ANSI parser, keep only what follows the last `\r` of a line, and honour `ESC[2K` /
`ESC[1A` the same minimal way.

### F10 · Hairlines at a fractional device pixel ratio smear over two rows — low

**Fixed.** Rules go on the device grid (`canvas/pixels.py`): a hairline is `floor(ratio)` device pixels (one at 1× and 1.5×, two at 2×, what a browser draws for `1px`) at the row the painter's transform maps it to, and a bordered rectangle (a well, a card, a box) has its edges snapped and a pen of that thickness, so its straight sides are crisp and only its corners anti-aliased. Used by the table rules, the quote rule, the wells, `rounded`, the Hairline node and widget, the divider, the rails of a thought, a tool and a fold, the composer's, the panel's and the dialogs' rules. After: at 1.25× and 1.5× every rule through a table is one row at the rule's colour, at 2× two (`s_hidpi.py`); cached and direct paints are still identical.

At 1.5× a 1 px rule lands on rows 240 and 241 at two different greys; at 2× it is crisp. Fix: snap
hairline y to the device grid (`round(y · dpr) / dpr`) or draw them with a cosmetic pen.

### F11 · An inline code span in a table cell can paint its pill past the cell — low

**Fixed.** The 16 px were not the pill. A table that does not fit even with every column at its least had every column squeezed in proportion, the one holding an `x` to 6 px; Qt lays a column out no narrower than its widest character and its padding (22 px), so the table ended 16 px past the plan. Now each column keeps that floor and the room above the floors is shared out, so the table ends where it was planned to, and the cells of a squeezed column lose the joiners that keep a code span whole, so the span breaks at its slashes and dots instead of wherever it must. A table being written tracks the same floor and starts over from its source the once a column runs out of room. After: the long span wraps to four lines inside its cell, the table and a 20-column one end at the column; a 40-column table is 914 px wide because its floors are, as in a browser.

A span longer than its column (unbreakable by design) overhangs the cell by up to 16 px. The words
still read; only the pill crosses the rule. Accept, or let a span break when it is wider than the
column (the web's `white-space: break-spaces` does).

### F12 · Costs that scale with the conversation, noted but within budget

- A running card repaints on the clock at 31 paints/s, each the head's 30 px strip: 4 % of a core
  over idle. Acceptable; a card with a spinner in a long body would repaint the body.
- `highlight_lines` keeps 512 highlighted fences in an LRU keyed by the whole source: a session of
  big fences can pin tens of megabytes of strings. Cap the cache by size, not count. **Fixed:** kept
  under a budget of source characters (4 MB) as well as a count, least recently used first out; a
  fence over a quarter of the budget is highlighted but not kept.
- A fence of 3000 lines being written cost 26 ms a token by its end: the rectangle of a grown well
  was read by walking every block of the document at every paint. **Fixed:** read from the head
  and the last line, so 3000 lines stream at 5.0 → 5.7 ms a token.
- A tool result of several megabytes is parsed from JSON on the main thread as it arrives: one stall
  the size of the message. Rare; the server could cap what it sends.

## 3. What held up, and the numbers

Every scenario below ran clean: no placeholders, the anchor kept, the picture pixel-identical where
it should not move.

| Scenario | Result |
|---|---|
| A reply streamed while the reader is scrolled up | the visible picture is pixel-identical before and after |
| A realistic mixed reply (headings, lists, fences, tables), 432 tokens | 5–6 ms a token, 4 stalls, worst 39 ms |
| A 40 KB thought streamed | 3.5 ms a token, flat, no stalls |
| A live turn with 1500 steps, streaming on | 4.8 ms a token, no stalls (the tail regroups cheaply) |
| A fling through 1500 turns, 120 notches | 1.9 s, 0 placeholders, 0 stalls, 127 paints |
| A fold of 1200 steps opened | 1 ms; the cards get estimates, exact heights follow in the drain |
| A 60-step resize storm with 600 turns open | 5 ms a step, worst 15 ms, 10 stalls ≤ 31 ms, anchor kept, nothing left at the old width |
| The window resized 40 times with the panel open (mock) | 1.8 ms a step, 6 stalls ≤ 24 ms, 0 placeholders |
| Theme switch / text scale with 600 turns open | 23 ms / 16 ms |
| A cold open of a 1500-turn session (no server) | 192 ms to the first paint, 52 blocks warm |
| A reply streaming into the real session view | header render 0.1 ms, composer refresh 0.16 ms, scene sync 0.9 ms per flush |
| The panel and the sidebar toggled (after the first) | 6–11 ms, anchor kept, 0 placeholders |
| A permission card through three scrolls | 0 px behind the painted content, immediately and a frame later |
| A pointer sweep of 588 moves over heads and prose | 14 paints, no stalls |
| The sidebar with 500 sessions | 0.9 ms a rebuild |
| Find opened on 400 turns and a word typed | 5 ms / < 1 ms |
| Folds and cards toggled without animation (reduced motion) | no stale pixels after the change |
| Device pixel ratios 1.25 and 1.5 | cached and direct paints of a block identical; after F10, every rule one device row |
| A 200-character word, a 200-character URL, 200 Chinese characters, a 20-column table, emoji, entities, nested lists with tables | within the column |
| After F6–F11: a 2000 px image, a fence in a quote or a list, tabs, a table too wide for its room, a progress bar in output | within the column; the bar's last state |
| A fence of 3000 lines, a table of 500 rows, a list of 1000 items streamed (after F1 and the well fix) | 5.0 → 5.7, 5.0 → 5.4, 4.3 → 4.0 ms a token |
| A hundred file cards filling the Changes tab (after F4's second round) | 11–13 ms a turn, 3 000 geometry events where there were 50 000 |

## 4. Order of work

F1 first: it is the only finding that can freeze the window, and the fix (append instead of
remake) also removes most of F2's cost for the common case. F3 and F4 are the two things a person
sees on every long session (the flash, the hitch). F5 and F6 next. F7–F11 are an afternoon between
them.

All twelve are fixed as of this writing; the numbers under each heading are after the fix, and the
scripts in `tests/stress/` reproduce them.
