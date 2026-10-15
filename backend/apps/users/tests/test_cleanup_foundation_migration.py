"""Disposable PostGIS migration rehearsal; no working database is addressed."""

import pytest
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor

from apps.users.repositories.identity_cleanup_admission_repository import IdentityCleanupAdmissionRepository
from apps.users.tests.test_cleanup_admission_protocol import cleanup_fixture

pytestmark = pytest.mark.django_db(transaction=True)
BASELINE = ("users", "0003a_identity_audit_fence")
FOUNDATION = ("users", "0003b1_cleanup_admission_indexes")


def guards():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tgrelid::regclass::text,tgname,tgenabled,pg_get_triggerdef(oid) "
            "FROM pg_trigger WHERE NOT tgisinternal"
        )
        return set(cursor.fetchall())


def migrate(target):
    MigrationExecutor(connection).migrate([target])


def test_empty_foundation_roundtrip_preserves_existing_guards_and_legacy_rows():
    migrate(BASELINE)
    baseline = guards()
    cleanup, source, _ = cleanup_fixture()
    try:
        migrate(FOUNDATION)
        assert baseline <= guards()
        cleanup.refresh_from_db()
        assert cleanup.actor_user_pks == [source.pk]
        migrate(BASELINE)
        assert guards() == baseline
        cleanup.refresh_from_db()
        assert cleanup.status == "pending"
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.users_identity_cleanup_admissions')")
            assert cursor.fetchone()[0] is None
    finally:
        migrate(FOUNDATION)


def test_reverse_refuses_nonempty_protocol_before_dropping_evidence_or_guards():
    cleanup, _, _ = cleanup_fixture()
    owner = IdentityCleanupAdmissionRepository.admit(
        cleanup_id=cleanup.pk,
        expected_cleanup_version=1,
        expected_operation_version=1,
        build_id="foundation-test",
        resource_epoch="isolated-test",
    )
    before = guards()
    try:
        with pytest.raises(IrreversibleError, match="CLEANUP_FOUNDATION_LEDGER_NOT_EMPTY"):
            migrate(BASELINE)
        assert guards() == before
        with connection.cursor() as cursor:
            cursor.execute("SELECT state FROM users_identity_cleanup_admissions WHERE id=%s", [owner.admission_id])
            assert cursor.fetchone()[0] == "OPEN"
    finally:
        migrate(FOUNDATION)
