"""Dedicated DB phases only; not an executor or an authority guard."""

from contextlib import contextmanager
from dataclasses import dataclass

from django.db import connections, transaction

from apps.users.security.iam_retirement_barrier import acquire_iam_retirement_shared
from apps.users.security.identity_audit_hold import require_read_committed

IAM_CLEANUP_HOLD_CATALOG_FENCE = 6507031379205739152


class CleanupAdmissionError(ValueError):
    """Fail-closed foundation contract violation."""


@contextmanager
def cleanup_catalog_phase(*, using: str = "default"):
    connection = connections[using]
    if connection.in_atomic_block or not connection.get_autocommit():
        raise CleanupAdmissionError("DEDICATED_TRANSACTION_REQUIRED")
    with transaction.atomic(using=using, durable=True):
        require_read_committed(using=using)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '2s'")
            cursor.execute("SET LOCAL statement_timeout = '5s'")
        acquire_iam_retirement_shared(using=using)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [IAM_CLEANUP_HOLD_CATALOG_FENCE])
        yield


@dataclass(frozen=True, slots=True)
class CleanupTerminalReceipt:
    """Structural attestation from a future CLOSED sole-owner emitter.

    Constructing this value is not evidence that any Redis work actually drained.
    No runtime ingestion caller exists in the foundation release.
    """

    schema_version: int
    admission_id: str
    owner_incarnation: str
    expected_admission_version: int
    payload_digest: str
    cleanup_id: str
    operation_id: str
    expected_cleanup_version: int
    expected_operation_version: int
    protocol_version: int
    build_id: str
    resource_epoch: str
    emitter_state: str
    sent_count: int
    definitive_reply_count: int
    in_flight_count: int
    pending_buffer_count: int
    retry_child_count: int
    dedicated_connection_closed: bool
    seal_digest: str
    outcome: str
