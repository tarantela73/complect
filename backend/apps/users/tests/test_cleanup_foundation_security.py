"""NOW: DB contracts only. Synthetic attestation is not Redis executor proof."""

import hashlib
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.common.tenancy.context import tenant_rls_scope
from apps.users.models import IamTenantRegistryEntry, IdentityMergeOperation, TenantMembership
from apps.users.models_cleanup_protocol import (
    IdentityCleanupAdmission,
    IdentityCleanupLifecycleEvent,
    IdentityHoldRequest,
)
from apps.users.repositories.identity_cleanup_admission_repository import (
    CleanupAdmissionOwner,
    IdentityCleanupAdmissionRepository,
)
from apps.users.repositories.identity_hold_request_repository import IdentityHoldRequestRepository
from apps.users.security.identity_cleanup_admission import CleanupAdmissionError
from apps.users.tests.test_cleanup_admission_protocol import cleanup_fixture, synthetic_receipt
from apps.users.tests.test_hold_cleanup_concurrency import admit, request

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "changed",
    [
        {"schema_version": 2},
        {"admission_id": str(uuid4())},
        {"owner_incarnation": str(uuid4())},
        {"expected_admission_version": 2},
        {"payload_digest": "c" * 64},
        {"cleanup_id": str(uuid4())},
        {"operation_id": str(uuid4())},
        {"expected_cleanup_version": 2},
        {"expected_operation_version": 2},
        {"protocol_version": 2},
        {"build_id": "foreign-build"},
        {"resource_epoch": "foreign-resource"},
        {"emitter_state": "IDLE"},
        {"sent_count": -1},
        {"definitive_reply_count": 1},
        {"in_flight_count": 1},
        {"pending_buffer_count": 1},
        {"retry_child_count": 1},
        {"dedicated_connection_closed": False},
        {"seal_digest": ""},
        {"outcome": "RECOVERED_UNKNOWN"},
    ],
)
def test_receipt_rejects_wrong_binding_or_incomplete_structural_evidence(changed):
    cleanup, _, _ = cleanup_fixture()
    owner = admit(cleanup)
    admission = IdentityCleanupAdmission.objects.get(pk=owner.admission_id)
    with pytest.raises(DatabaseError):
        IdentityCleanupAdmissionRepository.record_terminal_receipt(
            owner=owner,
            expected_version=1,
            receipt=replace(synthetic_receipt(admission), **changed),
        )
    admission.refresh_from_db()
    assert (admission.state, admission.version) == ("OPEN", 1)
    assert IdentityCleanupLifecycleEvent.objects.filter(admission=admission).count() == 1


def test_raw_terminal_without_owner_and_receipt_is_rejected():
    cleanup, _, _ = cleanup_fixture()
    owner = admit(cleanup)
    for values in (
        {"state": "TERMINAL", "version": 2},
        {"state": "TERMINAL", "version": 2, "owner_proof": owner.capability},
    ):
        with pytest.raises(DatabaseError), transaction.atomic():
            IdentityCleanupAdmission.objects.filter(pk=owner.admission_id).update(**values)
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("DELETE FROM users_identity_cleanup_admissions WHERE id=%s", [owner.admission_id])


def test_complete_business_cas_does_not_remove_live_admission_or_prove_drain():
    from apps.users.repositories.identity_merge_cleanup_repository import IdentityMergeCleanupRepository

    cleanup, source, _ = cleanup_fixture()
    owner = admit(cleanup)
    assert IdentityMergeCleanupRepository.complete(cleanup)
    hold = request(source)
    assert hold.state == "REQUESTED"
    admission = IdentityCleanupAdmission.objects.get(pk=owner.admission_id)
    assert admission.state == "OPEN" and admission.receipt is None
    with pytest.raises(NotImplementedError):
        IdentityHoldRequestRepository.activate(request_id=hold.pk, expected_version=1)


def test_business_cas_conflict_is_separate_from_terminal_partial_receipt():
    from apps.users.repositories.identity_merge_cleanup_repository import IdentityMergeCleanupRepository

    cleanup, _, _ = cleanup_fixture()
    owner = admit(cleanup)
    admission = IdentityCleanupAdmission.objects.get(pk=owner.admission_id)
    IdentityMergeCleanupRepository.record_failure(cleanup, "COMPETING_OUTCOME")
    assert not IdentityMergeCleanupRepository.complete(cleanup)  # stale version remains stale
    assert IdentityCleanupAdmissionRepository.record_terminal_receipt(
        owner=owner,
        expected_version=1,
        receipt=synthetic_receipt(admission),
    )
    cleanup.refresh_from_db()
    admission.refresh_from_db()
    assert (cleanup.status, cleanup.version, cleanup.last_failure_code) == ("pending", 2, "COMPETING_OUTCOME")
    assert (admission.state, admission.outcome) == ("TERMINAL", "ACKED_PARTIAL")


def test_century_old_unknown_keeps_blocker_and_does_not_return_new_owner():
    cleanup, source, canonical = cleanup_fixture()
    ancient = timezone.now() - timedelta(days=36500)
    capability = "a" * 64
    admission = IdentityCleanupAdmission(
        cleanup=cleanup,
        operation=cleanup.operation,
        expected_cleanup_version=1,
        expected_operation_version=1,
        identity_ids=sorted([source.identity_id, canonical.identity_id]),
        actor_pks=[source.pk],
        tenant_ids=[],
        owner_incarnation=uuid4(),
        owner_hash=hashlib.sha256(capability.encode()).hexdigest(),
        build_id="foundation-test",
        resource_epoch="ancient-resource",
        created_at=ancient,
        updated_at=ancient,
    )
    admission.save_base(raw=True, force_insert=True)  # Preserve fixture age; all DB guards remain enabled.
    owner = CleanupAdmissionOwner(admission.pk, admission.owner_incarnation, capability)
    assert IdentityCleanupAdmissionRepository.record_unknown(owner=owner, expected_version=1)
    with pytest.raises(DatabaseError, match="CLEANUP_ADMISSION_BLOCKED"):
        admit(cleanup)
    admission.refresh_from_db()
    assert admission.created_at == ancient and admission.state == "UNKNOWN"


