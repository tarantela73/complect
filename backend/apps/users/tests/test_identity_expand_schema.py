import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.db.models.fields import NOT_PROVIDED
from django.utils import timezone

from apps.common.tenancy.context import clear_tenant_context, set_tenant_context
from apps.users.models import (
    IamTenantRegistryEntry,
    IdentityMergeOperation,
    IdentitySession,
    MfaCredential,
    PreAuthChallenge,
    RecoveryOperation,
    RecoveryToken,
    SessionReconciliationRecord,
    TenantAuthorizationState,
    TenantMembership,
    TenantSessionGrant,
    User,
)


@pytest.mark.django_db
def test_identity_expand_fields_are_nullable_and_not_unique():
    apps = MigrationExecutor(connection).loader.project_state([("users", "0003_identity_expand")]).apps
    expand_user = apps.get_model("users", "User")
    for name in ("identity_id", "normalized_email"):
        field = expand_user._meta.get_field(name)
        assert field.null is True
        assert field.unique is False
        assert field.db_index is False
    legal_hold = expand_user._meta.get_field("identity_legal_hold")
    assert legal_hold.null is True
    assert legal_hold.default is NOT_PROVIDED


def test_model_contracts_are_closed():
    assert "tenant_id" not in {field.name for field in IdentitySession._meta.fields}
    assert IamTenantRegistryEntry._meta.pk.name == "tenant_id"
    assert {value for value, _ in User.SecurityStatus.choices} == {
        "pending_credentials", "mfa_setup_required", "active", "locked"
    }
    assert {value for value, _ in TenantMembership.Status.choices} == {
        "draft", "invited", "active", "disabled"
    }
    assert "merge_convergence" in {value for value, _ in PreAuthChallenge.FlowKind.choices}
    assert TenantSessionGrant._meta.get_field("session").related_model is IdentitySession
    assert TenantSessionGrant._meta.get_field("membership").related_model is TenantMembership
    assert TenantMembership._meta.get_field("legacy_actor_user").remote_field.on_delete is models.PROTECT


def test_required_constraints_exist():
    by_model = {
        TenantMembership: {"uniq_users_active_membership_actor"},
        TenantSessionGrant: {"ck_users_grant_not_soft_deleted", "uniq_users_open_grant_per_session"},
        TenantAuthorizationState: {"ck_users_tenant_auth_state_not_soft_deleted"},
        MfaCredential: {"uniq_users_unrevoked_totp_per_identity"},
        RecoveryToken: {"uniq_users_open_recovery_per_identity_purpose"},
        PreAuthChallenge: {"ck_users_preauth_merge_binding"},
        SessionReconciliationRecord: {
            "uniq_users_session_reconciliation_kind_operation",
            "ck_users_reconciliation_switch_binding",
        },
    }
    for model, expected in by_model.items():
        assert expected <= {constraint.name for constraint in model._meta.constraints}
    assert SessionReconciliationRecord._meta.get_field("switch_operation").target_field.name == "operation_id"


