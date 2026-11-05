# GammaHandler

> entity: src/main/java/com/acme/routing/GammaHandler.java
> approval: seed · last: PR#72 · 2026-07-28

## Summary
Gamma protocol handler, used by the partner integrations. Partner traffic is
authenticated per frame, which makes it the only handler that touches the
credential cache on the hot path.

## Decisions
- Chose a per-frame signature check over a per-connection handshake because partners multiplex several tenants over one connection. [PR] [GammaHandler.java:57] `PR72-D1`
- Chose to cache partner certificates for five minutes and not longer, because partner rotations are announced with a ten-minute window and a longer cache turns a routine rotation into an outage. [PR] [GammaHandler.java:88] `PR68-D1`
- Kept per-tenant rate limits inside the handler rather than at the gateway, since a single partner connection multiplexes tenants and the gateway cannot see the tenant id before signature verification. [PR] [GammaHandler.java:104] `PR68-D2`

## Invariants
- Signature verification happens before any partner-supplied field is used for routing; a forged zone id would otherwise steer traffic into another tenant's pool. `PR72-I1`
- Credential cache misses fail closed. `PR72-I2`

## History
| PR | Date | Entry |
|---|---|---|
| PR#72 | 2026-07-28 | Handler contract recorded (feature) · cycle/silent |
| PR#67 | 2026-07-01 | Certificate cache capped at five minutes (bugfix) · cycle/explicit |
| PR#61 | 2026-06-12 | Per-tenant rate limits moved into the handler (feature) · cycle/silent |
