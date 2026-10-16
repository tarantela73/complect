from uuid import UUID

from django.db import connections

from apps.users.models import IamTenantRegistryEntry


class IamTenantRegistryRepository:
    @staticmethod
    def ensure_registered(
        tenant_id: UUID,
        source_code: str,
        *,
        using: str = "default",
        apps_registry=None,
    ) -> IamTenantRegistryEntry:
        if not source_code or len(source_code) > 32:
            raise ValueError("source_code must contain 1..32 characters")
        with connections[using].cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users_iam_tenant_registry (tenant_id, registered_at, version, source_code)
                VALUES (%s, CURRENT_TIMESTAMP, 1, %s)
                ON CONFLICT (tenant_id) DO NOTHING
                """,
                [tenant_id, source_code],
            )
        model = IamTenantRegistryEntry if apps_registry is None else apps_registry.get_model(
            "users", "IamTenantRegistryEntry"
        )
        return model.objects.using(using).select_for_update().get(tenant_id=tenant_id)
