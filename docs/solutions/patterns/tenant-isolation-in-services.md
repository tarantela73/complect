---
title: "Tenant isolation: how to scope queries and mutations in service layer"
date: 2026-06-09
category: patterns
module: common
problem_type: architecture_pattern
component: tenancy
applies_when:
  - Writing any service method that reads or writes domain models
  - Adding a new endpoint that touches tenant-scoped data
  - Writing tests that need tenant separation
tags:
  - multitenancy
  - tenant-isolation
  - security
  - orm
  - service-layer
---

# Tenant isolation: how to scope queries and mutations in service layer

## Context

Каждая БД-таблица содержит `tenant_id`. Запрос без фильтра по `tenant_id` — это data leak: пользователь одного тенанта получает данные другого. Одна упущенная строка фильтрации — нарушение безопасности, не просто баг.

## Guidance

**Правило 1. Все QuerySet-ы в сервисах начинаются с `tenant_id`:**

```python
# ✅ правильно
BankStatement.objects.filter(id=stmt_id, tenant_id=tenant_id, is_deleted=False)

# ❌ запрещено — нет фильтра по тенанту
BankStatement.objects.filter(id=stmt_id)
BankStatement.objects.get(id=stmt_id)
```

**Правило 2. Мутации через `.filter(id=..., tenant_id=...)`, никогда через `.get()` + `.save()`:**

```python
# ✅ правильно — одним запросом, tenant_id в WHERE
BankStatementLine.objects.filter(
    id=line.id,
    tenant_id=tenant_id,
).update(match_status=MatchStatus.MATCHED, match_confidence=100)

# ❌ уязвимо — .save() обновит строку даже если tenant_id не совпадает
line.match_status = MatchStatus.MATCHED
line.save()
```

**Правило 3. Cross-domain lookup: только через Protocol с tenant-scoped `_qs()`:**

```python
# ADR-0005: нельзя импортировать модели чужого домена
# ❌ from apps.finance.models import Transaction  — CI failure

# ✅ через Protocol (anti-IDOR guard — ADR-0021)
from apps.finance.services.transaction_finder import get_transaction_finder  # noqa: PLC0415

if get_transaction_finder().find_by_id(tenant_id, transaction_id) is None:
    raise PermissionDenied("TRANSACTION_NOT_IN_TENANT")
```

**Правило 4. В тестах — явно задавать tenant контекст:**

```python
from apps.common.tenancy.context import set_tenant_context

def setup_method(self):
    self.tenant_id = uuid.uuid4()
    set_tenant_context(self.tenant_id)
```

**Правило 5. API-слой: tenant_id извлекается из JWT, не из тела запроса:**

```python
# ✅ из verified JWT claim
def _get_tenant_id(request: HttpRequest) -> uuid.UUID:
    return uuid.UUID(str(request.jwt_tenant_id))

# ❌ никогда не принимать tenant_id от клиента в теле запроса
```

## Why This Matters

Без фильтрации по `tenant_id` ORM вернёт строки любого тенанта. Django не добавляет фильтр автоматически. Единственная защита — явный `.filter(tenant_id=...)` в каждом запросе сервисного слоя.

Anti-IDOR (ADR-0021): даже UUID, переданный в теле запроса, нужно верифицировать через tenant-scoped lookup, потому что клиент может передать UUID из чужого тенанта.

## When to Apply

- Каждый новый метод в `services/` и `queries/`
- Каждый новый API endpoint
- Code review: проверять первую строку каждого QuerySet

## Examples

**Корректный lookup в API:**

```python
# backend/apps/payments/api.py
def _get_line(stmt_id, line_id, tenant_id):
    try:
        return BankStatementLine.objects.get(
            id=line_id,
            statement_id=stmt_id,
            tenant_id=tenant_id,   # ← обязателен
            is_deleted=False,
        )
    except BankStatementLine.DoesNotExist:
        return None
```

**Тест на tenant isolation:**

```python
@pytest.mark.django_db
def test_attach_foreign_tenant_does_not_mutate_db(monkeypatch):
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    # line принадлежит tenant_a
    line = _make_line(stmt, tenant_a)
    # attach вызывается с transaction_id из tenant_b → PermissionDenied
    monkeypatch.setattr(
        "apps.finance.services.transaction_finder.get_transaction_finder",
        lambda: _mock_finder(find_by_id_result=None),
    )
    with pytest.raises(PermissionDenied):
        ManualMatchService.attach(tenant_id=tenant_a, line=line,
                                  transaction_id=uuid.uuid4(), confirmed_by=uuid.uuid4())
    refreshed = BankStatementLine.objects.get(id=line.id)
    assert refreshed.transaction_id is None  # БД не мутировала
```
