# StatementBuilder

> entity: src/main/java/com/acme/ledger/StatementBuilder.java
> approval: seed · last: PR#83 · 2026-08-08

## Summary
Renders monthly statements from entries. Pure assembly: it must not compute
anything that an entry already carries, or two sources of truth appear.

## Decisions
- Chose to render from stored entries only, with no recomputation, after a statement reprint disagreed with the original by four cents. [PR] [StatementBuilder.java:41] `PR83-D1`
- Kept statement generation idempotent by keying on (account, period) so that a retried job overwrites rather than duplicates. [PR] [StatementBuilder.java:63] `PR83-D2`

## Invariants
- Statements are assembled from stored entries; no figure is recomputed at render time. `PR83-I1`
- Generation is idempotent per (account, period). `PR83-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#83 | 2026-08-07 | Render-from-entries only, no recomputation (bugfix) · cycle/explicit |
| PR#78 | 2026-07-27 | Streaming render for large accounts (perf) · cycle/silent |
