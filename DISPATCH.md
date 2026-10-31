# Dispatch: one job, many repositories

A design for running the same piece of work across a set of repositories at once, watching it as a
whole, and finishing it as a whole. The reference point is herdr and the tools built around it
(a terminal runtime that keeps coding agents in panes on a background server, marks each one working,
blocked or idle, and gives agents a CLI and socket API to start, prompt and wait on one another). Pilcrow already owns the parts that make the hard half of
this easy: a server that runs sessions through the Agent SDK, git worktrees per session, a scheduler
that runs sessions unattended and collects what they did into an inbox, snapshots of every file a
session touched, a control room that watches live sessions and answers their permission requests, and
a Qt client whose canvas engine draws thousands of rows at 60 Hz on a CPU. Dispatch is the job object
that ties them together, and the two screens that show it.

The companion design, HELPERS.md, is the other shape of multi-repository work: one session that leads
other sessions, each with a brief of its own, the split decided by a model turn by turn. The two share
the worktree sessions, the permission rules, the fan-in review and the record.

## Status

All three phases are built. The server side is `server/dispatch_core.js` (the state machine, the scan,
the plan, briefs, rules, waves, prepare and verify, review, finish, the report, playbooks, the
agent-written report) wired to the real sessions, git, snapshots and inbox in `server/dispatch.js`,
with the RPC in `server/main.js` (`workspace.scan`, `dispatch.*`) and the `dispatch.changed`,
`dispatch.playbooks` and `finished` events; `server/schedules.js` runs a schedule of kind `dispatch`
through a playbook (unattended: no canary, permissions declined, the report is the inbox item). Its
tests are `test/unit/dispatch.test.ts` (fake sessions and the bus; real temporary repositories and
worktrees for the scan, the plan, the review and the finishing; playbooks, the unattended job, the
written report).

The Qt client has the board, the job view (rows on the canvas engine, patched in place from the
deltas), the composer with the target picker (also rows on the canvas engine, so a workspace of
hundreds is one surface) and the plan as you type, playbooks (load one into the composer, save one from
the composer or a job, Run again, Schedule this), Allow for all on the board, in the job view and on the
control room's cards, the canary card with the forecast and the brief editable before the main wave,
the review foot with Commit / Open PRs / Keep / Discard, the written report under the brief, the report
as its own card in the inbox with the Markdown export, the schedule dialog's "A dispatch playbook"
choice, and the job's runs grouped under the job in the sidebar (`qt/pilcrow_qt/widgets/dispatch/`,
tests in `qt/tests/test_dispatch.py` against `test/mock/mock-server.mjs`, which models the engine so a
job can be driven end to end offscreen).

The web client has the same screens over the same RPC (`web/src/components/dispatch/`): the board,
the job view, the composer with playbooks and the plan, the Dispatch entry in the sidebar with its
badge, the report card in the inbox, and the schedule dialog's playbook choice.

Pull requests through `gh` are implemented on the server and not yet exercised against a live GitHub
remote. Not built: a lead agent that splits one task into per-repository briefs (the brief is yours),
and sessions on other machines (Pilcrow runs where its server runs).

## 0. What herdr does, and where the room is

This section is written against the Herdr TUI, the product at the time. The desktop app on herdr-core
(`hc`) that followed, with its parent and child agents, is covered in HELPERS.md §2.

herdr (herdr.dev, Apache-2.0, one Rust binary) is the runtime many people run their agents on, and it
is good at being that: panes survive the client closing or an SSH drop because a background server owns
them; the sidebar marks every pane *working*, *blocked* or *idle*; a CLI and a socket API let one agent
start another, prompt it, wait until it is blocked and read its screen (`herdr agent start | prompt
--wait | wait --until | read`, `events.subscribe`); `herdr worktree create` opens a worktree as a
workspace under its repository; saved SSH machines appear beside local work; plugins add sandboxes and
coordinators. The fan-out people do on top of it is a pattern, not a feature: *worktree-fleet* splits a
task into file-disjoint buckets, gives each a tab, a worktree and a branch, hands each a short contract
(paths owned, a verify command, push after every commit, an evidence block at the end), watches the
agents' state from a bash loop so no tokens are spent waiting, and merges the branches one by one from
the main pane; *herdr-projects* runs a coordinator agent that spawns one agent per task on its own
branch.

