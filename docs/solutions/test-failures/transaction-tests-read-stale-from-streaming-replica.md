---
title: "transaction=True Tests Read Stale State from Streaming Replica Under Worker-Induced Lag"
date: "2026-06-20"
category: test-failures
module: apps/common
problem_type: test_failure
component: testing_framework
severity: high
symptoms:
  - "CausalEmissionMissingError raised in FilesConsumer despite files_consumer_file_available being logged"
  - "AssertionError: assert 'SCANNING' == FileStatus.AVAILABLE after mark_available() succeeded"
  - "Intermittent — only in the full suite while complect_celery_worker is running; passes in isolation/subsets and when the worker is stopped"
root_cause: test_isolation
resolution_type: code_fix
related_components:
  - background_job
  - database
  - read_replica
tags:
  - cqrs
  - read-replica
  - replication-lag
  - read-your-own-writes
  - transaction-test-case
  - django-db-router
  - test-mirror
  - celery
  - test-isolation
---

# transaction=True Tests Read Stale State from Streaming Replica Under Worker-Induced Lag

## Problem

Два теста падали только в полном прогоне `pytest` при запущенном `complect_celery_worker`:
`test_metrics_reset_between_batches` (`CausalEmissionMissingError`) и
`test_available_cannot_go_back_to_scanning` (`status == SCANNING` вместо `AVAILABLE`).
В изоляции, в подмножествах и при остановленном воркере — `851 passed`.

## Symptoms

- `test 1`: лог `files_consumer_file_available` присутствует (запись `FileAvailable` в Outbox
  выполнена), но `_verify_emission()` через `.exists()` не находит её по `correlation_id` → `CausalEmissionMissingError`
- `test 2`: `mark_scanning()`→`mark_available()` отработали, но `get_by_id().status` возвращает `SCANNING`
- Воспроизводится только в полном прогоне с живым воркером; `make test-all` с остановленными
  `celery_worker`+`celery_beat` → `851 passed`

## What Didn't Work

- **Гипотеза «воркер мутирует строки теста»** — опровергнута: `pg_stat_activity` показал, что к
  `test_complect_db` подключается только backend-контейнер (172.18.0.15); воркер работает на
  `complect_db`. Прямой DB-контеншн исключён.
- **Гипотеза «загрязнение от предыдущих app в порядке коллекции»** — опровергнута: подмножество
  `common+contracts+contragents+files` (533) и `files`-only (119) проходят. Порядок коллекции
  алфавитный, полизагрязнитель не найден.
- **Расширение `_block_outbox_live_dispatch`** (патч `apply_async`) — нерелевантно: проблема не в
  постановке задач, а в маршрутизации чтений на физическую реплику.

## Solution

**Причина (доказана рантайм-интроспекцией):**

1. `@pytest.mark.django_db(transaction=True)` тесты **не** обёрнуты во внешний `atomic`, поэтому
   `EnterpriseClusterRouter.db_for_read` (`apps/common/db/router.py`) возвращает `"replica"`
   (обычные `django_db`-тесты обёрнуты в `atomic` → `in_atomic_block=True` → всегда `default`).
2. Алиас `replica` в тестах — **реальный streaming standby**: `HOST=postgres_replica`,
   `pg_is_in_recovery()=True`, `NAME=test_complect_db` (`TEST: {MIRROR: default}` не делит
   соединение — это отдельное физическое подключение к standby). Записи идут на primary, чтения —
   на реплику с асинхронным лагом.
3. Command-side код (consumer, repository) выполняется **вне** HTTP-middleware
   `ReadYourOwnWritesStickyMiddleware`, поэтому флаг `force_primary` не взводится, и чтение
   сразу после записи попадает на отстающую реплику.
4. Живой воркер непрерывно пишет в `complect_db` на том же кластере primary (beat-bookkeeping
   каждые 10с, outbox-dispatch) → общий WAL-поток → лаг репликации растёт на весь кластер →
   окно устаревшего чтения для `test_complect_db` расширяется. Воркер выключен → лаг≈0 → тесты
   проходят.

**Фикс — взвести `force_primary` на command-side путях, которые минуют RYW-middleware:**

```python
# apps/common/services/consumer_base.py — handle()
_prev_force_primary = is_primary_db_enforced()
set_force_primary_db(True)
try:
    self._handle_inner(event)   # process → _verify_emission → ack
finally:
    set_force_primary_db(_prev_force_primary)
```

```python
# apps/files/repositories/file_repo.py — get_by_id()
record = cls.get_queryset().using("default").filter(id=file_id).first()
```

`get_queryset()`/`get_counts()` (query-side: dashboard, file_queries) **не** трогаем — они
корректно читают с реплики.

## Why This Works

- **Read-your-own-writes для фоновых воркеров**: `ReadYourOwnWritesStickyMiddleware` взводит
  `force_primary` только для HTTP-мутаций. Celery-консьюмеры идут мимо middleware, поэтому
  read-after-write внутри `handle()` обязан явно форсить primary (rule 4.12/4.13).
- **`_verify_emission` — consistency-критичный inline SELECT**: его docstring-инвариант
  («writes visible on the same DB connection») нарушался роутером, уводящим SELECT на отдельное
  соединение к standby. Форс primary восстанавливает инвариант.
- **`.using("default")` в `get_by_id`**: command-side авторитетное чтение (rule 4.12 — репозитории
  читают с primary), исключает устаревшее состояние после CAS-записи независимо от контекста.

**Это также латентный production-баг**: консьюмер под реальным лагом реплики мог ложно поднять
`CausalEmissionMissingError` на корректно обработанном событии и уйти в retry.

## Prevention

- **Любой новый Celery-консьюмер/таск с read-after-write** наследует `BaseDomainConsumer.handle()`
  и автоматически получает primary-pinning. Для standalone-тасков, читающих свои записи, явно
  вызывать `set_force_primary_db(True)`.
- **`transaction=True`-тесты, проверяющие read-after-write**, по умолчанию читают со streaming
  standby — учитывать при написании ассертов на свежесть данных.
- **Командная сторона (`services/`, `repositories/`, `tasks/`)** читает только с primary
  (rule 4.12); реплика — исключительно для `queries/`/`selectors/`.

## Related Issues

- `docs/solutions/test-failures/outbox-live-worker-lock-contention-skips-test-dispatcher.md` —
  смежный класс (живой воркер ломает `transaction=True`-тесты через общий Redis-брокер); здесь
  механизм иной — общий WAL-поток primary→standby, а не SKIP LOCKED.
- `apps/common/db/router.py` — `EnterpriseClusterRouter.db_for_read` (источник маршрутизации).
- `apps/common/middleware/lifecycle/read_your_own_writes.py` — HTTP-аналог защиты, которого
  лишены фоновые воркеры.
