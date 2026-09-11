import uuid

import pytest
from django.db import transaction

from apps.users.infrastructure.iam_tenant_inventory import (
    DurableIamTenantInventoryProvider,
    LegacyIamTenantDiscovery,
)
from apps.users.models import User
from apps.users.repositories.iam_tenant_registry_repository import IamTenantRegistryRepository


@pytest.mark.django_db(transaction=True)
def test_durable_inventory_is_explicit_and_uuid_sorted():
    tenant_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    User.objects.create_user(username="inventory-user", tenant_id=tenant_ids[0])
    discovered = set(LegacyIamTenantDiscovery().iter_tenant_ids())
    assert tenant_ids[0] in discovered
    with transaction.atomic():
        for tenant_id in tenant_ids:
            row = IamTenantRegistryRepository.ensure_registered(tenant_id, "test_inventory")
            assert row.tenant_id == tenant_id
    expected = sorted(tenant_ids, key=lambda value: value.bytes)
    assert list(DurableIamTenantInventoryProvider().iter_tenant_ids()) == expected


@pytest.mark.django_db(transaction=True)
def test_registry_registration_is_idempotent_and_preserves_first_source():
    tenant_id = uuid.uuid4()
    with transaction.atomic():
        first = IamTenantRegistryRepository.ensure_registered(tenant_id, "first_source")
    with transaction.atomic():
        second = IamTenantRegistryRepository.ensure_registered(tenant_id, "second_source")
    assert first.tenant_id == second.tenant_id
    assert second.source_code == "first_source"
