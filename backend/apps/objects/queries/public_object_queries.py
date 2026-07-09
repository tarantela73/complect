from __future__ import annotations

import uuid
from typing import Optional

from django.core.exceptions import PermissionDenied

from apps.common.contracts.storage_contracts import StorageBackend, get_storage_backend
from apps.objects.models.supply_object import SupplyObject
from apps.objects.repositories.object_repo import ObjectRepository
from apps.objects.schemas.object_dto import (
    PublicObjectListResponse,
    PublicObjectLocationSchema,
    PublicObjectSchema,
)

_COVER_URL_TTL = 3600


def _to_public_schema(obj: SupplyObject, storage: StorageBackend) -> PublicObjectSchema:
    cover_image_url: Optional[str] = None
    if obj.cover_image_key:
        cover_image_url = storage.generate_download_url(object_key=obj.cover_image_key, expires_in=_COVER_URL_TTL)

    location: Optional[PublicObjectLocationSchema] = None
    if obj.location:
        location = PublicObjectLocationSchema(lat=obj.location.y, lon=obj.location.x)

    return PublicObjectSchema(
        id=obj.id,
        slug=obj.slug,
        name=obj.name,
        address=obj.address,
        resource_type=obj.resource_type,
        object_type=obj.object_type,
        description=obj.description,
        cover_image_url=cover_image_url,
        location=location,
    )


def get_public_objects_catalog(
    resource_type: Optional[str] = None,
    cursor: Optional[uuid.UUID] = None,
    limit: int = 20,
    storage: Optional[StorageBackend] = None,
) -> PublicObjectListResponse:
    _storage = storage or get_storage_backend()

    qs = ObjectRepository.get_queryset().filter(is_public=True, slug__isnull=False).order_by("-id")

    if resource_type:
        qs = qs.filter(resource_type=resource_type)
    if cursor:
        qs = qs.filter(id__lt=cursor)

    rows = list(qs[: limit + 1])
    has_next = len(rows) > limit
    rows = rows[:limit]

    next_cursor = str(rows[-1].id) if has_next and rows else None

    return PublicObjectListResponse(
        results=[_to_public_schema(o, _storage) for o in rows],
        next_cursor=next_cursor,
        count=len(rows),
    )


def get_public_object_by_slug(
    slug: str,
    storage: Optional[StorageBackend] = None,
) -> PublicObjectSchema:
    _storage = storage or get_storage_backend()

    obj = ObjectRepository.get_queryset().filter(is_public=True, slug=slug).first()
    if obj is None:
        raise PermissionDenied("Объект не найден или недоступен.")

    return _to_public_schema(obj, _storage)
