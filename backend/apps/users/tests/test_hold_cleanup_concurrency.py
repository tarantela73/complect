"""Real independent PostgreSQL connections and explicit interleavings; no Redis."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import pytest
from django.db import DatabaseError, connections, transaction

from apps.users.models import User
from apps.users.models_cleanup_protocol import IdentityCleanupAdmission, IdentityCleanupLifecycleEvent
from apps.users.repositories.identity_cleanup_admission_repository import IdentityCleanupAdmissionRepository
from apps.users.repositories.identity_hold_request_repository import IdentityHoldRequestRepository
from apps.users.tests.test_cleanup_admission_protocol import cleanup_fixture, synthetic_receipt

pytestmark = pytest.mark.django_db(transaction=True)


def admit(cleanup):
    return IdentityCleanupAdmissionRepository.admit(
        cleanup_id=cleanup.pk,
        expected_cleanup_version=1,
        expected_operation_version=1,
        build_id="foundation-test",
        resource_epoch="isolated-test",
    )


def request(source):
    return IdentityHoldRequestRepository.request(
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


def threaded(action, wrapper=None):
    db = connections["default"]
    try:
        if wrapper is not None:
            with db.execute_wrapper(wrapper):
                return action()
        return action()
    except DatabaseError as error:
        return str(error).splitlines()[0]
    finally:
        db.close()


@pytest.mark.parametrize("request_first", [False, True])
def test_admission_and_request_serialize_in_both_commit_orders(request_first):
    cleanup, source, _ = cleanup_fixture()
    first_inserted, second_attempted, release_first = Event(), Event(), Event()
    first_table = "users_identity_hold_requests" if request_first else "users_identity_cleanup_admissions"
    pids = []

    def pause_first(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if sql.startswith("INSERT INTO") and first_table in sql:
            with context["connection"].cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pids.append(cursor.fetchone()[0])
            first_inserted.set()
            assert release_first.wait(5)
        return result

    def observe_second(execute, sql, params, many, context):
        if sql == "SELECT pg_advisory_xact_lock(%s)":
            with context["connection"].cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                pids.append(cursor.fetchone()[0])
            second_attempted.set()
        return execute(sql, params, many, context)

    first_action = (lambda: request(source)) if request_first else (lambda: admit(cleanup))
    second_action = (lambda: admit(cleanup)) if request_first else (lambda: request(source))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(threaded, first_action, pause_first)
        try:
            assert first_inserted.wait(5)
            second = pool.submit(threaded, second_action, observe_second)
            assert second_attempted.wait(5)
        finally:
            release_first.set()
        first_result, second_result = first.result(timeout=8), second.result(timeout=8)
    assert len(set(pids)) == 2
    if request_first:
        assert first_result.state == "REQUESTED"
        assert "HOLD_REQUESTED_OR_RESUME_REQUIRED" in second_result
        assert not IdentityCleanupAdmission.objects.exists()
    else:
        assert first_result.admission_id
        assert second_result.state == "REQUESTED"
        assert IdentityCleanupAdmission.objects.get(pk=first_result.admission_id).state == "OPEN"


def test_duplicate_delivery_contenders_have_only_one_owner():
    cleanup, _, _ = cleanup_fixture()
    start = Barrier(2)

    def contender():
        start.wait(timeout=5)
        return admit(cleanup)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(threaded, contender) for _ in range(2)]
        results = [future.result(timeout=8) for future in futures]
    assert sum(not isinstance(result, str) for result in results) == 1
    assert any(isinstance(result, str) and "CLEANUP_ADMISSION_BLOCKED" in result for result in results)
    assert IdentityCleanupAdmission.objects.count() == 1


def test_same_owner_stale_cas_cannot_replace_competing_terminal_or_unknown():
    cleanup, _, _ = cleanup_fixture()
    owner = admit(cleanup)
    receipt = synthetic_receipt(IdentityCleanupAdmission.objects.get(pk=owner.admission_id))
    start = Barrier(2)

    def terminal():
        start.wait(timeout=5)
        return IdentityCleanupAdmissionRepository.record_terminal_receipt(
            owner=owner, expected_version=1, receipt=receipt
        )

    def unknown():
        start.wait(timeout=5)
        return IdentityCleanupAdmissionRepository.record_unknown(owner=owner, expected_version=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(threaded, terminal)
        b = pool.submit(threaded, unknown)
        outcomes = a.result(timeout=8), b.result(timeout=8)
    assert sorted(outcomes) == [False, True]
    admission = IdentityCleanupAdmission.objects.get(pk=owner.admission_id)
    assert admission.version == 2
    assert admission.state == ("TERMINAL" if outcomes[0] else "UNKNOWN")
    assert IdentityCleanupLifecycleEvent.objects.filter(admission=admission).count() == 2


def test_disjoint_actors_in_same_tenant_admit_but_tenant_request_blocks_both():
    tenant = uuid4()
    first, source_a, canonical_a = cleanup_fixture()
    second, source_b, canonical_b = cleanup_fixture()
    User.objects.filter(pk__in=[source_a.pk, canonical_a.pk, source_b.pk, canonical_b.pk]).update(tenant_id=tenant)
    start = Barrier(2)

    def contender(cleanup):
        start.wait(timeout=5)
        return admit(cleanup)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(threaded, lambda item=item: contender(item)) for item in (first, second)]
        owners = [future.result(timeout=8) for future in futures]
    assert all(not isinstance(owner, str) for owner in owners)
    hold = IdentityHoldRequestRepository.request(
        idempotency_key=uuid4(),
        identity_ids=(),
        actor_pks=(),
        tenant_ids=(tenant,),
        targets=({"kind": "TENANT", "id": str(tenant)},),
        requester_reference="synthetic-authority",
        reason_code="TEST_HOLD",
        request_id=uuid4(),
        correlation_id=uuid4(),
    )
    assert hold.state == "REQUESTED"
    assert IdentityCleanupAdmission.objects.filter(tenant_ids__contains=[tenant], state="OPEN").count() == 2
    for owner in owners:
        admission = IdentityCleanupAdmission.objects.get(pk=owner.admission_id)
        assert IdentityCleanupAdmissionRepository.record_terminal_receipt(
            owner=owner,
            expected_version=1,
            receipt=synthetic_receipt(admission),
        )
    with pytest.raises(DatabaseError, match="HOLD_REQUESTED_OR_RESUME_REQUIRED"):
        admit(first)
    with pytest.raises(DatabaseError, match="HOLD_REQUESTED_OR_RESUME_REQUIRED"):
        admit(second)


def test_raw_writer_fails_without_reverse_lock_wait():
    cleanup, source, _ = cleanup_fixture()
    held, release = Event(), Event()

    def hold_catalog():
        with transaction.atomic(), connections["default"].cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [6507031379205739152])
            held.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(threaded, hold_catalog)
        try:
            assert held.wait(5)
            from apps.users.models_cleanup_protocol import IdentityHoldRequest

            with pytest.raises(DatabaseError, match="CLEANUP_CATALOG_BUSY"), transaction.atomic():
                IdentityHoldRequest.objects.create(
                    idempotency_key=uuid4(),
                    identity_ids=[source.identity_id],
                    actor_pks=[],
                    tenant_ids=[],
                    targets=[{"kind": "IDENTITY", "id": str(source.identity_id)}],
                    requester_reference="synthetic-authority",
                    reason_code="TEST_HOLD",
                    request_id=uuid4(),
                    correlation_id=uuid4(),
                    payload_digest="a" * 64,
                )
        finally:
            release.set()
        assert holder.result(timeout=8) is None


@pytest.mark.parametrize("fence_kind", ["bootstrap", "identity"])
def test_raw_admission_respects_existing_graph_fences_without_reverse_wait(fence_kind):
    import hashlib

    from apps.users.security.identity_merge_locking import identity_advisory_key

    cleanup, source, canonical = cleanup_fixture()
    key = identity_advisory_key(source.identity_id)
    if fence_kind == "bootstrap":
        key = int.from_bytes(hashlib.sha256(f"iam-user:{source.pk}".encode()).digest()[:8], "big", signed=True)
    held, release = Event(), Event()

    def hold_fence():
        with transaction.atomic(), connections["default"].cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])
            held.set()
            assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(threaded, hold_fence)
        try:
            assert held.wait(5)
            with pytest.raises(DatabaseError, match="CLEANUP_GRAPH_FENCE_BUSY"), transaction.atomic():
                IdentityCleanupAdmission.objects.create(
                    cleanup=cleanup,
                    operation=cleanup.operation,
                    expected_cleanup_version=1,
                    expected_operation_version=1,
                    identity_ids=sorted([source.identity_id, canonical.identity_id]),
                    actor_pks=[source.pk],
                    tenant_ids=[],
                    owner_incarnation=uuid4(),
                    owner_hash="a" * 64,
                    build_id="foundation-test",
                    resource_epoch="isolated-test",
                )
        finally:
            release.set()
        assert holder.result(timeout=8) is None


def test_raw_admission_old_statement_snapshot_sees_newly_committed_request():
    from time import monotonic

    db = connections["default"]
    cleanup, source, canonical = cleanup_fixture()
    pause_key = 6507031379205739153
    ready, pid = Event(), []
    with db.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s)", [pause_key])
        cursor.execute(f"""CREATE FUNCTION public.foundation_snapshot_pause() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN PERFORM pg_advisory_xact_lock({pause_key}); RETURN NEW; END; $$""")
        cursor.execute(
            "CREATE TRIGGER trg_00_foundation_snapshot_pause BEFORE INSERT "
            "ON users_identity_cleanup_admissions FOR EACH ROW "
            "EXECUTE FUNCTION public.foundation_snapshot_pause()"
        )

    def raw_insert():
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            pid.append(cursor.fetchone()[0])
        ready.set()
        return IdentityCleanupAdmission.objects.create(
            cleanup=cleanup,
            operation=cleanup.operation,
            expected_cleanup_version=1,
            expected_operation_version=1,
            identity_ids=sorted([source.identity_id, canonical.identity_id]),
            actor_pks=[source.pk],
            tenant_ids=[],
            owner_incarnation=uuid4(),
            owner_hash="a" * 64,
            build_id="foundation-test",
            resource_epoch="isolated-test",
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(threaded, raw_insert)
            try:
                assert ready.wait(5)
                deadline = monotonic() + 5
                while True:
                    with db.cursor() as cursor:
                        cursor.execute("SELECT cardinality(pg_blocking_pids(%s)) > 0", [pid[0]])
                        blocked = cursor.fetchone()[0]
                    if blocked:
                        break
                    assert monotonic() < deadline, "raw INSERT never reached the database barrier"
                hold = request(source)
                assert hold.state == "REQUESTED"
            finally:
                with db.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [pause_key])
            assert "HOLD_REQUESTED_OR_RESUME_REQUIRED" in future.result(timeout=8)
        assert not IdentityCleanupAdmission.objects.exists()
    finally:
        with db.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [pause_key])
            cursor.execute("DROP TRIGGER trg_00_foundation_snapshot_pause ON users_identity_cleanup_admissions")
            cursor.execute("DROP FUNCTION public.foundation_snapshot_pause()")
