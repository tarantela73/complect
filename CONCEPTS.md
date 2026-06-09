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

### MatchStatus

Reconciliation state of a BankStatementLine. Four states: `UNMATCHED` (no candidate found), `PARTIAL` (single candidate by amount+INN, low confidence), `AMBIGUOUS` (2+ candidates, human must choose), `MATCHED` (single unambiguous match — algorithmic or manual). Only `MATCHED` + `match_confidence=100` is eligible for auto-posting. See ADR-0020 for confidence scoring rules and precedence order.

### match_confidence

Integer 0–100 assigned by the reconciliation algorithm. Values are business invariants defined in ADR-0020: 100 (exact doc_number or manual attach), 85 (amount+date, single hit), 60 (amount+INN, single hit), 50 (any strategy, 2+ hits via doc_number or date), 40 (amount+INN, 2+ hits), 0 (UNMATCHED). Changing these thresholds requires updating the ADR.

### FinanceTransactionFinder

A `typing.Protocol` in `apps/common/contracts/finance_contracts.py` that defines the cross-domain search interface from `apps/payments` into `apps/finance`. Implemented by `DjangoTransactionFinder` in `apps/finance`. The only legal way for the payments domain to look up finance transactions — direct model imports are forbidden (ADR-0005).

---

## Finance Domain

### Transaction

A financial transaction linked to a Contract. Amount stored in kopecks (`BigIntegerField`) — never as float or `Decimal` in the DB. `external_ref` is the bank or 1C payment reference used for reconciliation matching (unique per tenant when non-empty). `value_date` is when money actually cleared, not the posting date.

### TransactionStatus

Lifecycle: `PENDING` (created, not yet reconciled) → `POSTED` (reconciled and accepted into ledger) | `REVERSED` (compensating entry created) | `FAILED` (processing error). Only `PENDING` and `POSTED` transactions are candidates for reconciliation matching (`_MATCHABLE_STATUSES` in `DjangoTransactionFinder`).

### amount_kopecks

Convention used throughout the Finance and Payments domains: monetary amounts are integers in kopecks (1 рубль = 100 копеек). `BankStatementLine.amount` is stored as `Decimal` rubles; `Transaction.amount` is stored as `BigInteger` kopecks. Conversion: `int(decimal_rubles * 100)`. The Protocol `TxCandidateDTO.amount_kopecks` uses kopecks to avoid float arithmetic in cross-domain comparisons.

### TxCandidateDTO

A frozen dataclass (`apps/common/contracts/finance_contracts.py`) returned by `FinanceTransactionFinder` methods. Fields: `transaction_id`, `contract_id`, `amount_kopecks`, `external_ref`, `counterparty_inn`, `value_date`. The only representation of a Finance Transaction visible to the Payments domain.

---

## Contracts Domain

### Contract

A supply contract between a tenant and a counterparty. `resource_type` classifies the type of supply (electricity, gas, water, heat, other). `total_amount` and `paid_amount` are stored in kopecks. `remaining_amount = total_amount - paid_amount` is computed at query time, never stored. Status lifecycle: `DRAFT` → `ACTIVE` → `CLOSED` | `LEGAL_HOLD`.

### ContractStatus

`DRAFT`: created, not yet active. `ACTIVE`: in force, can receive payments. `LEGAL_HOLD`: frozen by legal or compliance action — mutations, soft delete, and retention cleanup are all blocked while in this status. `CLOSED`: lifecycle complete, read-only. Transitions are managed by `ContractLifecycleService` (not yet extracted to service layer).

### Legal Hold

A flag (`is_legal_hold=True` on `BaseEnterpriseModel`) that blocks soft delete, hard delete, mutation, and retention cleanup. Applied when a record is under legal or audit investigation. Checked at the service layer before any mutating operation. Raising a 403 with code `LEGAL_HOLD_ACTIVE` is the correct response.

---

## Common / Infrastructure

### BaseEnterpriseModel

The base Django model inherited by every domain model. Fields: `id` (UUID PK), `tenant_id` (UUID, required), `created_at`, `updated_at`, `deleted_at` (nullable, soft-delete), `is_deleted` (bool), `version` (optimistic concurrency), `is_legal_hold` (bool). Mutations via `EnterpriseSreRepository.atomic_cas_update()`, never via `.save()` or `.delete()`.

### Tenant

A logical isolated organisation within the multi-tenant ERP. Every DB row belongs to exactly one tenant. `tenant_id` is extracted from the JWT claim at the API boundary and propagated via `ContextVar` through the request lifecycle. Cross-tenant data access is a security violation — enforced by requiring `tenant_id` in every QuerySet filter.

### Transactional Outbox

The mechanism for reliable at-least-once domain event delivery. Events are written to `common_outbox_records` inside `transaction.atomic()` alongside the state mutation. A Celery dispatcher (queue `outbox`) polls the table and publishes to Redis. The `transaction.on_commit()` hook triggers a fast-path wake-up signal after commit. Direct Celery task dispatch (bypassing the outbox) is forbidden for state-change notifications.
