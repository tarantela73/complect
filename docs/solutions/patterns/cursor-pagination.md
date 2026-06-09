---
title: "Cursor pagination: WHERE id < cursor ORDER BY id DESC"
date: 2026-06-09
category: patterns
module: common
problem_type: architecture_pattern
component: pagination
applies_when:
  - Adding list endpoints for any domain model
  - "Adding pagination to: audit logs, finance, notifications, websocket events, chats"
  - Replacing OFFSET pagination in existing queries
tags:
  - pagination
  - cursor
  - performance
  - queries
  - adr-0008
---

# Cursor pagination: WHERE id < cursor ORDER BY id DESC

## Context

`OFFSET N` деградирует по скорости с ростом данных: PostgreSQL сканирует первые N строк, чтобы их пропустить. На таблице с 1M строк страница 500 требует перебора 499 страниц. Для финансов, аудита, нотификаций — это неприемлемо.

`WHERE id < cursor ORDER BY id DESC` использует B-tree индекс напрямую и работает за O(log N) независимо от номера страницы. ADR-0008 запрещает OFFSET для перечисленных доменов.

## Guidance

**Query Side (selectors/queries):**

```python
def get_statements_page(
    tenant_id: uuid.UUID,
    cursor: uuid.UUID | None = None,
    limit: int = 20,
) -> dict:
    qs = BankStatement.objects.filter(
        tenant_id=tenant_id,
        is_deleted=False,
    ).order_by("-id")                   # ← DESC по UUID (v4 не монотонный, но created_at+id combo работает)

    if cursor:
        qs = qs.filter(id__lt=cursor)   # ← WHERE id < cursor

    rows = list(qs[: limit + 1])        # ← берём limit+1 для проверки наличия следующей страницы
    has_next = len(rows) > limit
    rows = rows[:limit]

    return {
        "results": rows,
        "next_cursor": str(rows[-1].id) if has_next and rows else None,
    }
```

**API Schema:**

```python
class ListOut(Schema):
    items: list[ItemOut]
    next_cursor: Optional[uuid.UUID] = None

@router.get("/items/")
def list_items(
    request: HttpRequest,
    cursor: Optional[uuid.UUID] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    result = get_statements_page(tenant_id=..., cursor=cursor, limit=limit)
    return 200, ListOut(items=result["results"], next_cursor=result["next_cursor"])
```

**Frontend (TanStack Query + generated SDK):**

```typescript
// nextCursor из предыдущей страницы передаётся как cursor в следующий запрос
const { data, fetchNextPage } = useInfiniteQuery({
  queryKey: ['statements'],
  queryFn: ({ pageParam }) => api.getStatements({ cursor: pageParam }),
  getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
})
```

**Что нельзя использовать:**

```python
# ❌ OFFSET — запрещён для: audit, finance, notifications, ws events, chats (ADR-0008)
qs[page * limit : (page + 1) * limit]
qs.offset(page * limit).limit(limit)
```

## Why This Matters

На таблице 100k+ строк OFFSET-запрос на страницу 1000 может занимать секунды. Cursor-пагинация стабильна: каждый запрос использует индекс `(tenant_id, id)` и выдаёт результат за константное время.

Дополнительный бонус: cursor-пагинация не пропускает и не дублирует строки при конкурентной вставке между страницами (OFFSET — пропускает).

## When to Apply

Обязательно для: audit logs, finance transactions, bank statement lines, notifications, WebSocket events, chats.

Для административных списков с малым объёмом данных (< 10k строк) OFFSET допустим, но cursor рекомендуется.

## Examples

Реальный пример из `payments/api.py`:

```python
# cursor через created_at (альтернативный вариант — по created_at+id для монотонности)
if cursor:
    anchor = BankStatement.objects.filter(id=cursor, tenant_id=tenant_id).values("created_at").first()
    if anchor:
        qs = qs.filter(created_at__lt=anchor["created_at"])

items = list(qs[: limit + 1])
next_cursor = items[limit].id if len(items) > limit else None
```

Реальный пример из `contracts/queries/contract_queries.py`:

```python
qs = qs.order_by("-id")
if cursor:
    qs = qs.filter(id__lt=cursor)      # чистый WHERE id < cursor
rows = list(qs[: limit + 1])
has_next = len(rows) > limit
next_cursor = str(rows[-1].id) if has_next and rows else None
```
