import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import pyotp
import pytest
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, connection, connections, transaction
from django.utils import timezone

from apps.users.models import IdentityMergeOperation, IdentitySecurityAuditRecord, IdentitySession, User
from apps.users.security.iam_retirement_barrier import IAM_RETIREMENT_BARRIER_KEY
from apps.users.security.identity_audit_hold import AuditHoldBindingError
from apps.users.security.identity_merge_locking import (
    IdentityMergeLockCoordinator,
    acquire_identity_merge_advisory_locks,
    identity_advisory_key,
)
from apps.users.services.identity_audit_hold_service import latch_identity_audit_hold
from apps.users.services.merge_convergence_service import MergeConvergenceService

pytestmark = pytest.mark.django_db(
    transaction=True,
    databases=["default", "replica", "iam_concurrency_a", "iam_concurrency_b"],
)


def audit(identity_id=None, session_id=None, held=False, using="default"):
    return IdentitySecurityAuditRecord.objects.using(using).create(
        identity_id=identity_id,
        session_id=session_id,
        is_legal_hold=held,
        event_type="test.hold",
        actor_type="test",
        actor_ref="test",
        reason_code="test",
        request_id=uuid4(),
        correlation_id=uuid4(),
        occurred_at=timezone.now(),
    )


def session(identity_id, **kwargs):
    return IdentitySession.objects.create(
        identity_id=identity_id,
        security_version_snapshot=1,
        expires_at=timezone.now() - timedelta(hours=1),
        **kwargs,
    )


def authorized(_ids):
    pass


def legacy_audit(**fields):
    """Historical unresolved evidence is possible at canonical 0003, never fabricated by disabling triggers."""
    from django.db.migrations.executor import MigrationExecutor

    MigrationExecutor(connection).migrate([("users", "0003_identity_expand")])
    try:
        return audit(**fields)
    finally:
        MigrationExecutor(connection).migrate([("users", "0003a_identity_audit_fence")])


def sqlstate(exc):
    return getattr(exc.__cause__, "sqlstate", None) or getattr(exc.__cause__, "pgcode", None)


def wait_blocked(pid, blocker):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [blocker, pid])
            if cursor.fetchone()[0]:
                return
        threading.Event().wait(0.01)
    pytest.fail(f"backend {pid} did not block on {blocker}")


def backend_pid(using):
    with connections[using].cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def on_connection(fn, ready, pids, using="iam_concurrency_b"):
    try:
        with connections[using].cursor() as cursor:
            cursor.execute("SET lock_timeout = '5s'")
            cursor.execute("SET statement_timeout = '10s'")
        pids.append(backend_pid(using))
        ready.set()
        return fn(using)
    finally:
        connections.close_all()


VECTORS = [
    ("00000000-0000-0000-0000-000000000000", 3587376288989347619),
    ("00000000-0000-4000-8000-000000000001", -5115811478849578066),
    ("ffffffff-ffff-ffff-ffff-ffffffffffff", -8483742672244012164),
    ("12345678-1234-5678-9abc-def012345678", -3015736668283943721),
]


@pytest.mark.parametrize("identity,expected", VECTORS)
def test_identity_key_fixed_vectors(identity, expected):
    assert identity_advisory_key(UUID(identity)) == expected


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("identity,expected", VECTORS)
def test_postgresql_key_matches_python(identity, expected):
    with connection.cursor() as cursor:
        cursor.execute("SELECT public.users_identity_fence_key(%s::uuid)", [identity])
        assert cursor.fetchone()[0] == expected == identity_advisory_key(UUID(identity))


