# Helpers: a session that leads other sessions

A design for cross-session coordination in Pilcrow. One session (the **lead**) starts other sessions
(**helpers**), each in its own folder and worktree, gives each its own brief, waits for them, reads what
they did, steers them, and brings the results together. The person watches every helper as a real
session, answers its permissions in the control room, and reviews the changes across all of them in one
place. The reference point is the Herdr desktop app and its `hc` runtime, whose parent/child agents do
this in a terminal; §2 says what it does and §9 what this design does differently.

This is the second shape of multi-repository work in Pilcrow. **Dispatch** (DISPATCH.md) is *one brief,
many repositories*: the fan-out is a fixed script with a plan, a canary and a review. **Helpers** is *one
task, several repositories, each doing its part*: the fan-out is decided by a model, turn by turn. They
share the machinery underneath (worktree sessions, permission rules, the fan-in review, the report) and
the person meets them with the same glyphs, words and cards.

## Status

Design only. Nothing in this document is built yet. The phases are in §10; the first is the one to
start with.

## 1. What it is, in one flow

The person opens a session in `~/code` and says: *Add invoice download. The API needs
`GET /orders/{id}/invoice`; the web app needs a button on the order page. The API is in `~/code/api`,
the web app in `~/code/web`.*

The session reads both repositories, then calls `start` twice: a helper **api** in `~/code/api` with a
brief that fixes the response shape, and a helper **web** in `~/code/web` with `after: ["api"]`, so it
starts when **api** is done and its brief ends with where **api**'s work is. The lead ends its turn:
*Two helpers are working; I will continue when api finishes.* Its session shows a bar under the header
with two chips, `api · running 0:41` and `web · waiting for api`. In the sidebar the two helpers sit
under the lead. When **api** finishes, Pilcrow writes a **note** into the lead's conversation with
**api**'s handoff, **web** starts on its own, and the lead's next turn reads **api**'s diff where it
must. When **web** finishes, the lead reads its handoff, checks the two pieces agree, and tells the
person. The Changes panel of the lead shows two sections, one per helper, with **Commit all…**,
**Open pull requests…**, **Discard…**. The inbox has the record.

Everything the lead did is in its conversation as cards; everything a helper did is in the helper's
conversation; nothing was typed into a terminal and read back off a screen.

## 2. What Herdr does, and where the room is

Herdr (herdr.dev) is now two products. The original **Herdr TUI** is a terminal multiplexer with a socket
API (DISPATCH.md §0 and §7 are written against it). The current **Herdr desktop app** sits on
**herdr-core** (`hc`), a background runner for terminals and agents. Coordination in that world is a
parent agent driving children through a CLI:

- `hc run --bg --name api --cwd ~/code/api claude "<task> … Done: hc send $HC_TERMINAL_ID"` starts a
  child; children are terminals the parent started, they end with the parent unless
  `--outlive-parent`, and the app lists them under their parent.
- `hc wait api`, `hc wait --children all|any` block until a child stops working or until the next news;
  `hc read api --lines 100` reads its screen; `hc send api "<text>"` steers it (`--if idle|working`,
  `--wait`); `hc` types a note into the parent's terminal when a child blocks, ends or needs attention.
- A state is what the program reports through OSC 7501 (working, done, blocked); a child that reports
  nothing has no state. Templates are Markdown role files (`hc run reviewer "review src/x.rs"`).
- Machines over SSH take the same commands (`hc run --on vps --cwd ~/api …`, `hc exec`, `hc file`).

What that leaves to the person and to the model: every result is a screen read back as text, so the
parent's context fills with the children's output; a handoff is whatever the parent copies from one
screen into another prompt; there is no order between children beyond the parent's own sequencing; each
child asks its own permissions in its own terminal; nothing collects the children's changes for review,
commits them together or writes a record; and the parent's context is the only memory of the whole,
gone when its session goes. The strengths are real and this design keeps them: the model decides the
split; the primitives are few (start, wait, read, send); children are first-class, visible, attachable;
the parent is told when a child needs attention instead of polling.

Sources: herdr.dev (home, /core/, /guide/), herdrdev/herdr-core (README, docs/commands.md,
docs/templates.md), herdrdev/herdr-app (README, defaults/AGENTS.md).

