"""DB foundation contracts; synthetic receipts are not executor acceptance."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, transaction
from django.utils import timezone

from apps.users.models import IdentityMergeOperation, IdentityMergeRedisCleanupRecord, User

pytestmark = pytest.mark.django_db(transaction=True)


def cleanup_fixture(*, canonical=None):
    source = User.objects.create(username=uuid4().hex, identity_id=uuid4(), identity_legal_hold=False)
    if canonical is None:
        canonical = User.objects.create(username=uuid4().hex, identity_id=uuid4(), identity_legal_hold=False)
    now = timezone.now()
    operation = IdentityMergeOperation.objects.create(
        source_user=source,
        source_identity_id=source.identity_id,
        canonical_identity_id=canonical.identity_id,
        state="approved_pending_convergence",
        approval_reference="foundation-test",
        approval_digest="a" * 64,
        approval_key_id="fixture",
        approval_signature=b"fixture",
        approved_at=now,
        approval_expires_at=now + timedelta(days=1),
        synthetic_normalized_email=f"{uuid4().hex}@example.invalid",
        retention_until=now + timedelta(days=1),
    )
    return (
        IdentityMergeRedisCleanupRecord.objects.create(operation=operation, actor_user_pks=[source.pk]),
        source,
        canonical,
    )


def test_admission_scope_is_immutable_even_through_raw_update():
    from apps.users.models_cleanup_protocol import IdentityCleanupAdmission
    from apps.users.repositories.identity_cleanup_admission_repository import IdentityCleanupAdmissionRepository

    cleanup, source, canonical = cleanup_fixture()
    admitted = IdentityCleanupAdmissionRepository.admit(
        cleanup_id=cleanup.pk,
        expected_cleanup_version=cleanup.version,
        expected_operation_version=cleanup.operation.version,
        build_id="foundation-test",
        resource_epoch="isolated-test",
    )
    admission = IdentityCleanupAdmission.objects.get(pk=admitted.admission_id)
    assert admission.actor_pks == [source.pk]
    assert set(admission.identity_ids) == {source.identity_id, canonical.identity_id}
    assert admission.operation_id == cleanup.operation_id
    with pytest.raises(DatabaseError), transaction.atomic():
        IdentityCleanupAdmission.objects.filter(pk=admission.pk).update(actor_pks=[canonical.pk])
    admission.refresh_from_db()
    assert admission.actor_pks == [source.pk]


def test_duplicate_delivery_never_receives_another_owner():
    from apps.users.models_cleanup_protocol import IdentityCleanupAdmission
    from apps.users.repositories.identity_cleanup_admission_repository import IdentityCleanupAdmissionRepository

    cleanup, _, _ = cleanup_fixture()
    args = dict(
        cleanup_id=cleanup.pk,
        expected_cleanup_version=1,
        expected_operation_version=1,
        build_id="foundation-test",
        resource_epoch="isolated-test",
    )
    first = IdentityCleanupAdmissionRepository.admit(**args)
    with pytest.raises(DatabaseError, match="CLEANUP_ADMISSION_BLOCKED"):
        IdentityCleanupAdmissionRepository.admit(**args)
    assert IdentityCleanupAdmission.objects.filter(cleanup=cleanup).count() == 1
    assert first.capability not in repr(first)


def test_requested_before_admission_closes_scope_without_claiming_active():
    from apps.users.repositories.identity_cleanup_admission_repository import IdentityCleanupAdmissionRepository
    from apps.users.repositories.identity_hold_request_repository import IdentityHoldRequestRepository

    cleanup, source, _ = cleanup_fixture()
    request = IdentityHoldRequestRepository.request(
        idempotency_key=uuid4(),
        identity_ids=(source.identity_id,),
        actor_pks=(),
        tenant_ids=(),
        targets=({"kind": "IDENTITY", "id": str(source.identity_id)},),
        requester_reference="synthetic-authority",
        reason_code="TEST_HOLD",
        request_id=uuid4(),
        correlation_id=uuid4(),
    )
    assert request.state == "REQUESTED"
    with pytest.raises(NotImplementedError, match="HOLD_ACTIVATION_NOT_IMPLEMENTED"):
        IdentityHoldRequestRepository.activate(request_id=request.pk, expected_version=1)
    with pytest.raises(DatabaseError, match="HOLD_REQUESTED_OR_RESUME_REQUIRED"):
        IdentityCleanupAdmissionRepository.admit(
            cleanup_id=cleanup.pk,
            expected_cleanup_version=1,
            expected_operation_version=1,
            build_id="foundation-test",
            resource_epoch="isolated-test",
        )


def synthetic_receipt(admission, *, outcome="ACKED_PARTIAL"):
    """An attestation fixture only: this test does not execute or certify Redis."""
    from apps.users.security.identity_cleanup_admission import CleanupTerminalReceipt

    return CleanupTerminalReceipt(
        schema_version=1,
        admission_id=str(admission.pk),
        owner_incarnation=str(admission.owner_incarnation),
        expected_admission_version=admission.version,
        payload_digest=admission.payload_digest,
        cleanup_id=str(admission.cleanup_id),
        operation_id=str(admission.operation_id),
        expected_cleanup_version=admission.expected_cleanup_version,
        expected_operation_version=admission.expected_operation_version,
        protocol_version=admission.protocol_version,
        build_id=admission.build_id,
        resource_epoch=admission.resource_epoch,
        emitter_state="CLOSED",
        sent_count=2,
        definitive_reply_count=2,
        in_flight_count=0,
        pending_buffer_count=0,
        retry_child_count=0,
        dedicated_connection_closed=True,
        seal_digest="b" * 64,
        outcome=outcome,
    )


def test_existing_owner_can_acknowledge_lifecycle_under_requested_without_business_mutation():
    from apps.users.models_cleanup_protocol import IdentityCleanupAdmission, IdentityCleanupLifecycleEvent
    from apps.users.repositories.identity_cleanup_admission_repository import IdentityCleanupAdmissionRepository
    from apps.users.repositories.identity_hold_request_repository import IdentityHoldRequestRepository

    cleanup, source, _ = cleanup_fixture()
    owner = IdentityCleanupAdmissionRepository.admit(
        cleanup_id=cleanup.pk,
        expected_cleanup_version=1,
        expected_operation_version=1,
        build_id="foundation-test",
        resource_epoch="isolated-test",
    )
    admission = IdentityCleanupAdmission.objects.get(pk=owner.admission_id)
    request = IdentityHoldRequestRepository.request(
        idempotency_key=uuid4(),
        identity_ids=(source.identity_id,),
        actor_pks=(),
        tenant_ids=(),
        targets=({"kind": "IDENTITY", "id": str(source.identity_id)},),
        requester_reference="synthetic-authority",
        reason_code="TEST_HOLD",
        request_id=uuid4(),
        correlation_id=uuid4(),
    )
    assert IdentityCleanupAdmissionRepository.record_terminal_receipt(
        owner=owner,
        expected_version=1,
        receipt=synthetic_receipt(admission),
    )
    admission.refresh_from_db()
    cleanup.refresh_from_db()
    request.refresh_from_db()
    assert (admission.state, admission.version, admission.outcome) == ("TERMINAL", 2, "ACKED_PARTIAL")
    assert admission.owner_proof is None
    assert (cleanup.status, cleanup.version, cleanup.attempt_count) == ("pending", 1, 0)
    assert request.state == "REQUESTED"
    assert list(
        IdentityCleanupLifecycleEvent.objects.filter(admission=admission)
        .order_by("version")
        .values_list("kind", flat=True)
    ) == ["ADMITTED", "TERMINAL"]


def test_unknown_is_irreversible_and_owner_version_cas_preserves_competing_result():
    from dataclasses import replace

    from apps.users.models_cleanup_protocol import IdentityCleanupAdmission, IdentityCleanupLifecycleEvent
    from apps.users.repositories.identity_cleanup_admission_repository import IdentityCleanupAdmissionRepository

    cleanup, _, _ = cleanup_fixture()
    owner = IdentityCleanupAdmissionRepository.admit(
        cleanup_id=cleanup.pk,
        expected_cleanup_version=1,
        expected_operation_version=1,
        build_id="foundation-test",
        resource_epoch="isolated-test",
    )
    admission = IdentityCleanupAdmission.objects.get(pk=owner.admission_id)
    receipt = synthetic_receipt(admission)
    assert not IdentityCleanupAdmissionRepository.record_unknown(
        owner=replace(owner, owner_incarnation=uuid4()),
        expected_version=1,
    )
    with pytest.raises(DatabaseError, match="OWNER_PROOF_REQUIRED"):
        IdentityCleanupAdmissionRepository.record_unknown(owner=replace(owner, capability="0" * 64), expected_version=1)
    assert IdentityCleanupAdmissionRepository.record_unknown(owner=owner, expected_version=1)
    assert not IdentityCleanupAdmissionRepository.record_terminal_receipt(
        owner=owner, expected_version=1, receipt=receipt
    )
    assert not IdentityCleanupAdmissionRepository.record_terminal_receipt(
        owner=owner, expected_version=2, receipt=receipt
    )
    with pytest.raises(DatabaseError), transaction.atomic():
        IdentityCleanupAdmission.objects.filter(pk=admission.pk).update(
            state="TERMINAL", version=3, owner_proof=owner.capability
        )
    admission.refresh_from_db()
    assert (admission.state, admission.version) == ("UNKNOWN", 2)
    assert list(
        IdentityCleanupLifecycleEvent.objects.filter(admission=admission)
        .order_by("version")
        .values_list("kind", flat=True)
    ) == ["ADMITTED", "UNKNOWN"]


def test_subjects_are_exact_immutable_db_projections_and_events_are_append_only():
    from apps.users.models_cleanup_protocol import (
        IdentityCleanupAdmissionSubject,
        IdentityCleanupLifecycleEvent,
        IdentityHoldLifecycleEvent,
        IdentityHoldRequestSubject,
    )
    from apps.users.repositories.identity_cleanup_admission_repository import IdentityCleanupAdmissionRepository
    from apps.users.repositories.identity_hold_request_repository import IdentityHoldRequestRepository

    cleanup, source, canonical = cleanup_fixture()
    owner = IdentityCleanupAdmissionRepository.admit(
        cleanup_id=cleanup.pk,
        expected_cleanup_version=1,
        expected_operation_version=1,
        build_id="foundation-test",
        resource_epoch="isolated-test",
    )
    expected = {
        ("IDENTITY", str(source.identity_id)),
        ("IDENTITY", str(canonical.identity_id)),
        ("ACTOR", str(source.pk)),
    }
    subjects = IdentityCleanupAdmissionSubject.objects.filter(admission_id=owner.admission_id)
    assert set(subjects.values_list("kind", "value")) == expected
    with pytest.raises(DatabaseError), transaction.atomic():
        subjects.update(value=str(uuid4()))
    with pytest.raises(DatabaseError), transaction.atomic():
        subjects.delete()
    with pytest.raises(DatabaseError), transaction.atomic():
        IdentityCleanupAdmissionSubject.objects.create(
            admission_id=owner.admission_id, kind="IDENTITY", value=str(uuid4())
        )
    with pytest.raises(DatabaseError), transaction.atomic():
        IdentityCleanupLifecycleEvent.objects.filter(admission_id=owner.admission_id).update(kind="TERMINAL")
    request = IdentityHoldRequestRepository.request(
        idempotency_key=uuid4(),
        identity_ids=(source.identity_id,),
        actor_pks=(),
        tenant_ids=(),
        targets=({"kind": "IDENTITY", "id": str(source.identity_id)},),
        requester_reference="synthetic-authority",
        reason_code="TEST_HOLD",
        request_id=uuid4(),
        correlation_id=uuid4(),
    )
    assert set(IdentityHoldRequestSubject.objects.filter(request=request).values_list("kind", "value")) == {
        ("IDENTITY", str(source.identity_id))
    }
    assert list(IdentityHoldLifecycleEvent.objects.filter(request=request).values_list("kind", flat=True)) == [
        "REQUESTED"
    ]


def test_raw_admission_cannot_substitute_operation_actor_or_identity_snapshot():
    import hashlib

    from apps.users.models_cleanup_protocol import IdentityCleanupAdmission

    cleanup, source, canonical = cleanup_fixture()
    other, _, _ = cleanup_fixture()
    data = dict(
        cleanup=cleanup,
        operation=cleanup.operation,
        expected_cleanup_version=1,
        expected_operation_version=1,
        identity_ids=sorted([source.identity_id, canonical.identity_id]),
        actor_pks=[source.pk],
        tenant_ids=[],
        owner_incarnation=uuid4(),
        owner_hash=hashlib.sha256(b"a" * 64).hexdigest(),
        build_id="foundation-test",
        resource_epoch="isolated-test",
        payload_digest="",
    )
    for changed in (
        {"operation": other.operation},
        {"identity_ids": [uuid4()]},
        {"actor_pks": [canonical.pk]},
        {"expected_cleanup_version": 2},
        {"expected_operation_version": 2},
        {"identity_ids": []},
        {"actor_pks": []},
        {"identity_ids": [None]},
        {"owner_hash": "short"},
    ):
        with pytest.raises(DatabaseError), transaction.atomic():
            IdentityCleanupAdmission.objects.create(**(data | changed))
    assert not IdentityCleanupAdmission.objects.exists()


def test_released_request_cannot_reactivate_or_resume_cleanup():
    from apps.users.models_cleanup_protocol import IdentityHoldLifecycleEvent, IdentityHoldRequest
    from apps.users.repositories.identity_cleanup_admission_repository import IdentityCleanupAdmissionRepository
    from apps.users.repositories.identity_hold_request_repository import IdentityHoldRequestRepository

    cleanup, source, _ = cleanup_fixture()
    request = IdentityHoldRequestRepository.request(
        idempotency_key=uuid4(),
        identity_ids=(source.identity_id,),
        actor_pks=(),
        tenant_ids=(),
        targets=({"kind": "IDENTITY", "id": str(source.identity_id)},),
        requester_reference="synthetic-authority",
        reason_code="TEST_HOLD",
        request_id=uuid4(),
        correlation_id=uuid4(),
    )
    assert IdentityHoldRequestRepository.release_requested(
        request_id=request.pk,
        expected_version=1,
        decision_reference="synthetic-release-decision",
        actor_reference="synthetic-authority",
    )
    assert not IdentityHoldRequestRepository.release_requested(
        request_id=request.pk,
        expected_version=1,
        decision_reference="stale",
        actor_reference="synthetic-authority",
    )
    request.refresh_from_db()
    assert (request.state, request.version) == ("RELEASED", 2)
    with pytest.raises(NotImplementedError):
        IdentityHoldRequestRepository.activate(request_id=request.pk, expected_version=2)
    with pytest.raises(DatabaseError), transaction.atomic():
        IdentityHoldRequest.objects.filter(pk=request.pk).update(state="ACTIVE", version=3)
    with pytest.raises(DatabaseError, match="HOLD_REQUESTED_OR_RESUME_REQUIRED"):
        IdentityCleanupAdmissionRepository.admit(
            cleanup_id=cleanup.pk,
            expected_cleanup_version=1,
            expected_operation_version=1,
            build_id="foundation-test",
            resource_epoch="isolated-test",
        )
    assert list(
        IdentityHoldLifecycleEvent.objects.filter(request=request).order_by("version").values_list("kind", flat=True)
    ) == ["REQUESTED", "RELEASED"]


def test_lost_ack_response_readback_is_idempotent_without_new_lifecycle_or_owner():
    from dataclasses import replace

    from apps.users.models_cleanup_protocol import IdentityCleanupAdmission, IdentityCleanupLifecycleEvent
    from apps.users.repositories.identity_cleanup_admission_repository import IdentityCleanupAdmissionRepository

    cleanup, _, _ = cleanup_fixture()
    owner = IdentityCleanupAdmissionRepository.admit(
        cleanup_id=cleanup.pk,
        expected_cleanup_version=1,
        expected_operation_version=1,
        build_id="foundation-test",
        resource_epoch="isolated-test",
    )
    admission = IdentityCleanupAdmission.objects.get(pk=owner.admission_id)
    receipt = synthetic_receipt(admission)
    assert IdentityCleanupAdmissionRepository.record_terminal_receipt(owner=owner, expected_version=1, receipt=receipt)
    assert IdentityCleanupAdmissionRepository.record_terminal_receipt(owner=owner, expected_version=1, receipt=receipt)
    assert not IdentityCleanupAdmissionRepository.record_terminal_receipt(
        owner=owner,
        expected_version=1,
        receipt=replace(receipt, seal_digest="c" * 64),
    )
    assert not IdentityCleanupAdmissionRepository.record_terminal_receipt(
        owner=replace(owner, capability="0" * 64),
        expected_version=1,
        receipt=receipt,
    )
    assert IdentityCleanupLifecycleEvent.objects.filter(admission=admission).count() == 2


@pytest.mark.parametrize(
    "changed",
    [
        {"targets": [{"kind": "IDENTITY", "id": "not-an-id"}]},
        {"targets": [{"kind": "IDENTITY", "id": str(uuid4()), "raw_email": "forbidden"}]},
        {"targets": []},
        {"identity_ids": [None]},
        {"actor_pks": [-1]},
        {"reason_code": ""},
        {"requester_reference": ""},
        {"payload_digest": "short"},
    ],
)
def test_raw_request_rejects_malformed_scope_and_non_reference_payload(changed):
    from apps.users.models_cleanup_protocol import IdentityHoldRequest

    identity = uuid4()
    data = dict(
        idempotency_key=uuid4(),
        identity_ids=[identity],
        actor_pks=[],
        tenant_ids=[],
        targets=[{"kind": "IDENTITY", "id": str(identity)}],
        requester_reference="synthetic-authority",
        reason_code="TEST_HOLD",
        request_id=uuid4(),
        correlation_id=uuid4(),
        payload_digest="",
    )
    with pytest.raises(DatabaseError), transaction.atomic():
        IdentityHoldRequest.objects.create(**(data | changed))


def test_nested_trigger_cannot_forge_lifecycle_evidence():
    from django.db import connection

    from apps.users.models_cleanup_protocol import IdentityCleanupLifecycleEvent
    from apps.users.repositories.identity_cleanup_admission_repository import IdentityCleanupAdmissionRepository

    cleanup, _, _ = cleanup_fixture()
    owner = IdentityCleanupAdmissionRepository.admit(
        cleanup_id=cleanup.pk,
        expected_cleanup_version=1,
        expected_operation_version=1,
        build_id="foundation-test",
        resource_epoch="isolated-test",
    )
    # Trigger depth is not authority: a caller can have TEMP privileges.
    with connection.cursor() as cursor:
        cursor.execute("CREATE TEMP TABLE foundation_injection (admission_id uuid)")
        cursor.execute("""CREATE FUNCTION pg_temp.foundation_inject() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            INSERT INTO public.users_identity_cleanup_lifecycle(id,admission_id,version,kind,evidence,created_at)
            VALUES (gen_random_uuid(),NEW.admission_id,77,'TERMINAL','{}'::jsonb,now());
            RETURN NEW;
        END; $$""")
        cursor.execute(
            "CREATE TRIGGER inject AFTER INSERT ON foundation_injection FOR EACH ROW "
            "EXECUTE FUNCTION pg_temp.foundation_inject()"
        )
    try:
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("INSERT INTO foundation_injection VALUES (%s)", [owner.admission_id])
        assert IdentityCleanupLifecycleEvent.objects.filter(admission_id=owner.admission_id).count() == 1
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE foundation_injection")
            cursor.execute("DROP FUNCTION pg_temp.foundation_inject()")


def test_raw_request_cannot_supply_digest_for_different_payload():
    import hashlib
    import json

    from apps.users.models_cleanup_protocol import IdentityHoldRequest

    identity = uuid4()
    payload = dict(
        identity_ids=[identity],
        actor_pks=[],
        tenant_ids=[],
        targets=[{"kind": "IDENTITY", "id": str(identity)}],
        requester_reference="synthetic-authority",
        reason_code="TEST_HOLD",
        request_id=uuid4(),
        correlation_id=uuid4(),
    )
    other = uuid4()
    forged = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    with pytest.raises(DatabaseError), transaction.atomic():
        IdentityHoldRequest.objects.create(
            idempotency_key=uuid4(),
            **(payload | {"identity_ids": [other], "targets": [{"kind": "IDENTITY", "id": str(other)}]}),
            payload_digest=forged,
        )


def test_request_idempotency_binds_complete_payload_and_returns_stored_digest():
    from apps.users.repositories.identity_hold_request_repository import IdentityHoldRequestRepository
    from apps.users.security.identity_cleanup_admission import CleanupAdmissionError

    identity = uuid4()
    args = dict(
        idempotency_key=uuid4(),
        identity_ids=(identity,),
        actor_pks=(),
        tenant_ids=(),
        targets=({"kind": "IDENTITY", "id": str(identity)},),
        requester_reference="synthetic-authority",
        reason_code="TEST_HOLD",
        request_id=uuid4(),
        correlation_id=uuid4(),
    )
    first = IdentityHoldRequestRepository.request(**args)
    assert len(first.payload_digest) == 64
    second = IdentityHoldRequestRepository.request(**args)
    assert first.pk == second.pk and first.payload_digest == second.payload_digest
    with pytest.raises(CleanupAdmissionError, match="HOLD_IDEMPOTENCY_PAYLOAD_CONFLICT"):
        IdentityHoldRequestRepository.request(**(args | {"reason_code": "ANOTHER_REASON"}))
