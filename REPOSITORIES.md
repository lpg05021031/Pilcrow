# Repositories: the registry

A design for one list the person maintains: the repositories Pilcrow may work in, how the person refers
to each of them, what each is, how helpers (HELPERS.md) and Dispatch runs (DISPATCH.md) may use it, and
the standing notes any agent sent there should know. It replaces the per-feature answers to "which
folders" with one place, and it gives the model the person's own words for their repositories, so
*the order FE* in a prompt lands in `~/code/web` without a guess.

## Status

Design only. HELPERS.md §5.6 and DISPATCH.md §3.1 refer to it; nothing is built.

## 1. Principles

- **The person's words are the key.** A repository has a name and any number of aliases (*order FE*,
  *storefront*, *前台*). Resolution is by those words, insensitive to case, spaces, hyphens and
  underscores. Nothing asks the person to learn Pilcrow's name for their own repository.
- **One list, every user.** Helpers, Dispatch, the new-session folder picker, the composer's `@`, the
  sidebar's project names and the prompt all read the same entries. There is no second list anywhere.
- **Facts are scanned, settings are typed.** Branch, dirtiness, manifest kind, package name, local
  dependencies and whether a `CLAUDE.md` exists are read from the folder and refreshed; the person types
  only what a scan cannot know: aliases, what the repository is for, policy, defaults, notes.
- **Policy decides the card.** *Allowed* starts helpers and runs there without asking; *Ask* shows the
  permission card each time; *Never* refuses with the reason. The lead's own project is always allowed.
- **Standing notes travel with the repository.** What every agent sent there should know (*pnpm, not
  npm*; *the ESLint config lives in config/eslint.cjs*) is written once on the entry and appended to
  every helper brief and Dispatch brief for that repository. It is the home the Scout idea (DISPATCH.md,
  discussion) was looking for.
- **Nothing is required.** An unregistered folder still works everywhere, through the permission card,
  as today. The registry removes questions; it never adds a step before the first use.

## 2. An entry

```
{ id,                      stable; jobs, playbooks and helpers refer to it, so renames keep it
  path,                    absolute, this machine
  name,                    short, unique; default: the folder's name (the package name when the folder is generic)
  aliases: [],             the person's words; unique across the registry together with every name
  about: '',               one line: what it is, what it talks to ("Next.js storefront; calls api")
  policy: 'allowed' | 'ask' | 'never',
  defaults: { mode?, worktree?: true, base?, prepare?, verify?, model? },
  notes: '',               standing notes for any agent working here; a few lines
  deps: [id...],           repositories this one depends on; scanned from manifests, editable
  facts: { manifest, packageName, branch, dirty, hasClaudeMd, size, seenAt },   scanned, never typed
  addedAt, machine: 'local' }
```

Resolution of a word the person or the model used, in order: an exact path; a name; an alias; the
basename of a registered path. A miss returns the three nearest names (by normalized edit distance) in
the error, so a tool call that says `repo: "order-fe"` learns `order FE` without a second guess.

## 3. Where it is read

**The prompt.** Every session's system prompt carries a digest of the registry, after the project memory
(`memory.promptFor`), bounded to about 2 000 characters: one line per entry,
`- web (also: order FE, storefront): Next.js storefront; calls api · ~/code/web · helpers allowed`, the
first forty entries in full, the rest as names and aliases only. The model reads *update the order FE*
and knows the folder, the policy and what the repository is for, with no tool call. The digest carries
no live facts (branch, dirtiness) so it is built from the config alone and costs no `git` at session
start.

**The composer.** The `@` popup lists registered repositories above files, with the worktree icon,
inserting `@web`; the digest names each entry's `@name`, so the mention is unambiguous to the model and
coloured in the bubble like a file mention. Typing an alias filters too.

**Helpers.** `start` takes `repo` (a name, alias or path) or `cwd`. The policy decides whether a card
is shown (HELPERS.md §5.6); the entry's `defaults` fill the helper's mode, worktree, base branch and
model unless `start` says otherwise; the entry's `notes` are appended to the brief under *About this
repository*. `list.known` is the registry first (marked `registered`), then recent projects and the
repositories under the lead's folder.

**Dispatch.** The target picker shows registered repositories first with a *registered* chip and the
`about` line as its hint; a target's notes default to the entry's `notes` (editable per job as today);
a job's blank `prepare` and `verify` take the entry's; `deps` come from the entry when set, else from the
scan. `{{notes}}` renders the entry's notes and then the target's.