## 3. Principles

- **A helper is a session.** Its own transcript, Changes panel, permission cards, worktree, terminal. The
  person can open it, type into it, take it over, keep it. Nothing is a second-class "sub-process".
- **Typed, bounded, structured.** The lead gets tools with schemas, not a terminal. Every result is a
  small, bounded shape (a state, a handoff, a diff cut at a budget) that says when it was cut and how to
  get more. The lead's context is spent on decisions, not on screens.
- **Told, not polling.** When a helper finishes, fails or stops, the lead gets a note and a turn. `wait`
  exists for the lead that has nothing else to do; it costs no tokens while it waits.
- **Handoff is a mechanism.** A helper is asked to end with a *Handoff*; a helper that starts `after`
  another receives that handoff and the path of the other's work, appended by the server, not retyped by
  the lead.
- **Permissions belong to the person.** A helper asking to run `npm install` is asking the person, in the
  control room, like every session. The lead is told, cannot answer, and is told not to try. One answer
  can cover every helper of the lead that asks the same thing.
- **Exact state.** A helper is *running* because the SDK says a turn is open, *needs action* because a
  permission is pending, *done* because the turn ended. No screen is read; no prompt is guessed.
- **The layout explains.** A helper's state is a glyph and a colour, the same the status pill and
  Dispatch use. The bar under the lead's header is the whole picture; the cards are the history; the
  sidebar is the tree. No modal explains anything.
- **Bounded.** A lead has caps on helpers, on parallel helpers, on minutes per helper and on cost; a cap
  reached is a state the lead and the person both see, not a surprise.
- **Depth one.** A helper cannot start helpers. The tree is a lead and its helpers, which is what a
  person can hold in their head and what a sidebar can draw.
- **Lightweight.** No new page, no new board, no new event channel. Helpers ride on `session.state`,
  `sessions.list` and the existing permission events; the client adds a bar, a group, four cards and a
  panel section.

## 4. Concepts

| Term | What it is |
|---|---|
| **Lead** | A session that has started at least one helper. Any session can become one; nothing is configured ahead. Marked in its meta (`lead: { since }`). |
| **Helper** | A session started by a lead: `helper: { leadId, name, brief, after, startedAt }` in its meta. Has a name unique within its lead (`[a-z0-9][a-z0-9-_]{0,31}`), a folder, usually a worktree, a brief, a state, a cost, and when done a handoff. |
| **Brief** | The helper's first message, written by the lead. Pilcrow prepends a short header (§5.8) that says what a helper is and how to end. |
| **Handoff** | The section titled *Handoff* at the end of a helper's last reply: what changed, interfaces others depend on, what was left undone, how to verify. Falls back to the last reply when the section is missing. Bounded to 4 000 characters. |
| **Note** | A message Pilcrow writes into the lead's conversation when a helper's state changes in a way the lead should know (done, failed, stopped, skipped, or removed by the person). Coalesced; suppressed while a `wait` covering that helper is in flight. |
| **Wait** | A tool call that blocks until helpers reach a state, with no tokens spent; returns a check-in at its timeout. |
| **Bar** | The row of helper chips under the lead's header; one glyph, name and short state per helper, the lead's rolled-up cost, and *Review*. |
| **Review** | The lead's Changes panel with one section per helper worktree and the finish actions (commit, pull requests, discard, keep), reusing Dispatch's fan-in. |
| **Rule** | *Allow for all helpers*: an answer to a permission request that covers every helper of this lead asking the same request key (DISPATCH.md §3.3), now and later. |

## 5. The server

### 5.1 Attaching the tools

Every session Pilcrow starts gets one more in-process MCP server, `helpers`, built with the Agent SDK's
`createSdkMcpServer` and passed in `options.mcpServers` (the SDK supports this; version 0.3.300 in use).
Its tools are deferred behind tool search like other MCP tools, so they cost nothing in a session that
never leads. The server's `instructions` carry §5.9. A session that is itself a helper, or is running
unattended for the scheduler, gets the server too but its `start` refuses (§5.7), so the tools' shape is
the same everywhere and the refusal explains itself. A setting (`helpers.enabled`, default on) removes
the server entirely.

