import uuid
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest
from ninja import Query, Router

from apps.common.api.errors import make_error_response
from apps.objects.queries.object_queries import (
    get_object_detail,
    get_objects_geojson,
    get_objects_page,
)
from apps.objects.schemas.object_dto import (
    FeatureCollectionSchema,
    FeatureSchema,
    SupplyObjectCreateRequest,
    SupplyObjectListResponse,
    UpdateLocationRequest,
)
from apps.objects.services.object_service import (
    ObjectCreateService,
    ObjectDeleteService,
    ObjectLocationService,
)

router = Router(tags=["objects"])


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


@router.get("/", response={200: SupplyObjectListResponse})
def list_objects(
    request: HttpRequest,
    cursor: Optional[uuid.UUID] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    object_type: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    contract_id: Optional[uuid.UUID] = Query(None),
    bbox: Optional[str] = Query(None, description="lon_min,lat_min,lon_max,lat_max"),
):
    result = get_objects_page(
        cursor=cursor,
        limit=limit,
        object_type=object_type,
        resource_type=resource_type,
        contract_id=contract_id,
        bbox=_parse_bbox(bbox),
    )
    return 200, result


@router.get("/geojson/", response={200: FeatureCollectionSchema})
def objects_geojson(
    request: HttpRequest,
    contract_id: Optional[uuid.UUID] = Query(None),
    bbox: Optional[str] = Query(None, description="lon_min,lat_min,lon_max,lat_max"),
):
    result = get_objects_geojson(contract_id=contract_id, bbox=_parse_bbox(bbox))
    return 200, result


@router.get("/{object_id}/", response={200: FeatureSchema, 403: dict})
def get_object(request: HttpRequest, object_id: uuid.UUID):
    try:
        return 200, get_object_detail(object_id)
    except PermissionDenied:
        return 403, make_error_response("OBJECT_NOT_FOUND", "Объект не найден.")


@router.post("/", response={201: FeatureSchema, 400: dict})
def create_object(request: HttpRequest, body: SupplyObjectCreateRequest):
    try:
        obj = ObjectCreateService.execute(body)
        return 201, get_object_detail(obj.id)
    except (ValidationError, ValueError) as exc:
        return 400, make_error_response("VALIDATION_ERROR", str(exc))


@router.patch("/{object_id}/location/", response={204: None, 400: dict, 403: dict})
def update_location(request: HttpRequest, object_id: uuid.UUID, body: UpdateLocationRequest):
    try:
        ObjectLocationService.update_location(object_id, body)
        return 204, None
    except PermissionDenied:
        return 403, make_error_response("OBJECT_NOT_FOUND", "Объект не найден.")


@router.delete("/{object_id}/", response={204: None, 403: dict})
def delete_object(request: HttpRequest, object_id: uuid.UUID):
    try:
        ObjectDeleteService.soft_delete(object_id)
        return 204, None
    except PermissionDenied:
        return 403, make_error_response("DELETE_FORBIDDEN", "Удаление запрещено.")
