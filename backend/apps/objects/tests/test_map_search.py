import uuid

import pytest

from apps.common.tenancy.context import set_tenant_context
from apps.objects.models.supply_object import ResourceType, SupplyObject, SupplyObjectType
from apps.objects.queries.map_search import ObjectMapDTO, search_objects_for_map
from apps.objects.schemas.object_dto import SupplyObjectCreateRequest
from apps.objects.services.object_service import ObjectCreateService


def _make_obj(
    tenant_id: uuid.UUID,
    name: str = "Объект",
    address: str = "",
    lon: float | None = None,
    lat: float | None = None,
) -> SupplyObject:
    set_tenant_context(tenant_id)
    req = SupplyObjectCreateRequest(
        name=name,
        address=address,
        object_type=SupplyObjectType.BUILDING,
        resource_type=ResourceType.GAS,
        longitude=lon,
        latitude=lat,
    )
    return ObjectCreateService.execute(req)


@pytest.mark.django_db(transaction=True)
class TestSearchObjectsForMap:
    def setup_method(self):
        self.tenant_id = uuid.uuid4()

    def test_finds_by_name(self):
        _make_obj(self.tenant_id, name="Солнечная ТП-1")
        _make_obj(self.tenant_id, name="Лунная ТП-2")

        results = search_objects_for_map("Солнечная", self.tenant_id)

        assert len(results) == 1
        assert isinstance(results[0], ObjectMapDTO)
        assert "Солнечная" in results[0].name

    def test_finds_by_address(self):
        _make_obj(self.tenant_id, name="Объект-А", address="ул. Северная 1")
        _make_obj(self.tenant_id, name="Объект-Б", address="ул. Южная 2")

        results = search_objects_for_map("Северная", self.tenant_id)

        assert len(results) == 1
        assert results[0].name == "Объект-А"

    def test_returns_empty_for_no_match(self):
        _make_obj(self.tenant_id, name="Объект")

        results = search_objects_for_map("xyz_notexist", self.tenant_id)

        assert results == []

    def test_tenant_isolation(self):
        other_tenant = uuid.uuid4()
        _make_obj(other_tenant, name="Чужой объект")

        results = search_objects_for_map("Чужой", self.tenant_id)

        assert results == []

    def test_deleted_not_returned(self):
        set_tenant_context(self.tenant_id)
        obj = _make_obj(self.tenant_id, name="Удалённый объект")
        obj.is_deleted = True
        obj.save()

        results = search_objects_for_map("Удалённый", self.tenant_id)

        assert results == []

    def test_limit_respected(self):
        for i in range(6):
            _make_obj(self.tenant_id, name=f"Газопровод {i}")

        results = search_objects_for_map("Газопровод", self.tenant_id, limit=2)

        assert len(results) == 2

    def test_location_included_when_set(self):
        set_tenant_context(self.tenant_id)
        _make_obj(self.tenant_id, name="С координатами", lon=38.97, lat=45.03)

        results = search_objects_for_map("координат", self.tenant_id)

        assert len(results) == 1
        assert results[0].lat == pytest.approx(45.03, abs=0.001)
        assert results[0].lon == pytest.approx(38.97, abs=0.001)