Tool names as the model sees them: `mcp__helpers__start`, `mcp__helpers__wait`, `mcp__helpers__read`,
`mcp__helpers__send`, `mcp__helpers__stop`, `mcp__helpers__list`.

### 5.2 The tools

```
start  { name, cwd, brief,
         worktree?: boolean = true when cwd is a git repository,
         base?: string,                       branch to start the worktree from (default: the checkout's)
         after?: string[],                    helper names this one waits for
         mode?: 'default' | 'acceptEdits' | 'plan',   default: the lead's mode; never wider than it
         model?: string, effort?: string,
         maxMinutes?: number }                default and ceiling: config helpers.maxMinutes (30)
  → { name, id, state: 'running' | 'queued', cwd, worktree: { path, branch } | null,
      queuedBehind?: string[] }               or an error (§8)

wait   { names?: string[] = all, until?: 'done' | 'any' = 'done', timeoutSec?: number ≤ 600 = 300 }
  → { timedOut: boolean, helpers: [HelperState...] }
     'done': returns when every named helper is done, failed, stopped or skipped.
     'any':  returns on the first change of any named helper's state, including needs_action.
     A helper waiting on a permission does not end a 'done' wait: it is waiting on the person.

read   { name, what: 'summary' | 'last' | 'files' | 'diff' | 'steps' = 'summary',
         path?: string,                       diff: one file in full
         maxChars?: number ≤ 24000 = 8000 }
  → { name, state, text, truncated: boolean, more?: string }
     summary: the Handoff section, else the last reply.       last: the last reply.
     files:   changed files with +/−, from the worktree.       diff: unified diff, cut per file at the budget.
     steps:   a digest of the helper's turn (tool names, texts), newest last.

send   { name, text, now?: boolean = false }
  → { name, state, delivered: 'now' | 'held' }
     now: interrupt the helper's step and deliver when its turn ends (the composer's steer).

stop   { name }
  → { name, state: 'stopped' }

list   { }
  → { helpers: [HelperState...], caps: { parallel, total, maxMinutes, costUsd }, cost,
      known: [{ path, name, branch, dirty, allowed }] }   the folders a helper may start in without a card (§5.6)

HelperState = { name, id, cwd, branch, worktree, state, since, elapsedMs, cost, files, add, del,
                pending: number, lastLine, after: string[], reason?: string }
```

`wait`, `read` and `list` are answered by Pilcrow itself without a permission card: they change nothing.
`send` and `stop` likewise: they steer a session the person can see. `start` goes through a permission
card, *Start helper api in ~/code/api (worktree)*, and the card's *Always allow* is scoped to the
folder's repository, not to the tool (§5.6). So the first helper in a repository is approved by the
person once, and after that the lead may start helpers there freely. In `bypassPermissions` nothing
asks, as everywhere.

Results are plain text for the model (the MCP `content` text), with the same object as
`structuredContent` so the clients draw cards from data, not from parsing prose.

### 5.3 States

A helper's state is derived from its session, never stored separately:

| Session | Helper state | Glyph |
|---|---|---|
| created, dependencies not done | `queued` | hollow |
| worktree being created, process starting | `preparing` | accent, breathing |
| `running` | `running` | accent, breathing |
| `requires_action` | `needs_action` | warn |
| `idle` after at least one turn | `done` | ok |
| `error`, or the minutes cap | `failed` / `stopped (30 min)` | err / muted |
| interrupted by `stop` or by the person | `stopped` | muted |
| a dependency failed or was stopped | `skipped` | hollow |

A `done` helper that is sent more text becomes `running` again; `done` is *idle with a handoff*, not
*finished for ever*. The lead's bar and cards redraw from `session.state`, which for a lead carries
`helpers: [HelperState...]` (§5.11). Files, additions and deletions come from snapshots while a turn
runs (the files it touched) and from `git diff --shortstat` against the base once it ends, throttled to
once per two seconds.

### 5.4 Notes

When a helper reaches `done`, `failed`, `stopped` or `skipped`, or the person deletes it, Pilcrow writes
a note to the lead unless a `wait` that names the helper is in flight (the result carries the news).
A note is a user-role message with `_note: 'helper'` set, so both clients draw it as a quiet block, not
as something the person typed:

