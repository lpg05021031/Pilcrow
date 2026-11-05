# EntryWriter

> entity: src/main/java/com/acme/ledger/EntryWriter.java
> approval: seed · last: PR#81 · 2026-08-08

## Summary
Write side of the ledger: appends double-entry rows and nudges the projection.
The append is the system of record; everything else in this package is a
derived view and can be rebuilt from it.

## Decisions
- Chose append-only rows with a reversal convention over in-place correction, because auditors require the original entry to remain readable after a correction. [PR] [EntryWriter.java:45] `PR81-D1`
- Kept the projection update inside the same transaction as the append so that a crash cannot leave a balance that no entry explains. [PR] [EntryWriter.java:71] `PR81-D2`

## Invariants
- Entries are append-only. Corrections are new reversing entries; no row is ever updated or deleted. `PR81-I1`
- The projection update commits with the append, in one transaction. `PR81-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#81 | 2026-08-03 | Append-only with reversal convention (feature) · cycle/explicit |
| PR#76 | 2026-07-24 | Per-account sequence replaces the global one (perf) · cycle/silent |
