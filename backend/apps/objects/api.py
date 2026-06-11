import uuid
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest
from ninja import Query, Router

from apps.common.api.errors import make_error_response
from apps.common.api.permissions import require_permission
from apps.objects.queries.object_queries import (
    get_object_detail,
    get_objects_geojson,
    get_objects_page,
)
from apps.objects.schemas.object_dto import (
    FeatureCollectionSchema,
    FeatureSchema,
    ObjectContractLinkRequest,
    ObjectContractResponse,
    StageUpdateRequest,
    SupplyObjectCreateRequest,
    SupplyObjectDetailResponse,
    SupplyObjectListResponse,
    SupplyObjectUpdateRequest,
    UpdateLocationRequest,
)
from apps.objects.services.object_service import (
    ObjectCreateService,
    ObjectDeleteService,
    ObjectLocationService,
    ObjectUpdateService,
)
from apps.objects.services.object_contract_service import ObjectContractService
from apps.objects.services.stage_service import StageUpdateService
from apps.users.contracts.user_info_impl import DjangoUserInfoProvider

router = Router(tags=["objects"])

_user_info = DjangoUserInfoProvider()


def _parse_bbox(bbox: Optional[str]) -> Optional[tuple[float, float, float, float]]:
    if not bbox:
        return None
    try:
        parts = [float(x) for x in bbox.split(",")]
        if len(parts) != 4:
            return None
        return (parts[0], parts[1], parts[2], parts[3])
    except ValueError:
        return None


@router.get("/", response={200: SupplyObjectListResponse, 403: dict})
@require_permission("read", "objects")
def list_objects(
    request: HttpRequest,
    cursor: Optional[uuid.UUID] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    object_type: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    contract_id: Optional[uuid.UUID] = Query(None),
    status: Optional[str] = Query(None),
    manager_id: Optional[uuid.UUID] = Query(None),
    bbox: Optional[str] = Query(None, description="lon_min,lat_min,lon_max,lat_max"),
):
    result = get_objects_page(
        cursor=cursor,
        limit=limit,
        object_type=object_type,
        resource_type=resource_type,
        contract_id=contract_id,
        status=status,
        manager_id=manager_id,
        bbox=_parse_bbox(bbox),
        user_info=_user_info,
    )
    return 200, result


@router.get("/geojson/", response={200: FeatureCollectionSchema, 403: dict})
@require_permission("read", "objects")
def objects_geojson(
    request: HttpRequest,
    contract_id: Optional[uuid.UUID] = Query(None),
    bbox: Optional[str] = Query(None, description="lon_min,lat_min,lon_max,lat_max"),
):
    result = get_objects_geojson(contract_id=contract_id, bbox=_parse_bbox(bbox))
    return 200, result


@router.get("/{object_id}/", response={200: SupplyObjectDetailResponse, 403: dict})
@require_permission("read", "objects")
def get_object(request: HttpRequest, object_id: uuid.UUID):
    try:
        return 200, get_object_detail(object_id, user_info=_user_info)
    except PermissionDenied:
        return 403, make_error_response("OBJECT_NOT_FOUND", "Объект не найден.")


@router.post("/", response={201: SupplyObjectDetailResponse, 400: dict, 403: dict})
@require_permission("create", "objects")
def create_object(request: HttpRequest, body: SupplyObjectCreateRequest):
    try:
        obj = ObjectCreateService.execute(body)
        return 201, get_object_detail(obj.id, user_info=_user_info)
    except (ValidationError, ValueError) as exc:
        return 400, make_error_response("VALIDATION_ERROR", str(exc))


@router.patch("/{object_id}/", response={200: SupplyObjectDetailResponse, 400: dict, 403: dict, 422: dict})
@require_permission("write", "objects")
def update_object(request: HttpRequest, object_id: uuid.UUID, body: SupplyObjectUpdateRequest):
    try:
        ObjectUpdateService.execute(object_id, body)
        return 200, get_object_detail(object_id, user_info=_user_info)
    except PermissionDenied:
        return 403, make_error_response("OBJECT_NOT_FOUND", "Объект не найден или доступ запрещён.")
    except ValidationError as exc:
        return 422, make_error_response("VALIDATION_ERROR", str(exc))


