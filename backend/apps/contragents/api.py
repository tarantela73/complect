import uuid
from datetime import UTC, datetime
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpRequest
from ninja import Query, Router

from apps.common.api.errors import make_error_response
from apps.common.api.permissions import require_permission
from apps.common.cache.dashboard_cache import invalidate_dashboard
from apps.common.events.contragents import (
    ContragentCreatedEvent,
    ContragentDeletedEvent,
    ContragentUpdatedEvent,
)
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tenancy.context_core import get_context, get_tenant_id
from apps.contragents.models.contragent import Contragent, ContragentType
from apps.contragents.policies.validation_matrix import ContragentValidationPolicy
from apps.contragents.queries.contragent_queries import (
    get_contragent_detail,
    get_contragents_page,
    get_person_profiles,
)
from apps.contragents.queries.timeline_queries import get_contragent_timeline
from apps.contragents.schemas.attachment_dto import (
    ContragentAttachFileRequest,
    ContragentAttachmentListResponse,
    ContragentAttachmentResponse,
    ContragentTimelineResponse,
)
from apps.contragents.schemas.contragent_dto import (
    ContragentCreateRequest,
    ContragentListResponse,
    ContragentResponse,
    ContragentUpdateRequest,
    PersonCreateRequest,
    PersonListResponse,
)
from apps.contragents.services.attachment_service import ContragentAttachmentService
from apps.contragents.services.registration import ContragentRegistrationService
from apps.files.services.file_common_service import default_file_service

router = Router(tags=["contragents"])
_attachment_service = ContragentAttachmentService(file_service=default_file_service)


@router.get("/", response={200: ContragentListResponse, 403: dict})
@require_permission("read", "contragents")
def list_contragents(
    request: HttpRequest,
    cursor: Optional[uuid.UUID] = Query(None),  # noqa: B008
    limit: int = Query(20, ge=1, le=100),  # noqa: B008
    contragent_type: Optional[str] = Query(None),  # noqa: B008
    inn: Optional[str] = Query(None),  # noqa: B008
    q: Optional[str] = Query(None),  # noqa: B008
):
    result = get_contragents_page(cursor=cursor, limit=limit, contragent_type=contragent_type, inn=inn, q=q)
    return 200, result


@router.get("/{contragent_id}/", response={200: ContragentResponse, 403: dict, 404: dict})
@require_permission("read", "contragents")
def get_contragent(request: HttpRequest, contragent_id: uuid.UUID):
    result = get_contragent_detail(contragent_id)
    if result is None:
        return 404, make_error_response("CONTRAGENT_NOT_FOUND", "Контрагент не найден.")
    return 200, result


@router.post("/", response={201: ContragentResponse, 400: dict, 403: dict, 409: dict})
@require_permission("create", "contragents")
def create_contragent(request: HttpRequest, body: ContragentCreateRequest):
    try:
        if body.contragent_type not in ContragentType.values:
            return 400, make_error_response(
                "INVALID_TYPE",
                f"Допустимые типы: {ContragentType.values}",
            )
        ContragentValidationPolicy.verify_inn_compliance(body.contragent_type, body.inn)
    except ValidationError as exc:
        return 400, make_error_response("VALIDATION_ERROR", str(exc))

    ctx = get_context()
    tenant_id = ctx.tenant_id
    with transaction.atomic():
        contragent = Contragent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name=body.name,
            inn=body.inn,
            contragent_type=body.contragent_type,
        )
        contragent.save()

        event = ContragentCreatedEvent.create(
            correlation_id=ctx.correlation_id,
            contragent_id=contragent.id,
            user_id=ctx.user_id,
            name=contragent.name,
            inn=contragent.inn,
        )
        TransactionalOutboxService.push_to_outbox(event)

        _tid = tenant_id
        transaction.on_commit(lambda t=_tid: invalidate_dashboard(t))

    return 201, ContragentResponse(
        id=contragent.id,
        name=contragent.name,
        inn=contragent.inn,
        contragent_type=contragent.contragent_type,
    )


