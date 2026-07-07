---
title: "Cursor pagination: WHERE id < cursor ORDER BY id DESC"
date: 2026-06-09
last_updated: 2026-07-06
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

**Две рабочие схемы курсора для UUID PK.** Обе сортируют по `(created_at, id)`, различаются способом кодирования курсора:

| Схема | Кто использует | Курсор | Резолв позиции | Специфичный риск |
| --- | --- | --- | --- | --- |
| **Anchor-lookup** | `contracts`, `payments` | UUID строки (`Optional[uuid.UUID]`) | точечный запрос `filter(id=cursor)` → `(created_at, id)` | anchor lookup НЕ должен фильтровать `is_deleted` (только `tenant_id`): soft-deleted якорь — валидная граница. `is_deleted=False` тут → удалённый якорь не резолвится → `None` → страница перезапускается → дубли |
| **Opaque-token** | `tasks` | base64(`created_at\|id`) (`Optional[str]`) | `_decode_cursor` декодит напрямую, без запроса к БД | malformed base64 обязан давать `400`, а не пустой `200` |

**Схема 1 — Anchor-lookup (контракты, платежи):**

```python
from django.db.models import Q

def get_contracts_page(
    cursor: uuid.UUID | None = None,   # UUID контракта — API-контракт не меняется
    limit: int = 20,
) -> dict:
    ctx = get_context()
    qs = Contract.objects.filter(
        tenant_id=ctx.tenant_id,
        is_deleted=False,
    ).order_by("-created_at", "-id")   # сортировка по времени, id как tiebreaker

    if cursor:
        # Разрешаем cursor-UUID в (created_at, id) через точечный поиск.
        # НЕ фильтруем is_deleted: soft-deleted строка остаётся валидной границей.
        # Фильтр is_deleted=False здесь — регрессия: удалённый между страницами
        # якорь перестаёт резолвиться → anchor=None → фильтр границы пропущен →
        # страница стартует с начала → дубли. tenant_id-скоуп сохранён (чужой
        # курсор резолвится в None — safe).
        anchor = (
            Contract.objects
            .filter(id=cursor, tenant_id=ctx.tenant_id)
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
        "results": rows,
        "next_cursor": str(rows[-1].id) if has_next and rows else None,
    }
```

Требуется составной индекс (пример из `apps/contracts/migrations/0009_contracts_cursor_index.py`):

```sql
CREATE INDEX CONCURRENTLY contracts_contracts_cursor_idx
ON contracts_contracts (tenant_id, created_at DESC, id DESC)
WHERE is_deleted = FALSE;
```

**API Schema.** Тип cursor-параметра зависит от схемы:

```python
# Anchor-lookup (contracts): cursor — Optional[uuid.UUID].
# Django Ninja сам валидирует формат UUID → битый ввод даёт 422 без ручной проверки.
@router.get("/contracts/", response={200: ContractListOut, 403: dict})
def list_contracts(
    request: HttpRequest,
    cursor: Optional[uuid.UUID] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    result = get_contracts_page(cursor=cursor, limit=limit)
    return 200, result

# Opaque-token (tasks): cursor — Optional[str], Ninja НЕ валидирует содержимое.
# Обязательна ручная проверка _decode_cursor → 400 (см. секцию ниже).
@router.get("/tasks/", response={200: TaskListOut, 400: dict, 403: dict})
def list_tasks(
    request: HttpRequest,
    cursor: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    if cursor is not None and _decode_cursor(cursor) is None:
        return 400, make_error_response("INVALID_CURSOR", "Недопустимый cursor пагинации.")
    return 200, get_tasks_page(request.jwt_tenant_id, cursor=cursor, limit=limit)
```

`next_cursor` в обеих схемах сериализуется как `Optional[str]` (для anchor-lookup — `str(uuid)`, для opaque — base64-токен), поэтому SDK-тип фронтенда одинаков.

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

**Декодирование курсора: bare `except Exception` маскирует невалидный ввод**

`_decode_cursor()` разбирает opaque base64-токен курсора. Голый `except Exception: return None` перехватывает не только ожидаемые `ValueError`/`binascii.Error` на битом base64, но и `TypeError`, если вызывающий код передал не строку (например, `uuid.UUID` объект). API-слой должен трактовать `None` от decode как явную ошибку клиента — 400, а не как «курсора нет, отдать пустую страницу» с 200 OK.

```python
# apps/tasks/queries/task_queries.py
def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID] | None:
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode())
        ...
    except Exception:          # OK как fallback внутри decode-хелпера
        return None

# apps/tasks/api.py — ОБЯЗАТЕЛЬНАЯ валидация до вызова query-слоя
@router.get("/", response={200: TaskListOut, 400: dict, 403: dict})
def list_tasks(request: HttpRequest, cursor: Optional[str] = Query(None), ...):
    if cursor is not None and _decode_cursor(cursor) is None:
        return 400, make_error_response("INVALID_CURSOR", "Недопустимый cursor пагинации.")
    result = get_tasks_page(tenant_id, cursor=cursor, ...)
    return 200, result
```

Bare `except Exception` в самом decode-хелпере допустим как defence-in-depth — проблема не в нём, а в том, что вызывающий API-слой не проверял результат на `None` явно.

## Why This Matters

На таблице 100k+ строк OFFSET-запрос на страницу 1000 может занимать секунды. Cursor-пагинация стабильна: каждый запрос использует индекс и выдаёт результат за константное время.

