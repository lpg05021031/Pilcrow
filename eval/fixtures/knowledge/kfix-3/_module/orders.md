# orders

> module seed · approval: seed

## Summary
Order lifecycle and its audit trail. The module's whole reason for having a
hand-written page is the audit contract below: it is invisible in the code and
every reviewer who has not been burned by it reads the async version as an
optimisation.

## Invariants
- Audit rows are written inside the same transaction as the mutation they
  describe. An audit write that runs after commit — @Async, an event listener
  bound to AFTER_COMMIT, or a second transaction — survives a rollback of the
  mutation and produces an audit trail describing state that never existed.
  Compliance reads this table as ground truth. `PR63-I2`
- Order state transitions go through OrderStateMachine; no service sets
  `order.status` directly. `PR63-I3`

## History
| PR | Date | Entry |
|---|---|---|
| PR#63 | 2026-07-19 | Audit writes pinned to the mutating transaction (bugfix) · cycle/silent |
