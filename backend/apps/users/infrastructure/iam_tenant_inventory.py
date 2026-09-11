from collections.abc import Iterator
from uuid import UUID

from apps.common.models.rbac import RoleBinding
from apps.users.models import IamTenantRegistryEntry, TenantGrantSwitchOperation, User


class LegacyIamTenantDiscovery:
    """Bounded Release-N discovery; it never scans tenant RLS tables."""

    def __init__(self, *, using: str = "default") -> None:
        self.using = using

    def iter_tenant_ids(self) -> Iterator[UUID]:
        tenant_ids = set(
            User.objects.using(self.using)
            .exclude(tenant_id__isnull=True)
            .values_list("tenant_id", flat=True),
        )
        tenant_ids.update(RoleBinding.objects.using(self.using).values_list("tenant_id", flat=True))
        switch_rows = TenantGrantSwitchOperation.objects.using(self.using).values_list(
            "expected_current_tenant_id",
            "target_tenant_id",
            "result_tenant_id",
        )
        for row in switch_rows:
            tenant_ids.update(value for value in row if value is not None)
        yield from sorted(tenant_ids, key=lambda value: value.bytes)


class DurableIamTenantInventoryProvider:
    def __init__(self, *, using: str = "default") -> None:
        self.using = using

    def iter_tenant_ids(self) -> Iterator[UUID]:
        yield from IamTenantRegistryEntry.objects.using(self.using).order_by("tenant_id").values_list(
            "tenant_id",
            flat=True,
        )
