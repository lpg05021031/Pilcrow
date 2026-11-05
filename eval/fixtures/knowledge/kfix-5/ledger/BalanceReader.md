# BalanceReader

> entity: src/main/java/com/acme/ledger/BalanceReader.java
> approval: seed · last: PR#80 · 2026-08-08

## Summary
Read side of the ledger. Serves balances to the app tier from the projection
table, never from the entry log, because replaying the log per request was
what made the 2026.02 month-end unusable.

## Decisions
- Chose a materialised projection over on-demand replay of the entry log, since month-end concentrates reads on the largest accounts and replay cost grows with account age rather than with traffic. [PR] [BalanceReader.java:37] `PR80-D1`
- Kept the projection eventually consistent with an explicit staleness header instead of reading through the writer's transaction, because a read-through put month-end reporting behind the write lock. [PR] [BalanceReader.java:62] `PR80-D2`

## Invariants
- Balances are read from the projection, never by replaying the entry log on the request path. `PR80-I1`
- Every balance response carries the projection watermark; callers that cannot tolerate staleness compare it themselves. `PR80-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#80 | 2026-08-01 | Projection-based reads with an explicit watermark (feature) · cycle/explicit |
| PR#74 | 2026-07-21 | Fail closed beyond five minutes of lag (bugfix) · cycle/silent |
