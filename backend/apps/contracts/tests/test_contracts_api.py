"""Tests for PATCH /api/v1/contracts/{id}/ and POST /api/v1/contracts/{id}/activate/."""

from __future__ import annotations

import uuid

import pytest
from django.test import override_settings

from apps.common.tenancy.context import set_tenant_context
from apps.contracts.models.contract import Contract, ContractStatus, ResourceSupplyType
from apps.users.services.auth_service import _pk_to_uuid4

GW_TOKEN = "complect_internal_secure_mesh_token_2026"


def _make_contract(
    tenant_id: uuid.UUID,
    *,
    title: str = "Тестовый договор",
    status: str = ContractStatus.DRAFT,
) -> Contract:
    set_tenant_context(tenant_id)
    return Contract.objects.create(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        contract_number=f"TEST-{uuid.uuid4().hex[:8].upper()}",
        title=title,
        status=status,
        resource_type=ResourceSupplyType.ELECTRICITY,
        total_amount=100_000,
        paid_amount=0,
    )


@pytest.mark.django_db(transaction=True)
class TestUpdateContract:
    @pytest.fixture(autouse=True)
    def setup(self):
        from apps.users.models import User

        self.tenant_id = uuid.uuid4()
        self.user = User.objects.create_user(
            username="test_update_contract",
            email="test_update_contract@example.com",
            password="TestPass123!",
            tenant_id=self.tenant_id,
        )

    def _auth(self):
        return {
            "HTTP_X_GATEWAY_TOKEN": GW_TOKEN,
            "HTTP_X_TENANT_ID": str(self.tenant_id),
            "HTTP_X_USER_ID": _pk_to_uuid4(self.user.pk),
            "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
        }

    @override_settings(RBAC_ENFORCEMENT_MODE="OFF")
    def test_update_draft_title_returns_200(self, client):
        c = _make_contract(self.tenant_id, title="Старый заголовок")
        resp = client.patch(
            f"/api/v1/contracts/{c.id}/",
            data={"title": "Новый заголовок"},
            content_type="application/json",
            **self._auth(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Новый заголовок"
        assert body["id"] == str(c.id)

    @override_settings(RBAC_ENFORCEMENT_MODE="OFF")
    def test_update_non_draft_returns_422(self, client):
        c = _make_contract(self.tenant_id, status=ContractStatus.ACTIVE)
        resp = client.patch(
            f"/api/v1/contracts/{c.id}/",
            data={"title": "Попытка"},
            content_type="application/json",
            **self._auth(),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "INVALID_STATUS"

    @override_settings(RBAC_ENFORCEMENT_MODE="OFF")
    def test_update_unknown_contract_returns_404(self, client):
        resp = client.patch(
            f"/api/v1/contracts/{uuid.uuid4()}/",
            data={"title": "X"},
            content_type="application/json",
            **self._auth(),
        )
        assert resp.status_code == 404


@pytest.mark.django_db(transaction=True)
class TestActivateContract:
    @pytest.fixture(autouse=True)
    def setup(self):
        from apps.users.models import User

        self.tenant_id = uuid.uuid4()
        self.user = User.objects.create_user(
            username="test_activate_contract",
            email="test_activate_contract@example.com",
            password="TestPass123!",
            tenant_id=self.tenant_id,
        )

    def _auth(self):
        return {
            "HTTP_X_GATEWAY_TOKEN": GW_TOKEN,
            "HTTP_X_TENANT_ID": str(self.tenant_id),
            "HTTP_X_USER_ID": _pk_to_uuid4(self.user.pk),
            "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
        }

    @override_settings(RBAC_ENFORCEMENT_MODE="OFF")
    def test_activate_draft_returns_200_and_active_status(self, client):
        c = _make_contract(self.tenant_id)
        resp = client.post(
            f"/api/v1/contracts/{c.id}/activate/",
            content_type="application/json",
            **self._auth(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == ContractStatus.ACTIVE
        assert body["id"] == str(c.id)

    @override_settings(RBAC_ENFORCEMENT_MODE="OFF")
    def test_activate_already_active_returns_422(self, client):
        c = _make_contract(self.tenant_id, status=ContractStatus.ACTIVE)
        resp = client.post(
            f"/api/v1/contracts/{c.id}/activate/",
            content_type="application/json",
            **self._auth(),
        )
        assert resp.status_code == 422
