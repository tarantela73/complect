# Concepts

Shared domain vocabulary for this project — entities, named processes, and status concepts with project-specific meaning. Seeded with core domain vocabulary, then accretes as ce-compound and ce-compound-refresh process learnings; direct edits are fine. Glossary only, not a spec or catch-all.

---

## Integration Domain

### WebhookSubscription

An external endpoint registered by a tenant to receive domain event notifications. A subscription specifies which event types it listens to, the target URL, and the HMAC secret used to sign payloads. A subscription can be active or inactive; inactive subscriptions are skipped during fan-out.

### WebhookDelivery

A single delivery attempt for one domain event to one WebhookSubscription. Each delivery tracks attempt count, HTTP response, latency, and status. Multiple deliveries may exist for the same event+subscription pair — the fan-out consumer creates the first; replay creates subsequent ones.

Lifecycle: PENDING → DELIVERED (success) or FAILED (non-2xx/network error, retry scheduled) → DEAD (all retries exhausted). A DEAD delivery can be replayed manually, which creates a new delivery record with attempt=1.

### Dead Delivery

A WebhookDelivery whose retry budget is exhausted (status=DEAD). The source event is not re-fetched from the Outbox — the delivery is reconstructed from the record's own metadata. Dead deliveries are surfaced in the Integrations health dashboard and can be replayed by operators.

---

## Payments Domain

### BankStatement

A parsed bank statement file uploaded by an operator. Holds header metadata (account number, period, opening/closing balances) and contains one or more BankStatementLines. Status lifecycle: `parsed` (file processed, pending review) → `imported` (lines accepted, ready for matching) | `cancelled` (rejected by operator). Only `parsed` statements can be confirmed or cancelled.

### BankStatementLine

A single transaction line within a BankStatement. Direction is derived from the source format: `debit` when the field `ДатаСписано` is populated (money left the account), `credit` when `ДатаПоступило` is populated (money arrived). Stores counterparty name, INN, amount, and payment purpose. Links to contracts and finance transactions via plain UUID fields (no FK constraints — cross-domain isolation).
