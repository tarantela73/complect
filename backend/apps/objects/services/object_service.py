import uuid

from django.contrib.gis.geos import Point
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.text import slugify

from apps.objects.models.object_stage import ObjectStage
from apps.objects.models.supply_object import SupplyObject
from apps.objects.repositories.object_repo import ObjectRepository
from apps.objects.schemas.object_dto import SupplyObjectCreateRequest, UpdateLocationRequest


def _generate_unique_slug(name: str, tenant_id: uuid.UUID, exclude_id: uuid.UUID | None = None) -> str:
    base = slugify(name, allow_unicode=True)
    if not base:
        base = "object"
    candidate = base
    n = 2
    qs = SupplyObject.objects.filter(tenant_id=tenant_id, slug=candidate, is_deleted=False)
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    while qs.exists():
        candidate = f"{base}-{n}"
        n += 1
        qs = SupplyObject.objects.filter(tenant_id=tenant_id, slug=candidate, is_deleted=False)
        if exclude_id is not None:
            qs = qs.exclude(id=exclude_id)
    return candidate


class ObjectCreateService:
    @staticmethod
    def execute(data: SupplyObjectCreateRequest) -> SupplyObject:
        from apps.common.tenancy.context_core import get_tenant_id
        tenant_id = get_tenant_id()

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

        with transaction.atomic():
            obj.slug = _generate_unique_slug(data.name, tenant_id)
            ObjectRepository.create(obj)
            ObjectStage.objects.bulk_create(ObjectStage.build_initial_stages(obj))

        return obj


class ObjectLocationService:
    @staticmethod
    def update_location(object_id: uuid.UUID, data: UpdateLocationRequest) -> None:
        location = Point(data.longitude, data.latitude, srid=4326)
        updated = ObjectRepository.update_location(object_id, location=location)
        if not updated:
            raise PermissionDenied("Объект не найден, удалён или под юридическим удержанием.")


class ObjectUpdateService:
    @staticmethod
    def execute(object_id: uuid.UUID, data) -> None:
        from apps.common.tenancy.context_core import get_tenant_id
        tenant_id = get_tenant_id()

        obj = ObjectRepository.get_by_id(object_id)
        if obj.is_legal_hold:
            raise PermissionDenied("Объект под юридическим удержанием — изменение запрещено.")

        fields: dict = {}
        if data.name is not None:
            fields["name"] = data.name
        if data.address is not None:
            fields["address"] = data.address
        if data.description is not None:
            fields["description"] = data.description
        if data.status is not None:
            fields["status"] = data.status
        if data.manager_id is not None:
            fields["manager_id"] = data.manager_id
        if data.foreman_id is not None:
            fields["foreman_id"] = data.foreman_id
        if data.team_name is not None:
            fields["team_name"] = data.team_name
        if data.planned_start_date is not None:
            fields["planned_start_date"] = data.planned_start_date
        if data.planned_end_date is not None:
            fields["planned_end_date"] = data.planned_end_date
        if data.actual_start_date is not None:
            fields["actual_start_date"] = data.actual_start_date
        if data.actual_end_date is not None:
            fields["actual_end_date"] = data.actual_end_date

        # Public site fields
        if data.is_public is not None:
            fields["is_public"] = data.is_public
        if data.cover_image_key is not None:
            fields["cover_image_key"] = data.cover_image_key

        # Slug handling: explicit override takes precedence; else regenerate if name changed
        if data.slug is not None:
            fields["slug"] = data.slug
        elif data.name is not None and data.name != obj.name:
            fields["slug"] = _generate_unique_slug(data.name, tenant_id, exclude_id=object_id)

        if fields:
            SupplyObject.objects.filter(
                id=object_id, tenant_id=tenant_id, is_deleted=False
            ).update(**fields)


class ObjectDeleteService:
    @staticmethod
    def soft_delete(object_id: uuid.UUID) -> None:
        obj = ObjectRepository.get_by_id(object_id)
        if obj.is_legal_hold:
            raise PermissionDenied("Объект под юридическим удержанием — удаление запрещено.")
        deleted = ObjectRepository.soft_delete(object_id)
        if not deleted:
            raise ValidationError("Не удалось удалить объект.")