def test_writer_rejects_manual_autocommit_before_hold_dml():
    row = audit(uuid4())
    caller = connections["iam_concurrency_a"]
    caller.set_autocommit(False)
    try:
        with pytest.raises(RuntimeError, match="AUDIT_HOLD_DEDICATED_TRANSACTION_REQUIRED"):
            latch_identity_audit_hold([row.id], authorize=authorized, using="iam_concurrency_a")
        assert caller.get_autocommit() is False
        for alias in ("iam_concurrency_a", "iam_concurrency_b"):
            assert not IdentitySecurityAuditRecord.objects.using(alias).get(pk=row.id).is_legal_hold
    finally:
        caller.rollback()
        caller.set_autocommit(True)

    assert latch_identity_audit_hold([row.id], authorize=authorized, using="iam_concurrency_a") == 1
    assert IdentitySecurityAuditRecord.objects.using("iam_concurrency_b").get(pk=row.id).is_legal_hold


def test_writer_monotonic_idempotent_and_authorization(identity):
    row = audit(identity.identity_id)

    def deny(_ids):
        raise PermissionDenied("denied")

    with pytest.raises(PermissionDenied):
        latch_identity_audit_hold([row.id], authorize=deny)
    assert latch_identity_audit_hold([row.id], authorize=authorized) == 1
    assert latch_identity_audit_hold([row.id], authorize=authorized) == 0
    row.refresh_from_db()
    assert row.is_legal_hold
    with transaction.atomic(), pytest.raises(RuntimeError, match="DEDICATED"):
        latch_identity_audit_hold([row.id], authorize=authorized)


@pytest.mark.parametrize("kind", ["unresolved", "missing", "mismatch"])
def test_unresolved_or_mismatched_hold_fails_closed(identity, kind):
    s = session(identity.identity_id)
    fields = {
        "unresolved": {},
        "missing": {"session_id": uuid4()},
        "mismatch": {"identity_id": uuid4(), "session_id": s.id},
    }[kind]
    row = legacy_audit(**fields)
    before = dict(IdentitySecurityAuditRecord.objects.filter(pk=row.pk).values().get())
    with pytest.raises(AuditHoldBindingError):
        latch_identity_audit_hold([row.id], authorize=authorized)
    with pytest.raises(DatabaseError) as exc, transaction.atomic():
        IdentitySecurityAuditRecord.objects.filter(pk=row.pk).update(is_legal_hold=True)
    assert sqlstate(exc.value) == "23514"
    with pytest.raises(DatabaseError) as exc, transaction.atomic():
        audit(**fields, held=True)
    assert sqlstate(exc.value) == "23514"
    assert IdentitySecurityAuditRecord.objects.filter(pk=row.pk).values().get() == before


@pytest.mark.parametrize("operation", ["payload", "delete", "release", "true_to_true"])
def test_append_only_contract_survives_new_guards(identity, operation):
    row = audit(identity.identity_id, held=True)
    with pytest.raises(DatabaseError) as exc, transaction.atomic():
        qs = IdentitySecurityAuditRecord.objects.filter(pk=row.pk)
        if operation == "delete":
            qs.delete()
        else:
            qs.update(
                **{
                    "payload": {"reason_code": "changed"},
                    "release": {"is_legal_hold": False},
                    "true_to_true": {"is_legal_hold": True},
                }[operation]
            )
    assert sqlstate(exc.value) == "23514"


@pytest.mark.parametrize("operation", ["insert", "update"])
@pytest.mark.parametrize("session_only", [False, True])
def test_bypass_contention_rolls_back_without_reverse_wait(identity, operation, session_only):
    s = session(identity.identity_id)
    fields = {"session_id": s.id} if session_only else {"identity_id": identity.identity_id}
    row = audit(**fields)
    with transaction.atomic(using="iam_concurrency_a"):
        acquire_identity_merge_advisory_locks(
            user_pks=(), identity_ids=[identity.identity_id], using="iam_concurrency_a"
        )
        with pytest.raises(DatabaseError) as exc, transaction.atomic():
            if operation == "insert":
                audit(**fields, held=True)
            else:
                IdentitySecurityAuditRecord.objects.filter(pk=row.id).update(is_legal_hold=True)
        assert sqlstate(exc.value) == "55P03"
        assert not IdentitySecurityAuditRecord.objects.filter(is_legal_hold=True).exists()
    with transaction.atomic():
        if operation == "insert":
            audit(**fields, held=True)
        else:
            IdentitySecurityAuditRecord.objects.filter(pk=row.id).update(is_legal_hold=True)


