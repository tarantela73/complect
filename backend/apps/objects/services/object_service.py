import uuid

from django.contrib.gis.geos import Point
from django.core.exceptions import PermissionDenied, ValidationError

from apps.objects.models.supply_object import SupplyObject
from apps.objects.repositories.object_repo import ObjectRepository
from apps.objects.schemas.object_dto import SupplyObjectCreateRequest, UpdateLocationRequest


class ObjectCreateService:
    @staticmethod
    def execute(data: SupplyObjectCreateRequest) -> SupplyObject:
        obj = SupplyObject(
            name=data.name,
            address=data.address,
            object_type=data.object_type,
            resource_type=data.resource_type,
            contract_id=data.contract_id,
            contragent_id=data.contragent_id,
        )
        if data.longitude is not None and data.latitude is not None:
            obj.location = Point(data.longitude, data.latitude, srid=4326)
        return ObjectRepository.create(obj)


class ObjectLocationService:
    @staticmethod
    def update_location(object_id: uuid.UUID, data: UpdateLocationRequest) -> None:
        location = Point(data.longitude, data.latitude, srid=4326)
        updated = ObjectRepository.update_location(object_id, location=location)
        if not updated:
            raise PermissionDenied("Объект не найден, удалён или под юридическим удержанием.")


class ObjectDeleteService:
    @staticmethod
    def soft_delete(object_id: uuid.UUID) -> None:
        obj = ObjectRepository.get_by_id(object_id)
        if obj.is_legal_hold:
            raise PermissionDenied("Объект под юридическим удержанием — удаление запрещено.")
        deleted = ObjectRepository.soft_delete(object_id)
        if not deleted:
            raise ValidationError("Не удалось удалить объект.")
