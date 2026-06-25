---
title: "PATCH partial update: _UNSET sentinel + model_fields_set"
date: 2026-06-25
category: patterns
module: tasks
problem_type: design_pattern
component: api
applies_when:
  - "Implementing PATCH endpoints in Django Ninja + Pydantic v2 with nullable optional fields"
  - "Service method receives nullable fields where null and 'not provided' have distinct semantics"
  - "Field is typed UUID | None or date | None and client can legitimately clear it to null"
tags:
  - django-ninja
  - pydantic-v2
  - patch
  - partial-update
  - sentinel
  - null-clearing
  - service-layer
---

# PATCH partial update: _UNSET sentinel + model_fields_set

## Context

В PATCH-эндпоинтах Django Ninja + Pydantic v2 есть класс опциональных полей, которые клиент может:

1. **Не передать** — поле не должно измениться
2. **Передать явно как `null`** — поле должно быть очищено (легитимная операция: снять исполнителя, убрать дедлайн)

Проблема возникает когда схема PATCH-запроса объявляет такие поля с дефолтом `None`:

```python
class TaskUpdateIn(Schema):
    title: str | None = None
    assigned_to_id: UUID | None = None  # опасно
    due_date: date | None = None        # опасно
```

`PATCH /tasks/123/ {"title": "foo"}` и `PATCH /tasks/123/ {"title": "foo", "assigned_to_id": null}` дают одинаковый `payload.assigned_to_id == None`. Если хендлер безусловно передаёт это значение в сервис, любой PATCH без поля молча обнуляет его.

**Баг, который поймал регрессию**: `test_update_task_by_creator` отправил PATCH только с `title`, ожидал сохранения `assigned_to_id`. Эндпоинт передавал `assigned_to_id=None` безусловно — исполнитель был стёрт. Дополнительно: guard без `is not _UNSET` вызывал `user_exists_in_tenant(object(), ...)` → `AttributeError: 'object' object has no attribute 'int'`.

## Guidance

### Шаг 1 — Sentinel в сервисном слое

```python
# apps/tasks/services/task_service.py

_UNSET = object()  # модульный синглтон, не экспортируется

class TaskService:
    @staticmethod
    def update(
        task: Task,
        *,
        actor_id: uuid.UUID,
        title: str | None = None,          # nullable, но None = "не задан" — безопасно
        description: str | None = None,
        priority: str | None = None,
        assigned_to_id: uuid.UUID | None = _UNSET,  # type: ignore[assignment]
        due_date: date | None = _UNSET,             # type: ignore[assignment]
    ) -> Task:
        # Порядок guard-условий критичен: сначала is not _UNSET, потом is not None
        if (
            assigned_to_id is not _UNSET
            and assigned_to_id is not None
            and not user_exists_in_tenant(assigned_to_id, task.tenant_id)
        ):
            raise ValidationError({"assigned_to_id": "User not found in tenant."})

        if title is not None:
            task.title = title
        if description is not None:
            task.description = description
        if priority is not None:
            task.priority = priority

        # Nullable поля: применять только когда явно переданы клиентом
        if assigned_to_id is not _UNSET:
            task.assigned_to_id = assigned_to_id  # None допускается — снимает исполнителя
        if due_date is not _UNSET:
            task.due_date = due_date

        with transaction.atomic():
            return TaskRepository.save(task)
```

### Шаг 2 — `model_fields_set` в API-хендлере

```python
# apps/tasks/api.py

@router.patch("/{task_id}/", response={200: TaskOut, ...})
@require_permission("write", "tasks")
def update_task(request: HttpRequest, task_id: uuid.UUID, payload: TaskUpdateIn):
    task = TaskRepository.get_by_id(task_id, request.jwt_tenant_id)
    if task is None:
        return 404, make_error_response("TASK_NOT_FOUND", "Задача не найдена.")

    fields = payload.model_fields_set  # set[str] — только поля из тела запроса

    # Ненullable поля: передаём None как "не задан" — сервис их игнорирует
    update_kwargs: dict = {
        "title": payload.title if "title" in fields else None,
        "description": payload.description if "description" in fields else None,
        "priority": payload.priority if "priority" in fields else None,
    }
    # Nullable поля: передаём ТОЛЬКО если клиент включил их в запрос
    if "assigned_to_id" in fields:
        update_kwargs["assigned_to_id"] = payload.assigned_to_id  # может быть None
    if "due_date" in fields:
        update_kwargs["due_date"] = payload.due_date

    TaskService.update(task, actor_id=request.jwt_user_id, **update_kwargs)
    return 200, get_task_detail(task_id, request.jwt_tenant_id)
```

