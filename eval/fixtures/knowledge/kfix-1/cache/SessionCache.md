# SessionCache

> entity: src/main/java/com/acme/cache/SessionCache.java
> approval: seed · last: PR#41 · 2026-06-11

## Summary
Holds authenticated sessions for the edge tier. The one thing newcomers get
wrong is the time unit: the cache speaks milliseconds everywhere, while the
properties file that feeds it is written in seconds.

## Decisions
- Kept a single millisecond-based TTL on the cache boundary instead of an
  overloaded setTtl(value, unit), because the overload was silently picked by
  autoboxing at two call sites during the 2026.04 incident. [PR] [SessionCache.java:58] `PR41-D1`

## Invariants
- Every TTL crossing the SessionCache boundary is in milliseconds. A caller
  reading a value configured in seconds must multiply by 1000 before calling
  setTtl; passing the raw seconds value expires sessions a thousand times too
  early and shows up as random logouts, not as an error. `PR41-I1`

## Affected entities
`SessionConfigLoader.java`, `EdgeAuthFilter.java`

## History
| PR | Date | Entry |
|---|---|---|
| PR#41 | 2026-06-11 | TTL unit fixed at milliseconds on the cache boundary (bugfix) · cycle/explicit |
