---
description: Check the audit's own work — an independent reader for every finding, a canary for every check, and seeded defects if you have some
argument-hint: <system> [recheck|canary|seeds <defects file>|all]
arguments: [system, part, defects]
---

Find out how far the audit of `$system` can be trusted, in three ways that do not depend on each other:

- **recheck** — each finding is handed to a reader who is told only the statement and where the evidence
  is, and who tries to refute it;
- **canary** — each check the model cites is switched off in a copy of the code, to see whether any case
  notices;
- **seeds** — defects a person supplies are put into a copy of the code one at a time, to see which of
  the audit's checks notice.

With `$part` empty or `all`: recheck and canary, and seeds when a defects file is named. Otherwise only
the part named.

Read first: `audit/$system/INTENT.md` and `audit/$system/STATUS.md` if it exists. If
`audit/$system/findings.json` does not exist, stop: the step before this one is
`/java-logic-audit:audit-characterize $system` (and `/java-logic-audit:audit-verify $system` for the
checks beyond the matrix).

Ground rules:

- Run every command from the workspace root. Write nothing by hand under `audit/$system/` except the
  readers' replies, saved unchanged where step 1 says.
- **Nothing here changes the repository's code.** The canary and the seeds work in temporary copies
  outside the repository, which the scripts delete. If a script fails half-way, do not clean up by hand
  inside the workspace: there is nothing of theirs in it.
- You do not recheck a finding yourself, and you add nothing to a brief: what a reader is not told is
  the point.
- Canary and seeds build and run the project many times and empty the audit's database over and over.
  They use it as the harness step set it up (`audit/$system/HARNESS.md`; an offered database needs the
  same three `AUDIT_DB_*` environment variables in front of each command). Never point them at a
  database that holds anything worth keeping.
- Speak to the person in the language `INTENT.md` names (`auto`: the language they write in).

## 1 — Recheck (one reader for each finding)

Bring the findings up to date, then list them:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" findings $system
```

Take every finding in `audit/$system/findings.json` under `findings` (not the unconfirmed candidates).
With more than 40, say how many there are and ask whether to recheck all or only the ones classed
defect. For each:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/assure.py" brief $system F-007
```

Launch one `adversary` agent whose prompt is that output, whole and unchanged, and nothing more. Up to
six at a time; they only read. Save each reply, unchanged, as the file the brief names in
`save_reply_as` (`audit/$system/assure/recheck/F-007.json`). The reply must be one JSON object; when it
is not, ask the same agent once to send only the object, and if that fails too leave the finding without
a file. Never repair a reply, and never ask a reader to reconsider a verdict.

A finding whose saved reply carries the fingerprint the finding has now is not rechecked again.

If you cannot launch agents, skip this step and say so in the report: a session that produced the
findings cannot be their independent reader.

## 2 — Canary (a script does it)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/canary.py" $system
```

It prints one line for each cited check as it goes, and takes a minute or more for each: say how many
checks `audit/$system/model/commands.json` cites before you start, and with more than 60 ask whether to
run all of them now or a first part (`--limit 30`; the result is then marked incomplete and the verdict
says so). Extra arguments for the build tool, such as `-o` where there is no network, go in the
environment variable `AUDIT_BUILD_ARGS`. Keep the console output under `audit/$system/logs/`.

What the words mean: **killed** — some case gives another result with the check switched off;
**survived** — none does; **expected** — none does, and the model already says no reachable case is
decided by this check; **invalid** — the copy did not build without it; **no-mutant** — the cited lines
hold nothing the script can switch off.

## 3 — Seeded defects (only when the person names a defects file)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/seed_run.py" $system "$defects"
```

The file's form is in the script's first lines (`--help` prints it). You do not invent defects for the
person, and you do not edit their file; if an entry's text to replace is not found exactly once, the
script stops and says which, and you report that. Each defect runs every check once, so this takes
several minutes for each.

## 4 — Bring the results together (scripts do it)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" findings $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" verdict $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/assure.py" summary $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/manifest.py" $system
```

`judge.py findings` shows each reader's verdict with its finding; "weakened" and "refuted" become a
reservation that lowers the grade, and nothing is removed. `judge.py verdict` now computes the canary
item for every entity. `assure.py summary` writes `audit/$system/ASSURANCE.md`.

## 5 — Report

Read `audit/$system/ASSURANCE.md`. Show what each script printed, unchanged, and then say plainly:

- how many findings a reader let stand, weakened or refuted, with the reason for each that did not
  stand. A finding a reader refuted is still in `FINDINGS.md`, with that reservation: the runs show
  what they show, and a person decides;
- the signs of intent the readers found, each with its place: these go to whoever answers the question
  in `/java-logic-audit:audit-review $system`;
- every check that **survived**: either the check is redundant where it stands, or the cases miss the
  situation it exists for. Do not say which; give the place and the commands that rest on it;
- how many cited places could not be switched off, and that the canary says nothing about those;
- for seeds: how many were noticed, by which check, and every one that was **not noticed**, with its
  one-sentence description from the file;
- what none of this shows (the last section of `ASSURANCE.md`).

End with the next step: `/java-logic-audit:audit-report $system`.