@router.patch("/{contragent_id}/", response={200: ContragentResponse, 400: dict, 403: dict, 404: dict, 422: dict})
@require_permission("write", "contragents")
def update_contragent(request: HttpRequest, contragent_id: uuid.UUID, body: ContragentUpdateRequest):
    tenant_id = get_tenant_id()
    ctx = get_context()
    with transaction.atomic():
        try:
            contragent = Contragent.objects.select_for_update().get(
                id=contragent_id, tenant_id=tenant_id, is_deleted=False
            )
        except Contragent.DoesNotExist:
            return 404, make_error_response("CONTRAGENT_NOT_FOUND", "Контрагент не найден.")

        if contragent.is_legal_hold:
            return 422, make_error_response("LEGAL_HOLD", "Изменение заблокировано: юридическое удержание.")

        contragent.name = body.name
        contragent.version += 1
        contragent.save(update_fields=["name", "version", "updated_at"])

        event = ContragentUpdatedEvent.create(
            correlation_id=ctx.correlation_id,
            contragent_id=contragent_id,
            user_id=ctx.user_id,
            name=body.name,
        )
        TransactionalOutboxService.push_to_outbox(event)

        _tid = tenant_id
        transaction.on_commit(lambda t=_tid: invalidate_dashboard(t))

    return 200, get_contragent_detail(contragent_id)


@router.delete("/{contragent_id}/", response={204: None, 403: dict, 404: dict, 422: dict})
@require_permission("delete", "contragents")
def delete_contragent(request: HttpRequest, contragent_id: uuid.UUID):
    tenant_id = get_tenant_id()
    ctx = get_context()
    with transaction.atomic():
        try:
            contragent = Contragent.objects.select_for_update().get(
                id=contragent_id, tenant_id=tenant_id, is_deleted=False
            )
        except Contragent.DoesNotExist:
            return 404, make_error_response("CONTRAGENT_NOT_FOUND", "Контрагент не найден.")

        if contragent.is_legal_hold:
            return 422, make_error_response("LEGAL_HOLD", "Удаление заблокировано: юридическое удержание.")

        contragent.is_deleted = True
        contragent.deleted_at = datetime.now(UTC)
        contragent.version += 1
        contragent.save(update_fields=["is_deleted", "deleted_at", "version", "updated_at"])

        event = ContragentDeletedEvent.create(
            correlation_id=ctx.correlation_id,
            contragent_id=contragent_id,
            user_id=ctx.user_id,
        )
        TransactionalOutboxService.push_to_outbox(event)

        _tid = tenant_id
        transaction.on_commit(lambda t=_tid: invalidate_dashboard(t))

    return 204, None


# ── Persons ───────────────────────────────────────────────────────────────────

@router.get("/{contragent_id}/persons/", response={200: PersonListResponse, 403: dict, 404: dict})
@require_permission("read", "contragents")
def list_persons(request: HttpRequest, contragent_id: uuid.UUID):
    if get_contragent_detail(contragent_id) is None:
        return 404, make_error_response("CONTRAGENT_NOT_FOUND", "Контрагент не найден.")
    profiles = get_person_profiles(contragent_id)
    return 200, {"results": profiles, "count": len(profiles)}


@router.post("/{contragent_id}/persons/", response={201: dict, 400: dict, 403: dict, 404: dict})
@require_permission("write", "contragents")
def create_person(request: HttpRequest, contragent_id: uuid.UUID, body: PersonCreateRequest):
    if get_contragent_detail(contragent_id) is None:
        return 404, make_error_response("CONTRAGENT_NOT_FOUND", "Контрагент не найден.")

    try:
        ContragentValidationPolicy.verify_fio_regex(body.first_name, body.last_name)
    except ValidationError as exc:
        return 400, make_error_response("VALIDATION_ERROR", str(exc))

    try:
        profile_id = ContragentRegistrationService.register_person_profile(
            contragent_id=contragent_id,
            first_name=body.first_name,
            last_name=body.last_name,
            middle_name=body.middle_name,
            passport=body.passport,
            snils=body.snils,
            phone=body.phone,
        )
        return 201, {"id": str(profile_id)}
    except ValidationError as exc:
        return 400, make_error_response("REGISTRATION_ERROR", str(exc))
    except PermissionDenied:
        # no-oracle: same response as "not found" to prevent tenant enumeration
        return 404, make_error_response("CONTRAGENT_NOT_FOUND", "Контрагент не найден.")


# ── Attachments ───────────────────────────────────────────────────────────────

