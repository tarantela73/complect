import uuid
from unittest.mock import patch

import pytest
from django.test import Client, override_settings

from apps.common.tenancy.context import set_tenant_context
from apps.objects.models.supply_object import ResourceType, SupplyObject, SupplyObjectType
from apps.objects.schemas.object_dto import SupplyObjectCreateRequest
from apps.objects.services.object_service import ObjectCreateService

PUBLIC_TENANT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PUBLIC_TENANT_UUID = uuid.UUID(PUBLIC_TENANT_ID)


def _make_public_obj(name: str, resource_type: str = ResourceType.GAS, is_public: bool = True) -> SupplyObject:
    set_tenant_context(PUBLIC_TENANT_UUID)
    obj = ObjectCreateService.execute(
        SupplyObjectCreateRequest(
            name=name,
            object_type=SupplyObjectType.BUILDING,
            resource_type=resource_type,
        )
    )
    SupplyObject.objects.filter(id=obj.id).update(is_public=is_public)
    obj.refresh_from_db()
    return obj


@pytest.mark.django_db
class TestListPublicObjects:
    def test_returns_200_empty_list(self):
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            r = client.get("/api/v1/public/objects/")
        assert r.status_code == 200
        data = r.json()
        assert "results" in data
        assert isinstance(data["results"], list)

    def test_returns_public_objects(self):
        _make_public_obj("Gas Station")
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            r = client.get("/api/v1/public/objects/")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 1
        names = [o["name"] for o in data["results"]]
        assert "Gas Station" in names

    def test_resource_type_filter(self):
        _make_public_obj("Gas Obj", resource_type=ResourceType.GAS)
        _make_public_obj("Water Obj", resource_type=ResourceType.WATER)
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            r = client.get("/api/v1/public/objects/?resource_type=WATER")
        assert r.status_code == 200
        data = r.json()
        resource_types = [o["resource_type"] for o in data["results"]]
        assert all(rt == "WATER" for rt in resource_types)
        assert "Water Obj" in [o["name"] for o in data["results"]]

    def test_no_jwt_required(self):
        """Public endpoint must not require authentication."""
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            r = client.get("/api/v1/public/objects/")
        assert r.status_code != 401
        assert r.status_code != 403

    def test_response_excludes_internal_fields(self):
        _make_public_obj("Test Obj")
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            r = client.get("/api/v1/public/objects/")
        data = r.json()
        if data["count"] > 0:
            obj = data["results"][0]
            forbidden = {"status", "manager_id", "foreman_id", "contract_id", "contragent_id", "tenant_id"}
            assert not forbidden.intersection(obj.keys())

    def test_private_objects_excluded(self):
        _make_public_obj("Private", is_public=False)
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            r = client.get("/api/v1/public/objects/")
        data = r.json()
        names = [o["name"] for o in data["results"]]
        assert "Private" not in names


@pytest.mark.django_db
class TestGetPublicObjectBySlug:
    def test_returns_object_by_slug(self):
        obj = _make_public_obj("Подстанция Иванова")
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            r = client.get(f"/api/v1/public/objects/{obj.slug}/")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Подстанция Иванова"
        assert data["slug"] == obj.slug

    def test_non_existent_slug_returns_404(self):
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            r = client.get("/api/v1/public/objects/does-not-exist/")
        assert r.status_code == 404
        data = r.json()
        assert data["error"]["code"] == "OBJECT_NOT_FOUND"

    def test_private_object_returns_404(self):
        obj = _make_public_obj("Private Obj", is_public=False)
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            r = client.get(f"/api/v1/public/objects/{obj.slug}/")
        assert r.status_code == 404

    def test_response_no_internal_fields(self):
        obj = _make_public_obj("Test Detail")
        client = Client()
        with override_settings(PUBLIC_SITE_TENANT_ID=PUBLIC_TENANT_ID):
            r = client.get(f"/api/v1/public/objects/{obj.slug}/")
        data = r.json()
        forbidden = {"status", "manager_id", "foreman_id", "contract_id", "contragent_id", "tenant_id"}
        assert not forbidden.intersection(data.keys())