def test_full_connected_closure_blocks_other_operation_with_disjoint_actor():
    first, source_a, canonical = cleanup_fixture()
    now = timezone.now()
    IdentityMergeOperation.objects.filter(pk=first.operation_id).update(
        state="approved_deferred",
        version=2,
        convergence_mode="owner_confirmed",
        credential_epoch=1,
        credentials_changed_at=now,
        credentials_converged_at=now,
        updated_at=now,
    )
    second, source_b, _ = cleanup_fixture(canonical=canonical)
    owner = IdentityCleanupAdmissionRepository.admit(
        cleanup_id=first.pk,
        expected_cleanup_version=1,
        expected_operation_version=2,
        build_id="foundation-test",
        resource_epoch="isolated-test",
    )
    admission = IdentityCleanupAdmission.objects.get(pk=owner.admission_id)
    assert set(admission.identity_ids) == {source_a.identity_id, source_b.identity_id, canonical.identity_id}
    assert admission.actor_pks == [source_a.pk]
    with pytest.raises(DatabaseError, match="CLEANUP_ADMISSION_BLOCKED"):
        admit(second)


def test_repository_rejects_ambient_atomic_and_manual_autocommit():
    cleanup, _, _ = cleanup_fixture()
    with transaction.atomic(), pytest.raises(CleanupAdmissionError, match="DEDICATED_TRANSACTION_REQUIRED"):
        admit(cleanup)
    connection.set_autocommit(False)
    try:
        with pytest.raises(CleanupAdmissionError, match="DEDICATED_TRANSACTION_REQUIRED"):
            admit(cleanup)
    finally:
        connection.rollback()
        connection.set_autocommit(True)
    assert not IdentityCleanupAdmission.objects.exists()


def test_raw_request_refuses_repeatable_read():
    identity = uuid4()
    with (
        pytest.raises(DatabaseError, match="READ_COMMITTED_REQUIRED"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        IdentityHoldRequest.objects.create(
            idempotency_key=uuid4(),
            identity_ids=[identity],
            actor_pks=[],
            tenant_ids=[],
            targets=[{"kind": "IDENTITY", "id": str(identity)}],
            requester_reference="synthetic-authority",
            reason_code="TEST_HOLD",
            request_id=uuid4(),
            correlation_id=uuid4(),
        )


@contextmanager
def restricted_runtime_role():
    role = "foundation_runtime_" + uuid4().hex
    with connection.cursor() as cursor:
        cursor.execute(f'CREATE ROLE "{role}" NOSUPERUSER NOBYPASSRLS NOINHERIT')
        cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        cursor.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"')
        cursor.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
        cursor.execute(f'SET ROLE "{role}"')
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
            cursor.execute(f'DROP OWNED BY "{role}"')
            cursor.execute(f'DROP ROLE "{role}"')


def test_restricted_role_cannot_disable_guards_and_tenant_snapshot_obeys_rls():
    cleanup, source, _ = cleanup_fixture()
    tenant = uuid4()
    IamTenantRegistryEntry.objects.create(tenant_id=tenant)
    with transaction.atomic(), tenant_rls_scope(tenant):
        TenantMembership.objects.create(
            tenant_id=tenant,
            identity_id=source.identity_id,
            legacy_actor_user=source,
            display_name="Synthetic fixture",
            status="active",
        )
    with restricted_runtime_role():
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert cursor.fetchone() == (False, False)
        owner = admit(cleanup)
        admission = IdentityCleanupAdmission.objects.get(pk=owner.admission_id)
        assert admission.tenant_ids == [tenant]
        for sql in (
            "ALTER TABLE users_identity_cleanup_admissions DISABLE TRIGGER USER",
            "TRUNCATE users_identity_cleanup_admissions CASCADE",
            "SET session_replication_role='replica'",
        ):
            with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(sql)
        with pytest.raises(DatabaseError), transaction.atomic():
            IdentityCleanupAdmission.objects.filter(pk=admission.pk).update(state="TERMINAL", version=2)
        assert IdentityCleanupAdmissionRepository.record_terminal_receipt(
            owner=owner,
            expected_version=1,
            receipt=synthetic_receipt(admission),
        )
        previous = str(uuid4())
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_tenant_id',%s,true)", [previous])
            cursor.execute("SELECT users_cleanup_snapshot_tenants(%s::uuid[])", [admission.identity_ids])
            assert cursor.fetchone()[0] == [tenant]
            cursor.execute("SELECT current_setting('app.current_tenant_id')")
            assert cursor.fetchone()[0] == previous


def test_terminal_receipt_cannot_persist_raw_owner_capability_as_seal_digest():
    cleanup, _, _ = cleanup_fixture()
    owner = admit(cleanup)
    admission = IdentityCleanupAdmission.objects.get(pk=owner.admission_id)
    with pytest.raises(DatabaseError):
        IdentityCleanupAdmissionRepository.record_terminal_receipt(
            owner=owner,
            expected_version=1,
            receipt=replace(synthetic_receipt(admission), seal_digest=owner.capability),
        )
    admission.refresh_from_db()
    assert admission.state == "OPEN"
