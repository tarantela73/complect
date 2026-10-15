"""Transaction preconditions shared by audit latches and merge graph commands."""

from django.db import connections


class AuditHoldBindingError(ValueError):
    """Permanent unresolved/inconsistent evidence; requires reconciliation."""


class AuditHoldBindingChangedError(RuntimeError):
    """Discard the complete transaction and rediscover; never extend a lock set."""


def require_read_committed(*, using: str = "default") -> None:
    connection = connections[using]
    if not connection.in_atomic_block:
        raise RuntimeError("IAM_HOLD_TRANSACTION_REQUIRED")
    with connection.cursor() as cursor:
        cursor.execute("SHOW transaction_isolation")
        if cursor.fetchone()[0] != "read committed":
            raise RuntimeError("IAM_HOLD_READ_COMMITTED_REQUIRED")