What that leaves to the person, in herdr's own words and its users': the state is a heuristic (a screen
manifest matches known approval prompts; an unfamiliar prompt reads as *idle*, so an agent can wait on a
question while the sidebar says nothing is wrong); a worktree isolates files only (ports, `.env`,
dependencies are the person's per-worktree chore); the work has no identity across panes (a fleet is
the set of panes on `fleet/*` branches, gone with them); nothing is planned before the fan-out, so a
bad brief runs twenty times; the same permission is answered twenty times, once per pane; results are
read pane by pane and merged by hand; nothing is measured per repository; and the docs name the poor
fit themselves: a team that wants a graphical diff and review board.

Pilcrow already owns the parts herdr does not have and cannot easily grow: the server runs sessions
through the Agent SDK and gets their state from the SDK's own events, not from a screen; every session
can have a worktree; the scheduler runs sessions unattended and collects what they did into an inbox;
snapshots keep every file a session touched; the control room watches live sessions and answers their
permission requests; the Qt client draws thousands of rows at 60 Hz on a CPU and shows diffs. Dispatch
keeps what herdr's users like (one place, every repository, live, scriptable) and gives the work an
identity with a lifecycle: a **job** with a brief and targets, which **plans** before it runs, runs each
target in its own **worktree** as a real session, lets a **canary** go first, answers a permission
**once for every repository** that asks it, **reviews** the result across repositories as one diff, and
**finishes** with one commit or one set of pull requests, with the cost and time of every repository
written down.

## 1. Principles

- **A job is a thing.** It has a name, a brief, targets, a state, a cost and a report. It survives the
  client closing and the server restarting. You can open it tomorrow.
- **Every run is a real session.** Full transcript, the same permission cards, the same Changes panel,
  the same ability to take over and continue by hand. Nothing is a second-class "headless" mode.
- **Nothing runs on a checkout a person is using.** Every run gets a worktree on a branch of its own
  (`pilcrow/<job>`), from the branch the person chose. A dispatch cannot dirty anybody's tree.
- **Plan first.** The fan-out is preceded by a dry run that costs no tokens: each target is checked and
  the brief is shown as each repository will receive it. A blocked target is explained, not skipped
  silently.
- **One goes first.** A canary wave of one (or a few) runs before the rest, and the person can read its
  result, change the brief, and let the others learn from it.
- **The layout explains.** A run's state is its colour and glyph, the same ones a session's status pill
  uses everywhere else; a job's progress is a bar whose segments are its runs' states; a blocked run
  says what it is waiting for in the words of the permission card. No modal explains anything.
- **Bounded.** A job has a cap on minutes per run, on parallel runs, and on cost; a cap reached is a
  state, not a surprise.
- **Fast under load.** Fifty streaming runs are fifty sessions; the board paints only what changed, the
  server sends per-run deltas coalesced to a frame, and the rows are drawn by the canvas engine.
- **One answer, every repository.** Twenty agents asking to run `npm install` is one question. A
  permission answered with *for all* answers every run of the job that asks the same thing, now and
  later; the answer is scoped to the job and to the exact request, never wider.
- **Exact state.** A run is *blocked* because the SDK said a permission is pending, *done* because the
  turn ended, not because a screen looked like it. The board never shows *idle* over an agent waiting
  on a question.

## 2. Concepts

| Term | What it is |
|---|---|
| **Workspace** | A folder whose subfolders (to a depth) hold repositories, or a hand-picked set of repositories. Scanned by the server, cached, refreshed on demand. |
| **Target** | One repository in a job: path, name, the branch to start from, and optional notes appended to the brief for this repository only. |
| **Brief** | The prompt. Markdown, with variables (`{{repo.name}}`, `{{repo.path}}`, `{{repo.branch}}`, `{{job.name}}`, `{{notes}}`), optionally saved as a **playbook** for reuse. |
| **Job** | A brief, its targets, its settings and policy, and its runs. States: `draft` → `planned` → `running` (canary / main wave) → `reviewing` → `finished`; or `stopped`. |
| **Run** | One target of one job: a session in a worktree. States: `queued` → `preparing` → `running` → `needs_action` ⇄ `running` → `done` \| `failed` \| `stopped` \| `skipped`. Carries its result: summary, files touched, diffstat, cost, duration, branch, commit, PR. |
| **Wave** | The targets that run together: the canary wave (1 by default) and then the main wave, which waits for the person after the canary unless told not to. Inside a wave, a target that depends on another (a local path dependency, a `go.mod` replace, a Cargo path) runs after it and is told what it did. |
| **Prepare** | An optional command run in the worktree before the agent starts (`npm ci`, copying `.env` from the main checkout): a worktree isolates files only, and a run needs its dependencies. Its failure fails the run before a token is spent. |
| **Verify** | An optional command run in the worktree after the agent finishes (`npm test`, `cargo check`), whose exit code is part of the run's result. |
| **Fan-in** | The review of a job's results together: one list of changed files across repositories, one place to commit, open pull requests, or discard. |
| **Report** | The job's record once finished: a table of targets with state, summary, files, cost and duration; an inbox item; exportable as Markdown. |