Cursor-пагинация не пропускает и не дублирует строки при конкурентной вставке между страницами. Но это свойство нарушается, если порядок недетерминирован: `order_by("-id")` на UUID PK делает каждую страницу нестабильной, независимо от логики cursor-фильтра.

## When to Apply

Обязательно для: audit logs, finance transactions, bank statement lines, notifications, WebSocket events, chats.

Для административных списков с малым объёмом данных (< 10k строк) OFFSET допустим, но cursor рекомендуется.

**Когда нужен составной `(created_at, id)` курсор:** всегда, когда PK — UUID v4. Если PK — serial/bigserial (целочисленный, монотонно возрастающий), простой `id__lt=cursor` достаточен.

## Examples

Схема 2 — Opaque-token, реальный пример из `apps/tasks/queries/task_queries.py`. Позиция `(created_at, id)` кодируется прямо в base64-токен, поэтому anchor lookup в БД не нужен — `is_deleted=False` живёт только в базовом `_task_qs`:

```python
def _encode_cursor(created_at: datetime, task_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{task_id}".encode()).decode()

def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, tid = raw.split("|")
        return datetime.fromisoformat(ts), uuid.UUID(tid)
    except Exception:
        return None

def _task_qs(tenant_id: uuid.UUID):
    return Task.objects.filter(
        tenant_id=tenant_id, is_deleted=False
    ).order_by("-created_at", "-id")

def get_tasks_page(tenant_id, *, cursor=None, limit=20, **filters):
    qs = _task_qs(tenant_id)
    if cursor:
        anchor = _decode_cursor(cursor)
        if anchor is None:            # malformed → API-слой уже вернул 400; здесь defence-in-depth
            return TaskListOut(items=[], next_cursor=None)
        anchor_ts, anchor_id = anchor
        qs = qs.filter(
            Q(created_at__lt=anchor_ts)
            | Q(created_at=anchor_ts, id__lt=anchor_id)
        )
    rows = list(qs[: limit + 1])
    has_next = len(rows) > limit
    rows = rows[:limit]
    next_cursor = _encode_cursor(rows[-1].created_at, rows[-1].id) if has_next and rows else None
    return TaskListOut(items=[_task_to_out(t) for t in rows], next_cursor=next_cursor)
```

Схема 1 — Anchor-lookup, пример из `apps/payments/` (cursor — UUID `BankStatement`, разрешается в `(created_at, id)` точечным поиском):

```python
# cursor — UUID BankStatement, разрешается в (created_at, id) через точечный поиск.
# Якорь фильтруется только tenant_id (без is_deleted) — корректно: soft-deleted
# граница резолвится и пагинация не перезапускается.
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

Тесты на edge cases, найденные в code review (Sprint C, 2026-07-06). Реальный anchor-scheme тест из `apps/contracts/tests/test_contracts_filter.py` (возвращаемый ключ — `results`, элементы — DTO с атрибутом `.id`, `next_cursor` — `str(uuid)`):

```python
def test_pagination_survives_soft_delete_of_anchor(self):
    """Soft-delete якоря между страницами не должен приводить к дублям."""
    for _ in range(6):
        _make_contract(self.tenant_id)
    page1 = get_contracts_page(limit=3)
    cursor = page1["next_cursor"]                       # = id последнего элемента page1
    # Soft-delete через .update() (в обход save-гардов BaseEnterpriseModel):
    Contract.objects.filter(id=uuid.UUID(cursor)).update(is_deleted=True)
    page2 = get_contracts_page(cursor=uuid.UUID(cursor), limit=3)
    ids1 = {str(r.id) for r in page1["results"]}
    ids2 = {str(r.id) for r in page2["results"]}
    # FAIL с is_deleted=False в anchor lookup (page2 стартует с начала → дубли);
    # PASS когда якорь фильтруется только tenant_id.
    assert ids1.isdisjoint(ids2)

def test_invalid_cursor_returns_400(client):
    """Opaque-token: битый/поддельный cursor — 400 INVALID_CURSOR, не пустой 200."""
    response = client.get("/api/v1/tasks/", {"cursor": "not-a-valid-base64-cursor"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_CURSOR"
```

Общие правила проверки:

- **Anchor-lookup** (домены с `BaseEnterpriseModel`): якорь фильтруется **только** `tenant_id` — добавление `is_deleted=False` в anchor lookup — дефект (ломает пагинацию на soft-deleted якоре). Основной list-qs при этом обязан фильтровать `is_deleted=False` — это разные запросы.
- **Opaque-token**: тест на невалидный курсор должен передавать строку, которая реально ломает decode (`"garbage!!"`), а не объект неверного типа (`uuid.UUID`) — иначе тест обходит настоящий failure path.

## Related Issues

- ADR-0008: OFFSET pagination forbidden for audit/finance/notifications/ws/chats
- `apps/tasks/queries/task_queries.py` — opaque-token реализация с составным курсором (Sprint C)
- `apps/contracts/queries/contract_queries.py:68` — anchor lookup фильтрует только `tenant_id`; ранее добавленный `is_deleted=False` был откачен как регрессия (дубли на soft-deleted якоре), покрыто `test_pagination_survives_soft_delete_of_anchor` (2026-07-06)
- `apps/tasks/api.py` — `list_tasks` валидирует cursor через `_decode_cursor` и возвращает 400 INVALID_CURSOR (Sprint C review, 2026-07-06)
- `apps/tasks/migrations/0002_taskattachment_partial_unique.py` — связанный паттерн: partial unique index для soft-delete
