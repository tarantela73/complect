---
title: "Outbox Test Dispatcher Skipped by Live Celery Worker Lock Contention"
date: "2026-06-18"
category: test-failures
module: apps/common
problem_type: test_failure
component: testing_framework
severity: high
symptoms:
  - "assert False is True on record.is_processed after calling run_outbox_polling_dispatcher_task() directly"
  - "Intermittent — only reproducible when complect_celery_worker container is running"
  - "SELECT FOR UPDATE SKIP LOCKED returns 0 rows because live worker already holds the lock"
root_cause: test_isolation
resolution_type: code_fix
related_components:
  - background_job
  - database
tags:
  - outbox
  - celery
  - select-for-update-skip-locked
  - test-isolation
  - django-db-transaction
  - on-commit
  - pytest-fixture
  - redis-broker
---

# Outbox Test Dispatcher Skipped by Live Celery Worker Lock Contention

## Problem

В тестах с `@pytest.mark.django_db(transaction=True)` вызов `push_to_outbox()` запускал
`run_outbox_polling_dispatcher_task.apply_async()` через `transaction.on_commit()` в live
Redis broker, где `complect_celery_worker` подхватывал задачу раньше теста и захватывал
строку через `SELECT FOR UPDATE SKIP LOCKED`. Прямой вызов диспетчера в тесте видел 0 строк,
`is_processed` оставался `False`.

## Symptoms

- Тест `test_successful_dispatch_marks_processed` падал с `assert False is True` после `record.refresh_from_db()`
- Падение интермиттентное: воспроизводилось в ~30–60% запусков при запущенном dev-окружении
- В изоляции (без `complect_celery_worker`) тест всегда проходил
- `836 passed` → `835 passed, 1 failed` при запущенном Docker Compose

## What Didn't Work

- **Проверка Redis-коннективности**: Redis db=2 был доступен и задачи ставились корректно — красной нити не давало
- **Первичная гипотеза о баге в диспетчере** (неверный фильтр `is_processed`) — опровергнута: в изоляции без воркера всё работало
- **`time.sleep()` в тесте**: смягчало симптом, но не устраняло race condition и делало тест flaky по таймауту

## Solution

**Причина**: в `transaction=True` тестах Django autocommit mode заставляет `transaction.on_commit()`
срабатывать немедленно после каждого `save()`. `push_to_outbox()` регистрирует `_fire_dispatcher`
как on_commit-колбэк, который вызывает `apply_async(countdown=0)` в тот же live Redis broker,
который слушает `complect_celery_worker`.

Фикс — глобальная `autouse` фикстура в `backend/conftest.py`, патчащая только `apply_async`:

```python
# backend/conftest.py
from unittest.mock import patch
import pytest


@pytest.fixture(autouse=True)
def _block_outbox_live_dispatch():
    """Prevent on_commit _fire_dispatcher from queuing tasks to the live celery_worker.

    complect_celery_worker shares the same Redis broker as tests. Without this patch,
    push_to_outbox() fires apply_async on commit, the live worker grabs SELECT FOR UPDATE,
    and direct dispatcher calls in tests see 0 rows via SKIP LOCKED — intermittent failure.
    """
    from apps.common.tasks.outbox_dispatcher import run_outbox_polling_dispatcher_task

    with patch.object(run_outbox_polling_dispatcher_task, "apply_async", return_value=None):
        yield
```

**До фикса**: `push_to_outbox()` → `on_commit(_fire_dispatcher)` → `apply_async()` → live Redis
→ `complect_celery_worker` захватывает `OutboxRecord` через `SELECT FOR UPDATE SKIP LOCKED`
→ тест вызывает задачу напрямую → `SKIP LOCKED` возвращает 0 строк → `is_processed=False`.

**После фикса**: `apply_async()` заглушен → live worker не получает задачу → тест вызывает
задачу напрямую → `SELECT FOR UPDATE SKIP LOCKED` видит незахваченную строку → диспетчер
обрабатывает, выставляет `is_processed=True`.

## Why This Works

**Точность патча**: `patch.object(task, "apply_async")` блокирует только постановку задачи
в очередь, не затрагивая саму логику `run_outbox_polling_dispatcher_task()`. Когда тест вызывает
задачу напрямую (`task()` или `task.apply()`), код выполняется в том же процессе pytest — патч
не мешает.

**`on_commit` + `SKIP LOCKED` interaction**: `SELECT FOR UPDATE SKIP LOCKED` — стандартная
защита от двойной обработки в outbox-паттерне. В production она гарантирует, что два воркера
не возьмут одну запись. В тестах с `transaction=True` тот же механизм становится ловушкой:
live worker и тест конкурируют за одну строку, и поскольку у воркера нулевая latency
(`countdown=0` + Redis на localhost), он выигрывает гонку в большинстве случаев.

**Почему `transaction=True` усугубляет**: в стандартных `@pytest.mark.django_db` тестах все
операции обёрнуты в транзакцию, которую pytest откатывает. `on_commit` колбэки **не срабатывают**
в этом режиме (Django подавляет их в рамках savepoint-транзакции). В `transaction=True` режиме
savepoints не используются, реальные коммиты происходят немедленно — `on_commit` стреляет
по-настоящему.

## Prevention

- **Глобальный autouse-патч** в `backend/conftest.py` (уже применён): патчит `apply_async`
  для всех тестов без необходимости помнить об этом в каждом тест-модуле.

- **Правило для новых on_commit-задач**: если в другом домене появляется новый тип задачи,
  которая вызывается через `on_commit`, — добавить её в `_block_outbox_live_dispatch` или создать
  аналогичный патч в соответствующем `conftest.py`.

- **Избегать `CELERY_TASK_ALWAYS_EAGER=True`** как замены: меняет семантику задач (нет очереди,
  нет retry-механики), deprecated в Celery 5.x (заменён на `task_always_eager` в dict-настройках).
  Не подходит для тестов, проверяющих поведение очереди или retry-логику.

- **Чеклист для outbox-тестов с `transaction=True`**:
  1. Убедиться, что autouse-фикстура из `backend/conftest.py` активна (глобальная — автоматически)
  2. Тесты, проверяющие `SELECT FOR UPDATE SKIP LOCKED` конкурентность, должны эмулировать
     конкурентность через `threading.Thread` + `connection.close()`, а не через live worker

## Related Issues

- `docs/solutions/patterns/transactional-outbox-usage.md` — паттерн использования outbox в тестах;
  раздел "When to Apply" не предупреждает об интерференции с live Celery worker в `transaction=True`
  тестах — требует дополнения
- `docs/adr/0010-transactional-outbox-delivery-guarantees.md` — документирует `SELECT FOR UPDATE SKIP LOCKED`
  в production semantics; новый fix не противоречит гарантийной матрице
