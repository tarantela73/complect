import uuid
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpRequest
from ninja import Query, Router

from apps.common.api.errors import make_error_response
from apps.common.api.permissions import require_permission
from apps.common.tenancy.context_core import get_tenant_id
from apps.contragents.models.contragent import Contragent, ContragentType
from apps.contragents.policies.validation_matrix import ContragentValidationPolicy
from apps.contragents.queries.contragent_queries import (
    get_contragent_detail,
    get_contragents_page,
    get_person_profiles,
)
from apps.contragents.schemas.contragent_dto import (
    ContragentCreateRequest,
    ContragentListResponse,
    ContragentResponse,
    PersonCreateRequest,
    PersonListResponse,
)
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
):
    result = get_contragents_page(cursor=cursor, limit=limit, contragent_type=contragent_type, inn=inn)
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

    return 201, ContragentResponse(
        id=contragent.id,
        name=contragent.name,
        inn=contragent.inn,
        contragent_type=contragent.contragent_type,
    )


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
