# BetaHandler

> entity: src/main/java/com/acme/routing/BetaHandler.java
> approval: seed · last: PR#71 · 2026-07-28

## Summary
Beta protocol handler — the current default for edge traffic. Carries the
backpressure contract for the whole routing package: when the downstream
queue is full the handler must refuse, not buffer.

## Decisions
- Chose bounded refusal over an unbounded in-memory queue after the 2026.03 outage, where buffering turned a five-minute downstream stall into a forty-minute heap exhaustion. [PR] [BetaHandler.java:88] `PR71-D1`
- Chose a fixed 8 KiB read buffer over an adaptive one because the adaptive sizing interacted badly with the refusal path: a large buffer made the handler look healthy while the downstream queue was already saturated. [PR] [BetaHandler.java:131] `PR67-D1`
- Kept metrics emission synchronous inside the handler so that refusals are counted even when the dispatch executor is the component that is stuck. [PR] [BetaHandler.java:146] `PR67-D2`

## Invariants
- When the downstream queue reports full, the handler answers with a retryable refusal and drops the frame; it must not buffer frames on the heap. `PR71-I1`
- Handler threads are never blocked on I/O; anything blocking goes to the dispatch executor. `PR71-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#71 | 2026-07-28 | Handler contract recorded (feature) · cycle/silent |
| PR#64 | 2026-06-24 | Fixed read buffer; adaptive sizing reverted (revert) · cycle/explicit |
| PR#59 | 2026-06-06 | Refusal metrics made synchronous (bugfix) · cycle/silent |