## 3. The server

A new module, `server/dispatch.js`, beside `schedules.js`, which it resembles (a job is a schedule's run
fanned out over targets, run once, watched). State in `<xdg state>/dispatch.json`: jobs with their runs,
written on every change; a `dispatch.changed` event, coalesced to 16 ms, carrying per-run deltas.

### 3.1 Finding repositories

`workspace.scan({ root, depth = 3 })` walks the tree, stops at the first `.git` on a path (a repository is
not searched for repositories inside it except for submodules, which are listed as such), and returns for
each: `path`, `name`, `branch`, `dirty` (uncommitted changes), `ahead/behind` its upstream, `remote`
(GitHub owner/repo when it is one), `lastSession` (the most recent Pilcrow session there, from the
history), `hasClaudeMd`, `size` class. Cached per root with the directory's mtimes; `fs.browse` already
marks repositories, and the folder picker reuses the scan. Hand-picked targets and recent projects
(`store.addRecentProject`) feed the same list.

### 3.2 Planning (the dry run)

`dispatch.plan(job)` checks every target without spending a token and returns a verdict per target:

| Check | Verdict |
|---|---|
| The folder exists and is a git repository | blocked otherwise |
| The base branch exists (or the current branch when none is named) | blocked otherwise |
| The worktree root has room (the repository's size class against free space) | warning |
| Uncommitted changes in the main checkout | warning: *the run starts from the branch's last commit; these changes are not in it* |
| `CLAUDE.md` present | note (the brief may want to say more where there is none) |
| A run of another job is still open on this repository | warning |
| The brief after substitution, per target | shown for reading; too long for the model's context → blocked |
| The job's caps: targets × max minutes, parallel, cost | shown as the worst case |

The plan is what the person reads before pressing Start; it is kept on the job and shown again in the
report, so a repository that was skipped is explained months later.

### 3.3 Running

Each run is `sessions.create({ cwd, worktree: { name: <job slug>, base }, mode, model, effort, title })`
then `session.send({ text: brief })`, with the session's meta carrying `dispatch: { jobId, runId }` the way
a scheduled run carries `scheduled`, so `sessions.list` groups it and the sidebar keeps it under its job
rather than among the person's own sessions. The state bus (`bus 'state'`) drives the run's state exactly
as `schedules.runNow` does: `idle` → done, `error` → failed, `stopped` → failed or done by whether it
replied. Differences from a scheduled run:

- **Permissions are queued, not denied.** A scheduled run is `unattended` (nobody will answer). A dispatch
  run asks: the request goes to the job's needs-action list (and the control room's cards, which already
  answer permissions), the run's state becomes `needs_action`, and the person's answer resumes it.
  A policy per job narrows what gets asked: `ask` (default), `allow edits in the worktree` (acceptEdits
  with the worktree as the allowed directory, which is what most jobs want), or an allow-list of tools.
  `bypassPermissions` is not offered: twenty agents without a leash is not a feature.
- **Waves.** The canary wave starts first; the job pauses at `reviewing (canary)` until the person
  continues, edits the brief, or stops. The main wave runs with the (possibly edited) brief and, when
  asked, with the canary's summary appended (*"In <repo>, this was done as follows: …"*), which is how the
  rest learn from the first.
- **One answer for all.** Requests are keyed by tool and normalized input (`Bash: npm install`,
  `Edit .eslintrc.cjs`, `WebFetch host`). When a request of a key already seen in this job arrives from
  another run, the board shows one card with the count. *Allow* answers that run; *Allow for all*
  answers every run of the job that asks the same key, the ones waiting now and the ones that will ask
  later, and is written on the job as a rule the report lists. The rule never outlives the job and
  never reaches another one.