```
<helper-note name="api" state="done" elapsed="4m12s" cost="$0.31" files="6" add="212" del="18">
## Handoff
GET /orders/{id}/invoice added in src/routes/orders.ts; returns application/pdf …
</helper-note>
Read more with read(name: "api", what: "diff"). Helpers still running: web.
```

Notes within two seconds are one message. If the lead is busy, the note is held and delivered when its
turn ends, exactly as a typed message would be, but a held note is listed apart from the person's queued
messages and cannot be edited. If the lead's process has been closed for idleness, the note starts it
and is delivered: the lead must be able to react to a helper that finished at night. A setting
(`helpers.wakeLead`, default on) turns that off, in which case the notes wait for the next time the lead
is opened. Notes mention `needs_action` only in a `wait` result or in `list`: a permission is the
person's to answer, and a lead told about it would only repeat it.

### 5.5 Handoff and `after`

A helper's brief ends with the header's request to finish with a *Handoff* section. The server takes the
section from the helper's last reply (a heading matching `/^#+\s*handoff\b/im` to the end, else the whole
reply), bounded to 4 000 characters, and stores it on the helper's meta as `handoff` once the turn ends.

`start` with `after: ["api"]` creates the helper at once (its row appears, `queued`) and starts it when
every named helper is `done`, appending to its brief:

```
## From helpers you waited for
- api (done, 4m12s): worktree /home/you/.local/share/pilcrow/worktrees/api/api-3f2a1c on branch
  pilcrow/api-3f2a1c. Read the change there before you start.
  <its handoff>
```

If a dependency fails or is stopped, the dependent is `skipped` with the reason and the lead gets a
note; the lead can `start` it again without `after`. A dependency on an unknown name is an error at
`start`. Cycles are impossible: a helper can only wait for helpers that already exist.

### 5.6 Permissions and rules

