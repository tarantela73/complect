import uuid

import pytest
from django.core.exceptions import PermissionDenied

from apps.common.context.core import (
    set_correlation_id,
    set_request_id,
    set_tenant_id,
    set_user_id,
)
from apps.common.tenancy.context import set_tenant_context
from apps.contragents.models.contragent import Contragent, ContragentType
from apps.contragents.services.registration import ContragentRegistrationService


def _set_full_context(tenant_id: uuid.UUID) -> None:
    set_tenant_id(tenant_id)
    set_user_id(uuid.uuid4())
    set_request_id(uuid.uuid4())
    set_correlation_id(uuid.uuid4())


def _make_contragent(tenant_id: uuid.UUID) -> Contragent:
    set_tenant_context(tenant_id)
    return Contragent.objects.create(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name="Тест АО",
        inn=str(tenant_id.int)[:10].zfill(10),
        contragent_type=ContragentType.LEGAL_ENTITY,
    )


@pytest.mark.django_db(transaction=True)
class TestRegistrationTenancyIsolation:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.tenant_a = uuid.uuid4()
        self.tenant_b = uuid.uuid4()

    def test_cross_tenant_pii_registration_raises_permission_denied(self):
        """Cross-tenant PII write: tenant B cannot create a person profile on tenant A's contragent."""
        contragent = _make_contragent(self.tenant_a)

        _set_full_context(self.tenant_b)
        with pytest.raises(PermissionDenied):
            ContragentRegistrationService.register_person_profile(
                contragent_id=contragent.id,
                first_name="Иван",
                last_name="Иванов",
                middle_name="",
                passport="1234567890",
                snils="12345678901",
                phone="+79001234567",
            )

    def test_same_tenant_pii_registration_succeeds(self):
        """Same-tenant registration must work normally."""
        contragent = _make_contragent(self.tenant_a)

        _set_full_context(self.tenant_a)
        profile_id = ContragentRegistrationService.register_person_profile(
            contragent_id=contragent.id,
            first_name="Пётр",
            last_name="Петров",
            middle_name="",
            passport="9876543210",
            snils="09876543210",
            phone="+79009876543",
        )
        assert isinstance(profile_id, uuid.UUID)

    def test_nonexistent_contragent_raises_permission_denied(self):
        """Unknown contragent UUID raises PermissionDenied (no oracle — same response as wrong tenant)."""
        _set_full_context(self.tenant_a)
        with pytest.raises(PermissionDenied):
            ContragentRegistrationService.register_person_profile(
                contragent_id=uuid.uuid4(),
                first_name="Иван",
                last_name="Иванов",
                middle_name="",
                passport="1234567890",
                snils="12345678901",
                phone="+79001234567",
            )
