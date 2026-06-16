import uuid
from dataclasses import dataclass
from typing import Optional

from django.db.models import Q

from apps.objects.models.supply_object import SupplyObject


@dataclass(slots=True, frozen=True)
class ObjectMapDTO:
    id: str
    name: str
    address: str
    lat: Optional[float]
    lon: Optional[float]
    status: str


def search_objects_for_map(
    q: str,
    tenant_id: uuid.UUID,
    limit: int = 5,
) -> list[ObjectMapDTO]:
    qs = (
        SupplyObject.objects.using("replica")
        .filter(tenant_id=tenant_id, is_deleted=False)
        .filter(Q(name__icontains=q) | Q(address__icontains=q))
        .order_by("name")[:limit]
    )

    results: list[ObjectMapDTO] = []
    for obj in qs:
        lat = obj.location.y if obj.location else None
        lon = obj.location.x if obj.location else None
        results.append(
            ObjectMapDTO(
                id=str(obj.id),
                name=obj.name,
                address=obj.address,
                lat=lat,
                lon=lon,
                status=obj.status,
            )
        )
    return results
