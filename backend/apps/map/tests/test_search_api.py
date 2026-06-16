import uuid
from unittest.mock import AsyncMock, patch

import pytest
from django.test import override_settings

from apps.common.tenancy.context import set_tenant_context
from apps.objects.models.supply_object import ResourceType, SupplyObjectType
from apps.objects.schemas.object_dto import SupplyObjectCreateRequest
from apps.objects.services.object_service import ObjectCreateService
from apps.users.services.auth_service import _pk_to_uuid4

GW_TOKEN = "complect_internal_secure_mesh_token_2026"


def _make_obj(tenant_id: uuid.UUID, name: str, address: str = ""):
    set_tenant_context(tenant_id)
    req = SupplyObjectCreateRequest(
        name=name,
        address=address,
        object_type=SupplyObjectType.BUILDING,
        resource_type=ResourceType.GAS,
    )
    return ObjectCreateService.execute(req)


@pytest.mark.django_db(transaction=True)
class TestMapSearchAPI:
    @pytest.fixture(autouse=True)
    def setup(self):
        from apps.users.models import User
        self.tenant_id = uuid.uuid4()
        self.user = User.objects.create_user(
            username="testmap",
            email="testmap@example.com",
            password="TestPass123!",
            tenant_id=self.tenant_id,
        )

    def _auth_headers(self):
        return {
            "HTTP_X_GATEWAY_TOKEN": GW_TOKEN,
            "HTTP_X_TENANT_ID": str(self.tenant_id),
            "HTTP_X_USER_ID": _pk_to_uuid4(self.user.pk),
        }

    @override_settings(RBAC_ENFORCEMENT_MODE="OFF")
    @patch("apps.map.api.search_api.suggest_address", new_callable=AsyncMock)
    @patch("apps.map.api.search_api.search_cadastre", new_callable=AsyncMock)
    def test_returns_grouped_response(self, mock_cadastre, mock_dadata, client):
        mock_dadata.return_value = []
        mock_cadastre.return_value = None

        set_tenant_context(self.tenant_id)
        _make_obj(self.tenant_id, name="Солнечная ТП-1")

        resp = client.get(
            "/api/v1/map/search?q=Солнечная",
            **self._auth_headers(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "objects" in data
        assert "addresses" in data
        assert "cadastre" in data

    @override_settings(RBAC_ENFORCEMENT_MODE="OFF")
    def test_short_query_returns_422(self, client):
        resp = client.get(
            "/api/v1/map/search?q=а",
            **self._auth_headers(),
        )
        assert resp.status_code == 400
