---
title: "Cursor pagination: WHERE id < cursor ORDER BY id DESC"
date: 2026-06-09
last_updated: 2026-06-24
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
  - uuid-pitfall
  - composite-cursor
---

# Cursor pagination: WHERE id < cursor ORDER BY id DESC

## Context

`OFFSET N` деградирует по скорости с ростом данных: PostgreSQL сканирует первые N строк, чтобы их пропустить. На таблице с 1M строк страница 500 требует перебора 499 страниц. Для финансов, аудита, нотификаций — это неприемлемо.

`WHERE id < cursor ORDER BY id DESC` использует B-tree индекс напрямую и работает за O(log N) независимо от номера страницы. ADR-0008 запрещает OFFSET для перечисленных доменов.

## Guidance

**Query Side (selectors/queries) — целочисленный PK:**

```python
def get_statements_page(
    tenant_id: uuid.UUID,
    cursor: int | None = None,
    limit: int = 20,
) -> dict:
    qs = BankStatement.objects.filter(
        tenant_id=tenant_id,
        is_deleted=False,
    ).order_by("-id")                   # OK только для serial/bigserial PK

    if cursor:
        qs = qs.filter(id__lt=cursor)   # WHERE id < cursor

    rows = list(qs[: limit + 1])        # берём limit+1 для проверки наличия следующей страницы
    has_next = len(rows) > limit
    rows = rows[:limit]

    return {
        "results": rows,
        "next_cursor": rows[-1].id if has_next and rows else None,
    }
```

**UUID v4 Primary Keys: `id__lt=cursor` неверен — нужен составной `(created_at, id)` курсор**

UUID v4 — случайные 128-битные значения. `order_by("-id")` на UUID-поле даёт недетерминированный порядок: каждая новая вставка меняет логическую последовательность. `id__lt=cursor` сравнивает UUID лексикографически, что не связано со временем вставки. Результат: страницы пересекаются, строки пропускаются, total count не сходится.

**Правильный паттерн для UUID PK:**

```python
from django.db.models import Q

def get_tasks_page(
    tenant_id: uuid.UUID,
    cursor: uuid.UUID | None = None,   # UUID задачи — API-контракт не меняется
    limit: int = 20,
) -> dict:
    qs = Task.objects.filter(
        tenant_id=tenant_id,
        is_deleted=False,
    ).order_by("-created_at", "-id")   # сортировка по времени, id как tiebreaker

    if cursor:
        # Разрешаем cursor-UUID в (created_at, id) через точечный поиск
        anchor = (
            Task.objects
            .filter(id=cursor, tenant_id=tenant_id)
            .values("created_at", "id")
            .first()
        )
        if anchor:
            qs = qs.filter(
                Q(created_at__lt=anchor["created_at"])
                | Q(created_at=anchor["created_at"], id__lt=anchor["id"])
            )

    rows = list(qs[: limit + 1])
    has_next = len(rows) > limit
    rows = rows[:limit]

    return {
        "items": rows,
        "next_cursor": rows[-1].id if has_next and rows else None,
    }
```

Требуется составной индекс:

```sql
CREATE INDEX CONCURRENTLY tasks_tenant_created_at_id_idx
ON tasks_tasks (tenant_id, created_at DESC, id DESC)
WHERE is_deleted = FALSE;
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
    result = get_tasks_page(tenant_id=..., cursor=cursor, limit=limit)
    return 200, ListOut(items=result["items"], next_cursor=result["next_cursor"])
```

**Frontend (TanStack Query + generated SDK):**

```typescript
// nextCursor из предыдущей страницы передаётся как cursor в следующий запрос
const { data, fetchNextPage } = useInfiniteQuery({
  queryKey: ['tasks'],
  queryFn: ({ pageParam }) => api.getTasks({ cursor: pageParam }),
  getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
})
```

**Что нельзя использовать:**

```python
# OFFSET — запрещён для: audit, finance, notifications, ws events, chats (ADR-0008)
qs[page * limit : (page + 1) * limit]
qs.offset(page * limit).limit(limit)

# Для UUID-моделей — НЕВЕРНО (случайный порядок, перекрывающиеся страницы):
qs.order_by("-id")
qs.filter(id__lt=cursor)
```

