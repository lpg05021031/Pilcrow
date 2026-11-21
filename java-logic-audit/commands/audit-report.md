---
description: The one-page report - findings with their evidence, what was checked for each entity, and what still waits for a person
argument-hint: <system>
arguments: system
---

Produce the report of the audit of `$system`: one HTML file that opens anywhere, with the findings and
the evidence behind each, the seven checks entity by entity, what was run, the open questions, the
decisions, and a sign-off block. Every number in it is computed by a script from result files at this
moment; nothing is carried over from an earlier report and nothing is typed in.

Read first: `audit/$system/INTENT.md`. If `audit/$system/questions.json` does not exist, stop: there is
nothing to report before `/java-logic-audit:audit-characterize $system`.

Ground rules:

- Run every command from the workspace root. Write nothing by hand.
- The report says what the files say. Do not soften a verdict, upgrade a grade, or leave a finding out;
  if you think a script is wrong, say so in your reply and leave its output as it is.
- Speak to the person in the language `INTENT.md` names (`auto`: the language they write in). The report
  itself is in English.

## 1 - Recompute, then lay out

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/reconcile.py" $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build_questions.py" $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" findings $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/judge.py" verdict $system
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/manifest.py" check . --manifest audit/$system/manifest.json
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/build_report.py" $system
```

When the manifest check answers `changed`, the source is no longer what the results were made from: say
so first, name the files, and point to `/java-logic-audit:audit-rerun $system`. The report is still
written, and its verdict already shows the source item as failed.

## 2 - Tell the person what it says

Give the path of `audit/$system/REPORT.html`, then in at most twelve lines:

- the verdict per entity, as printed, and for each entity that is not "reviewed completely" the items
  that kept it from it, in plain words;
- the findings by class: defects (a person said "not intended", or a confirmed rule broke), then
  inconsistencies and anomalies that still wait for an answer, with their grades;
- how many flagged questions are open, and that until they are answered those findings are observations;
- what was not run at all (a part of `audit-verify` skipped, no canary run, no specification), because a
  missing check reads the same as a clean one unless it is said.

Then say what a signature on this report would mean: that the person has read the open questions and
the list of what was not checked, and accepts the verdict for the scope stated. The plugin never signs.

There is no next command: this is the end of the road. `/java-logic-audit:audit-rerun $system` is the
way back in after the code changes.
