# B1: policy для cleanup foundation

Этот runbook описывает только локальный preflight policy для двух миграций
foundation. Сами migration bytes и применение к базе данных этим шагом не
изменяются.

## Разрешённые артефакты

В `backend/config/dev_migration_policy.json` должны присутствовать ровно
следующие новые записи (существующие записи policy сохраняются):

| Migration | SHA-256 | Правило |
| --- | --- | --- |
| `users.0003b_cleanup_admission_expand` | `b2cdcb849cab7259ac3e7bd39e25520d716d7702dac8ebddc7f2a7d345438931` | `approved` и `bootstrap_only` |
| `users.0003b1_cleanup_admission_indexes` | `45946cef3f3d26896320cc132d52cb42b962b8ec4171a86d8aa4b4d1c889c991` | `approved` и `bootstrap_only` |

Digest считается по полному содержимому файла миграции. Изменение байтов после
утверждения делает запись policy устаревшей и блокирует preflight.

## Семантика preflight

Единственный проверяемый seam — публичная функция
`backend/config/dev_database.py:assess_migrations`. Она fail-closed:

- неизвестная применённая миграция, отсутствие receipt для существующей схемы,
  незавершённый receipt, расхождение receipt с ledger и drift применённого
  файла завершаются ошибкой;
- migration с отсутствующим или неверным `approved` hash считается
  `Unreviewed migration`;
- `bootstrap_only` запрещает автоматическое применение foundation поверх уже
  существующей схемы, даже если hash approved;
- на пустой схеме pending foundation допустим, а receipt с обеими миграциями
  переводит preflight в готовое состояние.

`bootstrap_only` — это дополнительный release gate, а не разрешение на обход
ADR и не доказательство того, что миграция уже применена.

## Матрица B1

Адресный тест `backend/apps/users/tests/test_cleanup_foundation_policy.py`
проверяет через `assess_migrations`:

| Сценарий | Ожидание |
| --- | --- |
| A. Пустая схема | Разрешён fresh install, foundation pending |
| B. Managed baseline + корректный receipt, foundation pending | Отказ `Gated migration` |
| C. Existing schema без receipt | Отказ `Unmanaged existing schema` |
| D. Partial bootstrap с исходным `in_progress` receipt | Отказ `Incomplete migration batch` |
| E. Unknown applied migration | Отказ `Unknown applied migration` |
| F. Changed pinned content | Pending: `Unreviewed migration`; applied: `Applied migration content changed` |
| Полностью применённая foundation + корректный ready receipt | Разрешён no-op, pending пуст |
| Missing hash | Отказ `Unreviewed migration` |
| Wrong hash | Отказ `Unreviewed migration` |

## Проверка B1

Команды выполняются с образом `98aa05d4bada`, без сети и с кандидатским
checkout, смонтированным только для чтения:

```bash
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -v "$PWD:/app:ro" -w /app 98aa05d4bada \
  pytest --noconftest -q \
    backend/apps/users/tests/test_cleanup_foundation_policy.py \
    infra/scripts/tests/test_dev_migrations.py
```

Ожидается успешное завершение адресных тестов; количество тестов может
изменяться при расширении матрицы. Этот pure-test результат не заменяет
штатный apply на реальном PostgreSQL/PostGIS; такой apply и его release
evidence выполняются отдельным шагом.