@router.get(
    "/{contragent_id}/attachments/",
    response={200: ContragentAttachmentListResponse, 403: dict, 404: dict},
    tags=["contragents"],
)
@require_permission("read", "contragents")
def list_contragent_attachments(request: HttpRequest, contragent_id: uuid.UUID):
    if get_contragent_detail(contragent_id) is None:
        return 404, make_error_response("CONTRAGENT_NOT_FOUND", "Контрагент не найден.")

    tenant_id = request.jwt_tenant_id  # type: ignore[attr-defined]
    from apps.contragents.repositories.attachment_repo import ContragentAttachmentRepository
    from apps.common.contracts.file_service_protocol import FileMetaDTO

    file_ids = ContragentAttachmentRepository.list_file_ids(contragent_id)
    results: list[ContragentAttachmentResponse] = []
    for file_id in file_ids:
        meta: FileMetaDTO | None = default_file_service.get_file_meta(file_id, tenant_id)
        if meta is None:
            continue
        attachment = ContragentAttachmentRepository.get_active(contragent_id, file_id)
        if attachment is None:
            continue
        results.append(
            ContragentAttachmentResponse(
                file_id=meta.file_id,
                name=meta.original_name,
                size_bytes=meta.size_bytes,
                status=meta.status,
                download_url=meta.download_url,
                attached_by=attachment.attached_by,
                attached_at=attachment.created_at,
            )
        )
    return 200, ContragentAttachmentListResponse(results=results, count=len(results))


@router.post(
    "/{contragent_id}/attachments/",
    response={201: dict, 400: dict, 403: dict, 422: dict},
    tags=["contragents"],
)
@require_permission("write", "contragents")
def attach_contragent_file(
    request: HttpRequest,
    contragent_id: uuid.UUID,
    body: ContragentAttachFileRequest,
):
    tenant_id = request.jwt_tenant_id  # type: ignore[attr-defined]
    actor_id = request.jwt_user_id  # type: ignore[attr-defined]
    try:
        _attachment_service.attach(
            contragent_id=contragent_id,
            file_id=body.file_id,
            actor_id=actor_id,
            tenant_id=tenant_id,
        )
        return 201, {"status": "attached"}
    except PermissionDenied as exc:
        code = str(exc)
        if code == "FILE_NOT_FOUND":
            return 403, make_error_response("FILE_NOT_FOUND", "Файл не найден.")
        if code == "CONTRAGENT_LEGAL_HOLD":
            return 422, make_error_response("LEGAL_HOLD", "Контрагент заблокирован юридическим удержанием.")
        return 403, make_error_response("CONTRAGENT_NOT_FOUND", "Контрагент не найден.")
    except ValidationError as exc:
        return 422, make_error_response("BUSINESS_RULE_VIOLATION", exc.message)


@router.delete(
    "/{contragent_id}/attachments/{file_id}/",
    response={204: None, 403: dict, 422: dict},
    tags=["contragents"],
)
@require_permission("write", "contragents")
def detach_contragent_file(
    request: HttpRequest,
    contragent_id: uuid.UUID,
    file_id: uuid.UUID,
):
    actor_id = request.jwt_user_id  # type: ignore[attr-defined]
    try:
        _attachment_service.detach(
            contragent_id=contragent_id,
            file_id=file_id,
            actor_id=actor_id,
        )
        return 204, None
    except PermissionDenied as exc:
        code = str(exc)
        if code == "ATTACHMENT_LEGAL_HOLD":
            return 422, make_error_response("LEGAL_HOLD", "Вложение заблокировано юридическим удержанием.")
        return 403, make_error_response("ATTACHMENT_NOT_FOUND", "Вложение не найдено.")


# ── Timeline / Historia ───────────────────────────────────────────────────────

@router.get(
    "/{contragent_id}/timeline/",
    response={200: ContragentTimelineResponse, 403: dict, 404: dict},
    tags=["contragents"],
)
@require_permission("read", "contragents")
def get_contragent_timeline_view(request: HttpRequest, contragent_id: uuid.UUID):
    if get_contragent_detail(contragent_id) is None:
        return 404, make_error_response("CONTRAGENT_NOT_FOUND", "Контрагент не найден.")
    tenant_id = request.jwt_tenant_id  # type: ignore[attr-defined]
    return 200, get_contragent_timeline(contragent_id, tenant_id)
