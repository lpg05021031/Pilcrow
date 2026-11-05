# billing

> module seed · approval: seed

## Summary
Invoice issuance and archival. Everything money-touching in this module must
commit atomically; partial writes here are incidents, not bugs.

## Invariants
- issue + archive commit together (see InvoiceService) — @Transactional on the
  entry point, never on internal methods (self-invocation bypasses the proxy).
- Amounts are BigDecimal end to end; long-cents conversions live only at the
  API boundary.

## History
| PR | Date | Entry |
|---|---|---|
| seed | 2026-08-22 | Hand-written module seed · seed |
