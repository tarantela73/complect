import uuid
from typing import Optional

from django.contrib.gis.geos import Polygon
from django.core.exceptions import PermissionDenied

from apps.common.tenancy.context_core import get_tenant_id
from apps.objects.models.supply_object import SupplyObject


class ObjectRepository:
    @staticmethod
    def get_queryset():
        return SupplyObject.objects.filter(tenant_id=get_tenant_id(), is_deleted=False)

    @classmethod
    def get_by_id(cls, object_id: uuid.UUID) -> SupplyObject:
        obj = cls.get_queryset().filter(id=object_id).first()
        if not obj:
            raise PermissionDenied("Объект не найден или недоступен для текущего тенанта.")
        return obj

    @staticmethod
    def bbox_filter(qs, bbox: tuple[float, float, float, float]):
        poly = Polygon.from_bbox(bbox)
        poly.srid = 4326
        return qs.filter(location__within=poly)

    @staticmethod
    def create(obj: SupplyObject) -> SupplyObject:
        obj.tenant_id = get_tenant_id()
        obj.save()
        return obj

    @staticmethod
    def update_location(
        object_id: uuid.UUID,
        location=None,
        boundary=None,
    ) -> int:
        tenant_id = get_tenant_id()
        fields: dict = {}
        if location is not None:
            fields["location"] = location
        if boundary is not None:
            fields["boundary"] = boundary
        return SupplyObject.objects.filter(
            id=object_id, tenant_id=tenant_id, is_deleted=False, is_legal_hold=False
        ).update(**fields)

    @staticmethod
    def soft_delete(object_id: uuid.UUID) -> int:
        import django.utils.timezone as tz

        tenant_id = get_tenant_id()
        return SupplyObject.objects.filter(
            id=object_id, tenant_id=tenant_id, is_deleted=False, is_legal_hold=False
        ).update(is_deleted=True, deleted_at=tz.now())
