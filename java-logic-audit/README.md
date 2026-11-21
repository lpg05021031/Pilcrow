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
| M3 | `audit-verify`: failures injected into every step, random sequences with rules of the data | built and run on sample 1. Not built: comparing paths that should agree (preview against commit), relations between calls, rules derived from the code's own checks |
| M4 | `audit-verify` also runs two commands at once, stopping one between its database steps | built and run on sample 1: one stop per schedule, pairs chosen from cases that share a starting situation |
| M5 | `audit-spec`, `audit-assure`, `audit-report`, `audit-rerun` | built, and each run through the plugin by a fresh headless session on sample 1. Not built: mutation testing beyond the guard canary, and seeds taken from the project's history |
| M6 | A second stack (Spring Boot 2.7, MyBatis, MySQL 8, Gradle, JUnit 4) and a hostile sample | done: the whole road from `audit` to `audit-report` ran on sample 2 through the plugin, command by command, each in a fresh headless session; on the hostile sample no planted instruction was followed and every one was reported |

What has not happened: no command has been run on a real system, and nothing was run in an interactive
session with a person answering pop-up questions (the headless runs took their answers from the request).
The samples are small (about 700 and 1,800 lines), so what they show is that each step works and what it
costs to start, not how the cost grows.

## Try it

Install it from this repository (Claude Code 2.1.275 or later):

```
/plugin install java-logic-audit --marketplace lpg05021031/Pilcrow
```

or, from the one-file installer (`python3 tools/make_installer.py` in this repository builds it), which
unpacks the plugin as a marketplace in a local directory and prints the two commands to add and install it:

```
sh java-logic-audit-<version>.sh [directory]
```

or load it from a checkout without installing:

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

## What exists

| Command | What you get |
|---|---|
| `audit [system]` | Says what you want; writes `INTENT.md`; shows the road |
| `audit-preflight <system>` | What this repository lets the audit prove: technical profile, whether the method applies, where tests would connect, a baseline of the existing tests, a manifest of the source |
| `audit-harness <system>` | The test harness installed and shown to run on the application: six checks, the facts it extracts, and what the audit can prove here |
| `audit-map <system>` | Entry points, entities, and every place each column is written, with the entry points that reach it and the transaction it runs in |
| `audit-model <system> [pattern]` | The command model: for each entry point, the facts its checks look at, the checks in order, and what an allowed call writes. Claims with line numbers, each re-derived by a second reader |
| `audit-characterize <system>` | Every case of every command executed on the application: what agrees is pinned, what differs or would surprise a caller becomes a question with the cases that show it |
| `audit-review <system> [flagged\|all]` | Your answers to those questions, kept for good. "Not intended" becomes a defect that the cases reproduce until the code changes |
| `audit-verify <system> [faults\|races\|sequences\|all]` | Whether two commands at the same time leave what one after the other would; what a failure in the middle of a call leaves behind (data in between, something sent that was not kept, a retry that does not converge); and rules of the data checked after every call of random sequences, with the shortest sequence that breaks one |
| `audit-spec <system> <file>` | Your specification, clause by clause, against what was observed. Readers that never see the code write what each clause requires; a script ties it to a case and says where the two agree, differ, or cannot be compared, and which behaviour no clause speaks about |
| `audit-assure <system> [recheck\|canary\|seeds <file>\|all]` | How far the audit itself can be trusted: every finding handed to a reader who is told nothing but the statement and the evidence; every cited check switched off in a copy of the code to see whether a case notices; defects you supply seeded one at a time |
| `audit-report <system>` | Findings, the verdict per entity with its seven checks, what was run, open questions, decisions and an empty sign-off, in one self-contained `REPORT.html` |
| `audit-rerun <system>` | After the code changed: which cases are still valid, which must run again, what behaves differently than pinned, which findings are fixed |
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

The verify step adds `model/invariants.json` (candidate rules from fixed templates over the schema, and
what the runs showed about each; written by `scripts/invariants.py`) and the runs of the failure and
sequence checks.

The later steps add:

| Path | Written by | Contents |
|---|---|---|
| `spec/sections.json`, `spec/vocabulary.json` | `scripts/spec.py split` | The document cut at its headings, and the names an analyst is given: commands, variables, values. Nothing about behaviour |
| `spec/raw/` | the command | Every reply of an analyst and of the second reader, unchanged |
| `spec/clauses.json`, `spec/expectations.json`, `SPEC_TRACE.md` | `scripts/spec.py join` | Clauses whose quote stands word for word in the document, each expectation with the case it was tied to and the result |
| `assure/recheck/` | the command | Every reply of an independent reader, unchanged |
| `assure/canary.json` | `scripts/canary.py` | For each cited check: whether a case noticed when it was switched off |
| `assure/seeds.json` | `scripts/seed_run.py` | For each seeded defect: which checks noticed |
| `ASSURANCE.md` | `scripts/assure.py` | The three together |
| `REPORT.html` | `scripts/build_report.py` | The report |

What the verdict can say: an entity is "reviewed completely" only when all seven checks pass, and several
of them cannot pass without something the plugin cannot give itself: a database confirmed to match
production, a person's confirmation of at least one rule of the data, and a complete canary run with no
survivor. The verdict names the check that held it back.

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
(`fixtures/expected/orders-jpa.model/`), which the plugin's own derivation is measured against. `fixtures/`
also holds a specification for sample 1 and sixteen seeded defects with what noticed each. Scripts
are Python 3.8+, standard library only. The harness itself (`assets/auditkit/`) is exercised by running
it on sample 1, which needs a JDK, Maven and a local PostgreSQL.
