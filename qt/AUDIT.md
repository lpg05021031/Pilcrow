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

48 KB in one paragraph (no blank line): 3.9 ms → 15 ms per token, 178 stalls, worst 57 ms. 21 KB of
Chinese: 3.9 → 7.3 ms. The cursor insert is cheap; QTextDocument re-lays the block it touched, which
is the whole paragraph. Rare in practice (a paragraph seldom passes 5 KB) and inherent to
QTextDocument; the only real fix is to keep a paragraph past a size in two blocks that paint as one
(no margin, no indent between them).

### F3 · Opening a long conversation shows its beginning for 0.4 s, then jumps to the end — medium

1500 turns through the mock: the first slice paints at 283 ms with the view at the top; fourteen
more frames show turn 1 and the slices piling up under it; at 745 ms the whole is loaded and the
view jumps to the end. It is by design (the view holds the top while slices arrive so the easing is
not retargeted), and it reads as a flash on every open of a long session. Fix: keep the skeleton
until `loaded`, or follow the growing end silently (no easing) while slices arrive.

### F4 · The first open of the side panel stalls 240 ms — medium

`store.set_ui(panel=True)` the first time in a session: 33 ms to the call's return and a 240 ms
stall while `RightPanel` and its tabs are built. Later toggles cost 8 ms; the sidebar 6–11 ms. Fix:
build the panel at idle once a session is open (hidden), or build each tab when it is first shown.

### F5 · Typing in the composer with a long draft costs 54 ms a keystroke — medium

After a 3000-line paste (149 ms, acceptable) every keystroke takes 54 ms: `_on_text_changed`
re-applies the line height to the whole document, detects the popup, autosizes and refreshes the
whole composer. A pasted log is a common draft. Fix: set the line height once as the document's
default block format, debounce the detection, and autosize from the document's size signal.

### F6 · An image wider than the column is clipped — medium

`![big](...)` of 2000 px in a 600 px column: 1400 px cut off on the right (the web has
`max-width: 100%`). Fix: after `setHtml`, clamp every `QTextImageFormat` width to the column and
scale its height.

### F7 · A fence inside a blockquote overflows the column by the quote's indent — low

The well is a `width="100%"` table; inside a quote that is 100 % of the frame, not of the room left
by the 14 px margin, so the well runs 14 px past the right edge and is clipped (the light theme loses
its right border). A fence in a list item sizes correctly. Fix: emit a pixel width for fences nested
in quotes (the render knows the width and the nesting from the tokens).

### F8 · Tabs in code: an 80 px tab stop, and a hanging indent that assumes four columns — low

Tab-indented code (Go, Makefiles): Qt's default tab stop puts text after one tab at 80 px (about
eleven columns) while the wrapped continuation is indented as if a tab were four columns, so
continuations land short of the code they belong to. Fix: set the document's tab stop distance to
four (or eight, the browser's) mono advances and compute the hanging indent from the same number.

### F9 · Carriage-return progress output shows every intermediate state — low

`npm install`-style output (`\r progress 10% \r progress 20% …`) renders as
"progress 0%progress 10%progress 20%…" on one line: the `\r` is dropped and nothing is overwritten.
Fix: in the ANSI parser, keep only what follows the last `\r` of a line, and honour `ESC[2K` /
`ESC[1A` the same minimal way.

### F10 · Hairlines at a fractional device pixel ratio smear over two rows — low

At 1.5× a 1 px rule lands on rows 240 and 241 at two different greys; at 2× it is crisp. Fix: snap
hairline y to the device grid (`round(y · dpr) / dpr`) or draw them with a cosmetic pen.

### F11 · An inline code span in a table cell can paint its pill past the cell — low

A span longer than its column (unbreakable by design) overhangs the cell by up to 16 px. The words
still read; only the pill crosses the rule. Accept, or let a span break when it is wider than the
column (the web's `white-space: break-spaces` does).

### F12 · Costs that scale with the conversation, noted but within budget

- A running card repaints on the clock at 31 paints/s, each the head's 30 px strip: 4 % of a core
  over idle. Acceptable; a card with a spinner in a long body would repaint the body.
- `highlight_lines` keeps 512 highlighted fences in an LRU keyed by the whole source: a session of
  big fences can pin tens of megabytes of strings. Cap the cache by size, not count.
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
| Device pixel ratios 1.25 and 1.5 | cached and direct paints of a block identical |
| A 200-character word, a 200-character URL, 200 Chinese characters, a 20-column table, emoji, entities, nested lists with tables | within the column |

## 4. Order of work

F1 first: it is the only finding that can freeze the window, and the fix (append instead of
remake) also removes most of F2's cost for the common case. F3 and F4 are the two things a person
sees on every long session (the flash, the hitch). F5 and F6 next. F7–F11 are an afternoon between
them.
