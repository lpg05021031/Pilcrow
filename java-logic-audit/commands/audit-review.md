---
description: Answer the questions only a person can — is each observed behaviour intended? Decisions are kept for good
argument-hint: <system> [flagged|all]
arguments: [system, which]
---

Put the open questions of `$system` to the person, one behaviour at a time, and record the answers. An
answer is a decision: it is kept for good, it is never asked again, and "not intended" turns the
observation into a defect that the cases reproduce until the code changes.

Read first: `audit/$system/INTENT.md` and `audit/$system/questions.json`. If `questions.json` does not
exist, stop: the step before this one is `/java-logic-audit:audit-characterize $system`.

Ground rules:

- Run every command from the workspace root. Write nothing by hand: decisions are recorded by
  `apply_decisions.py`, and every other file is rebuilt by scripts.
- **You do not answer for the person, and you do not steer.** Present what was observed and why it is
  asked, both as `questions.json` has them, translated into the person's language. Do not add what you
  think the answer is, and do not call the behaviour a bug or a feature.
- An answer you were not given is not recorded. Without a way to ask (a headless run, no pop-up tool)
  and without answers written in the request, record nothing and say how many questions wait.
- Speak to the person in the language `INTENT.md` names (`auto`: the language they write in).

## 1 — Which questions

Take the questions with `"status": "open"`. With `$which` empty or `flagged`, only those with
`"flagged": true`; with `all`, every open one. Say how many are already answered and are skipped, and how
many will be asked. Ask them in the order of their ids.

If the person's request already states decisions ("Q-004 is not intended: a shipment must never be
lost"), record those first, exactly as in step 3, and do not ask them again.

## 2 — Ask

Use the pop-up question tool, at most four questions in one call, one behaviour per question. For each:

- the header is the question id;
- the text gives, in this order: what was observed (`observed`), why it is asked (`why`), where in the
  code (`cites`), and one case to run to see it (the first id in `cells`);
- the options are always these three, in this order and with these meanings:
  1. **Not intended** — this should not happen; treat it as a defect.
  2. **Intended** — this is how it is meant to work; keep it as the expected behaviour.
  3. **Not sure** — leave it open; it will not be used as a basis for anything.

The person may type their own words instead: record the option that matches what they wrote and keep
their words as the note. When what they wrote does not clearly match one option, record `unsure` with
their words, and say so.

When the person asks to see the evidence before answering, show the case's line in the latest run's
`results.jsonl` and the changed rows from its evidence file, then ask again.

## 3 — Record

For each answer:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apply_decisions.py" record $system --question Q-004 \
    --answer not-intended --note "the person's own words"
```

The answer is one of `not-intended`, `intended`, `unsure`. The note is what the person wrote, unchanged
and untranslated; leave it out when they only picked an option. Pass it as one shell argument, quoted so
that nothing in it is interpreted by the shell.

## 4 — Apply and re-run (scripts do it)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apply_decisions.py" apply $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build_matrix.py" $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/harness_install.py" command $system --run matrix --mode verify
```

Run the printed command once, with the same database settings as in the characterize step. **It is
expected to fail when something was answered "not intended":** those cases are now compared with what
the person said should happen, and the application does not do that yet. A failure of any other case
means behaviour changed since it was pinned; report it. Then:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/reconcile.py" $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build_questions.py" $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" findings $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" verdict $system
```

## 5 — Report

Show the printed line of each script unchanged. Then say three things plainly:

- what was answered "not intended" is now a defect in `FINDINGS.md`, with the cases that reproduce it,
  and the audit's verify run will keep failing on those cases until the code is changed;
- what was answered "not sure" stays open and is not used as a basis for any judgement;
- decisions are in `DECISIONS.md` for good: a later run does not ask again, and a different answer later
  is recorded as a new decision that supersedes the old one.

The plugin does not fix anything. End with the next step: `/java-logic-audit:audit-verify $system`. If the
file `${CLAUDE_PLUGIN_ROOT}/commands/audit-verify.md` does not exist, say that this command is not in this
version of the plugin yet, and do not improvise its work.