@pytest.mark.parametrize("held", [False, True])
def test_session_binding_delete_and_uuid_reuse_guards(identity, held):
    s = session(identity.identity_id)
    audit(session_id=s.id, held=held)
    for column, value in [("id", uuid4()), ("identity_id", uuid4())]:
        with pytest.raises(DatabaseError) as exc, transaction.atomic():
            IdentitySession.objects.filter(pk=s.id).update(**{column: value})
        assert sqlstate(exc.value) == "23514"
    with pytest.raises(DatabaseError) as exc, transaction.atomic():
        IdentitySession.objects.filter(pk=s.id).delete()
    assert sqlstate(exc.value) == "23514"
    # Legacy orphan evidence also forbids a newly created session reusing its UUID.
    orphan = uuid4()
    legacy_audit(session_id=orphan)
    with pytest.raises(DatabaseError) as exc, transaction.atomic():
        session(identity.identity_id, id=orphan)
    assert sqlstate(exc.value) == "23514"


def test_session_delete_race_uses_try_fence(identity):
    s = session(identity.identity_id)
    row = audit(session_id=s.id)
    with transaction.atomic(using="iam_concurrency_a"):
        acquire_identity_merge_advisory_locks(
            user_pks=(), identity_ids=[identity.identity_id], using="iam_concurrency_a"
        )
        IdentitySecurityAuditRecord.objects.using("iam_concurrency_a").filter(pk=row.id).update(is_legal_hold=True)
        with pytest.raises(DatabaseError) as exc, transaction.atomic():
            IdentitySession.objects.filter(pk=s.id).delete()
        assert sqlstate(exc.value) == "55P03"
    assert IdentitySession.objects.filter(pk=s.id).exists()
    row.refresh_from_db()
    assert row.is_legal_hold


def test_uncommitted_unheld_audit_prevents_session_delete_and_reuse(identity):
    s = session(identity.identity_id)
    with transaction.atomic(using="iam_concurrency_a"):
        audit(session_id=s.id, using="iam_concurrency_a")
        with pytest.raises(DatabaseError) as exc, transaction.atomic():
            IdentitySession.objects.filter(pk=s.id).delete()
        assert sqlstate(exc.value) == "55P03"
    s.refresh_from_db()
    assert s.identity_id == identity.identity_id


def test_new_unheld_session_reference_requires_existing_consistent_binding(identity):
    s = session(identity.identity_id)
    for fields in ({"session_id": uuid4()}, {"session_id": s.id, "identity_id": uuid4()}):
        with pytest.raises(DatabaseError) as exc, transaction.atomic():
            audit(**fields)
        assert sqlstate(exc.value) == "23514"


