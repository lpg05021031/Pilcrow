---
description: After the code changed — run every check again, see what is fixed and what is new, and re-derive only what the change touched
argument-hint: <system>
arguments: system
---

The code of `$system` has changed since the audit was made. Run the audit's own checks against the code
as it is now, show what changed in behaviour, and say which parts of the model were read from code that
no longer exists in that form. Decisions already made are kept; questions already answered are not asked
again.

Read first: `audit/$system/INTENT.md` and `audit/$system/HARNESS.md`. If `audit/$system/model/expected.json`
does not exist, stop: there is nothing to run again; the audit has not been characterized yet
(`/java-logic-audit:audit-status $system` says where it stands).

Ground rules:

- Run every command from the workspace root. Write nothing by hand.
- **The harness empties every table of its database before every case**, as before; same database, same
  environment variables.
- Never change application code, tests or build files, and never change an expectation to make a case
  pass. A case that now differs from its pin is the result of this step.
- Speak to the person in the language `INTENT.md` names (`auto`: the language they write in).

## 1 — What changed

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/manifest.py" check . --manifest audit/$system/manifest.json
```

`untouched`: say that nothing changed since the audit recorded the source, and stop unless the person
asked for the checks to be run again anyway. Otherwise list the files added, removed and modified.

For each changed file, name the groups of entry points whose readers were given it: it is listed under
`files` of that group in `audit/$system/shards.json`. The model of those groups was read from the old
code. Their checks may have moved, and new ones are not in the model at all.

## 2 — Run everything again

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/harness_install.py" copy $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/harness_install.py" command $system --run matrix --mode verify
```

Run the printed command once. It fails when a case differs from its pin or from a decision: that is the
information wanted. A build that does not compile or start is a different failure; read the log.

Then the three parts of `/java-logic-audit:audit-verify $system` exactly as that command describes them
(failures three times, overlapping calls three times, sequences once), and:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/reconcile.py" $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/invariants.py" assess $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build_questions.py" $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" findings $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" verdict $system
```

## 3 — Tell the person

Show what each script printed, unchanged. Then:

- findings that now read `fixed`, by id, and what was observed before;
- cases that behave differently than pinned (`reconcile.py` lists them under "behave differently than
  pinned"; `audit/$system/model/reconciliation.json` → `against_expectation.changed_since_pinned` has
  them all), grouped by command: each is either the intended effect of the change or a regression, and
  only the person knows which;
- cases that still do what a decision says they must not;
- rules a person confirmed that are now broken, and anything new the failure, overlap and sequence checks
  show;
- the groups from step 1 whose model is out of date.

Do not call a changed case a regression or a fix yourself.

## 4 — Bring the audit up to date (only with the person's go-ahead)

Ask before doing any of this; in a headless run, do none of it and say what waits.

- **Re-derive the model of the groups that changed**: `/java-logic-audit:audit-model $system <group>` for
  each, after deleting that group's saved replies under `audit/$system/model_raw/` (`mine-`, `referee-`
  and `recipes-` files of the group), then `/java-logic-audit:audit-characterize $system`. Cells keep
  their ids where their command and case are unchanged; decisions stay attached to their questions.
- **Accept the new behaviour as the pinned one**, when the person says the changed cases are intended:
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/reconcile.py" $system --repin` after a matrix run in record
  mode. Decisions are never overwritten by this.
- **Record the source as it is now**, so that later steps compare against it:
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/manifest.py" build . --out audit/$system/manifest.json`, and
  update `base_commit` in `INTENT.md` to the current commit.

End with `/java-logic-audit:audit-report $system` for a report of the state after the change.
