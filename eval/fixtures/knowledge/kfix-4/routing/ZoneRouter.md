# ZoneRouter

> entity: src/main/java/com/acme/routing/ZoneRouter.java
> approval: seed · last: PR#75 · 2026-07-30

## Summary
Picks the destination pool for a frame from its zone id. The zone ids reaching
this class come from two sources with different lifetimes, which is the whole
reason this page exists.

## Decisions
- Kept zone ids as plain strings rather than interning them or wrapping them in
  a ZoneId value type, because the partner-supplied ids are parsed at runtime
  and any wrapper would have to be created on the hot path anyway. [PR]
  [ZoneRouter.java:44] `PR75-D1`

## Invariants
- Zone ids must be compared with equals(). The ids in the static routing table
  are compile-time constants and therefore interned, while the ids arriving on
  partner frames are parsed at runtime and are not; reference comparison happens
  to work for the constants and silently fails for parsed ids, sending partner
  traffic to the default pool without any error. `PR75-I1`

## History
| PR | Date | Entry |
|---|---|---|
| PR#75 | 2026-07-30 | Zone ids kept as plain strings; equals() mandated (bugfix) · cycle/explicit |
