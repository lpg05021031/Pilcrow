# DeltaHandler

> entity: src/main/java/com/acme/routing/DeltaHandler.java
> approval: seed · last: PR#73 · 2026-07-28

## Summary
Delta protocol handler for the internal control plane. Low traffic, high
blast radius: it carries the drain and failover commands.

## Decisions
- Kept delta on its own listener port so that a flood of edge traffic cannot starve control-plane commands. [PR] [DeltaHandler.java:33] `PR73-D1`
- Chose to reject rather than queue commands that arrive while a drain is in progress; queueing produced command storms after the drain finished during the 2026.05 failover drill. [PR] [DeltaHandler.java:61] `PR69-D1`
- Kept the control-plane listener on a dedicated thread pool of two threads, sized deliberately small so that a runaway operator script cannot consume the shared executor. [PR] [DeltaHandler.java:77] `PR69-D2`

## Invariants
- Control-plane commands are idempotent and carry a monotonic epoch; an out-of-order drain command must be ignored, not replayed. `PR73-I1`
- The handler logs every accepted command with its epoch. `PR73-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#73 | 2026-07-28 | Handler contract recorded (feature) · cycle/silent |
| PR#65 | 2026-06-26 | Commands rejected during drain instead of queued (bugfix) · cycle/explicit |
| PR#57 | 2026-06-02 | Dedicated two-thread control-plane pool (feature) · cycle/silent |
