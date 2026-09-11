import uuid

import pytest
from django.db import connection, transaction

from apps.common.context.core import ContextCore
from apps.common.tenancy.context import scoped_tenant_context, tenant_rls_scope
from apps.users.models import IamTenantRegistryEntry, TenantMembership, User
from apps.users.repositories.membership_repository import MembershipRepository


@pytest.fixture
def iam_nobypassrls_role():
    role = f"iam_nobypass_{uuid.uuid4().hex}"
    quoted = connection.ops.quote_name(role)
    tables = (
        "users_iam_tenant_registry",
        "users_tenant_authorization_state",
        "users_tenant_memberships",
        "users_tenant_session_grants",
        "users_users",
    )
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE ROLE {quoted} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS")
        cursor.execute(f"GRANT USAGE ON SCHEMA public TO {quoted}")
        for table in tables:
            cursor.execute(f"GRANT SELECT, INSERT, UPDATE ON TABLE {table} TO {quoted}")
    try:
        yield role
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
            cursor.execute(f"DROP OWNED BY {quoted}")
            cursor.execute(f"DROP ROLE IF EXISTS {quoted}")


@pytest.fixture
def two_tenant_rls_rows(db):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    actor_a = User.objects.create_user(username=f"rls-a-{uuid.uuid4().hex}")
    actor_b = User.objects.create_user(username=f"rls-b-{uuid.uuid4().hex}")
    identity_a, identity_b = uuid.uuid4(), uuid.uuid4()
    IamTenantRegistryEntry.objects.bulk_create(
        [
            IamTenantRegistryEntry(tenant_id=tenant_a, source_code="rls_test"),
            IamTenantRegistryEntry(tenant_id=tenant_b, source_code="rls_test"),
        ],
    )
    TenantMembership.objects.bulk_create(
        [
            TenantMembership(
                tenant_id=tenant_a,
                identity_id=identity_a,
                legacy_actor_user=actor_a,
                display_name="Tenant A",
                status=TenantMembership.Status.DRAFT,
            ),
            TenantMembership(
                tenant_id=tenant_b,
                identity_id=identity_b,
                legacy_actor_user=actor_b,
                display_name="Tenant B",
                status=TenantMembership.Status.DRAFT,
            ),
        ],
    )
    return tenant_a, tenant_b, actor_a, actor_b, identity_a, identity_b


def _set_local_role(role: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"SET LOCAL ROLE {connection.ops.quote_name(role)}")


def _database_tenant() -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.current_tenant_id', true)")
        return cursor.fetchone()[0] or ""


@pytest.mark.django_db(transaction=True)
def test_contextvar_alone_does_not_scope_explicit_database_alias(
    iam_nobypassrls_role,
    two_tenant_rls_rows,
):
    tenant_a, _tenant_b, *_rest = two_tenant_rls_rows
    with transaction.atomic(using="default"):
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_tenant_id', '', true)")
        _set_local_role(iam_nobypassrls_role)
        with scoped_tenant_context(tenant_a):
            assert ContextCore.tenant_id.get() == tenant_a
            assert _database_tenant() == ""
            assert TenantMembership.objects.using("default").filter(tenant_id=tenant_a).count() == 0


@pytest.mark.django_db(transaction=True)
def test_tenant_rls_scope_keeps_contextvar_and_same_connection_guc_in_sync(
    iam_nobypassrls_role,
    two_tenant_rls_rows,
):
    tenant_a, tenant_b, *_rest = two_tenant_rls_rows
    with transaction.atomic(using="default"):
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_tenant_id', '', true)")
        _set_local_role(iam_nobypassrls_role)
        with tenant_rls_scope(tenant_a, using="default"):
            assert ContextCore.tenant_id.get() == tenant_a
            assert _database_tenant() == str(tenant_a)
            assert list(
                TenantMembership.objects.using("default").values_list("tenant_id", flat=True),
            ) == [tenant_a]
            with tenant_rls_scope(tenant_b, using="default"):
                assert ContextCore.tenant_id.get() == tenant_b
                assert _database_tenant() == str(tenant_b)
                assert list(
                    TenantMembership.objects.using("default").values_list("tenant_id", flat=True),
                ) == [tenant_b]
            assert ContextCore.tenant_id.get() == tenant_a
            assert _database_tenant() == str(tenant_a)


@pytest.mark.django_db(transaction=True)
def test_tenant_rls_scope_restores_both_contexts_after_exception(
    iam_nobypassrls_role,
    two_tenant_rls_rows,
):
    tenant_a, tenant_b, *_rest = two_tenant_rls_rows
    with transaction.atomic(using="default"):
        _set_local_role(iam_nobypassrls_role)
        with tenant_rls_scope(tenant_a, using="default"):
            with pytest.raises(RuntimeError, match="boom"):
                with tenant_rls_scope(tenant_b, using="default"):
                    raise RuntimeError("boom")
            assert ContextCore.tenant_id.get() == tenant_a
            assert _database_tenant() == str(tenant_a)


@pytest.mark.django_db(transaction=True)
def test_membership_worker_repository_sets_rls_scope_for_explicit_alias(
    iam_nobypassrls_role,
    two_tenant_rls_rows,
):
    tenant_a, _tenant_b, actor_a, _actor_b, identity_a, _identity_b = two_tenant_rls_rows
    with transaction.atomic(using="default"):
        _set_local_role(iam_nobypassrls_role)
        membership = MembershipRepository.ensure_foundation(
            tenant_id=tenant_a,
            identity_id=identity_a,
            legacy_actor_user_id=actor_a.pk,
            display_name="Tenant A",
            status=TenantMembership.Status.DRAFT,
            using="default",
        )
        assert membership.tenant_id == tenant_a
        assert _database_tenant() == ""
