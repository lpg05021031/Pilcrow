# FeeCalculator

> entity: src/main/java/com/acme/ledger/FeeCalculator.java
> approval: seed · last: PR#82 · 2026-08-08

## Summary
Computes transaction fees for the ledger. Small class, disproportionate blast
radius: its output is written as a ledger entry and cannot be corrected in
place afterwards.

## Decisions
- Chose to resolve the fee schedule at entry time and store the resolved rate on the entry, because schedules change and a recomputed fee would not match the entry that was written. [PR] [FeeCalculator.java:29] `PR82-D1`
- Kept rounding at the fee level rather than at the statement level, so that the sum of the entries always equals the statement total. [PR] [FeeCalculator.java:54] `PR82-D2`

## Invariants
- The resolved rate is stored on the entry; fees are never recomputed from the current schedule when reprinting an old statement. `PR82-I1`
- Rounding is HALF_EVEN at the fee level. `PR82-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#82 | 2026-08-05 | Resolved rate stored on the entry (bugfix) · cycle/explicit |
| PR#77 | 2026-07-26 | HALF_EVEN aligned with the settlement partner (bugfix) · cycle/silent |