## Why This Matters

**`model_fields_set`** — атрибут Pydantic v2, содержащий имена полей, фактически присутствовавших во входных данных при десериализации. Поля с дефолтами, не переданные клиентом, в этот set не попадают:

```python
payload = TaskUpdateIn(**{"title": "Новая задача"})
payload.model_fields_set  # → {"title"}
# assigned_to_id и due_date в set НЕТ, даже если их дефолт = None
```

**`_UNSET = object()`** создаёт уникальный синглтон на уровне модуля. Identity check (`is`/`is not`) работает надёжно: в отличие от `None`, `0` или `""`, `_UNSET` никогда не может быть передан случайно из API-слоя.

**Антипаттерн — guard без проверки sentinel первым:**

```python
# НЕПРАВИЛЬНО — AttributeError когда assigned_to_id == _UNSET
if assigned_to_id is not None and not user_exists_in_tenant(assigned_to_id, ...):
    ...
# user_exists_in_tenant(object(), ...) → AttributeError: 'object' object has no attribute 'int'
```

Short-circuit evaluation Python гарантирует безопасность только при правильном порядке: `is not _UNSET` должно стоять **первым**.

## When to Apply

Применять паттерн когда **одновременно** выполняются все три условия:

1. HTTP-метод — `PATCH` (partial update)
2. Поле объявлено как `SomeType | None` (nullable)
3. Значение `null` от клиента является **легитимной операцией** (снять исполнителя, убрать дедлайн, сбросить категорию)

**Не требуется** для:
- `PUT` — полная замена, все поля обязательны
- Полей типа `str | None` где `null` означает «не задано» и обнуление при PATCH допустимо
- Ненullable полей (`str`, `int`, `UUID`) — там `if value is not None` достаточно

## Examples

### До — молчаливое обнуление (баг)

```python
# PATCH /tasks/123/ {"title": "Новое название"}
# payload.assigned_to_id == None (default, не был в теле)

TaskService.update(
    task,
    title=payload.title,
    assigned_to_id=payload.assigned_to_id,  # ← всегда None при отсутствии поля
)
# Результат: исполнитель задачи стёрт, хотя клиент его не трогал
```

### После — корректное partial update

```python
# PATCH /tasks/123/ {"title": "Новое название"}
# payload.model_fields_set == {"title"}
# "assigned_to_id" не в fields → kwargs не содержит ключа
# TaskService.update() получает _UNSET по умолчанию → поле не тронуто

# PATCH /tasks/123/ {"title": "Новое название", "assigned_to_id": null}
# payload.model_fields_set == {"title", "assigned_to_id"}
# kwargs["assigned_to_id"] = None → сервис применяет None → исполнитель снят
```

### Тест для регрессии

```python
def test_patch_preserves_unmentioned_nullable_fields(api_client, task_with_assignee):
    """PATCH с одним полем не должен затрагивать остальные nullable-поля."""
    original_assignee = task_with_assignee.assigned_to_id
    original_due_date = task_with_assignee.due_date

    resp = api_client.patch(
        f"/api/v1/tasks/{task_with_assignee.id}/",
        data={"title": "Обновлённое название"},
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["assigned_to_id"] == str(original_assignee)
    assert data["due_date"] == original_due_date.isoformat()
```

## Related

- [tenant-isolation-in-services.md](tenant-isolation-in-services.md) — аналогичный паттерн service-layer guidance
- [runtime-errors/django-ninja-trailing-slash-routing.md](../runtime-errors/django-ninja-trailing-slash-routing.md) — другие Django Ninja API-соглашения
