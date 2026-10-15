"""PII audit commits must survive caller rollback; real PostgreSQL only."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.management import CommandError, call_command
from django.db import OperationalError, connection, connections, transaction
from django.db.models.query import QuerySet
from django.test.utils import CaptureQueriesContext

from apps.users.models import IdentitySecurityAuditRecord, User
from apps.users.repositories.identity_collision_repository import IdentityCollisionRepository
from apps.users.services.pii_access_audit_service import PiiAccessAuditService, PiiAuditUnavailable

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "iam_concurrency_a", "iam_concurrency_b"])


class CallerRollbackError(Exception):
    pass


def audit_arguments(subjects, *, request_id=None, using="default"):
    return {
        "subjects": subjects,
        "actor_type": "operator",
        "actor_ref": "pii-durability-test",
        "reason_code": "IDENTITY_EMAIL_RECONCILIATION_REPORT",
        "request_id": request_id or uuid.uuid4(),
        "correlation_id": uuid.uuid4(),
        "using": using,
    }


def test_ambient_rollback_cannot_leave_pii_read_without_durable_audit():
    user = User.objects.create(username="durability-subject", email="fixture@example.invalid")
    subjects = IdentityCollisionRepository.enumerate_subjects(user_pks=[user.pk])
    arguments = audit_arguments(subjects)
    pii_read = False
    rejected = False

    with CaptureQueriesContext(connection) as captured:
        with pytest.raises(CallerRollbackError):
            with transaction.atomic():
                try:
                    PiiAccessAuditService.append_batch_and_commit(**arguments)
                except PiiAuditUnavailable:
                    rejected = True
                else:
                    IdentityCollisionRepository.load_pii_candidates([user.pk])
                    pii_read = True
                raise CallerRollbackError

    durable = IdentitySecurityAuditRecord.objects.filter(request_id=arguments["request_id"]).exists()
    boundaries = [item["sql"].split()[0].upper() for item in captured.captured_queries]
    assert "BEGIN" in boundaries and "ROLLBACK" in boundaries
    print(f"TRANSACTION_BOUNDARIES={boundaries}; PII_READ={pii_read}; AUDIT_DURABLE={durable}")
    assert rejected, "Ambient transaction must fail before PII can be read"
    assert not pii_read
    assert not durable


@pytest.mark.parametrize("using", ["default", "iam_concurrency_a"])
def test_normal_commit_is_visible_on_independent_connection_before_pii_and_survives_rollback(using):
    user = User.objects.create(username="normal-subject", email="fixture@example.invalid")
    arguments = audit_arguments(IdentityCollisionRepository.enumerate_subjects(user_pks=[user.pk]), using=using)
    writer, observer = connections[using], connections["iam_concurrency_b"]
    with writer.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        writer_pid = cursor.fetchone()[0]
    with observer.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        assert cursor.fetchone()[0] != writer_pid

    with CaptureQueriesContext(writer) as captured:
        PiiAccessAuditService.append_batch_and_commit(**arguments)
    boundaries = [item["sql"].split()[0].upper() for item in captured.captured_queries]
    assert "BEGIN" in boundaries and "COMMIT" in boundaries
    assert (
        IdentitySecurityAuditRecord.objects.using("iam_concurrency_b")
        .filter(request_id=arguments["request_id"])
        .count()
        == 1
    )
    assert not writer.in_atomic_block and writer.get_autocommit()
    with pytest.raises(CallerRollbackError):
        with transaction.atomic(using=using):
            assert IdentityCollisionRepository.load_pii_candidates([user.pk], using=using)
            raise CallerRollbackError
    assert (
        IdentitySecurityAuditRecord.objects.using("iam_concurrency_b")
        .filter(request_id=arguments["request_id"])
        .count()
        == 1
    )


@pytest.mark.parametrize("using", ["default", "iam_concurrency_a"])
@pytest.mark.parametrize("mode", ["nested", "manual-autocommit"])
def test_ambient_state_is_rejected_before_consuming_subjects(using, mode):
    consumed = []

    def subjects():
        consumed.append(True)
        yield SimpleNamespace(user_pk=1, identity_id=None)

    database = connections[using]

    def reject():
        with pytest.raises(PiiAuditUnavailable, match="dedicated transaction"):
            PiiAccessAuditService.append_batch_and_commit(**audit_arguments(subjects(), using=using))
        assert not consumed

    if mode == "nested":
        with transaction.atomic(using=using), transaction.atomic(using=using):
            reject()
            assert database.in_atomic_block
    else:
        database.set_autocommit(False)
        try:
            with database.cursor() as cursor:
                cursor.execute("SELECT 1")
            reject()
            assert not database.get_autocommit()
        finally:
            database.rollback()
            database.set_autocommit(True)


@pytest.mark.django_db
def test_django_testcase_wrapper_is_not_a_durability_exception():
    assert connection.in_atomic_block
    with pytest.raises(PiiAuditUnavailable, match="dedicated transaction"):
        PiiAccessAuditService.append_batch_and_commit(**audit_arguments([]))


def report_arguments(request_id):
    return (
        "--actor-type",
        "operator",
        "--actor-ref",
        "pii-durability-test",
        "--request-id",
        str(request_id),
        "--correlation-id",
        str(uuid.uuid4()),
    )


@pytest.fixture
def report_target(tmp_path):
    directory = tmp_path / "protected-reconciliation"
    directory.mkdir(mode=0o700)
    return directory / "report.json"


@pytest.mark.parametrize("existing", [False, True])
def test_report_ambient_rollback_never_reads_or_creates_or_replaces_output(report_target, existing, monkeypatch):
    User.objects.create(username="report-subject", email="fixture@example.invalid")
    if existing:
        report_target.write_bytes(b"existing-operator-artifact")
        original_stat = report_target.stat()
    query = Mock(side_effect=AssertionError("PII must not be read"))
    monkeypatch.setattr(IdentityCollisionRepository, "load_pii_candidates", query)
    request_id = uuid.uuid4()
    with pytest.raises(CallerRollbackError):
        with transaction.atomic():
            with pytest.raises(CommandError, match="PII audit unavailable"):
                call_command(
                    "report_identity_email_duplicates", "--output", str(report_target), *report_arguments(request_id)
                )
            raise CallerRollbackError
    query.assert_not_called()
    assert not IdentitySecurityAuditRecord.objects.filter(request_id=request_id).exists()
    if existing:
        assert report_target.read_bytes() == b"existing-operator-artifact"
        assert report_target.stat().st_ino == original_stat.st_ino
        assert report_target.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert set(report_target.parent.iterdir()) == ({report_target} if existing else set())


@pytest.mark.parametrize("phase", ["write", "commit"])
@pytest.mark.parametrize("existing", [False, True])
def test_audit_failure_blocks_report_pii_and_filesystem(phase, existing, report_target, monkeypatch):
    User.objects.create(username="failure-subject", email="fixture@example.invalid")
    if existing:
        report_target.write_bytes(b"existing-operator-artifact")
    query = Mock(side_effect=AssertionError("PII must not be read"))
    monkeypatch.setattr(IdentityCollisionRepository, "load_pii_candidates", query)
    original_bulk_create = QuerySet.bulk_create
    attempts = []

    def write_then_fail(queryset, *args, **kwargs):
        rows = original_bulk_create(queryset, *args, **kwargs)
        if queryset.model is IdentitySecurityAuditRecord:
            attempts.append("write")
            # A real PostgreSQL statement failure rolls back the inserted audit batch.
            with connections[queryset.db].cursor() as cursor:
                cursor.execute("SELECT 1 / 0")
        return rows

    def fail_commit():
        attempts.append("commit")
        raise OperationalError("injected audit commit failure")

    request_id = uuid.uuid4()
    with monkeypatch.context() as fault:
        if phase == "write":
            fault.setattr(QuerySet, "bulk_create", write_then_fail)
        else:
            fault.setattr(connection, "commit", fail_commit)
        with pytest.raises(CommandError, match="PII audit unavailable"):
            call_command(
                "report_identity_email_duplicates", "--output", str(report_target), *report_arguments(request_id)
            )
    assert attempts == [phase]
    query.assert_not_called()
    assert not IdentitySecurityAuditRecord.objects.using("iam_concurrency_b").filter(request_id=request_id).exists()
    assert connection.get_autocommit() and not connection.in_atomic_block
    if existing:
        assert report_target.read_bytes() == b"existing-operator-artifact"
    assert set(report_target.parent.iterdir()) == ({report_target} if existing else set())


def test_report_query_observes_committed_evidence_before_output(report_target, monkeypatch):
    User.objects.create(username="normal-report-subject", email="fixture@example.invalid")
    request_id = uuid.uuid4()
    original_query = IdentityCollisionRepository.load_pii_candidates
    observed = []

    def checked_query(*args, **kwargs):
        assert not report_target.exists()
        assert connection.get_autocommit() and not connection.in_atomic_block
        count = IdentitySecurityAuditRecord.objects.using("iam_concurrency_b").filter(request_id=request_id).count()
        assert count == 1
        observed.append(count)
        return original_query(*args, **kwargs)

    monkeypatch.setattr(IdentityCollisionRepository, "load_pii_candidates", checked_query)
    call_command("report_identity_email_duplicates", "--output", str(report_target), *report_arguments(request_id))
    assert observed == [1]
    assert report_target.is_file()
    assert set(report_target.parent.iterdir()) == {report_target}


@pytest.mark.parametrize("operation", ["correct_email", "approve_merge"])
def test_collision_callers_reject_ambient_before_graph_pii_load(operation):
    from apps.users.services.identity_collision_resolution import IdentityCollisionResolutionService

    first = User.objects.create(username="first-collision", email="fixture@example.invalid")
    second = User.objects.create(username="second-collision", email="fixture@example.invalid")
    coordinator = Mock()
    service = IdentityCollisionResolutionService(coordinator=coordinator)
    approval = SimpleNamespace(reference="pii-durability-test")
    with transaction.atomic():
        with pytest.raises(PiiAuditUnavailable, match="dedicated transaction"):
            if operation == "correct_email":
                service.correct_email(user_pk=first.pk, new_email="new@example.invalid", approval=approval)
            else:
                service.approve_merge(source_pk=first.pk, canonical_pk=second.pk, approval=approval)
    coordinator.lock.assert_not_called()