def test_bulk_guard_revalidates_each_session_not_just_identity_union():
    """A database scheduling gate pauses discovery; real identity locks/key bytes are unchanged."""
    import importlib

    migration = importlib.import_module("apps.users.migrations.0003a_identity_audit_fence")
    a, b = UUID(VECTORS[0][0]), UUID(VECTORS[1][0])
    s = session(a)
    gate = 2718281828459045
    original_key_sql = migration.KEY_SQL.split("CREATE FUNCTION public.users_audit_hold_precondition")[0]
    pause_key_sql = """
        CREATE OR REPLACE FUNCTION public.users_identity_fence_key(identity_uuid uuid)
        RETURNS bigint LANGUAGE plpgsql VOLATILE STRICT SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
            IF current_setting('application_name') = 'iam_test_discovery_pause' THEN
                PERFORM pg_advisory_xact_lock(2718281828459045);
            END IF;
            RETURN ('x' || encode(substring(sha256(convert_to('iam-identity:', 'UTF8')
                || uuid_send(identity_uuid)), 1, 8), 'hex'))::bit(64)::bigint;
        END; $$;
    """
    ready, pids = threading.Event(), []

    def insert_bulk(using):
        with transaction.atomic(using=using):
            with connections[using].cursor() as cursor:
                cursor.execute("SET LOCAL application_name = 'iam_test_discovery_pause'")
            IdentitySecurityAuditRecord.objects.using(using).bulk_create(
                [
                    IdentitySecurityAuditRecord(
                        identity_id=identity,
                        session_id=session_id,
                        is_legal_hold=identity is not None,
                        event_type="test.hold",
                        actor_type="test",
                        actor_ref="test",
                        reason_code="test",
                        request_id=uuid4(),
                        correlation_id=uuid4(),
                        occurred_at=timezone.now(),
                    )
                    for identity, session_id in [(None, s.id), (b, None)]
                ]
            )

    try:
        with connection.cursor() as cursor:
            cursor.execute(pause_key_sql)
            cursor.execute("SELECT pg_advisory_lock(%s)", [gate])
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(on_connection, insert_bulk, ready, pids)
            try:
                assert ready.wait(3)
                wait_blocked(pids[0], backend_pid("default"))
                IdentitySession.objects.filter(pk=s.id).delete()
                session(b, id=s.id)
            finally:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [gate])
            with pytest.raises(DatabaseError) as exc:
                future.result(timeout=8)
            assert sqlstate(exc.value) == "55P03"
        assert not IdentitySecurityAuditRecord.objects.exists()
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [gate])
            cursor.execute(original_key_sql.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))


def test_bypass_session_row_recheck_is_nowait(identity):
    s = session(identity.identity_id)
    row = audit(session_id=s.id)
    with transaction.atomic(using="iam_concurrency_a"):
        IdentitySession.objects.using("iam_concurrency_a").select_for_update().get(pk=s.id)
        with pytest.raises(DatabaseError) as exc, transaction.atomic():
            IdentitySecurityAuditRecord.objects.filter(pk=row.id).update(is_legal_hold=True)
        assert sqlstate(exc.value) == "55P03"


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_unsupported_isolation_explicitly_refused(identity, isolation):
    row = audit(identity.identity_id)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
        with pytest.raises(RuntimeError, match="READ_COMMITTED"):
            IdentityMergeLockCoordinator().lock(user_pks=[identity.pk])
    with pytest.raises(DatabaseError) as exc, transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation}")
        IdentitySecurityAuditRecord.objects.filter(pk=row.id).update(is_legal_hold=True)
    assert sqlstate(exc.value) == "25001"
    with connection.cursor() as cursor:
        cursor.execute(f"SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL {isolation}")
    try:
        with pytest.raises(RuntimeError, match="READ_COMMITTED"):
            latch_identity_audit_hold([row.id], authorize=authorized)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL READ COMMITTED")


def test_audit_retirement_guard_uses_existing_key(identity):
    row = audit(identity.identity_id)
    with transaction.atomic(using="iam_concurrency_a"):
        with connections["iam_concurrency_a"].cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [IAM_RETIREMENT_BARRIER_KEY])
        with pytest.raises(DatabaseError) as exc, transaction.atomic():
            IdentitySecurityAuditRecord.objects.filter(pk=row.id).update(is_legal_hold=True)
        assert sqlstate(exc.value) == "55P03"