**The new-session dialog and the folder picker.** Registered repositories first, by name, with
`about` as the second line; recent folders after.

**The sidebar.** A project group whose folder is registered is labelled with the entry's name.

**Scout, if built.** Its drafts land in `notes` with a *from scout* flag until the person edits them.

## 4. Where it is written

**Settings → Repositories.** One row per entry: name in bold, the path tildified and muted, alias chips,
a three-way policy control (*Allowed · Ask · Never*), and a chevron that opens the rest (about,
defaults, notes, dependencies, the scanned facts in muted type with *refresh*). Inline editing; no
sub-dialogs. Above the list: **Add…** (the folder picker) and **Scan a folder…** (Dispatch's
`workspace.scan`, then a list of found repositories with checkboxes, names and package names
prefilled, aliases empty). A filter field appears past ten entries. A row whose path no longer exists
is muted with *not found* and **Locate…**.

**From where the person already is.** The session header's folder crumb menu gains **Register this
repository…**, prefilled; *Always allow* on a helper's start card registers the folder with policy
*Allowed* (or sets an existing entry to it) and says so in a toast with a link to the settings row;
the Dispatch target picker's row menu gains **Register**.

**From the model, never.** Entries are the person's; no tool writes them. A lead that wants a folder
registered says so to the person.

## 5. The server

`server/repos.js`: `list()`, `save(entry)` (validation: path exists or is marked missing, names and
aliases unique after normalization, `deps` are ids), `remove(id)`, `resolve(word)`, `promptFor()` (the
digest), `scan(root)` (through Dispatch's `findRepos`, returning candidates not yet registered),
`refresh(ids?)` (facts, throttled to once a minute per entry, run when Settings opens and when a helper
or run starts there), `notesFor(id)`, `defaultsFor(id)`, `policyFor(path)` (the entry containing the
path, longest match; the lead's own project is `allowed` regardless). Stored in the config under
`repos`, so it travels with the person's other settings. RPC: `repos.list`, `repos.save`,
`repos.remove`, `repos.scan { root }`, `repos.refresh { ids }`, `repos.resolve { word }`; the clients hear
`repos.changed`. `sessions.js` appends `repos.promptFor()` to the system prompt beside the project
memory. `helpers.js` and `dispatch_core.js` call `policyFor`, `defaultsFor` and `notesFor`.

## 6. Failure and safety

| Case | Behaviour |
|---|---|
| a path no longer exists | the row says *not found*; `start` and Dispatch's plan say *registered but missing*; **Locate…** fixes it in place, keeping the id |
| two entries would share a name or alias (after normalization) | the save is refused naming the other entry |
| an alias equals another entry's name | refused the same way |
| a word matches nothing | the three nearest names, in the tool error and in the composer's popup |
| an entry is removed while a helper runs there | the helper runs on; its chip loses the registered mark; nothing else changes |
| the digest would exceed its budget | the first forty entries in full, then names and aliases only, then *and N more; ask list* |
| `policy: never` | `start` errors with the reason; the Dispatch plan marks the target blocked; the folder still opens as a session by hand |
| a registered folder is not a repository | allowed; `worktree` defaults to false for it; the facts say so |

## 7. Phases

1. The registry, Settings → Repositories, the digest in the system prompt, `repos.*` RPC, Helpers'
   `repo`, policy, defaults and notes.
2. The composer's `@`, Dispatch's picker, notes and defaults, the sidebar label, **Register this
   repository…** in the header menu, *Always allow* registering.
3. Scout drafts into `notes`; `machine` for repositories on another machine, when sessions can run there.

Estimated: server ≈ 250 lines; Qt ≈ 300 (the settings section, the picker and popup changes); web
≈ 250; tests ≈ 200.

## 8. Decisions taken here, open to change

- Aliases are free text and matched loosely; there is no pattern language. If two people on one machine
  need different words, they add both.
- The digest is in the system prompt, not a tool: the point is that the model understands the person's
  first message. The cost is bounded and paid once per session.
- Policy has three values and applies to starting work there (helpers, Dispatch runs). Permissions for
  what an agent may do inside a repository remain Claude Code's own (`.claude/settings*.json`), not
  duplicated here.
- No tool writes the registry. The model may propose; the person registers.
- `notes` are appended to briefs, not injected into the helper's system prompt, so the helper's own
  `CLAUDE.md` and project memory keep their place and the person sees the notes in the first message.
