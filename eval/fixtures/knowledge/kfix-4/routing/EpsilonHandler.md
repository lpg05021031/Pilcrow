# EpsilonHandler

> entity: src/main/java/com/acme/routing/EpsilonHandler.java
> approval: seed · last: PR#74 · 2026-07-28

## Summary
Epsilon protocol handler — the experimental path behind a feature flag.
Present in production but disabled by default; kept in the routing package
so that the shared decision object stays honest about all five protocols.

## Decisions
- Chose a compile-time-registered handler over dynamic discovery so that an unflagged build cannot accidentally expose the experiment. [PR] [EpsilonHandler.java:24] `PR74-D1`
- Chose to mirror epsilon traffic into the beta path for comparison instead of routing it live, so that an experiment defect can never affect a real customer response. [PR] [EpsilonHandler.java:52] `PR70-D1`
- Kept the experiment's metrics under a separate prefix so that dashboards built on the routing package do not silently mix experimental traffic into production baselines. [PR] [EpsilonHandler.java:69] `PR70-D2`

## Invariants
- The epsilon path checks the feature flag on every frame, not once at startup; the flag is flipped during incidents and a cached value would keep the experiment live. `PR74-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#74 | 2026-07-28 | Handler contract recorded (feature) · cycle/silent |
| PR#68 | 2026-07-05 | Epsilon mirrored, never routed live (feature) · cycle/silent |
| PR#62 | 2026-06-15 | Experiment metrics moved to their own prefix (other) · cycle/silent |