- **Parallelism and order.** Up to `parallel` runs at once (default `min(3, cores ÷ 2)`), smallest
  repositories first so the person sees results sooner. When the scan found dependencies between
  targets (a local path dependency in `package.json`, a `replace` in `go.mod`, a `path` in `Cargo.toml`),
  the dependent waits for the dependency's run and gets its summary appended to the brief; the plan
  shows the order as levels. A rate-limit error from the API backs the whole job off (exponential,
  capped) rather than failing each run in turn.
- **Prepare.** When set, the command runs in the fresh worktree before `session.send` (through the same
  terminal module the Terminal tab uses, with a time cap); a non-zero exit fails the run with the tail
  of its output, before any token is spent. A per-target variant can copy files the agent needs from
  the main checkout (`.env`, local certificates), listed in the plan so nothing moves unseen.
- **Forecast.** When the canary finishes, the continue card shows the main wave's expected cost and
  wall time from the canary's, scaled by each target's size class and the parallel setting, against
  the job's caps; the person continues knowing what it will cost.
- **Caps.** Minutes per run (the session is interrupted, the run `stopped` with the reason); cost per job
  (no new run starts past it, running ones finish); both are states on the job, shown as such.
- **Verify.** When set, after the agent's turn ends the command runs in the worktree (through the same
  terminal module the Terminal tab uses, with a time cap); its exit code and tail go into the result. A
  failing verify can, by policy, send the run back to the agent once with the output ("the tests say…").
- **Results.** As `finishRun` does today: the last reply as the summary, the files from `snapshots.steps`,
  the diffstat from `git.diff` in the worktree, cost and duration from the session. Plus the branch and,
  after finishing, the commit and PR.
- **Resume.** On server start, runs that were `running` are re-attached by session id when the session
  is still live, or marked `stopped` with *the server restarted* and offered a one-click continue (open
  the session and send *"continue"*).

### 3.4 Finishing

`dispatch.finish(jobId, { action, message, pr })` per run or for all runs in a state:

- **commit**: `git.commit` in each worktree with the job's message (variables allowed), on the run's
  branch; the branch stays, the worktree directory is removed (`git.removeWorktree` keeps the branch).
- **pull request**: when the repository has a GitHub remote and `gh` is on the path, `gh pr create` from
  the branch with the brief as the body and the run's summary; the URL goes on the run. Without `gh`,
  the branch is pushed and the compare URL offered. Optional, off by default.
- **discard**: the worktree and branch removed.
- **keep**: leave the worktree for the person to continue in by hand (the session opens there).

Finishing writes the report: an inbox item of a new kind (`dispatch`), with the per-target table, that
the Inbox shows under the day like a schedule's run, and a Markdown export. The table is written by
the server from the results. Optionally (`report: 'agent'`), one more session reads every run's summary
and diffstat and writes the prose on top: what was done the same way everywhere, where a repository
differed and why, what still needs a hand. It costs one short run and is off by default.

### 3.5 RPC

```
workspace.scan      { root, depth, force }            → { root, repos: [...] }
dispatch.list       { }                                → { jobs: [summary...] }
dispatch.get        { id }                             → { job, runs }
dispatch.save       { job }                            → job          (draft; also playbooks)
dispatch.plan       { id | job }                       → { targets: [{ path, verdict, notes, brief }], caps }
dispatch.start      { id, wave?: 'canary' | 'all' }    → job
dispatch.continue   { id, brief?, learnFromCanary? }   → job          (after the canary)
dispatch.pause      { id } / dispatch.resume { id }    → job          (no new runs start / they do)
dispatch.stop       { id, runId? }                     → job          (interrupt; state 'stopped')
dispatch.retry      { id, runId, brief? }              → run          (a new run for the target)
dispatch.skip       { id, runId }                      → run
dispatch.finish     { id, runId?, action, message?, pr? } → job
dispatch.remove     { id, removeWorktrees }            → ok
dispatch.export     { id }                             → markdown
events: dispatch.changed { id, runs: [delta...], job: delta }, inbox.changed
```

Permission requests and answers keep using `session.permission` on the run's session; the job view
and the control room both answer the same request. `dispatch.allow { id, key }` writes the job's
*for all* rule and answers every waiting run that matches; later matching requests are answered by the
server as they arrive.

## 4. The client

Two screens and three places the job shows up. Qt first (the canvas engine makes the board cheap);
the web client follows with the same server API.