@pytest.fixture
def convergence_case(
    request,
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
    valid_runtime_merge_coverage_attestation,
):
    source, canonical = collision_pair.second, collision_pair.first
    dormant = request.param == "dormant"
    secret = pyotp.random_base32()
    if dormant:
        User.objects.filter(pk__in=[source.pk, canonical.pk]).update(
            is_active=False, security_status=User.SecurityStatus.LOCKED
        )
    else:
        User.objects.filter(pk=canonical.pk).update(mfa_enabled=True, mfa_secret=secret)
    approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk, canonical_pk=canonical.pk, reference="FENCE"
    )
    operation = identity_collision_resolution_service.approve_merge(
        source_pk=source.pk, canonical_pk=canonical.pk, approval=approval
    )
    challenge = None if dormant else MergeConvergenceService().start(operation_id=operation.id, actor_pk=source.pk)

    def run(using, coordinator=None):
        service = MergeConvergenceService(using=using)
        if coordinator is not None:
            service.coordinator = coordinator
        if dormant:
            return service.complete_dormant(operation_id=operation.id)
        return service.complete(
            challenge_token=challenge.challenge_token,
            actor_pk=source.pk,
            new_password="Fence Convergence Password 42!",
            totp_code=pyotp.TOTP(secret).now(),
            idempotency_key=challenge.idempotency_key,
        )

    return source, canonical, operation, run


@pytest.mark.parametrize("convergence_case", ["owner", "dormant"], indirect=True)
@pytest.mark.parametrize("session_only", [False, True])
@pytest.mark.parametrize("held_insert", [False, True])
def test_hold_wins_real_convergence_and_fresh_snapshot(convergence_case, session_only, held_insert):
    source, canonical, operation, converge = convergence_case
    s = session(source.identity_id)
    row = audit(session_id=s.id) if session_only else audit(source.identity_id)
    before = list(User.objects.filter(pk__in=[source.pk, canonical.pk]).order_by("pk").values())
    from apps.common.models.outbox import OutboxRecord

    outbox_before = OutboxRecord.objects.count()
    ready, pids = threading.Event(), []
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(using="iam_concurrency_a"):
            acquire_identity_merge_advisory_locks(
                user_pks=(), identity_ids=[source.identity_id], using="iam_concurrency_a"
            )
            if held_insert:
                audit(identity_id=row.identity_id, session_id=row.session_id, held=True, using="iam_concurrency_a")
            else:
                IdentitySecurityAuditRecord.objects.using("iam_concurrency_a").filter(pk=row.id).update(
                    is_legal_hold=True
                )
            future = pool.submit(on_connection, converge, ready, pids, "default")
            assert ready.wait(3)
            wait_blocked(pids[0], backend_pid("iam_concurrency_a"))
        with pytest.raises(PermissionDenied, match="LEGAL_HOLD_ACTIVE"):
            future.result(timeout=8)
    operation.refresh_from_db()
    assert operation.version == 1 and operation.state == IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE
    assert list(User.objects.filter(pk__in=[source.pk, canonical.pk]).order_by("pk").values()) == before
    assert OutboxRecord.objects.count() == outbox_before


@pytest.mark.parametrize("convergence_case", ["owner", "dormant"], indirect=True)
@pytest.mark.parametrize("session_only", [False, True])
def test_convergence_wins_writer_waits_before_dml(convergence_case, session_only):
    source, _, operation, converge = convergence_case
    s = session(source.identity_id)
    row = audit(session_id=s.id) if session_only else audit(source.identity_id)
    owns, release, ready = threading.Event(), threading.Event(), threading.Event()
    convergence_pids, writer_pids = [], []

    class ScheduledCoordinator(IdentityMergeLockCoordinator):
        def lock(self, **kwargs):
            graph = super().lock(**kwargs)
            owns.set()
            assert release.wait(5)
            return graph

    with ThreadPoolExecutor(max_workers=2) as pool:
        cf = pool.submit(
            on_connection,
            lambda using: converge(using, ScheduledCoordinator()),
            threading.Event(),
            convergence_pids,
            "default",
        )
        try:
            assert owns.wait(4)
            wf = pool.submit(
                on_connection,
                lambda using: latch_identity_audit_hold([row.id], authorize=authorized, using=using),
                ready,
                writer_pids,
            )
            assert ready.wait(3)
            wait_blocked(writer_pids[0], convergence_pids[0])
            row.refresh_from_db()
            assert not row.is_legal_hold
        finally:
            release.set()
        cf.result(timeout=8)
        assert wf.result(timeout=8) == 1
    operation.refresh_from_db()
    row.refresh_from_db()
    assert operation.state == IdentityMergeOperation.State.APPROVED_DEFERRED and row.is_legal_hold


