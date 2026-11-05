# MoneyFormat

> entity: src/main/java/com/acme/money/MoneyFormat.java
> approval: seed · last: PR#85 · 2026-08-12

## Summary
The money type boundary for the whole service: parsing, scaling and formatting
of monetary amounts. Nothing in this codebase is allowed to invent its own
arithmetic for money, and the ledger package is the one that keeps trying,
because its inputs arrive as rates and percentages rather than as amounts.

## Decisions
- Chose BigDecimal with an explicit scale over long-cents after the 2025.11
  migration, because several currencies in the partner set use three decimal
  places and the long-cents helpers silently truncated them. [PR]
  [MoneyFormat.java:31] `PR85-D1`
- Kept parsing and formatting in one class so that the scale used to parse an
  amount is provably the scale used to print it; the earlier split produced
  statements whose totals did not match their rows. [PR] [MoneyFormat.java:58]
  `PR85-D2`

## Invariants
- Money is BigDecimal end to end. No intermediate step converts an amount to
  double or float — not for a percentage, not for a fee rate, not "just for the
  multiplication". Binary floating point cannot represent the decimal rates used
  by the fee schedules, so a double round-trip loses fractions of a cent per row
  and reconciliation breaks at month-end scale, long after the change shipped.
  `PR85-I3`
- Scale is set once at the boundary and carried; downstream code must not call
  setScale to "tidy" a value. `PR85-I4`

## Affected entities
`FeeCalculator.java`, `StatementBuilder.java`, `SettlementExport.java`

## History
| PR | Date | Entry |
|---|---|---|
| PR#85 | 2026-08-12 | BigDecimal end to end; no double round-trips (bugfix) · cycle/explicit |
| PR#79 | 2026-07-29 | Parse and format kept in one class (refactor) · cycle/silent |
