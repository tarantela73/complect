"""Tests for PATCH /api/v1/contragents/{id}/ — update_contragent endpoint."""
from __future__ import annotations

import uuid

import pytest
from django.test import override_settings

from apps.common.tenancy.context import set_tenant_context
from apps.contragents.models.contragent import Contragent, ContragentType
from apps.users.services.auth_service import _pk_to_uuid4

GW_TOKEN = "complect_internal_secure_mesh_token_2026"


def _make_contragent(tenant_id: uuid.UUID, *, name: str = "ООО Тест") -> Contragent:
    set_tenant_context(tenant_id)
    return Contragent.objects.create(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=name,
        inn="1234567890",
        contragent_type=ContragentType.LEGAL_ENTITY,
    )


@pytest.mark.django_db(transaction=True)
class TestUpdateContragent:
    @pytest.fixture(autouse=True)
    def setup(self):
        from apps.users.models import User

        self.tenant_id = uuid.uuid4()
        self.user = User.objects.create_user(
            username="test_update_contragent",
            email="test_update_contragent@example.com",
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
    def test_update_name_returns_200(self, client):
        c = _make_contragent(self.tenant_id, name="Старое название")
        resp = client.patch(
            f"/api/v1/contragents/{c.id}/",
            data={"name": "Новое название"},
            content_type="application/json",
            **self._auth(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Новое название"
        assert body["id"] == str(c.id)

    @override_settings(RBAC_ENFORCEMENT_MODE="OFF")
    def test_update_unknown_id_returns_404(self, client):
        resp = client.patch(
            f"/api/v1/contragents/{uuid.uuid4()}/",
            data={"name": "X"},
            content_type="application/json",
            **self._auth(),
        )
        assert resp.status_code == 404

    @override_settings(RBAC_ENFORCEMENT_MODE="OFF")
    def test_update_legal_hold_returns_422(self, client):
        c = _make_contragent(self.tenant_id)
        c.is_legal_hold = True
        c.save(update_fields=["is_legal_hold"])
        resp = client.patch(
            f"/api/v1/contragents/{c.id}/",
            data={"name": "Попытка"},
            content_type="application/json",
            **self._auth(),
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "LEGAL_HOLD"

    @override_settings(RBAC_ENFORCEMENT_MODE="OFF")
    def test_update_other_tenant_returns_404(self, client):
        other_tenant = uuid.uuid4()
        c = _make_contragent(other_tenant)
        resp = client.patch(
            f"/api/v1/contragents/{c.id}/",
            data={"name": "Попытка захвата"},
            content_type="application/json",
            **self._auth(),
        )
        assert resp.status_code == 404
