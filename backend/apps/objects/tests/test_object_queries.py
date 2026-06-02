import uuid

import pytest
from django.contrib.gis.geos import Point, Polygon
from django.core.exceptions import PermissionDenied

from apps.common.tenancy.context import set_tenant_context
from apps.objects.models.supply_object import ResourceType, SupplyObject, SupplyObjectType
from apps.objects.queries.object_queries import get_object_detail, get_objects_geojson, get_objects_page
from apps.objects.repositories.object_repo import ObjectRepository
from apps.objects.services.object_service import ObjectCreateService, ObjectDeleteService, ObjectLocationService
from apps.objects.schemas.object_dto import SupplyObjectCreateRequest, UpdateLocationRequest


def _make_obj(
    tenant_id: uuid.UUID,
    name: str = "Объект",
    object_type: str = SupplyObjectType.BUILDING,
    resource_type: str = ResourceType.GAS,
    contract_id: uuid.UUID | None = None,
    lon: float | None = None,
    lat: float | None = None,
) -> SupplyObject:
    set_tenant_context(tenant_id)
    req = SupplyObjectCreateRequest(
        name=name,
        object_type=object_type,
        resource_type=resource_type,
        contract_id=contract_id,
        longitude=lon,
        latitude=lat,
    )
    return ObjectCreateService.execute(req)


@pytest.mark.django_db(transaction=True)
class TestObjectQueries:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.tenant_id = uuid.uuid4()
        self.other_tenant_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    # --- Tenant isolation ---

    def test_cursor_pagination_isolates_by_tenant(self):
        for i in range(3):
            _make_obj(self.tenant_id, name=f"Мой {i}")
        for i in range(2):
            _make_obj(self.other_tenant_id, name=f"Чужой {i}")

        set_tenant_context(self.tenant_id)
        result = get_objects_page(limit=20)

        assert result["count"] == 3
        returned_ids = {r.properties.id for r in result["results"]}
        other_ids = set(SupplyObject.objects.filter(tenant_id=self.other_tenant_id).values_list("id", flat=True))
        assert returned_ids.isdisjoint(other_ids)

    # --- Cursor pagination ---

    def test_cursor_pagination_next_cursor(self):
        for i in range(3):
            _make_obj(self.tenant_id, name=f"Obj {i}")

        page1 = get_objects_page(limit=2)
        assert page1["count"] == 2
        assert page1["next_cursor"] is not None

        page2 = get_objects_page(cursor=uuid.UUID(page1["next_cursor"]), limit=2)
        assert page2["count"] == 1
        assert page2["next_cursor"] is None

    def test_cursor_pagination_no_overlap(self):
        for i in range(4):
            _make_obj(self.tenant_id, name=f"Obj {i}")

        page1 = get_objects_page(limit=2)
        page2 = get_objects_page(cursor=uuid.UUID(page1["next_cursor"]), limit=2)

        ids1 = {r.properties.id for r in page1["results"]}
        ids2 = {r.properties.id for r in page2["results"]}
        assert ids1.isdisjoint(ids2)

    # --- Filters ---

    def test_filter_by_object_type(self):
        _make_obj(self.tenant_id, object_type=SupplyObjectType.GRS)
        _make_obj(self.tenant_id, object_type=SupplyObjectType.BUILDING)

        result = get_objects_page(object_type=SupplyObjectType.GRS, limit=20)
        assert result["count"] == 1
        assert result["results"][0].properties.object_type == SupplyObjectType.GRS

    def test_filter_by_resource_type(self):
        _make_obj(self.tenant_id, resource_type=ResourceType.GAS)
        _make_obj(self.tenant_id, resource_type=ResourceType.WATER)

        result = get_objects_page(resource_type=ResourceType.WATER, limit=20)
        assert result["count"] == 1
        assert result["results"][0].properties.resource_type == ResourceType.WATER

    def test_filter_by_contract_id(self):
        contract_id = uuid.uuid4()
        _make_obj(self.tenant_id, contract_id=contract_id)
        _make_obj(self.tenant_id)  # без договора

        result = get_objects_page(contract_id=contract_id, limit=20)
        assert result["count"] == 1
        assert result["results"][0].properties.contract_id == contract_id

    def test_filter_by_contract_id_other_tenant_returns_empty(self):
        contract_id = uuid.uuid4()
        _make_obj(self.other_tenant_id, contract_id=contract_id)

        set_tenant_context(self.tenant_id)
        result = get_objects_page(contract_id=contract_id, limit=20)
        assert result["count"] == 0

    # --- Spatial ---

    def test_bbox_filter_returns_objects_within_bounds(self):
        # Москва ~37.6, 55.7
        _make_obj(self.tenant_id, name="В Москве", lon=37.6, lat=55.7)
        # Владивосток ~132.0, 43.1 — вне bbox
        _make_obj(self.tenant_id, name="Владивосток", lon=132.0, lat=43.1)

        moscow_bbox = (37.0, 55.0, 38.0, 56.0)
        result = get_objects_page(bbox=moscow_bbox, limit=20)

        assert result["count"] == 1
        assert result["results"][0].properties.name == "В Москве"

    def test_bbox_filter_excludes_objects_without_location(self):
        _make_obj(self.tenant_id, name="Без координат")  # location=None
        _make_obj(self.tenant_id, name="С координатами", lon=37.6, lat=55.7)

        moscow_bbox = (37.0, 55.0, 38.0, 56.0)
        result = get_objects_page(bbox=moscow_bbox, limit=20)

        names = {r.properties.name for r in result["results"]}
        assert "С координатами" in names
        assert "Без координат" not in names

    # --- GeoJSON shape ---

    def test_geojson_returns_feature_collection(self):
        _make_obj(self.tenant_id, lon=37.6, lat=55.7)

        fc = get_objects_geojson()
        assert fc.type == "FeatureCollection"
        assert len(fc.features) == 1
        assert fc.features[0].type == "Feature"
        geom = fc.features[0].geometry
        assert geom is not None
        assert geom.type == "Point"
        assert abs(geom.coordinates[0] - 37.6) < 0.001
        assert abs(geom.coordinates[1] - 55.7) < 0.001

    def test_geojson_geometry_none_for_objects_without_location(self):
        _make_obj(self.tenant_id, name="Без точки")

        fc = get_objects_geojson()
        assert fc.features[0].geometry is None

    # --- Detail ---

    def test_get_object_detail_returns_feature(self):
        obj = _make_obj(self.tenant_id, name="Детальный")
        feature = get_object_detail(obj.id)
        assert feature.properties.id == obj.id
        assert feature.properties.name == "Детальный"

    def test_get_object_detail_wrong_tenant_raises(self):
        obj = _make_obj(self.other_tenant_id)
        set_tenant_context(self.tenant_id)
        with pytest.raises(PermissionDenied):
            get_object_detail(obj.id)


