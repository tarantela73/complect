"""Internal supported audit latch; callers supply their trusted authorization policy."""

from collections.abc import Callable, Iterable
from uuid import UUID

from django.db import DatabaseError, connections, transaction

from apps.users.repositories.identity_audit_hold_repository import IdentityAuditHoldRepository
from apps.users.security.iam_retirement_barrier import acquire_iam_retirement_shared
from apps.users.security.identity_audit_hold import AuditHoldBindingChangedError, require_read_committed
from apps.users.security.identity_merge_locking import acquire_identity_merge_advisory_locks


def latch_identity_audit_hold(
    audit_ids: Iterable[UUID],
    *,
    authorize: Callable[[tuple[UUID, ...]], None],
    using: str = "default",
) -> int:
    """Return newly held count. Authorization must raise on denial for this exact set.

    No ambient transaction is accepted. Three whole attempts, each with bounded
    SQL/lock waits; permanent integrity failures are never retried. No network
    callbacks run under locks. Already-held records produce no UPDATE.
    """
    ids = tuple(sorted(set(audit_ids), key=lambda value: value.bytes))
    if not ids or len(ids) > 1000:
        raise ValueError("AUDIT_HOLD_BATCH_SIZE")
    if connections[using].in_atomic_block or not connections[using].get_autocommit():
        raise RuntimeError("AUDIT_HOLD_DEDICATED_TRANSACTION_REQUIRED")
    authorize(ids)
    for attempt in range(3):
        try:
            with transaction.atomic(using=using, durable=True):
                require_read_committed(using=using)
                with connections[using].cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '2s'")
                    cursor.execute("SET LOCAL statement_timeout = '5s'")
                acquire_iam_retirement_shared(using=using)
                records, sessions, identities = IdentityAuditHoldRepository.discover(ids, using=using)
                acquire_identity_merge_advisory_locks(user_pks=(), identity_ids=identities, using=using)
                return IdentityAuditHoldRepository.latch(records, sessions, using=using)
        except AuditHoldBindingChangedError:
            if attempt == 2:
                raise
        except DatabaseError as exc:
            state = getattr(exc.__cause__, "sqlstate", None) or getattr(exc.__cause__, "pgcode", None)
            if state != "55P03" or attempt == 2:
                raise
    raise AssertionError("unreachable")
