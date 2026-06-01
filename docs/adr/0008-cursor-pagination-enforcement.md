# 0008 — Cursor Pagination Enforcement для высоконагруженных коллекций

## 1. Статус

| Поле     | Значение                        |
| -------- | ------------------------------- |
| Статус   | APPROVED                        |
| Дата     | 2026-06-01                      |
| Владелец | Core Architecture Lead          |

---

## 2. Контекст

Стандартная `OFFSET`-пагинация (`LIMIT N OFFSET M`) имеет фундаментальные проблемы при росте данных:

1. **Деградация производительности**: PostgreSQL вынужден отсканировать `OFFSET + LIMIT` строк и выбросить первые `OFFSET`. При странице 1000 с размером 20 — это 20 000 прочитанных строк.
2. **Phantom reads**: если между запросами страниц вставляется новая запись, пользователь видит дубли или пропуски.
3. **Memory blow-up**: для audit logs, finance и websocket events `OFFSET` растёт без ограничений и может дать запрос, читающий миллионы строк.

---

## 3. Решение

Для критических коллекций обязательна **cursor-based pagination** вместо OFFSET.

### Стандарт реализации

```sql
-- Cursor pagination: WHERE id < :cursor ORDER BY id DESC
SELECT * FROM table
WHERE tenant_id = :tenant_id
  AND is_deleted = false
  AND id < :cursor      -- cursor — UUID последнего элемента предыдущей страницы
ORDER BY id DESC
LIMIT :limit + 1        -- +1 для определения наличия следующей страницы
```

### Обязательные коллекции (OFFSET запрещён)

- Audit logs (`common_audit_integrity_logs`)
- Finance транзакции (`finance_transactions`)
- Notifications
- WebSocket events / Realtime events
- Chat messages
- Dead Letter Queue

### Формат ответа API

```json
{
  "results": [...],
  "next_cursor": "uuid-последнего-элемента-или-null",
  "count": 20
}
```

`next_cursor = null` означает конец коллекции.

### Правила

- `limit` по умолчанию 20, максимум 100; передаётся как query-параметр.
- Cursor передаётся как query-параметр `?cursor=<uuid>`.
- При первом запросе (без cursor) возвращаются последние записи (newest-first).
- Запросы типа `ORDER BY id ASC` с offset допустимы только во внутренних admin/reporting задачах, не в публичном API.

---

## 4. Рассмотренные альтернативы

| Альтернатива           | Причина отказа                                                              |
| ---------------------- | --------------------------------------------------------------------------- |
| OFFSET pagination      | Деградирует с ростом данных; phantom reads; запрещено архитектурой.        |
| Keyset pagination (составной ключ) | Сложнее реализовать и отлаживать; UUID pk достаточен.        |
| Page tokens (опaque)   | UUID-cursor понятнее и проще в тестировании.                                |

---

## 5. Последствия

### Плюсы

- Постоянная сложность `O(log N)` для любого размера таблицы.
- Нет phantom reads при конкурентной вставке.
- Индекс по `(tenant_id, id DESC)` покрывает все cursor-запросы без full scan.

### Минусы

- Невозможно перейти на произвольную страницу (например, «страница 47»).
- При удалении записей cursor может «пропрыгнуть» через удалённые строки — учитывается через soft delete (`is_deleted=False`).

---

## 6. Связанные ADR и разделы

- [ADR-0001](0001-database-partitioning-strategy.md) — партиционирование write-heavy таблиц
- Раздел 4.11 архитектурной спецификации — Cursor Pagination Standard
- Раздел 4.6 — BaseEnterpriseModel (поле `id` UUID как cursor key)
