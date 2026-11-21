---
description: Where am I? Progress, what is stale, and the exact next command — run it any time
argument-hint: <system>
arguments: system
---

Report where the audit of `$system` stands, in one screen. This command inspects and never modifies.

If `$system` was not given, list `audit/`: one system means use it, several means say which exist and ask
for one, none means the answer is `/java-logic-audit:audit` (the front door).

Run, from the workspace root:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/status.py" $system
```

Show its table and its last three lines **unchanged**: *Where you are*, *What is stale*, *Next command*.
The script computes them from the files under `audit/$system/` by fixed rules, so do not replace its answer
with your own reading of the conversation. If the script cannot run (`python3` missing), say so in one line
and list which of these files exist instead: `INTENT.md`, `PREFLIGHT.md`, `profile.json`, `harness.json`,
`map.json`, `MODEL.md`, `questions.json`, `DECISIONS.json`, `VERDICT.md`.

Then add at most three lines of your own, only where they help:

- If a stage is `partial`, name the missing files and the command that writes them.
- If the source changed since preflight, say that results recorded before the change no longer describe the
  code, and that `audit-rerun` re-checks only what the change touched.
- If the next command belongs to a later milestone of the plugin and is not installed, say so plainly.
