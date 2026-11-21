# java-logic-audit

Point Claude at a stateful Java backend and get three things: a model of what the code actually does, the
places where the code disagrees with itself or with its PRD, and a measure of how far "no finding" can be
trusted. Every conclusion traces to a result file, and grades are computed by a script, not by the model.

The design is in [`DESIGN.md`](../DESIGN.md) at the repository root.

## Status

This is a work in progress, built milestone by milestone (design section 18).

| Milestone | Contents | State |
|---|---|---|
| M0 | Plugin skeleton; `audit`, `audit-preflight`, `audit-status`; verification samples 1 and 3 | done |
| M1 | auditkit core, `audit-harness`, `audit-map` | done |
| M2 | `audit-model`, `audit-characterize`, `audit-review`, the judge script | built; on sample 1 a model derived by fresh agents covers all 57 behaviours of the reviewed model. Not yet run on a real system |
| M3–M6 | sequences, faults, concurrency, PRD trace, assurance, report, more stacks | not started |

Commands that belong to a later milestone are named by `audit-status` as the next step but are not
installed yet.

## Try it

```
claude --plugin-dir /path/to/java-logic-audit
```

Then, in the repository you want to audit, on a branch made for the audit:

```
/java-logic-audit:audit
```

It asks what you want (two short questions), writes your answers once to `audit/<system>/INTENT.md`, and
starts preflight. Run `/java-logic-audit:audit-status <system>` at any time to see where you are and the
exact next command.

## What exists so far

| Command | What you get |
|---|---|
| `audit [system]` | Says what you want; writes `INTENT.md`; shows the road |
| `audit-preflight <system>` | What this repository lets the audit prove: technical profile, whether the method applies, where tests would connect, a baseline of the existing tests, a manifest of the source |
| `audit-harness <system>` | The test harness installed and shown to run on the application: six checks, the facts it extracts, and what the audit can prove here |
| `audit-map <system>` | Entry points, entities, and every place each column is written, with the entry points that reach it and the transaction it runs in |
| `audit-model <system> [pattern]` | The command model: for each entry point, the facts its checks look at, the checks in order, and what an allowed call writes. Claims with line numbers, each re-derived by a second reader |
| `audit-characterize <system>` | Every case of every command executed on the application: what agrees is pinned, what differs or would surprise a caller becomes a question with the cases that show it |
| `audit-review <system> [flagged\|all]` | Your answers to those questions, kept for good. "Not intended" becomes a defect that the cases reproduce until the code changes |
| `audit-status <system>` | Progress, what is stale, the next command |

Preflight writes four files under `audit/<system>/`:

| File | Written by | Contents |
|---|---|---|
| `profile.json` | `scripts/preflight_scan.py` | The technical profile, `support` and `applicability`. Never typed by the model |
| `manifest.json` | `scripts/manifest.py` | Every source, test and build file with its hash, to prove later that nothing was touched |
| `answers.json` | the command | The five answers only a person has, verbatim |
| `PREFLIGHT.md` | the command | The report: answers, one row per check, what can be proven, readiness of later commands |

The harness step adds:

| Path | Written by | Contents |
|---|---|---|
| `<module>/src/audit/java/auditkit/` | `scripts/harness_install.py` | The harness: generic Java sources with no logic about your application |
| `<module>/pom-audit.xml` | `scripts/harness_install.py` | Your `pom.xml` plus one profile, regenerated before every run. Your `pom.xml` is never edited, so the artifact of your normal build stays byte for byte the same |
| `audit/<system>/harness.json` | the `harness-engineer` agent | Which outside systems are replaced and with what answers |
| `audit/<system>/facts/` | the harness | Schema, endpoints, listeners, transaction settings, entity mapping, calls and field writes |
| `audit/<system>/runs/` | the harness | One result line per check, with a hash; not committed |
| `profile.json` → `capabilities` | `scripts/judge.py` | What works here and the highest grade each kind of check can reach |

The model, characterize and review steps add:

| Path | Written by | Contents |
|---|---|---|
| `model_raw/` | the commands | Every reply of a reading agent, unchanged. The model is built from these by scripts and never edited by hand |
| `model/commands.json`, `MODEL.md` | `scripts/render_model.py` | The commands, keeping only claims that stand up mechanically |
| `model/fixtures.json`, `model/recipes.json` | `scripts/model_store.py` | How each case is reached, through commands only |
| `model/matrix.json` | `scripts/build_matrix.py` | The cases: a base case per command and one for every other value of every variable, with what reading the code predicts |
| `model/reconciliation.json`, `model/expected.json` | `scripts/reconcile.py` | Prediction against observation; the pinned behaviour |
| `questions.json`, `QUESTIONS.md` | `scripts/build_questions.py` | What only a person can answer |
| `DECISIONS.json`, `DECISIONS.md` | `scripts/apply_decisions.py` | The answers |
| `findings.json`, `FINDINGS.md`, `VERDICT.json`, `VERDICT.md` | `scripts/judge.py` | Findings with status and grade; per entity, how far "nothing found" can be trusted |

What the verdict can say today: an entity is at most "reviewed in part", because four of the seven
checks behind "reviewed completely" (invariants, fault injection, concurrency, guard canary) are not
built yet. The verdict says so itself.

Settings live in `INTENT.md` (`language`, `budget`), not in plugin options: every command reads files, so
a second person or a fresh session continues from the same state.

## Safety

- The plugin never edits a file that already exists. It adds files under `audit/<system>/`, and from the
  harness step on under `src/audit/` plus a generated build entry (`pom-audit.xml`). It never commits,
  pushes or fixes anything.
- The harness empties every table of the database it runs on, and refuses, in code, any database address
  that is not local or a container. Credentials reach it through environment variables only.
- Preflight builds the project and runs its existing tests, so run it only where it is safe to execute
  that repository. Before running any test it lists where the test configuration would connect, and stops
  if an address is not local.
- Files in the audited repository are treated as data. Instruction-shaped text found in them is listed in
  the report and not followed.

## Developing

```
python3 -m unittest discover -s java-logic-audit/scripts/tests -v
```

The tests compare the scanner's output on the samples under `fixtures/` with reviewed expected profiles
(`fixtures/expected/`), and check the reviewed command model of sample 1
(`fixtures/expected/orders-jpa.model/`), which the plugin's own derivation is measured against. Scripts
are Python 3.8+, standard library only. The harness itself (`assets/auditkit/`) is exercised by running
it on sample 1, which needs a JDK, Maven and a local PostgreSQL.
