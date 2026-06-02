import uuid
from typing import Optional

from apps.objects.models.supply_object import SupplyObject
from apps.objects.repositories.object_repo import ObjectRepository
from apps.objects.schemas.object_dto import (
    FeatureCollectionSchema,
    FeatureSchema,
    PointGeometry,
    SupplyObjectProperties,
)


def _to_feature(obj: SupplyObject) -> FeatureSchema:
    geometry = None
    if obj.location:
        geometry = PointGeometry(coordinates=[obj.location.x, obj.location.y])
    return FeatureSchema(
        geometry=geometry,
        properties=SupplyObjectProperties(
            id=obj.id,
            name=obj.name,
            address=obj.address,
            object_type=obj.object_type,
            resource_type=obj.resource_type,
            contract_id=obj.contract_id,
            contragent_id=obj.contragent_id,
        ),
    )


def get_objects_page(
    cursor: Optional[uuid.UUID] = None,
    limit: int = 20,
    object_type: Optional[str] = None,
    resource_type: Optional[str] = None,
    contract_id: Optional[uuid.UUID] = None,
    bbox: Optional[tuple[float, float, float, float]] = None,
) -> dict:
    qs = ObjectRepository.get_queryset().order_by("-id")

    if cursor:
        qs = qs.filter(id__lt=cursor)
    if object_type:
        qs = qs.filter(object_type=object_type)
    if resource_type:
        qs = qs.filter(resource_type=resource_type)
    if contract_id:
        qs = qs.filter(contract_id=contract_id)
    if bbox:
        qs = ObjectRepository.bbox_filter(qs, bbox)

    rows = list(qs[: limit + 1])
    has_next = len(rows) > limit
    rows = rows[:limit]

    next_cursor = str(rows[-1].id) if has_next and rows else None

    return {
        "results": [_to_feature(o) for o in rows],
        "next_cursor": next_cursor,
        "count": len(rows),
    }


def get_objects_geojson(
    contract_id: Optional[uuid.UUID] = None,
    bbox: Optional[tuple[float, float, float, float]] = None,
) -> FeatureCollectionSchema:
    qs = ObjectRepository.get_queryset()

    if contract_id:
        qs = qs.filter(contract_id=contract_id)
    if bbox:
        qs = ObjectRepository.bbox_filter(qs, bbox)

    return FeatureCollectionSchema(features=[_to_feature(o) for o in qs])


def get_object_detail(object_id: uuid.UUID) -> FeatureSchema:
    return _to_feature(ObjectRepository.get_by_id(object_id))
