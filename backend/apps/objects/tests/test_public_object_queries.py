import uuid

import pytest
from django.contrib.gis.geos import Point
from django.core.exceptions import PermissionDenied

from apps.common.contracts.storage_contracts import StorageBackend
from apps.common.tenancy.context import set_tenant_context
from apps.objects.models.supply_object import ResourceType, SupplyObject, SupplyObjectType
from apps.objects.queries.public_object_queries import get_public_object_by_slug, get_public_objects_catalog
from apps.objects.schemas.object_dto import SupplyObjectCreateRequest
from apps.objects.services.object_service import ObjectCreateService


class StubStorage:
    """Minimal storage stub that satisfies StorageBackend Protocol."""

    def generate_upload_url(self, *, object_key, content_type, size_bytes, expires_in) -> str:
        return f"/stub/upload/{object_key}"

    def generate_download_url(self, *, object_key, expires_in) -> str:
        return f"/stub/download/{object_key}"

    def object_exists(self, *, object_key) -> bool:
        return True


def _make_obj(
    tenant_id: uuid.UUID,
    name: str = "Объект",
    is_public: bool = False,
    resource_type: str = ResourceType.GAS,
    cover_image_key: str | None = None,
    location: Point | None = None,
    slug: str | None = None,
) -> SupplyObject:
    set_tenant_context(tenant_id)
    obj = ObjectCreateService.execute(
        SupplyObjectCreateRequest(
            name=name,
            object_type=SupplyObjectType.BUILDING,
            resource_type=resource_type,
        )
    )
    update: dict = {"is_public": is_public}
    if cover_image_key:
        update["cover_image_key"] = cover_image_key
    if location:
        update["location"] = location
    if slug:
        update["slug"] = slug
    SupplyObject.objects.filter(id=obj.id).update(**update)
    obj.refresh_from_db()
    return obj


@pytest.mark.django_db(transaction=True)
class TestGetPublicObjectsCatalog:
    def setup_method(self):
        self.tenant = uuid.uuid4()
        set_tenant_context(self.tenant)
        self.storage = StubStorage()

    def test_returns_only_public_objects(self):
        _make_obj(self.tenant, "Public", is_public=True)
        _make_obj(self.tenant, "Private", is_public=False)
        result = get_public_objects_catalog(storage=self.storage)
        assert result.count == 1
        assert result.results[0].name == "Public"

    def test_resource_type_filter(self):
        _make_obj(self.tenant, "Gas", is_public=True, resource_type=ResourceType.GAS)
        _make_obj(self.tenant, "Water", is_public=True, resource_type=ResourceType.WATER)
        result = get_public_objects_catalog(resource_type=ResourceType.GAS, storage=self.storage)
        assert result.count == 1
        assert result.results[0].name == "Gas"

    def test_other_tenant_not_visible(self):
        other = uuid.uuid4()
        _make_obj(other, "Other Tenant Object", is_public=True)
        # Restore correct tenant context after creating object for another tenant
        set_tenant_context(self.tenant)
        result = get_public_objects_catalog(storage=self.storage)
        assert result.count == 0

    def test_cursor_pagination(self):
        objs = [_make_obj(self.tenant, f"Obj {i}", is_public=True) for i in range(5)]
        first_page = get_public_objects_catalog(limit=3, storage=self.storage)
        assert first_page.count == 3
        assert first_page.next_cursor is not None
        cursor_id = uuid.UUID(first_page.next_cursor)
        second_page = get_public_objects_catalog(limit=3, cursor=cursor_id, storage=self.storage)
        assert second_page.count == 2
        assert second_page.next_cursor is None
        first_ids = {r.id for r in first_page.results}
        second_ids = {r.id for r in second_page.results}
        assert first_ids.isdisjoint(second_ids)

    def test_cover_image_url_generated(self):
        _make_obj(self.tenant, "With Cover", is_public=True, cover_image_key="objects/abc.jpg")
        result = get_public_objects_catalog(storage=self.storage)
        assert result.results[0].cover_image_url == "/stub/download/objects/abc.jpg"

    def test_no_cover_image_url_when_key_missing(self):
        _make_obj(self.tenant, "No Cover", is_public=True)
        result = get_public_objects_catalog(storage=self.storage)
        assert result.results[0].cover_image_url is None

    def test_location_included_when_present(self):
        _make_obj(self.tenant, "With Location", is_public=True, location=Point(37.6, 55.7, srid=4326))
        result = get_public_objects_catalog(storage=self.storage)
        loc = result.results[0].location
        assert loc is not None
        assert abs(loc.lon - 37.6) < 0.001
        assert abs(loc.lat - 55.7) < 0.001

    def test_location_null_when_not_set(self):
        _make_obj(self.tenant, "No Location", is_public=True)
        result = get_public_objects_catalog(storage=self.storage)
        assert result.results[0].location is None

    def test_object_without_slug_excluded(self):
        obj = _make_obj(self.tenant, "No Slug", is_public=True)
        # Remove slug to simulate pre-migration object
        SupplyObject.objects.filter(id=obj.id).update(slug=None)
        result = get_public_objects_catalog(storage=self.storage)
        assert result.count == 0


@pytest.mark.django_db(transaction=True)
class TestGetPublicObjectBySlug:
    def setup_method(self):
        self.tenant = uuid.uuid4()
        set_tenant_context(self.tenant)
        self.storage = StubStorage()

    def test_returns_public_object_by_slug(self):
        obj = _make_obj(self.tenant, "Тест", is_public=True)
        result = get_public_object_by_slug(obj.slug, storage=self.storage)
        assert result.id == obj.id
        assert result.name == "Тест"

    def test_not_found_raises_permission_denied(self):
        with pytest.raises(PermissionDenied):
            get_public_object_by_slug("non-existent-slug", storage=self.storage)

    def test_private_object_raises_permission_denied(self):
        obj = _make_obj(self.tenant, "Private", is_public=False)
        with pytest.raises(PermissionDenied):
            get_public_object_by_slug(obj.slug, storage=self.storage)

    def test_no_internal_fields_in_response(self):
        obj = _make_obj(self.tenant, "Тест", is_public=True)
        result = get_public_object_by_slug(obj.slug, storage=self.storage)
        result_dict = result.model_dump()
        forbidden = {"status", "manager_id", "foreman_id", "contract_id", "contragent_id", "tenant_id"}
        assert not forbidden.intersection(result_dict.keys())