@pytest.mark.django_db(transaction=True)
class TestObjectLocationService:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.tenant_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    def test_update_location_sets_point(self):
        obj = _make_obj(self.tenant_id)
        ObjectLocationService.update_location(obj.id, UpdateLocationRequest(longitude=37.6, latitude=55.7))

        obj.refresh_from_db()
        assert obj.location is not None
        assert abs(obj.location.x - 37.6) < 0.001
        assert abs(obj.location.y - 55.7) < 0.001

    def test_update_location_wrong_tenant_raises(self):
        other_tenant_id = uuid.uuid4()
        obj = _make_obj(other_tenant_id)
        set_tenant_context(self.tenant_id)
        with pytest.raises(PermissionDenied):
            ObjectLocationService.update_location(obj.id, UpdateLocationRequest(longitude=37.6, latitude=55.7))

    def test_update_location_legal_hold_raises(self):
        obj = _make_obj(self.tenant_id)
        SupplyObject.objects.filter(id=obj.id).update(is_legal_hold=True)
        with pytest.raises(PermissionDenied):
            ObjectLocationService.update_location(obj.id, UpdateLocationRequest(longitude=37.6, latitude=55.7))


@pytest.mark.django_db(transaction=True)
class TestObjectDeleteService:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.tenant_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    def test_soft_delete_sets_is_deleted(self):
        obj = _make_obj(self.tenant_id)
        ObjectDeleteService.soft_delete(obj.id)
        obj.refresh_from_db()
        assert obj.is_deleted is True
        assert obj.deleted_at is not None

    def test_soft_delete_legal_hold_raises(self):
        obj = _make_obj(self.tenant_id)
        SupplyObject.objects.filter(id=obj.id).update(is_legal_hold=True)
        with pytest.raises(PermissionDenied, match="юридическим удержанием"):
            ObjectDeleteService.soft_delete(obj.id)

    def test_deleted_object_not_in_queryset(self):
        obj = _make_obj(self.tenant_id)
        ObjectDeleteService.soft_delete(obj.id)
        result = get_objects_page(limit=20)
        ids = {r.properties.id for r in result["results"]}
        assert obj.id not in ids