@pytest.mark.django_db
def test_rls_and_integrity_triggers_are_installed():
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
            WHERE relname IN ('users_tenant_memberships', 'users_tenant_authorization_state',
                              'users_tenant_session_grants') ORDER BY relname
            """
        )
        assert len(cursor.fetchall()) == 3
        cursor.execute(
            """
            SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname IN (
                'trg_users_grant_append_only', 'trg_users_identity_audit_append_only',
                'trg_users_identity_merge_operation_guard',
                'trg_users_membership_actor_immutable',
                'trg_users_recovery_operation_requires_token',
                'trg_users_recovery_token_operation_immutable',
                'trg_users_reconciliation_switch_session_match'
            ) ORDER BY tgname
            """
        )
        assert len(cursor.fetchall()) == 7


@pytest.mark.django_db(transaction=True)
def test_membership_actor_is_write_once(identity):
    tenant_id = uuid.uuid4()
    IamTenantRegistryEntry.objects.create(tenant_id=tenant_id, source_code="test_fixture")
    other = User.objects.create_user(username=f"other-{uuid.uuid4()}", password="TemporaryPassword123!")
    set_tenant_context(tenant_id)
    try:
        membership = TenantMembership.objects.create(
            tenant_id=tenant_id,
            identity_id=identity.identity_id,
            legacy_actor_user=identity,
            display_name="Identity",
            status=TenantMembership.Status.ACTIVE,
        )
        with pytest.raises(IntegrityError, match="membership_actor_immutable"):
            with transaction.atomic():
                TenantMembership.objects.filter(pk=membership.pk).update(legacy_actor_user=other)
    finally:
        clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_recovery_operation_requires_exact_token(identity, recovery_token):
    with pytest.raises(IntegrityError, match="recovery_operation_requires_token"):
        with transaction.atomic():
            RecoveryOperation.objects.create(
                delivery_ciphertext=b"orphan",
                delivery_nonce=b"nonce-12-byte",
                delivery_key_id="test-key",
                request_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
                causal_root_id=uuid.uuid4(),
            )
    with pytest.raises(IntegrityError, match="recovery_token_operation_immutable"):
        with transaction.atomic():
            replacement = RecoveryOperation.objects.create(
                delivery_ciphertext=b"replacement",
                delivery_nonce=b"nonce-12-byte",
                delivery_key_id="test-key",
                request_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
                causal_root_id=uuid.uuid4(),
            )
            RecoveryToken.objects.filter(pk=recovery_token.pk).update(operation=replacement)


@pytest.mark.django_db(transaction=True)
def test_merge_trigger_enforces_initial_state_and_transition(identity_merge_operation_factory):
    with pytest.raises(IntegrityError, match="identity_merge_invalid_initial_state"):
        with transaction.atomic():
            identity_merge_operation_factory(state=IdentityMergeOperation.State.FINALIZED)
    operation = identity_merge_operation_factory()
    with pytest.raises(IntegrityError, match="identity_merge_invalid_transition"):
        with transaction.atomic():
            IdentityMergeOperation.objects.filter(pk=operation.pk).update(
                state=IdentityMergeOperation.State.FINALIZED,
                version=models.F("version") + 1,
                finalized_at=timezone.now(),
            )


@pytest.mark.django_db(transaction=True)
def test_registry_fk_is_deferred_and_enforced(identity):
    tenant_id = uuid.uuid4()
    set_tenant_context(tenant_id)
    try:
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                TenantMembership.objects.create(
                    tenant_id=tenant_id,
                    identity_id=identity.identity_id,
                    legacy_actor_user=identity,
                    display_name="Unregistered",
                    status=TenantMembership.Status.DRAFT,
                )
        with transaction.atomic():
            TenantMembership.objects.create(
                tenant_id=tenant_id,
                identity_id=identity.identity_id,
                legacy_actor_user=identity,
                display_name="Registered in same transaction",
                status=TenantMembership.Status.DRAFT,
            )
            IamTenantRegistryEntry.objects.create(tenant_id=tenant_id, source_code="test_fixture")
    finally:
        clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_only_one_open_grant_per_session(identity):
    tenant_id = uuid.uuid4()
    IamTenantRegistryEntry.objects.create(tenant_id=tenant_id, source_code="test_fixture")
    session = IdentitySession.objects.create(
        identity_id=identity.identity_id,
        security_version_snapshot=1,
        expires_at=timezone.now() + timedelta(days=7),
    )
    set_tenant_context(tenant_id)
    try:
        membership = TenantMembership.objects.create(
            tenant_id=tenant_id,
            identity_id=identity.identity_id,
            legacy_actor_user=identity,
            display_name="Identity",
            status=TenantMembership.Status.ACTIVE,
        )
        values = {
            "tenant_id": tenant_id,
            "session": session,
            "membership": membership,
            "issued_at": timezone.now(),
            "expires_at": timezone.now() + timedelta(hours=8),
        }
        TenantSessionGrant.objects.create(context_version=1, **values)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                TenantSessionGrant.objects.create(context_version=2, **values)
    finally:
        clear_tenant_context()


@pytest.mark.django_db(transaction=True)
def test_identity_expand_migration_round_trip_0002_0003_0002_0003():
    executor = MigrationExecutor(connection)
    executor.migrate([("users", "0002_user_device_fingerprint_user_mfa_enabled_and_more")])
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('users_identity_merge_operations')")
        assert cursor.fetchone()[0] is None
    executor = MigrationExecutor(connection)
    executor.migrate([("users", "0003_identity_expand")])
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('users_identity_merge_operations')")
        assert cursor.fetchone()[0] == "users_identity_merge_operations"


@pytest.mark.django_db(transaction=True)
def test_reverse_0003_fails_after_first_approved_deferred(identity_merge_operation_factory):
    identity_merge_operation_factory.advance_to(IdentityMergeOperation.State.APPROVED_DEFERRED)
    executor = MigrationExecutor(connection)
    with pytest.raises(IrreversibleError, match="cannot be reversed"):
        executor.migrate([("users", "0002_user_device_fingerprint_user_mfa_enabled_and_more")])
