import uuid
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest
from ninja import Query, Router

from apps.common.api.errors import make_error_response
from apps.common.api.permissions import require_permission
from apps.common.repository.sre_engine import ConcurrentModificationError
from apps.tasks.queries.task_queries import (
    _decode_cursor,
    get_task_attachments,
    get_task_comments,
    get_task_detail,
    get_tasks_page,
)
from apps.tasks.repositories.task_repo import TaskRepository
from apps.tasks.schemas.task_dto import (
    TaskAttachFileIn,
    TaskAttachmentOut,
    TaskCommentIn,
    TaskCommentOut,
    TaskCreateIn,
    TaskListOut,
    TaskOut,
    TaskStatusChangeIn,
    TaskUpdateIn,
)
from apps.tasks.services.task_service import _UNSET, AttachmentNotFoundError, TaskService

router = Router(tags=["Tasks"])


@router.get("/", response={200: TaskListOut, 400: dict, 403: dict})
@require_permission("read", "tasks")
def list_tasks(
    request: HttpRequest,
    cursor: Optional[str] = Query(None),  # noqa: B008
    limit: int = Query(20, ge=1, le=100),  # noqa: B008
    status: Optional[str] = Query(None),  # noqa: B008
    assigned_to_id: Optional[uuid.UUID] = Query(None),  # noqa: B008
    entity_type: Optional[str] = Query(None),  # noqa: B008
    entity_id: Optional[uuid.UUID] = Query(None),  # noqa: B008
    created_by_id: Optional[uuid.UUID] = Query(None),  # noqa: B008
):
    tenant_id = request.jwt_tenant_id
    if cursor is not None and _decode_cursor(cursor) is None:
        return 400, make_error_response("INVALID_CURSOR", "Недопустимый cursor пагинации.")
    result = get_tasks_page(
        tenant_id,
        cursor=cursor,
        limit=limit,
        status=status,
        assigned_to_id=assigned_to_id,
        entity_type=entity_type,
        entity_id=entity_id,
        created_by_id=created_by_id,
    )
    return 200, result


@router.post("/", response={201: TaskOut, 400: dict, 403: dict})
@require_permission("write", "tasks")
def create_task(request: HttpRequest, payload: TaskCreateIn):
    tenant_id = request.jwt_tenant_id
    actor_id = request.jwt_user_id
    try:
        task = TaskService.create(
            tenant_id=tenant_id,
            created_by=actor_id,
            title=payload.title,
            description=payload.description,
            priority=payload.priority,
            assigned_to_id=payload.assigned_to_id,
            due_date=payload.due_date,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
        )
    except ValidationError as exc:
        return 400, make_error_response("VALIDATION_ERROR", str(exc))
    return 201, get_task_detail(task.id, tenant_id)


@router.get("/{task_id}/", response={200: TaskOut, 403: dict, 404: dict})
@require_permission("read", "tasks")
def get_task(request: HttpRequest, task_id: uuid.UUID):
    tenant_id = request.jwt_tenant_id
    result = get_task_detail(task_id, tenant_id)
    if result is None:
        return 404, make_error_response("TASK_NOT_FOUND", "Задача не найдена.")
    return 200, result


@router.patch("/{task_id}/", response={200: TaskOut, 400: dict, 403: dict, 404: dict, 409: dict, 422: dict})
@require_permission("write", "tasks")
def update_task(request: HttpRequest, task_id: uuid.UUID, payload: TaskUpdateIn):
    tenant_id = request.jwt_tenant_id
    actor_id = request.jwt_user_id
    task = TaskRepository.get_by_id(task_id, tenant_id)
    if task is None:
        return 404, make_error_response("TASK_NOT_FOUND", "Задача не найдена.")
    fields = payload.model_fields_set
    update_kwargs: dict = {
        "title": payload.title if "title" in fields else _UNSET,
        "description": payload.description if "description" in fields else _UNSET,
        "priority": payload.priority if "priority" in fields else _UNSET,
    }
    if "assigned_to_id" in fields:
        update_kwargs["assigned_to_id"] = payload.assigned_to_id
    if "due_date" in fields:
        update_kwargs["due_date"] = payload.due_date
    try:
        TaskService.update(task, actor_id=actor_id, **update_kwargs)
    except ConcurrentModificationError:
        return 409, make_error_response("CONCURRENT_MODIFICATION", "Задача была изменена параллельным запросом.")
    except PermissionDenied as exc:
        return 403, make_error_response(str(exc), "Доступ запрещён.")
    except ValidationError as exc:
        return 400, make_error_response("VALIDATION_ERROR", str(exc))
    return 200, get_task_detail(task_id, tenant_id)


@router.post("/{task_id}/status/", response={200: TaskOut, 400: dict, 403: dict, 404: dict, 409: dict, 422: dict})
@require_permission("write", "tasks")
def change_task_status(request: HttpRequest, task_id: uuid.UUID, payload: TaskStatusChangeIn):
    tenant_id = request.jwt_tenant_id
    actor_id = request.jwt_user_id
    task = TaskRepository.get_by_id(task_id, tenant_id)
    if task is None:
        return 404, make_error_response("TASK_NOT_FOUND", "Задача не найдена.")
    try:
        TaskService.change_status(task, payload.status, actor_id)
    except ConcurrentModificationError:
        return 409, make_error_response("CONCURRENT_MODIFICATION", "Задача была изменена параллельным запросом.")
    except PermissionDenied as exc:
        return 403, make_error_response(str(exc), "Доступ запрещён.")
    except ValueError as exc:
        return 422, make_error_response("INVALID_TRANSITION", str(exc))
    except ValidationError as exc:
        return 400, make_error_response("VALIDATION_ERROR", str(exc))
    return 200, get_task_detail(task_id, tenant_id)