@pytest.mark.parametrize("overlap", [False, True])
def test_bulk_writers_canonical_order_and_partial_overlap(overlap):
    ids = [
        UUID("00000000-0000-4000-8000-000000000001"),
        UUID("00000000-0000-4000-8000-000000000002"),
        UUID("00000000-0000-4000-8000-000000000003"),
    ]
    rows = [audit(i) for i in ids]
    groups = (
        ([rows[1].id, rows[0].id], [rows[2].id, rows[1].id])
        if overlap
        else (
            [rows[0].id, rows[1].id],
            [rows[1].id, rows[0].id],
        )
    )
    gate = threading.Barrier(2)

    def writer(using, group):
        gate.wait(timeout=4)
        return latch_identity_audit_hold(group, authorize=authorized, using=using)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(on_connection, lambda using, group=group: writer(using, group), threading.Event(), [], alias)
            for alias, group in zip(["iam_concurrency_a", "iam_concurrency_b"], groups, strict=True)
        ]
        assert sum(f.result(timeout=10) for f in futures) == (3 if overlap else 2)


def test_both_references_and_direct_stored_identity(identity):
    s = session(identity.identity_id)
    row = audit(identity.identity_id, s.id)
    assert latch_identity_audit_hold([row.id], authorize=authorized) == 1
    from apps.users.security.identity_merge_legal_hold import collect_legal_hold_snapshot

    with transaction.atomic():
        graph = IdentityMergeLockCoordinator().lock(user_pks=[identity.pk])
        snapshot = collect_legal_hold_snapshot(graph)
        assert {subject.identity_id for subject in snapshot.subjects} == {identity.identity_id}