@router.patch(
    "/{object_id}/stages/{stage_key}/",
    response={200: SupplyObjectDetailResponse, 400: dict, 403: dict, 422: dict},
)
@require_permission("write", "objects")
def update_stage(request: HttpRequest, object_id: uuid.UUID, stage_key: str, body: StageUpdateRequest):
    try:
        StageUpdateService.update(
            object_id=object_id,
            stage_key=stage_key,
            status=body.status,
            note=body.note,
            completed_by_id=body.completed_by_id,
        )
        return 200, get_object_detail(object_id, user_info=_user_info)
    except PermissionDenied:
        return 403, make_error_response("STAGE_NOT_FOUND", "Этап не найден или доступ запрещён.")
    except ValidationError as exc:
        return 422, make_error_response("STAGE_UPDATE_INVALID", str(exc))


@router.patch("/{object_id}/location/", response={204: None, 400: dict, 403: dict})
@require_permission("write", "objects")
def update_location(request: HttpRequest, object_id: uuid.UUID, body: UpdateLocationRequest):
    try:
        ObjectLocationService.update_location(object_id, body)
        return 204, None
    except PermissionDenied:
        return 403, make_error_response("OBJECT_NOT_FOUND", "Объект не найден.")


@router.delete("/{object_id}/", response={204: None, 403: dict})
@require_permission("delete", "objects")
def delete_object(request: HttpRequest, object_id: uuid.UUID):
    try:
        ObjectDeleteService.soft_delete(object_id)
        return 204, None
    except PermissionDenied:
        return 403, make_error_response("DELETE_FORBIDDEN", "Удаление запрещено.")


@router.get(
    "/{object_id}/contracts/",
    response={200: list[ObjectContractResponse], 403: dict},
)
@require_permission("read", "objects")
def list_object_contracts(request: HttpRequest, object_id: uuid.UUID):
    from apps.objects.repositories.object_contract_repo import ObjectContractRepository

    try:
        from apps.objects.repositories.object_repo import ObjectRepository
        ObjectRepository.get_by_id(object_id)
    except PermissionDenied:
        return 403, make_error_response("OBJECT_NOT_FOUND", "Объект не найден.")
    links = ObjectContractRepository.list_by_object(object_id)
    return 200, [ObjectContractResponse(contract_id=lnk.contract_id) for lnk in links]


@router.post(
    "/{object_id}/contracts/",
    response={200: ObjectContractResponse, 403: dict},
)
@require_permission("write", "objects")
def link_object_contract(
    request: HttpRequest,
    object_id: uuid.UUID,
    body: ObjectContractLinkRequest,
):
    try:
        lnk = ObjectContractService.link(object_id, body.contract_id)
        return 200, ObjectContractResponse(contract_id=lnk.contract_id)
    except PermissionDenied:
        return 403, make_error_response("OBJECT_NOT_FOUND", "Объект не найден.")


@router.delete(
    "/{object_id}/contracts/{contract_id}/",
    response={204: None, 403: dict},
)
@require_permission("write", "objects")
def unlink_object_contract(
    request: HttpRequest, object_id: uuid.UUID, contract_id: uuid.UUID
):
    try:
        ObjectContractService.unlink(object_id, contract_id)
        return 204, None
    except PermissionDenied:
        return 403, make_error_response("OBJECT_NOT_FOUND", "Объект не найден.")


@router.get(
    "/by-contract/{contract_id}/",
    response={200: SupplyObjectListResponse, 403: dict},
)
@require_permission("read", "objects")
def objects_by_contract(request: HttpRequest, contract_id: uuid.UUID):
    from apps.objects.repositories.object_contract_repo import ObjectContractRepository

    object_ids = ObjectContractRepository.list_by_contract(contract_id)
    if not object_ids:
        from apps.objects.schemas.object_dto import SupplyObjectListResponse as LR
        return 200, LR(results=[], count=0)
    result = get_objects_page(object_ids=object_ids, limit=100, user_info=_user_info)
    return 200, result