### 4.1 Where it lives

A **Dispatch** entry in the sidebar's navigation beside the control room and the inbox, with the
count of jobs needing a hand as its badge (runs in `needs_action` plus canaries waiting). Dispatch
sessions do not appear in the session list; their job does, as one collapsible group, so a job of
forty repositories is one row until opened.

### 4.2 The board

```
┌ Dispatch ────────────────────────────────────────────────────── [ New job ] ┐
│                                                                             │
│  Needs a hand (2)                                                           │
│  ▸ Bump eslint to v9 · 12 repos        ████████▒▒░░░░  7 done · 2 ask · 3 ▸ │
│      web-ui          needs action      Bash: npm install    [Allow] [Deny]  │
│      api-gateway     needs action      Edit .eslintrc.cjs   [Allow] [Deny]  │
│  ▸ Add SPDX headers · 40 repos     canary done — read it and continue  [→]  │
│                                                                             │
│  Running (1)                                                                │
│  ▸ Migrate to pnpm · 8 repos           ███▒▒▒░░  3 done · 2 running  $1.84 │
│                                                                             │
│  Finished today                                                             │
│    Remove dead feature flags · 6 repos   6 done · 4 PRs · $0.92 · 14 min    │
│    Pin node 22 in CI · 15 repos          13 done · 2 failed · $1.10 · 9 min │
└─────────────────────────────────────────────────────────────────────────────┘
```

