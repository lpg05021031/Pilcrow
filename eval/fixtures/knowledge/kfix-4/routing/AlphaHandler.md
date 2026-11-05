# AlphaHandler

> entity: src/main/java/com/acme/routing/AlphaHandler.java
> approval: seed · last: PR#70 · 2026-07-28

## Summary
Handles the alpha protocol frames that the legacy edge boxes still speak.
Kept alive only for the two datacentres that have not been migrated; every
change here is expected to be defensive rather than clever.

## Decisions
- Kept the hand-rolled frame parser instead of moving to the shared codec, because the legacy boxes emit a non-standard length prefix that the codec rejects outright. [PR] [AlphaHandler.java:41] `PR70-D1`
- Kept the retired 2019 opcode table in the parser rather than deleting it, because two customer appliances still negotiate down to it and removing the table turns their reconnect into a silent protocol error. [PR] [AlphaHandler.java:96] `PR66-D1`
- Chose to cap alpha connections per datacentre at 200 instead of scaling with the pool, since the legacy boxes leak sockets on their side and the cap is what makes the leak survivable until the migration finishes. [PR] [AlphaHandler.java:118] `PR66-D2`

## Invariants
- Alpha frames are parsed with an explicit length check before the payload is read; the legacy boxes occasionally emit a truncated frame after a link flap and an unchecked read blocks the handler thread until the socket timeout fires. `PR70-I1`
- The handler never mutates the shared RoutingTable; it returns a decision object and the router applies it. `PR70-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#70 | 2026-07-28 | Handler contract recorded (feature) · cycle/silent |
| PR#66 | 2026-06-30 | Legacy opcode table retained for two appliances (other) · cycle/explicit |
| PR#58 | 2026-06-04 | Per-datacentre connection cap introduced (bugfix) · cycle/silent |
