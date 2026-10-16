import uuid
from datetime import UTC, datetime
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpRequest
from ninja import Query, Router

from apps.common.api.auth import verified_request_context
from apps.common.api.errors import make_error_response
from apps.common.api.permissions import require_permission
from apps.common.cache.dashboard_cache import invalidate_dashboard
from apps.common.tenancy.context_core import get_tenant_id
from apps.contragents.models.contragent import Contragent, ContragentType
from apps.contragents.policies.validation_matrix import ContragentValidationPolicy
from apps.contragents.queries.contragent_queries import (
    get_contragent_detail,
    get_contragents_page,
)
from apps.contragents.schemas.contragent_dto import (
    ContragentCreateRequest,
    ContragentListResponse,
    ContragentResponse,
    ContragentUpdateRequest,
    PersonCreateRequest,
    PersonListResponse,
)
from apps.contragents.services.person_profile_read_service import PersonProfileReadService
from apps.contragents.services.registration import ContragentRegistrationService

router = Router(tags=["contragents"])


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

    tenant_id = get_tenant_id()
    with transaction.atomic():
        contragent = Contragent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            name=body.name,
            inn=body.inn,
            contragent_type=body.contragent_type,
        )
        contragent.save()
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
    try:
        contragent = Contragent.objects.select_for_update().get(
            id=contragent_id, tenant_id=tenant_id, is_deleted=False
        )
    except Contragent.DoesNotExist:
        return 404, make_error_response("CONTRAGENT_NOT_FOUND", "Контрагент не найден.")

    if contragent.is_legal_hold:
        return 422, make_error_response("LEGAL_HOLD", "Изменение заблокировано: юридическое удержание.")

    with transaction.atomic():
        contragent.name = body.name
        contragent.version += 1
        contragent.save(update_fields=["name", "version", "updated_at"])
        _tid = tenant_id
        transaction.on_commit(lambda t=_tid: invalidate_dashboard(t))

    return 200, get_contragent_detail(contragent_id)


@router.delete("/{contragent_id}/", response={204: None, 403: dict, 404: dict, 422: dict})
@require_permission("delete", "contragents")
def delete_contragent(request: HttpRequest, contragent_id: uuid.UUID):
    tenant_id = get_tenant_id()
    try:
        contragent = Contragent.objects.select_for_update().get(
            id=contragent_id, tenant_id=tenant_id, is_deleted=False
        )
    except Contragent.DoesNotExist:
        return 404, make_error_response("CONTRAGENT_NOT_FOUND", "Контрагент не найден.")

    if contragent.is_legal_hold:
        return 422, make_error_response("LEGAL_HOLD", "Удаление заблокировано: юридическое удержание.")

    with transaction.atomic():
        contragent.is_deleted = True
        contragent.deleted_at = datetime.now(UTC)
        contragent.version += 1
        contragent.save(update_fields=["is_deleted", "deleted_at", "version", "updated_at"])
        _tid = tenant_id
        transaction.on_commit(lambda t=_tid: invalidate_dashboard(t))

    return 204, None


@router.get("/{contragent_id}/persons/", response={200: PersonListResponse, 403: dict, 404: dict})
@require_permission("read", "contragents")
def list_persons(request: HttpRequest, contragent_id: uuid.UUID):
    if get_contragent_detail(contragent_id) is None:
        return 404, make_error_response("CONTRAGENT_NOT_FOUND", "Контрагент не найден.")
    trusted_context = verified_request_context(request)
    if trusted_context is None:
        return 403, make_error_response("UNTRUSTED_AUTH_CONTEXT", "Контекст авторизации не подтверждён.")
    try:
        profiles = PersonProfileReadService.read(contragent_id, request=request)
    except (PermissionDenied, PermissionError):
        return 403, make_error_response("PII_READ_DENIED", "Доступ к данным отклонён.")
    except RuntimeError:
        return 403, make_error_response("PII_AUDIT_UNAVAILABLE", "Чтение данных временно недоступно.")
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