@router.post("/{task_id}/cancel/", response={200: TaskOut, 403: dict, 404: dict, 409: dict, 422: dict})
@require_permission("write", "tasks")
def cancel_task(request: HttpRequest, task_id: uuid.UUID):
    tenant_id = request.jwt_tenant_id
    actor_id = request.jwt_user_id
    task = TaskRepository.get_by_id(task_id, tenant_id)
    if task is None:
        return 404, make_error_response("TASK_NOT_FOUND", "Задача не найдена.")
    try:
        TaskService.cancel(task, actor_id)
    except ConcurrentModificationError:
        return 409, make_error_response("CONCURRENT_MODIFICATION", "Задача была изменена параллельным запросом.")
    except PermissionDenied as exc:
        return 403, make_error_response(str(exc), "Доступ запрещён.")
    except ValueError as exc:
        return 422, make_error_response("INVALID_TRANSITION", str(exc))
    return 200, get_task_detail(task_id, tenant_id)


@router.get("/{task_id}/files/", response={200: list[TaskAttachmentOut], 403: dict, 404: dict})
@require_permission("read", "tasks")
def list_task_files(request: HttpRequest, task_id: uuid.UUID):
    tenant_id = request.jwt_tenant_id
    task = TaskRepository.get_by_id(task_id, tenant_id)
    if task is None:
        return 404, make_error_response("TASK_NOT_FOUND", "Задача не найдена.")
    return 200, get_task_attachments(task_id, tenant_id)


@router.post("/{task_id}/files/", response={201: TaskAttachmentOut, 400: dict, 403: dict, 404: dict})
@require_permission("write", "tasks")
def attach_file(request: HttpRequest, task_id: uuid.UUID, payload: TaskAttachFileIn):
    tenant_id = request.jwt_tenant_id
    actor_id = request.jwt_user_id
    task = TaskRepository.get_by_id(task_id, tenant_id)
    if task is None:
        return 404, make_error_response("TASK_NOT_FOUND", "Задача не найдена.")
    try:
        attachment = TaskService.attach_file(task, payload.file_id, actor_id)
    except PermissionDenied as exc:
        return 403, make_error_response(str(exc), "Доступ запрещён.")
    except ValidationError as exc:
        return 400, make_error_response("VALIDATION_ERROR", str(exc))
    return 201, TaskAttachmentOut(
        id=attachment.id,
        file_id=attachment.file_id,
        attached_by=attachment.attached_by_id,
        created_at=attachment.created_at,
    )


@router.delete("/{task_id}/files/{file_id}/", response={204: None, 403: dict, 404: dict, 409: dict})
@require_permission("delete", "tasks")
def detach_file(request: HttpRequest, task_id: uuid.UUID, file_id: uuid.UUID):
    tenant_id = request.jwt_tenant_id
    actor_id = request.jwt_user_id
    task = TaskRepository.get_by_id(task_id, tenant_id)
    if task is None:
        return 404, make_error_response("TASK_NOT_FOUND", "Задача не найдена.")
    try:
        TaskService.detach_file(task, file_id, actor_id)
    except AttachmentNotFoundError:
        return 404, make_error_response("ATTACHMENT_NOT_FOUND", "Вложение не найдено.")
    except PermissionDenied as exc:
        return 403, make_error_response(str(exc), "Доступ запрещён.")
    except ConcurrentModificationError:
        return 409, make_error_response("CONFLICT", "Конкурентное изменение: повторите запрос.")
    return 204, None


@router.get("/{task_id}/comments/", response={200: list[TaskCommentOut], 403: dict, 404: dict})
@require_permission("read", "tasks")
def list_task_comments(request: HttpRequest, task_id: uuid.UUID):
    tenant_id = request.jwt_tenant_id
    task = TaskRepository.get_by_id(task_id, tenant_id)
    if task is None:
        return 404, make_error_response("TASK_NOT_FOUND", "Задача не найдена.")
    return 200, get_task_comments(task_id, tenant_id)


@router.post("/{task_id}/comments/", response={201: TaskCommentOut, 400: dict, 403: dict, 404: dict, 422: dict})
@require_permission("write", "tasks")
def add_task_comment(request: HttpRequest, task_id: uuid.UUID, payload: TaskCommentIn):
    tenant_id = request.jwt_tenant_id
    actor_id = request.jwt_user_id
    task = TaskRepository.get_by_id(task_id, tenant_id)
    if task is None:
        return 404, make_error_response("TASK_NOT_FOUND", "Задача не найдена.")
    try:
        comment = TaskService.add_comment(task, actor_id, payload.body)
    except ValidationError as exc:
        return 422, make_error_response("BUSINESS_RULE_VIOLATION", str(exc))
    except PermissionDenied as exc:
        return 403, make_error_response(str(exc), "Доступ запрещён.")
    return 201, TaskCommentOut.model_validate(comment, from_attributes=True)
