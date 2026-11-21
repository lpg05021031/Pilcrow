---
description: Answer the questions only a person can - is each observed behaviour intended? Decisions are kept for good
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
- An answer you were not given is not recorded. Without a way to ask (a headless run, no pop-up tool),
  record only the answers written in the request, carry on with steps 3 to 5 for those, and list the
  questions that still wait in your reply, each with what was observed, why it is asked, the code and one
  case, so that the person can answer them in their next message. With no answer at all, record nothing,
  skip step 4 and say how many questions wait.
- Speak to the person in the language `INTENT.md` names (`auto`: the language they write in).

## 1 - Which questions

Take the questions with `"status": "open"`. With `$which` empty or `flagged`, only those with
`"flagged": true`; with `all`, every open one. Say how many were answered in earlier runs and are skipped,
how many the request itself answers, how many will be asked, and in one line how many unflagged ones
exist and that `all` asks them. Ask in the order of the ids.

If the person's request already states decisions ("Q-004 is not intended: a shipment must never be
lost"), record those first, exactly as in step 3, and do not ask them again. The note is then the
person's own sentence about that question, as written.

**A question can group several cases** (its `observed` text lists them, its `cells` are their ids). When
the person's words are clearly about some of them only ("for an order that is not confirmed yet", where
the question also covers closed and cancelled orders), record the answer for those cases alone with
`--cells`, and tell the person which cases their answer was taken to cover and which stay open. To match
cases to cells, read each cell's `deviation` in `audit/$system/model/matrix.json`. When you cannot tell
which cases they mean, record `unsure` with their words and say what you could not tell.

## 2 - Ask

Use the pop-up question tool, at most four questions in one call, one behaviour per question. For each:

- the header is the question id;
- the text gives, in this order: what was observed (`observed`), why it is asked (`why`), where in the
  code (`cites`), and one case to run to see it (the first id in `cells`);
- the options are always these three, in this order and with these meanings:
  1. **Not intended** - this should not happen; treat it as a defect.
  2. **Intended** - this is how it is meant to work; keep it as the expected behaviour.
  3. **Not sure** - leave it open; it will not be used as a basis for anything.

For a question of kind `rule?` ("in every state the audit reached, X: is this a rule?") the same three
options read differently, and you say so in the question text: **Intended** means "yes, this must always
hold", and from then on a state that breaks it is a defect; **Not intended** means "no, it only happened
to hold". For kinds `near-rule` and `rare-combination` (something held almost always and broke a few
times), **Not intended** means the exceptions should not happen, which makes it a rule; **Intended**
means the exceptions are fine.

The person may type their own words instead: record the option that matches what they wrote and keep
their words as the note. When what they wrote does not clearly match one option, record `unsure` with
their words, and say so.

When the person asks to see the evidence before answering, show the case's line in the latest run's
`results.jsonl` and the changed rows from its evidence file, then ask again.

## 3 - Record

For each answer:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apply_decisions.py" record $system --question Q-004 \
    --answer not-intended --note "the person's own words"
```

The answer is one of `not-intended`, `intended`, `unsure`. The note is what the person wrote, unchanged
and untranslated; leave it out when they only picked an option. Pass it as one shell argument, quoted so
that nothing in it is interpreted by the shell. Add `--cells CELL-0005,CELL-0006` when the answer covers
only some of the question's cases.

## 4 - Apply and re-run (scripts do it)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/apply_decisions.py" apply $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build_matrix.py" $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/harness_install.py" command $system --run matrix --mode verify
```

Do this step when at least one answer was recorded. Run the printed command once, keeping its console
output under `audit/$system/logs/`. It uses the audit's database as the harness step set it up
(`audit/$system/HARNESS.md` says which one; an offered database needs the same three `AUDIT_DB_*`
environment variables in front of the command). **It is expected to fail when something was answered
"not intended":** those cases are now compared with what the person said should happen, and the
application does not do that yet. The build's message for such a case names the decision; a case that
fails with "differs from what was pinned" instead means behaviour changed since it was pinned, and you
report it. Maven's own test counts are not the audit's; the scripts below give the counts to use. Then:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/reconcile.py" $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build_questions.py" $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" findings $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" verdict $system
```

`reconcile.py` prints two things: how the model's predictions compare with what was observed (that does
not change with a decision), and how many cases still do what a decision says they must not.
`judge.py verdict` prints one line per entity; `partial` means reviewed in part, which is the most this
version can say.

## 5 - Report

Read `audit/$system/FINDINGS.md` for the questions just answered. Show what each script printed,
unchanged. Then say these things plainly:

- what was answered "not intended" is now a defect in `FINDINGS.md`, with the cases that reproduce it,
  and a verify run of the matrix will keep failing on those cases until the code is changed. Say in one
  sentence what those cases now check (the question's `if_not_intended`: for instance only that the
  message is no longer dropped silently), because that can be narrower than what the person meant;
- what was answered "not sure" stays open and is not used as a basis for any judgement;
- decisions are in `DECISIONS.md` for good: a later run does not ask again, and a different answer later
  is recorded as a new decision that supersedes the old one.

The plugin does not fix anything. End with the next step: `/java-logic-audit:audit-verify $system`.