The same discipline as the control room: sections appear only when they have something; the thing
that needs the person is first; a card is a row (name, scope, a progress bar whose segments are its
runs' states in the status colours, counts, cost), and a job that needs a hand shows the requests
inline with the control room's Allow / Deny, so most jobs never need opening. A request several runs
asked alike shows once, with its count and *Allow for all*. Keyboard: j/k move, Enter opens, a/d answer,
A answers for all, Space pauses.

### 4.3 The job view

```
┌ Bump eslint to v9 · 12 repos · running 00:14:02 · $2.31 of $10 ── [ Pause ] [ Stop ] [ … ] ┐
│  Brief ▸  Upgrade eslint to v9 and the flat config… (edit · playbook)                     │
│  Plan: 12 ok · 1 warning (api-gateway: uncommitted changes in main checkout)              │
│                                                                                            │
│   state        repository       branch          elapsed   Δ files   cost    now           │
│   ● done       design-system    pilcrow/eslint9   3:12    +48 −120  $0.21   tests pass    │
│   ● done       cli              pilcrow/eslint9   2:40    +31  −77  $0.18   tests pass    │
│   ◐ running    web-ui           pilcrow/eslint9   4:01    +12  −30  $0.33   editing .eslintrc.cjs  │
│   ! needs action api-gateway    pilcrow/eslint9   1:55     +4   −9  $0.12   Bash: npm install   [Allow] [Deny] │
│   ◐ running    docs             pilcrow/eslint9   0:48     —          $0.04   reading package.json  │
│   ○ queued     mobile           —                 —        —          —      —             │
│   ✕ failed     legacy-api       pilcrow/eslint9   6:00    +2   −2    $0.40   stopped after 6 min  [Retry] [Open] │
│   …                                                                                        │
│                                                                                            │
│  Review ▸  7 repositories changed · 190 files   [ Commit all… ] [ Open PRs… ] [ Discard… ] │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

A row is a run: the state glyph and colour of a session pill, the repository, its branch, elapsed,
diffstat, cost, and *now*: the live label the fold heads already compute (`reading package.json`,
`editing …`, `Bash: npm test`), or the result (`tests pass`, the reason it failed). Enter opens the
run's session as any session (the conversation, the Changes panel on its worktree, the Terminal in
it); the job view stays in the sidebar's history to come back to. A needs-action row answers inline.
Rows sort by state (needs action, running, queued, done, failed) then by name; the sort is stable so
nothing jumps while the person reads.

The review at the foot groups identical hunks across repositories: a change made the same way in nine
repositories is one item with a count, read once; the repositories that differ are listed by themselves
with what differs. Enter on an item opens that run's Changes for the full diff.

The rows are a `RowScene` on the canvas engine, like the Timeline and Tasks lists: five hundred
repositories scroll at 60 Hz, a run's change repaints its row, and a streaming "now" label costs the
row's strip.

### 4.4 The composer (a new job)

```
┌ New job ───────────────────────────────────────────────────────────────────┐
│  Name      Bump eslint to v9                                      Playbook ▾│
│  Brief     ┌──────────────────────────────────────────────────────────────┐ │
│            │ Upgrade eslint to v9 with the flat config. Keep the rules we │ │
│            │ have; drop the deprecated ones. Run the linter and fix what  │ │
│            │ it reports. Repository: {{repo.name}} on {{repo.branch}}.    │ │
│            │ {{notes}}                                                     │ │
│            └──────────────────────────────────────────────────────────────┘ │
│  Targets   ~/code  (scan)   [filter: eslint in package.json]   12 of 31 ▾  │
│            ☑ design-system   main   clean        ☐ infra        main       │
│            ☑ web-ui          main   2 uncommitted ☑ docs        main       │
│            ☑ api-gateway     main   clean  ✎ notes…  …                      │
│  Run       mode  Allow edits in worktree ▾   model  default ▾   effort ▾    │
│            parallel 3   max 20 min each   cost cap $10   canary 1 ▾         │
│            verify  npm test            after the agent ▾                     │
│  Finish    branch pilcrow/eslint9   commit message "chore: eslint v9"       │
│            ☐ open pull requests (gh)                                        │
│                                                     [ Plan ]  [ Start → ]   │
└────────────────────────────────────────────────────────────────────────────┘
```

The target picker is the workspace scan: a tree of repositories with branch and cleanliness, a text
filter (name, or *has file*, *has dependency*, *language*, which the scan indexes cheaply from
`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`), saved sets, and per-target notes that
reach the brief through `{{notes}}`. Plan runs as you edit (debounced, a second after the last change)
and paints verdicts on the targets; Start is enabled when no target is blocked, and starts the canary.
The brief editor is the composer's edit field (the same slash commands, `@` file mentions resolved per
target at run time).

### 4.5 In the control room, the inbox and the sidebar

- **Control room**: a job's running runs sit in the Working section under one heading with the job's
  bar, their permission cards exactly as today's; a finished job is one row in Finished today with
  its counts. The Spend card attributes cost per job.
- **Inbox**: a finished job is an item of its own kind: the table of targets, the summary of each, the
  files, the cost, and the Commit / PR / Discard actions when the job was left in review.
- **Sidebar**: a job is a group; opening it lists its runs as sessions; closing it hides them.

### 4.6 What the eye gets, and what it does not

No frame drops: the board and the job view are canvas rows; a run's state change is a row repaint; the
progress bar animates on the shared clock only while something moves. No flicker: the server sends
deltas, the client patches rows in place (the same reconcile-by-key the transcript uses). No jumping:
rows keep their place while the person reads (stable sort; a state change recolours a row and, after a
beat, lets it settle to its section with the easing the folds use). No modal in the way: the plan is a
section of the composer, the permission answers are inline, confirmations only for discard.

## 5. Flows

1. **Create.** Name, brief, targets, settings. The plan paints while editing. Start.
2. **Canary.** One run (the smallest repository by default, or the one chosen). The job reads
   *canary running*; then *canary done: read it and continue*. The person opens the session, reads
   the diff in Changes, and continues (optionally with an edited brief and *let the rest learn from
   this*), or stops.
3. **Main wave.** Up to `parallel` runs at once. The board shows the bar growing; the job view shows
   the rows. Requests queue in needs-action and are answered inline or from the control room.
4. **Attend.** A failed run: Retry (new run, same or edited brief) or Open (take over by hand). A slow
   run: it stops at the cap with the reason; Open continues it.
5. **Review.** When no run is queued or running, the job is *reviewing*: the fan-in shows every
   changed file by repository; the person opens any run's Changes for the detail.
6. **Finish.** Commit all with one message (per run or all), open pull requests where possible,
   discard what should not land, keep what needs a hand. The report goes to the inbox; worktree
   directories go, branches stay.
7. **Later.** The job is in the inbox and the board's history; its report can be exported; its brief
   can be saved as a playbook and run again on the same or another set.

## 6. Failure and safety

| Case | Behaviour |
|---|---|
| A target is not a repository, or the base branch is missing | blocked in the plan; Start waits |
| Uncommitted changes in a main checkout | warning in the plan: the run starts from the last commit |
| An agent asks for a permission | the run waits in needs-action; the job continues elsewhere; the control room shows the card |
| Several runs ask the same permission | one card with the count; *Allow for all* answers every run of this job that asks it, now and later; the rule is on the report |
| A worktree needs dependencies or secrets | the prepare command runs first; a failure fails the run before a token is spent, with the output's tail |
| A target depends on another target | it waits for the dependency's run and receives its summary; a dependency that failed skips its dependents with the reason |
| An agent runs past its minutes | interrupted; `stopped (20 min)`; Open to continue |
| The job passes its cost cap | no new runs start; running ones finish; the job says so |
| The API rate-limits | the whole job backs off (exponential, capped at a minute), runs resume |
| The server restarts mid-job | live sessions re-attach; the rest are `stopped (server restarted)` with Continue |
| Disk: the worktree root fills | the plan warns from the size classes; a run that cannot create its worktree fails before it starts |
| `bypassPermissions` | not available to a job |
| Two jobs on one repository | warning in the plan (branches differ, so both can run; the person decides) |
| A verify command hangs | its own time cap; the output's tail is in the result |

## 7. Against herdr, concretely

| | herdr (and the fleet patterns on it) | Dispatch |
|---|---|---|
| Unit of work | a pane; a fleet is the panes on `fleet/*` branches | a job with runs, persisted, resumable, reported |
| Where agents run | the repository's checkout, or a worktree you create per pane | a worktree per run, always, on a branch of its own |
| State | a screen manifest: *blocked* when a known prompt is recognised, else *idle* | the SDK's own events: pending permission, turn ended, error |
| Before running | nothing | a plan: per-target checks, the brief as each will see it, the order, the worst-case caps |
| Rollout | all at once | canary, then the rest, which can learn from it; dependents after dependencies |
| Permissions | answered pane by pane | queued in one place; one answer for every repository that asks the same thing |
| Worktree setup | yours (ports, `.env`, dependencies) | a prepare command and copied files, listed in the plan |
| Results | read pane by pane; merged by hand from the main pane | one review across repositories, identical changes grouped; one commit; pull requests; a report |
| Limits | none | minutes per run, parallel, cost cap, rate-limit backoff |
| Cost and time | not measured | per run and per job, forecast after the canary |
| After the terminal closes | the panes live on the server; the fleet's meaning does not | the job is there tomorrow, with its report |
| Reuse | shell history, skills | playbooks with variables and saved target sets |
| Where | a terminal, with a socket API | the desktop client and the web client, over the same server, with the same RPC for scripts |

## 8. Phases

1. **Core** (server `dispatch.js` with scan, plan, start/stop/retry, runs as sessions, deltas; Qt
   board, job view, composer; sidebar grouping; control room integration of permission cards).
2. **Rollout and finish** (canary waves with learn-from-canary, verify command, fan-in review,
   commit / discard / keep, report to the inbox, Markdown export).
3. **Reuse and reach** (playbooks and target sets, pull requests through `gh`, the web client's
   screens, a schedule that dispatches on a cadence by reusing `schedules.js`).

## 9. Testing

- Server: the job state machine against a fake `sessions` module (vitest): waves, caps, backoff,
  restart re-attach, every state transition; `workspace.scan` on a fixture tree (with path dependencies
  between fixtures, so the order comes out as levels); the plan's verdicts; the approve-once rule (the
  same request from two runs answered once, a different one not); prepare failing before any send.
- Client: the board and job view against the mock server (`qt/tests/harness.py`): a job of forty runs
  streaming at once, permission answered from both screens, the sort staying stable, the rows repainting
  alone (the smoothness probe extended to the board: no stray repaints of the shell or the panel).
- Stress: five hundred repositories in the picker and the job view; fifty parallel runs; a job
  interrupted by a server restart.

## 10. Decisions taken here, open to change

- Branch name `pilcrow/<job slug>` in every repository; the worktree under the configured worktree root,
  removed on finish, the branch kept.
- Default policy *allow edits in the worktree*, default canary of one, default parallel `min(3, cores ÷ 2)`,
  default cap 20 minutes per run and no cost cap until one is set.
- Pull requests only through `gh`, off by default; a push without `gh` offers the compare URL.
- *Allow for all* is scoped to one job and one request key (tool + normalized input); it is never
  offered for `Bash` commands that differ in anything but whitespace, and never carried to another job.
- The agent-written report is off by default; the server's table is always there.
- The web client gets the screens in phase 3; until then a job started from the web shows as a group of
  sessions with the inbox report.
