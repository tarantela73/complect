---
title: "Cross-domain aggregation: delegate to domain queries, re-export function references"
date: 2026-06-24
category: docs/solutions/patterns/
module: apps.common
problem_type: architecture_pattern
component: service_object
severity: high
applies_when:
  - "a cross-domain aggregator in apps/common/ needs data owned by multiple bounded contexts"
  - "a shared dashboard or analytics module tempts you to write from apps.<domain>.models import X"
  - "a domain lacks a queries/ module but its data is needed outside the domain"
  - "ADR-0005 review flags cross-domain ORM model imports"
root_cause: scope_issue
resolution_type: code_fix
related_components:
  - apps/common/queries/director_queries.py
  - apps/contracts/queries/contract_queries.py
  - apps/objects/queries/object_queries.py
  - apps/payments/queries/payment_queries.py
tags:
  - adr-0005
  - domain-isolation
  - cqrs
  - cross-domain
  - re-exporter
  - aggregation
  - architecture
---

# Cross-domain aggregation: delegate to domain queries, re-export function references

## Context

ADR-0005 (Domain Isolation) forbids direct ORM model imports across domain boundaries. The prohibition applies at the model layer: `from apps.contracts.models import Contract` inside `apps/objects/` or `apps/common/` is a CI failure. When building a shared aggregation point — a director dashboard, an analytics module, a cross-domain report — the temptation is to import all the necessary models directly in one place. This was the violation in `apps/common/queries/director_queries.py`, which imported `Contract`, `SupplyObject`, `BankStatement`, and `BankStatementLine` directly from their respective domains, coupling `apps/common/` to the internal schema of three separate domains.

## Guidance

**Pattern: domain-specific aggregation functions + thin re-exporter.**

Each domain exposes the data it owns through a function in its own `queries/` module. The cross-domain aggregator in `apps/common/` imports only those functions — never models.

**Step 1.** Each domain defines its own aggregation function in `queries/`:

```python
# apps/contracts/queries/contract_queries.py
def get_open_contracts_count() -> int:
    return ContractRepository.get_queryset().exclude(status=ContractStatus.CLOSED).count()
```

```python
# apps/objects/queries/object_queries.py
def get_objects_summary() -> dict:
    today = date.today()
    qs = ObjectRepository.get_queryset().filter(status=ObjectStatus.IN_PROGRESS)
    return {
        "in_progress_count": qs.count(),
        "top5": list(qs.order_by("name").values("id", "name")[:5]),
        "overdue_count": qs.filter(
            planned_end_date__lt=today,
            planned_end_date__isnull=False,
        ).count(),
    }
```

```python
# apps/payments/queries/payment_queries.py  (new module — domain had no Query Side)
def get_payments_month_summary() -> dict[str, Decimal]:
    tenant_id = get_tenant_id()
    today = date.today()
    month_start = today.replace(day=1)
    imported_ids = BankStatement.objects.filter(
        tenant_id=tenant_id, status=BankStatementStatus.IMPORTED, is_deleted=False,
    ).values_list("id", flat=True)
    base_qs = BankStatementLine.objects.filter(
        tenant_id=tenant_id, statement_id__in=imported_ids,
        transaction_date__gte=month_start, transaction_date__lte=today, is_deleted=False,
    )
    agg = base_qs.aggregate(
        credit_sum=Sum("amount", filter=Q(direction="credit")),
        debit_sum=Sum("amount", filter=Q(direction="debit")),
    )
    return {
        "credit": agg["credit_sum"] or Decimal("0.00"),
        "debit": agg["debit_sum"] or Decimal("0.00"),
    }
```

**Step 2.** `apps/common/queries/director_queries.py` becomes a thin re-exporter — zero ORM imports:

```python
"""
CQRS Query Side — Director Dashboard KPI aggregation.
Re-exports domain-specific query functions. No direct cross-domain ORM imports (ADR-0005).
"""
from __future__ import annotations

from apps.contracts.queries.contract_queries import get_open_contracts_count
from apps.objects.queries.object_queries import get_objects_summary
from apps.payments.queries.payment_queries import get_payments_month_summary

__all__ = ["get_open_contracts_count", "get_objects_summary", "get_payments_month_summary"]
```

## Why This Matters

A direct cross-domain model import creates hidden coupling at the database schema level. Renaming a field, splitting a model, or moving it to another domain breaks every file that imported it — with no contract to detect the break until runtime. The violation is invisible to code review because the import resolves at startup; the breakage only surfaces when the internal schema changes.

Importing a **function** from another domain's `queries/` module is a stable contract. The ORM schema can evolve inside the domain without affecting consumers, as long as the function signature and return semantics remain stable. This is the same distinction as a public API vs its implementation.

A secondary effect: discovering that a domain has no `queries/` module forces you to create one. This happened with `apps/payments/` — it had no Query Side before this fix. The pattern surfaces missing CQRS structure naturally.

| Import type | Permitted | Reason |
|---|---|---|
| `from apps.contracts.models import Contract` | No | Couples to ORM schema |
| `from apps.contracts.queries.contract_queries import get_open_contracts_count` | Yes | Stable function contract |
| `from apps.common.contracts import SomeProtocol` | Yes | Explicit shared contracts module |

ADR-0005 states the prohibition (no model imports) but does not document the compliant aggregation pattern. This doc fills that gap.

## When to Apply

Apply whenever a module in `apps/common/` or any other cross-domain location needs data owned by multiple bounded contexts. The trigger is the thought: "I need to import a model from another domain here."

Do not create Query Side wrappers for trivial passthrough: a function that does nothing but `return Model.objects.all()` with no tenant filtering, no business scoping, and no domain logic is cargo cult. The Query Side function must encapsulate real selection logic that the caller should not need to know about.

## Examples

**Before — ADR-0005 violation:**

```python
# apps/common/queries/director_queries.py
from apps.contracts.models import Contract, ContractStatus          # ❌
from apps.objects.models.supply_object import SupplyObject, ObjectStatus  # ❌
from apps.payments.models.bank_statement import BankStatement       # ❌
from apps.payments.models.bank_statement_line import BankStatementLine    # ❌

def get_director_kpi() -> dict:
    open_contracts = Contract.objects.exclude(status=ContractStatus.CLOSED).count()
    in_progress = SupplyObject.objects.filter(status=ObjectStatus.IN_PROGRESS).count()
    ...
```

`apps/common/` now knows the internal schema of three domains. Any internal rename cascades here.

**After — compliant re-exporter:**

```python
# apps/common/queries/director_queries.py
from apps.contracts.queries.contract_queries import get_open_contracts_count   # ✅
from apps.objects.queries.object_queries import get_objects_summary            # ✅
from apps.payments.queries.payment_queries import get_payments_month_summary   # ✅

__all__ = ["get_open_contracts_count", "get_objects_summary", "get_payments_month_summary"]
```

Each domain owns its selection logic. `apps/common/` depends only on stable function signatures.

## Related

- [tenant-isolation-in-services.md](tenant-isolation-in-services.md) — ADR-0005 enforcement in service layer; Protocol-based cross-domain sync (complementary pattern)
- [docs/adr/0005-bounded-context-ownership.md](../../adr/0005-bounded-context-ownership.md) — the governing decision; forbids model imports, mandates Protocol + Outbox for cross-domain communication
