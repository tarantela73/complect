import uuid
from typing import Optional

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from ninja import Query, Router

from apps.common.api.errors import make_error_response
from apps.objects.queries.public_object_queries import get_public_object_by_slug, get_public_objects_catalog
from apps.objects.schemas.object_dto import PublicObjectListResponse, PublicObjectSchema

router = Router(tags=["public-objects"])


@router.get("/", response={200: PublicObjectListResponse})
def list_public_objects(
    request: HttpRequest,
    resource_type: Optional[str] = Query(None),
    cursor: Optional[uuid.UUID] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    return 200, get_public_objects_catalog(
        resource_type=resource_type,
        cursor=cursor,
        limit=limit,
    )


@router.get("/{slug}/", response={200: PublicObjectSchema, 404: dict})
def get_public_object(request: HttpRequest, slug: str):
    try:
        return 200, get_public_object_by_slug(slug)
    except PermissionDenied:
        return 404, make_error_response("OBJECT_NOT_FOUND", "Объект не найден.")