A helper's permission request is the session's own, through `canUseTool`, to the control room and to the
helper's conversation, with `agentId`-style provenance added: the request carries `helper: { leadId,
name }`. The control room card says *asked by api, helper of Invoice download* and offers **Allow for
all helpers**, which writes a rule on the lead (`lead.rules[]`, key from `ruleKey(req, worktreePath)`
in DISPATCH.md §3.3, moved to a shared `server/rules.js` with Dispatch) and answers every helper of this
lead asking the same key, now and later. The rule's label is on the lead's bar tooltip and in the record.

The lead cannot answer a helper's permission: there is no tool for it, and §5.9 says so. A lead that is
itself waiting on a permission is `requires_action` as any session; its helpers continue.

**Which folders.** Nothing is configured ahead, and the lead is not given a list to pick from by
Pilcrow's authority; the person's approval is the access decision, taken where the person already takes
it. `start` accepts any folder that exists on this machine. Whether it asks depends on the folder:

| The folder is… | `start` |
|---|---|
| the lead's own project, or inside it | allowed without a card: the person opened this session here |
| a repository the person approved before (*Always allow* on an earlier card, remembered per repository root in the config as `helpers.allowedRoots`) | allowed without a card |
| anything else that exists | a permission card naming the folder; *Allow* once, *Always allow* for that repository root |
| a folder that does not exist, or the person's home directory itself | an error result |

`list` returns `known`: the lead's project, Pilcrow's recent projects (the twelve the person last opened
sessions in), and the repositories found one level under the lead's folder (Dispatch's `findRepos`),
each with its branch, whether its checkout is dirty, and whether it is `allowed` today. The lead is told
to look there before guessing a path, and that a path the person named in the conversation is the one
to use even when it is not in the list. Access to files inside a helper is Claude Code's own: the helper's
tools work in its folder, and an edit outside it asks the person as in any session. Another machine is
not a folder here; helpers run where the server runs.

A helper's `mode` defaults to the lead's and can be narrower (`acceptEdits` → `default`) but never
wider; `bypassPermissions` is inherited only when the lead has it. A helper's worktree is its own; a
helper started with `worktree: false` works in the folder given, and its chip says *in your checkout*.

### 5.7 Caps and refusals

| | Default | Where set |
|---|---|---|
| helpers per lead | 8 | `helpers.total` |
| running at once | `min(3, cores ÷ 2)` | `helpers.parallel` |
| minutes per helper | 30 (`start` may ask for less) | `helpers.maxMinutes` |
| cost per lead | none until set | `helpers.costUsd` |

A `start` beyond the parallel cap is `queued` and starts when a slot frees (the result says
`queuedBehind`). A `start` beyond the total cap, from a helper, from an unattended session, with an
unknown folder, a duplicate name or an unknown `after` name is an error result that says why and what
to do instead; nothing is created. A helper past its minutes is interrupted and `stopped (30 min)`; the
lead gets a note; the person can open it and continue by hand. A lead past its cost cap gets an error
on `start` and a note once; running helpers finish.

### 5.8 What a helper is told

Prepended to the brief, in the first message, not in the system prompt (so the helper's project memory
and CLAUDE.md apply unchanged):

> You are a helper started by another Pilcrow session (the lead) for one part of a larger task. Work
> only in this folder. The person can see this conversation and will answer your permission requests;
> the lead cannot. If you must ask something, ask the person and keep going where you can. When you
> finish, end your reply with a section titled **Handoff**: what you changed (paths), any interface
> other parts depend on, what you could not do, and how to verify. Keep it under 40 lines.

### 5.9 What the lead is told

The `helpers` server's `instructions`, surfaced to the model when the tools load:

> Helpers are separate Pilcrow sessions you start for parallel work in other folders or repositories,
> each with its own brief, worktree and permissions. Use them when a task spans repositories or when
> parts can run at the same time; for a quick question inside this folder use the Agent tool. One
> helper, one folder, one brief with a definition of done and how to verify; say what it must not
> touch. Use a folder the person named; otherwise `list` shows the folders known here and which ones
> start without asking. A folder outside them asks the person on a permission card. Use `after` for ordered work: the handoff is appended for you. After starting helpers, end your
> turn and say what you are waiting for; Pilcrow will tell you when a helper finishes, fails or stops.
> Use `wait` only when you have nothing else to do. A helper waiting on a permission is waiting on the
> person, not on you; do not poll and do not ask the person to approve on its behalf. Read a result
> with `read` (summary first; diff only where you must check). Report to the person by helper name.
> Caps: {parallel} running at once, {total} in all, {maxMinutes} minutes each.

### 5.10 Persistence and restart

A helper is a session with meta; a lead is a session with meta. The registry in `helpers.js` (lead →
helpers by name, in-flight waits, rules) is rebuilt from meta at start. After a server restart, live
sessions re-attach as today; a `wait` in flight died with the lead's process, so any helper that reached
a terminal state while nobody was told is noted once. Deleting the lead asks *Also delete its 3 helpers
and their worktrees?*; deleting a helper notes the lead. Closing the lead's process for idleness changes
nothing for helpers; they are reaped by their own idleness like any session, and their worktrees stay
until the review's finish action or the person's delete.

### 5.11 RPC and shapes

No new event channel. `session.state` for a lead carries `helpers: [HelperState...]` and `lead: { cost,
caps, rules }`; for a helper it carries `helper: { leadId, leadTitle, name }`. `sessions.list` entries
carry `helper: { leadId, name } | null`, which the sidebar uses to nest. The permission event carries
`helper` on the request. Clients act through:

```
helpers.list      { leadId }                                       → { helpers, caps, cost, rules }
helpers.allow     { leadId, requestId }                            → ok      (Allow for all helpers)
helpers.stop      { leadId, name }                                 → HelperState
helpers.review    { leadId }                                       → { runs, groups }   (DISPATCH.md §4.3 shape)
helpers.finish    { leadId, name?, action: 'commit'|'pr'|'discard'|'keep', message? } → ok
helpers.export    { leadId }                                       → markdown
```

`helpers.review` and `helpers.finish` are Dispatch's `review` and `finish` given the helpers' worktrees
instead of a job's runs: one code path, two callers.

## 6. The client

### 6.1 The lead's conversation

**The bar.** Under the header line, where the MCP notice and the error bar live, a row appears when the
session has helpers: one chip per helper (glyph, name, short state: `api · done +212 −18`,
`web · running 2:10`, `docs · needs you`, `cli · waiting for api`), then the rolled-up cost, then
**Review** when at least one helper is done with changes. Clicking a chip opens the helper; hovering
shows its last line and folder; a chip with a pending permission is warm and its tooltip is the request.
The bar is thirty pixels, never wraps (chips elide, a `+2` chip collects the rest), and is drawn from
`session.state` only. It is the whole picture; the person reading the lead's prose never has to scroll
to know where the helpers are.

**The cards.** The four tool calls draw as cards in the conversation, by the generic tool card's shape:

- `start`: *Started **api** in ~/code/api · worktree pilcrow/api-3f2a1c*, the brief under a toggle
  (as the Agent card shows its instructions), and the helper's **live** glyph, state and last line read
  from `session.state` while the helper runs, with **Open** on the right. A queued one says *waits for
  api*. The `start` card is the helper's row for as long as the conversation is open at it.
- `wait`: *Waited for api, web · 4:12* and one line per helper with its state and the first line of its
  handoff; *timed out, still running: web* when it did.
- `read`: *Read api's diff · 6 files* with the text folded under a toggle, drawn by the code block for
  diffs and the prose block for summaries.
- `send`: *Told web: "…"*; `stop`: *Stopped docs*.

**The notes.** A note draws as a quiet block on the left, the helper's glyph and name in the head
(`api · done · 4:12 · $0.31`), the handoff as prose under a fold that opens by default when short, and
**Open** at the right. It is visibly not the person's message: no bubble, no edit, no resend.

**The header.** The status pill of a lead says *leading* with the count while any helper runs and the
lead itself is idle, in the accent used for running, so the sidebar and the overview both show a lead
as live work.

### 6.2 The helper's conversation

The header gains a crumb, *helper of Invoice download*, in the plan colour the worktree crumb uses,
clicking back to the lead. The first user message shows the brief with the header folded (*from the
lead*). A message sent by the lead draws as a user message with a small *lead* tag; one typed by the
person draws as usual. The composer works; the person can steer a helper directly. The session menu
gains **Detach from lead**, which makes it an ordinary session (the lead gets a note).

### 6.3 The sidebar

A lead's helpers sit under it, indented, with their state dots, hidden from their own project groups
(as Dispatch runs are). The lead's row is the group head: its chevron folds the helpers; the group is
open while any helper is active and folds itself a beat after the last one ends, like a fold in the
conversation. The lead's dot takes the warmest state of itself and its helpers (needs action over
running over idle), so a collapsed lead still says what the tree needs.

### 6.4 The control room

A helper's **needs** card says *asked by api, helper of Invoice download* and offers **Allow for all
helpers** beside *Allow* and *Always allow*. A lead's card shows the bar's chips in miniature under its
title. The *working* list shows helpers as sessions, with the lead's name as their scope.

### 6.5 Reading the result

The lead's Changes panel (right panel, Changes tab) gains one section per helper with a worktree, headed
by the helper's chip and diffstat, holding the same file cards as the session's own changes. A row
above the sections carries the finish actions from Dispatch's footer: **Commit all…** (one message, a
commit in each worktree; or per helper from its section head), **Open pull requests…** (through `gh`,
off without it), **Keep** (leave the worktrees; nothing else), **Discard…** (confirmation naming the
helpers, the only modal). Identical hunks across helpers are grouped as in Dispatch's review (a shared
file changed the same way in two repositories is read once). **Review** on the bar opens this tab.

### 6.6 The inbox

When every helper of a lead has reached a terminal state and at least one changed files, the inbox gets
one item, *Invoice download: 2 helpers done*, with a row per helper (glyph, name, files, cost, the
first line of its handoff) and **Open the lead**; it is updated, not duplicated, if a helper is started
again. The record exports as Markdown with the handoffs in full.

### 6.7 The web client

The same surfaces with the same names: `HelpersBar`, the four card bodies under `components/tools/`,
`HelperNote` in the transcript, nesting in `Sidebar.tsx`, the control room card's provenance and button,
the Changes panel sections, the inbox card. Types in `lib/types.ts` (`HelperState`, `Lead`), the store
reading `helpers` off `session.state`. No new page.

### 6.8 What the eye gets, and what it does not

No frame drops: the bar is one row of painted chips, repainted when `session.state` changes; the cards
read live state and repaint themselves alone. No flicker: a chip's state change recolours a glyph; the
row never reflows while chips are added to the right. No jumping: notes arrive as blocks at the end of
the conversation like any message; a held note is listed with the queue, not inserted above it. No
modal in the way: the only confirmation is Discard. Nothing to configure before the first use: the
person approves the first `start` on a permission card they already know.

## 7. Flows

**Front end and back end.** As §1. The lead's `start` of **web** with `after: ["api"]` saves a turn and
the retyping of **api**'s handoff; the lead's one decision after **api** finishes is whether the
interface matches the brief it wrote, which it checks with `read(api, diff, path: "src/routes/orders.ts")`.

**A reviewer.** The lead finishes a change and starts a helper **review** in its own folder, in `plan`
mode, with a brief that says *read only; report findings with paths and lines*; `wait` for it inside
the same turn (it has nothing else to do), reads the summary, fixes what is real, and says what it
dismissed. The helper's transcript holds the review; the person can read it independently.

**The person steps in.** **docs** asks to run `npm run build`; the chip warms; the control room card
says who asked; the person clicks *Allow for all helpers* because **web** will ask too. Later the
person opens **docs**, sees it drifting, types *only the API page, not the whole site* into its
composer; the lead is not told and does not need to be. When **docs** finishes, the lead's note carries
the handoff the person's steer produced.

**Away.** The lead starts three helpers and is closed for idleness; the person leaves. Two finish at
night; their notes coalesce into one message that starts the lead, which reads both and ends its turn
with a summary; the third finishes in the morning; the lead's final turn reports; the inbox has the
record. Three turns of the lead, none of them spent waiting.

## 8. Failure and safety

| Case | Behaviour |
|---|---|
| `start` with a folder that does not exist, a duplicate name, an unknown `after`, or over the total cap | an error result that says why; nothing is created |
| `start` from a helper, or from an unattended session | an error result: *helpers cannot start helpers* / *this session runs unattended* |
| `start` over the parallel cap | `queued`; starts when a slot frees; the result and the chip say behind whom |
| a worktree cannot be created (not a repository, disk full) | an error result naming the fix (`worktree: false`, or free space); no process started |
| a helper asks a permission | `needs_action`; the chip warms; the control room card says who asked; the lead is not woken |
| several helpers ask the same permission | *Allow for all helpers* answers every helper of this lead asking the same key, now and later |
| a helper's process errors | `failed`; a note with the stderr tail's first lines; `read(last)` has the rest |
| a helper runs past its minutes | interrupted; `stopped (30 min)`; a note; Open to continue by hand |
| a dependency fails or is stopped | dependents `skipped` with the reason; a note; `start` again without `after` |
| the lead's `wait` reaches its timeout | a check-in result with every state; the lead calls again or does something else |
| the lead passes its cost cap | `start` errors; one note; running helpers finish |
| the person deletes a helper | a note to the lead; its chip goes; its worktree goes if asked |
| the person deletes the lead | a confirmation that names the helpers and offers to delete them and their worktrees |
| the server restarts | sessions re-attach; waits are gone with the lead's turn; terminal states nobody was told are noted once |
| the lead and a helper work in the same checkout (`worktree: false`) | allowed; the chip says *in your checkout*; nothing stops it |
| the API rate-limits | each session backs off as today; the bar shows them running |
| a lead in `bypassPermissions` | helpers inherit it; the bar's tooltip says so; nothing else changes |

## 9. Against Herdr, and against the Agent tool

| | Herdr desktop (`hc`) | Claude Code's Agent tool | Helpers |
|---|---|---|---|
| Primitive | a terminal: run, wait, read, send; text in, screen out | an in-process subagent | typed tools; structured results with budgets |
| Where it works | any folder, any machine over SSH | the same folder (or a worktree it enters) | any folder on this machine; a worktree by default |
| Visible to the person | a terminal under the parent; attachable | a card in the parent's conversation | a session: transcript, Changes, terminal, composer |
| State | what the program reports (OSC 7501); none if it does not | the parent's task events | the SDK's own events |
| Handoff | the parent reads a screen and retypes | the subagent's final text | a *Handoff* section; `after` appends it with the worktree path |
| Order | the parent's own sequencing | none | `after`, enforced; dependents skipped on failure, with the reason |
| Permissions | each child asks in its own terminal | through the parent's cards | the person's, in the control room; one answer for all helpers |
| Being told | notes typed into the parent's terminal | a notification when the task ends | a note that is a message, coalesced, held while busy, waking an idle lead |
| Fan-in | none | none | the Changes panel across helpers; commit, PRs, discard; grouped hunks; a record |
| After the parent ends | children end unless `--outlive-parent`; the parent's context was the record | gone | helpers are sessions; the lead's meta, the handoffs and the inbox item remain |
| Reuse | templates (Markdown roles) | agent definitions | playbooks' briefs usable as helper briefs (§12) |

The Agent tool stays the right thing for a quick question inside the folder; §5.9 says so to the lead.

## 10. Phases

1. **Lead and helpers.** `server/helpers.js` (the MCP server factory, the registry, states, `start` with
   worktrees and `after`, `wait`, `read`, `send`, `stop`, `list`, notes, caps, refusals), attachment in
   `sessions.js`, `helper` on `sessions.list` and `session.state`, `rules.js` shared with Dispatch,
   vitest against the fake sessions and real temporary repositories. Qt: the bar, the four cards, the
   note block, the helper crumb and *lead* tag, sidebar nesting, the control room's provenance and
   **Allow for all helpers**, the delete cascade. Mock server: scripted helpers.
2. **Fan-in and record.** Changes panel sections with finish actions through Dispatch's `review` and
   `finish`; the inbox item and export; **Detach from lead**; the web client's bar, cards, note,
   nesting, control room, panel sections and inbox card.
3. **Reach.** Briefs from playbooks (`start { template }`), per-lead cost caps in Settings, a lead
   started by the scheduler with helpers allowed (`allowHelpers` on the schedule), and whatever the
   first weeks of use ask for.

Estimated: server ≈ 450 lines plus 60 in `sessions.js`; Qt ≈ 400; web ≈ 350; mock ≈ 150; tests ≈ 500.

## 11. Testing

- Server (vitest, the fake sessions of `test/unit/dispatch.test.ts` and real temporary git repositories):
  `start` creating a worktree session with the header and meta; `after` appending the handoff and the
  path, skipping on failure; `wait` for `done` ignoring `needs_action`, for `any` returning on it, and
  the timeout check-in; notes coalescing within two seconds, held while the lead is busy, suppressed
  during a covering `wait`, waking a closed lead once; the handoff extraction with and without the
  heading; `read` budgets and `path`; caps and every refusal; *Allow for all helpers* answering a
  second helper later; delete cascades; the registry rebuilt after a restart with the missed notes.
- Client (Qt, against the mock): the bar's chips following `session.state`; the `start` card live and
  then settled; a note drawn as a note; the sidebar nesting and the lead's warmest dot; the control room
  card's provenance and button; the Changes panel sections and a Discard confirmation naming the
  helpers; the delete dialog's cascade.
- Smoothness: the probes on a lead with eight running helpers: the bar repaints alone; the conversation
  does not reflow when chips change.

## 12. Decisions taken here, open to change

- Every session has the tools; the first `start` in a repository asks on a permission card. The
  alternative, a per-session switch, was rejected as a thing to configure before the first use.
- Which folders a lead may use is decided by the person on that card, remembered per repository root, not
  by an allow-list configured ahead. The lead's own project is allowed because the person opened the
  session there. A list of allowed roots in Settings would be the same decision taken earlier; it is not
  offered until someone asks for it.
- Depth one. Helpers of helpers would need a tree in the sidebar and a model of a model's model.
- The lead cannot answer helpers' permissions, even routine ones. Herdr's parent does (`send-keys`); the
  risk of a model approving a model's `rm -rf` is not worth a saved click. If it is wanted later it is a
  rule the person writes, not a tool the lead holds.
- Notes wake a closed lead by default (`helpers.wakeLead`). The alternative leaves a finished helper
  unread until morning.
- `done` means idle with a handoff; a helper is not *finished* until the review's finish action or the
  person's delete. There is no `finished` state.
- Branch `pilcrow/<name>-<id6>` and the worktree root from `sessions.create`, unchanged.
- The Handoff is asked for in the brief header, not enforced; a helper that forgets gives its last reply.
- Templates: Dispatch's playbooks hold briefs with variables already; `start { template }` in phase 3
  reuses them instead of a second format.
