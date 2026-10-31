# Dispatch: one job, many repositories

A design for running the same piece of work across a set of repositories at once, watching it as a
whole, and finishing it as a whole. The reference point is herdr and the tools built around it
(a terminal UI that finds the repositories under a folder, shows what Claude Code is doing in each,
and lets you start and steer agents there). Pilcrow already owns the parts that make the hard half of
this easy: a server that runs sessions through the Agent SDK, git worktrees per session, a scheduler
that runs sessions unattended and collects what they did into an inbox, snapshots of every file a
session touched, a control room that watches live sessions and answers their permission requests, and
a Qt client whose canvas engine draws thousands of rows at 60 Hz on a CPU. Dispatch is the job object
that ties them together, and the two screens that show it.

## 0. What herdr does, and where the room is

What it does well: find every repository under a tree, start an agent in each from one place, see at a
glance which agents are idle, working, blocked or done, and read a little git status beside them. It
is a pane manager with a repo finder; the agents are tmux panes.

What it leaves to the person: the work has no identity (there is no "job", only panes that happen to
share a prompt); nothing is isolated (agents edit the repository's checkout, so two jobs or a person's
own work collide); there is no plan before the fan-out, so a typo in the prompt runs twenty times;
blocked agents are found by looking; results are read pane by pane, and finishing (commit, branch,
PR, cleanup) is twenty manual acts; nothing survives the terminal closing; nothing is measured.

Dispatch keeps the good part (one place, every repo, live) and gives the work an identity with a
lifecycle: a **job** with a brief and targets, which **plans** before it runs, runs each target in its
own **worktree** as a real session, lets a **canary** go first, queues what the agents need to ask in
one place, **reviews** the result across repositories as one diff, and **finishes** with one commit or
one set of pull requests, with the cost and time of every repository written down.

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

## 2. Concepts

| Term | What it is |
|---|---|
| **Workspace** | A folder whose subfolders (to a depth) hold repositories, or a hand-picked set of repositories. Scanned by the server, cached, refreshed on demand. |
| **Target** | One repository in a job: path, name, the branch to start from, and optional notes appended to the brief for this repository only. |
| **Brief** | The prompt. Markdown, with variables (`{{repo.name}}`, `{{repo.path}}`, `{{repo.branch}}`, `{{job.name}}`, `{{notes}}`), optionally saved as a **playbook** for reuse. |
| **Job** | A brief, its targets, its settings and policy, and its runs. States: `draft` → `planned` → `running` (canary / main wave) → `reviewing` → `finished`; or `stopped`. |
| **Run** | One target of one job: a session in a worktree. States: `queued` → `preparing` → `running` → `needs_action` ⇄ `running` → `done` \| `failed` \| `stopped` \| `skipped`. Carries its result: summary, files touched, diffstat, cost, duration, branch, commit, PR. |
| **Wave** | The targets that run together: the canary wave (1 by default) and then the main wave, which waits for the person after the canary unless told not to. |
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
- **Parallelism and order.** Up to `parallel` runs at once (default `min(3, cores ÷ 2)`), smallest
  repositories first so the person sees results sooner; a rate-limit error from the API backs the whole
  job off (exponential, capped) rather than failing each run in turn.
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
the Inbox shows under the day like a schedule's run, and a Markdown export.

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
and the control room both answer the same request.

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
inline with the control room's Allow / Deny, so most jobs never need opening. Keyboard: j/k move,
Enter opens, a/d answer, Space pauses.

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
| An agent runs past its minutes | interrupted; `stopped (20 min)`; Open to continue |
| The job passes its cost cap | no new runs start; running ones finish; the job says so |
| The API rate-limits | the whole job backs off (exponential, capped at a minute), runs resume |
| The server restarts mid-job | live sessions re-attach; the rest are `stopped (server restarted)` with Continue |
| Disk: the worktree root fills | the plan warns from the size classes; a run that cannot create its worktree fails before it starts |
| `bypassPermissions` | not available to a job |
| Two jobs on one repository | warning in the plan (branches differ, so both can run; the person decides) |
| A verify command hangs | its own time cap; the output's tail is in the result |

## 7. Against herdr, concretely

| | herdr | Dispatch |
|---|---|---|
| Unit of work | a pane | a job with runs, persisted, resumable |
| Isolation | the repository's checkout | a worktree per run, on a branch of its own |
| Before running | nothing | a plan: per-target checks, the brief as each will see it, worst-case caps |
| Rollout | all at once | canary, then the rest, which can learn from it |
| Blocked agents | found by looking | queued in one place, answered inline or from the control room |
| Results | read pane by pane | one review across repositories; one commit; pull requests; a report |
| Limits | none | minutes per run, parallel, cost cap, rate-limit backoff |
| After the terminal closes | gone | the job is there tomorrow, with its report |
| Reuse | shell history | playbooks with variables and saved target sets |
| Where | a terminal with tmux | the desktop client and the web client, over the same server |

## 8. Phases

1. **Core** (server `dispatch.js` with scan, plan, start/stop/retry, runs as sessions, deltas; Qt
   board, job view, composer; sidebar grouping; control room integration of permission cards).
2. **Rollout and finish** (canary waves with learn-from-canary, verify command, fan-in review,
   commit / discard / keep, report to the inbox, Markdown export).
3. **Reuse and reach** (playbooks and target sets, pull requests through `gh`, the web client's
   screens, a schedule that dispatches on a cadence by reusing `schedules.js`).

## 9. Testing

- Server: the job state machine against a fake `sessions` module (vitest): waves, caps, backoff,
  restart re-attach, every state transition; `workspace.scan` on a fixture tree; the plan's verdicts.
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
- The web client gets the screens in phase 3; until then a job started from the web shows as a group of
  sessions with the inbox report.
