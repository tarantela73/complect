from django.db import connection, transaction

from apps.common.context.core import (
    get_tenant_id,
    is_primary_db_enforced,
)
from apps.common.infra.redis import get_default_redis


class EnterpriseClusterRouter:
    def _inject_rls(self) -> None:
        try:
            tenant_id = get_tenant_id()
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL app.current_tenant_id = %s;", [str(tenant_id)])
        except Exception:
            pass

    def db_for_read(self, model, **hints):
        self._inject_rls()

        if hints.get("force_primary") or is_primary_db_enforced():
            return "default"

        try:
            if get_default_redis().get("infra:postgres_replica:status") == "DEGRADED":
                return "default"
        except Exception:
            return "default"

        if transaction.get_connection("default").in_atomic_block:
            return "default"

        return "replica"

    def db_for_write(self, model, **hints):
        self._inject_rls()
        return "default"

    def allow_relation(self, obj1, obj2, **hints):
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db == "default"