## Why This Matters

На таблице 100k+ строк OFFSET-запрос на страницу 1000 может занимать секунды. Cursor-пагинация стабильна: каждый запрос использует индекс и выдаёт результат за константное время.

Cursor-пагинация не пропускает и не дублирует строки при конкурентной вставке между страницами. Но это свойство нарушается, если порядок недетерминирован: `order_by("-id")` на UUID PK делает каждую страницу нестабильной, независимо от логики cursor-фильтра.

## When to Apply

Обязательно для: audit logs, finance transactions, bank statement lines, notifications, WebSocket events, chats.

Для административных списков с малым объёмом данных (< 10k строк) OFFSET допустим, но cursor рекомендуется.

**Когда нужен составной `(created_at, id)` курсор:** всегда, когда PK — UUID v4. Если PK — serial/bigserial (целочисленный, монотонно возрастающий), простой `id__lt=cursor` достаточен.

## Examples

Правильный пример из `apps/tasks/queries/task_queries.py` (UUID PK, составной курсор):

```python
def _task_qs(tenant_id: uuid.UUID):
    return Task.objects.filter(
        tenant_id=tenant_id, is_deleted=False
    ).order_by("-created_at", "-id")

def get_tasks_page(tenant_id, *, cursor=None, limit=20, **filters):
    qs = _task_qs(tenant_id)
    if cursor:
        anchor = Task.objects.filter(id=cursor, tenant_id=tenant_id).values("created_at", "id").first()
        if anchor:
            qs = qs.filter(
                Q(created_at__lt=anchor["created_at"])
                | Q(created_at=anchor["created_at"], id__lt=anchor["id"])
            )
    rows = list(qs[:limit + 1])
    has_next = len(rows) > limit
    rows = rows[:limit]
    return {"items": [...], "next_cursor": rows[-1].id if has_next and rows else None}
```

Пример из `apps/payments/` (UUID PK, cursor через `created_at` + `id` tiebreaker):

```python
# cursor — UUID BankStatement, разрешается в (created_at, id) через точечный поиск
if cursor:
    anchor = BankStatement.objects.filter(id=cursor, tenant_id=tenant_id).values("created_at", "id").first()
    if anchor:
        qs = qs.filter(
            Q(created_at__lt=anchor["created_at"])
            | Q(created_at=anchor["created_at"], id__lt=anchor["id"])
        )

items = list(qs[: limit + 1])
next_cursor = items[limit - 1].id if len(items) > limit else None
```

## Prevention

Тесты для проверки корректности cursor-пагинации при UUID PK:

```python
def test_cursor_pagination_no_overlap(tenant_id):
    """Страницы не пересекаются."""
    tasks = [TaskFactory(tenant_id=tenant_id) for _ in range(12)]
    page1 = get_tasks_page(tenant_id, limit=5)
    cursor = page1["next_cursor"]
    page2 = get_tasks_page(tenant_id, cursor=cursor, limit=5)
    ids1 = {item["id"] for item in page1["items"]}
    ids2 = {item["id"] for item in page2["items"]}
    assert ids1.isdisjoint(ids2)

def test_cursor_pagination_covers_all_rows(tenant_id):
    """Все строки достижимы без дублей."""
    TaskFactory.create_batch(23, tenant_id=tenant_id)
    seen, cursor = set(), None
    while True:
        page = get_tasks_page(tenant_id, cursor=cursor, limit=7)
        for item in page["items"]:
            assert item["id"] not in seen
            seen.add(item["id"])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    assert len(seen) == 23
```

## Related Issues

- ADR-0008: OFFSET pagination forbidden for audit/finance/notifications/ws/chats
- `apps/tasks/queries/task_queries.py` — исправленная реализация с составным курсором (Sprint C)
- `apps/tasks/migrations/0002_taskattachment_partial_unique.py` — связанный паттерн: partial unique index для soft-delete