def test_guard_metadata_and_migration_round_trip():
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    executor.migrate([("users", "0003_identity_expand")])
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regprocedure('public.users_identity_fence_key(uuid)')")
            assert cursor.fetchone()[0] is None
            cursor.execute("SELECT tgname FROM pg_trigger WHERE tgname = 'trg_users_identity_audit_append_only'")
            assert cursor.fetchone()
    finally:
        MigrationExecutor(connection).migrate([("users", "0003a_identity_audit_fence")])
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT proname, provolatile, prosecdef, proconfig,
                   proowner = (SELECT oid FROM pg_roles WHERE rolname=current_user)
            FROM pg_proc JOIN pg_namespace n ON n.oid=pronamespace
            WHERE n.nspname='public' AND proname IN (
                'users_identity_fence_key', 'users_audit_hold_precondition', 'users_audit_hold_insert_guard',
                'users_audit_hold_update_guard', 'users_audit_session_binding_guard')
        """)
        rows = cursor.fetchall()
        assert len(rows) == 5
        for name, volatility, definer, config, owned in rows:
            assert volatility == ("i" if name == "users_identity_fence_key" else "v")
            assert not definer and config == ["search_path=pg_catalog"] and owned
        cursor.execute("""
            SELECT tgname, tgenabled FROM pg_trigger WHERE tgname IN (
                'trg_users_audit_hold_insert', 'trg_users_audit_hold_update',
                'trg_users_audit_session_binding', 'trg_users_iam_retirement_barrier_3',
                'trg_users_identity_audit_append_only')
        """)
        rows = cursor.fetchall()
        assert len(rows) == 5 and all(enabled == "O" for _, enabled in rows)


def test_restricted_runtime_role_cannot_bypass_guards(identity):
    role = "test_hold_" + uuid4().hex
    row = audit(identity.identity_id)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE ROLE "{role}" NOSUPERUSER NOBYPASSRLS NOLOGIN')
            cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
            cursor.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.users_identity_security_audit, "
                f'public.users_identity_sessions TO "{role}"'
            )
            cursor.execute(f'SET LOCAL ROLE "{role}"')
        IdentitySecurityAuditRecord.objects.filter(pk=row.id).update(is_legal_hold=True)
        forbidden = [
            "ALTER TABLE public.users_identity_security_audit DISABLE TRIGGER USER",
            "ALTER TABLE public.users_identity_sessions DISABLE TRIGGER USER",
            "DROP FUNCTION public.users_audit_hold_update_guard() CASCADE",
            "ALTER FUNCTION public.users_identity_fence_key(uuid) RENAME TO bypass_key",
            f'ALTER TABLE public.users_identity_security_audit OWNER TO "{role}"',
            "SET LOCAL session_replication_role = 'replica'",
        ]
        for sql in forbidden:
            with pytest.raises(DatabaseError) as exc, transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(sql)
            assert sqlstate(exc.value) == "42501"
        with pytest.raises(DatabaseError) as exc, transaction.atomic():
            IdentitySecurityAuditRecord.objects.filter(pk=row.id).update(is_legal_hold=False)
        assert sqlstate(exc.value) == "23514"
        transaction.set_rollback(True)  # Removes disposable role/grants and test latch together.


def test_writer_retry_is_bounded_on_real_fence_contention(identity, monkeypatch):
    from apps.users.repositories.identity_audit_hold_repository import IdentityAuditHoldRepository

    row = audit(identity.identity_id)
    calls = []
    original = IdentityAuditHoldRepository.discover

    def count(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(IdentityAuditHoldRepository, "discover", count)
    with transaction.atomic(using="iam_concurrency_a"):
        acquire_identity_merge_advisory_locks(
            user_pks=(), identity_ids=[identity.identity_id], using="iam_concurrency_a"
        )
        with pytest.raises(DatabaseError) as exc:
            latch_identity_audit_hold([row.id], authorize=authorized)
        assert sqlstate(exc.value) == "55P03"
    assert len(calls) == 3
    row.refresh_from_db()
    assert not row.is_legal_hold


def test_writer_acquires_uuid_order_not_signed_key_order():
    first, last = UUID(VECTORS[0][0]), UUID(VECTORS[1][0])
    rows = [audit(last), audit(first)]
    ready, pids = threading.Event(), []
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic(using="iam_concurrency_a"):
            acquire_identity_merge_advisory_locks(user_pks=(), identity_ids=[last], using="iam_concurrency_a")
            future = pool.submit(
                on_connection,
                lambda using: latch_identity_audit_hold(
                    [row.id for row in rows],
                    authorize=authorized,
                    using=using,
                ),
                ready,
                pids,
            )
            assert ready.wait(3)
            wait_blocked(pids[0], backend_pid("iam_concurrency_a"))
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [identity_advisory_key(first)])
                assert cursor.fetchone()[0] is False
        assert future.result(timeout=8) == 2


def test_migration_refuses_existing_unresolved_held_evidence():
    from django.db.migrations.executor import MigrationExecutor

    with transaction.atomic():
        MigrationExecutor(connection).migrate([("users", "0003_identity_expand")])
        audit(held=True)
        with pytest.raises(DatabaseError) as exc, transaction.atomic():
            MigrationExecutor(connection).migrate([("users", "0003a_identity_audit_fence")])
        assert sqlstate(exc.value) == "23514"
        transaction.set_rollback(True)


@pytest.mark.parametrize("convergence_case", ["owner"], indirect=True)
@pytest.mark.parametrize("session_only", [False, True])
def test_cleanup_fresh_audit_hold_blocks_completion(convergence_case, session_only):
    from apps.users.models import IdentityMergeRedisCleanupRecord
    from apps.users.tasks.identity_merge_redis_cleanup import process_identity_merge_redis_cleanup

    source, _, operation, converge = convergence_case
    s = session(source.identity_id)
    converge("default")
    audit(session_id=s.id, held=True) if session_only else audit(source.identity_id, held=True)
    before = IdentityMergeRedisCleanupRecord.objects.filter(operation=operation).values().get()
    result = process_identity_merge_redis_cleanup.run(batch_size=10)
    assert result["held"] == 1 and result["completed"] == 0
    assert IdentityMergeRedisCleanupRecord.objects.filter(operation=operation).values().get() == before


@pytest.mark.parametrize("session_only", [False, True])
def test_requeue_fresh_audit_hold_blocks_authority(
    identity_merge_operation_factory, signed_reconciliation_approval, settings, session_only
):
    from apps.users.models import IdentityMergeHoldBlock, IdentityMergeRequeue, IdentityMergeRequeueResult
    from apps.users.models_identity import MergeActionKind, MergeRequeueOutcome, MergeResumeMode
    from apps.users.security.reconciliation_approval import canonical_action_bytes
    from apps.users.tasks.identity_merge_requeue import process_identity_merge_requeues

    operation = identity_merge_operation_factory(approval_expires_at=timezone.now() - timedelta(minutes=1))
    s = session(operation.source_identity_id)
    audit(session_id=s.id, held=True) if session_only else audit(operation.source_identity_id, held=True)
    block = IdentityMergeHoldBlock.objects.create(
        operation=operation,
        subject_type="source_identity",
        subject_identity_id=operation.source_identity_id,
        hold_reference="test",
        hold_digest="1" * 64,
        action_digest="2" * 64,
        lock_plan_digest="3" * 64,
        hold_set_digest="4" * 64,
        operation_version_snapshot=1,
        request_id=uuid4(),
        correlation_id=uuid4(),
        observed_at=timezone.now(),
    )
    settings.ENVIRONMENT, settings.BUILD_COMMIT = "test", "test-build"
    expires = timezone.now() + timedelta(minutes=10)
    req, corr = uuid4(), uuid4()
    action = {
        "schema_version": 1,
        "purpose": "identity-merge-requeue",
        "environment": "test",
        "operation_id": str(operation.id),
        "hold_block_id": str(block.id),
        "prior_action_digest": block.action_digest,
        "fresh_action_digest": "5" * 64,
        "action_kind": MergeActionKind.EXPIRE_PENDING,
        "resume_mode": MergeResumeMode.AUTOMATIC,
        "expected_operation_version": 1,
        "request_id": str(req),
        "correlation_id": str(corr),
        "expires_at": expires.isoformat(),
        "approval_reference": "FENCE-REQUEUE",
    }
    requeue = IdentityMergeRequeue.objects.create(
        operation=operation,
        hold_block=block,
        prior_action_digest=block.action_digest,
        fresh_action_digest="5" * 64,
        action_kind=MergeActionKind.EXPIRE_PENDING,
        resume_mode=MergeResumeMode.AUTOMATIC,
        action_schema_version=1,
        environment="test",
        execution_build_digest="test-build",
        approval_reference="FENCE-REQUEUE",
        key_id=signed_reconciliation_approval.key_id,
        issued_at=timezone.now(),
        expires_at=expires,
        signature=signed_reconciliation_approval.private_key.sign(canonical_action_bytes(action)),
        expected_operation_version=1,
        requested_by="test",
        request_id=req,
        correlation_id=corr,
    )
    result = process_identity_merge_requeues.run(batch_size=10)
    assert result[MergeRequeueOutcome.HOLD_STILL_ACTIVE] == 1
    assert IdentityMergeRequeueResult.objects.get(requeue=requeue).outcome == MergeRequeueOutcome.HOLD_STILL_ACTIVE
    operation.refresh_from_db()
    assert operation.version == 1 and operation.state == IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE
