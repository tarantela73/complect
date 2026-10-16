import os
import secrets
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import connection

from apps.common.models.rbac import Role, RoleBinding
from apps.common.tenancy.context import tenant_rls_scope
from apps.users.models import (
    IamTenantRegistryEntry,
    IdentitySecurityAuditRecord,
    TenantMembership,
    User,
)
from apps.users.services.local_files_provisioning import provision_local_files_account


@pytest.fixture
def local_environment(settings, monkeypatch):
    settings.DEBUG = True
    monkeypatch.setenv('ENVIRONMENT', 'local')
    monkeypatch.setenv('DJANGO_SETTINGS_MODULE', 'config.settings.local')
    settings.SETTINGS_MODULE = 'config.settings.local'
    from apps.common.models.rbac import Permission
    for action in ['read', 'create', 'delete']:
        Permission.objects.get_or_create(resource='files', action=action)


LOCAL_PASSWORD = secrets.token_urlsafe(24)


def provision(email='local.tester@example.test'):
    from apps.users.services.local_files_provisioning import local_operator_scope
    operator_ref = f'local-cli:uid:{os.getuid()}'
    with local_operator_scope(operator_ref):
        return _provision(email, operator_ref)


def _provision(email, operator_ref):
    return provision_local_files_account(
        expected_database=connection.settings_dict['NAME'],
        email=email,
        password=LOCAL_PASSWORD,
        operator_ref=operator_ref,
    )


@pytest.mark.django_db(transaction=True)
def test_local_profile_has_exact_permissions_and_tenant_actor(local_environment):
    from unittest.mock import patch

    from apps.common.tasks.outbox_dispatcher import run_outbox_polling_dispatcher_task
    with patch.object(run_outbox_polling_dispatcher_task, 'apply_async', return_value=None) as wake:
        result = provision()
    assert wake.called
    role = Role.objects.get(name='local_files_e2e')
    assert set(role.permissions.values_list('resource', 'action')) == {
        ('files', 'read'), ('files', 'create'), ('files', 'delete'),
    }
    user = User.objects.get(pk=result.user_id)
    assert not user.is_superuser and not user.is_staff
    assert user.check_password(LOCAL_PASSWORD)
    assert user.security_status == User.SecurityStatus.MFA_SETUP_REQUIRED
    with connection.cursor() as cursor:
        cursor.execute('SELECT tenant_id, device_fingerprint, mfa_enabled, mfa_secret FROM users_users WHERE id=%s',
                       [user.pk])
        assert cursor.fetchone() == (result.tenant_id, '', False, '')
    assert IamTenantRegistryEntry.objects.filter(tenant_id=result.tenant_id).exists()
    with tenant_rls_scope(result.tenant_id):
        member = TenantMembership.objects.get(identity_id=user.identity_id)
        from apps.users.services.user_admin_service import _to_uuid
        binding = RoleBinding.objects.get(tenant_id=result.tenant_id, role=role)
        assert binding.user_id == _to_uuid(member.legacy_actor_user_id)
        assert binding.granted_by is None
    audit = IdentitySecurityAuditRecord.objects.get(
        identity_id=user.identity_id, event_type='identity.created',
    )
    assert audit.actor_type == 'operator'
    assert audit.actor_ref == f'local-cli:uid:{os.getuid()}'
    assert audit.reason_code == 'LOCAL_TEST_USER_CREATED'
    from apps.common.models.outbox import OutboxRecord
    outbox = OutboxRecord.objects.get(event_type='identity.created')
    assert outbox.causal_root_id == outbox.id


@pytest.mark.django_db(transaction=True)
def test_repeat_preserves_password_and_creates_no_duplicates(local_environment):
    first = provision()
    user = User.objects.get(pk=first.user_id)
    password_hash = user.password
    second = provision()
    assert second.tenant_id == first.tenant_id
    assert not second.created
    assert User.objects.count() == 1
    assert User.objects.get(pk=first.user_id).password == password_hash


@pytest.mark.django_db(transaction=True)
def test_foreign_role_name_conflict_does_not_create_identity(local_environment):
    Role.objects.create(name='local_files_e2e', description='foreign role')
    with pytest.raises(ValidationError, match='LOCAL_ROLE_CONFLICT'):
        provision()
    assert User.objects.count() == 0
    assert IamTenantRegistryEntry.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_database_mismatch_refuses_before_any_write(local_environment):
    with pytest.raises(ValidationError, match='LOCAL_DATABASE_MISMATCH'):
        provision_local_files_account(
            expected_database='not-this-database', email='local.tester@example.test',
            password=LOCAL_PASSWORD, operator_ref=f'local-cli:uid:{os.getuid()}',
        )
    assert not Role.objects.filter(name='local_files_e2e').exists()


@pytest.mark.django_db(transaction=True)
def test_nonlocal_refuses_before_any_write(local_environment, monkeypatch):
    monkeypatch.setenv('ENVIRONMENT', 'production')
    with pytest.raises(ValidationError, match='LOCAL_ENVIRONMENT_REQUIRED'):
        provision()
    assert not Role.objects.filter(name='local_files_e2e').exists()


@pytest.mark.django_db(transaction=True)
def test_private_command_preserves_settings_and_credentials_on_repeat(local_environment, tmp_path):
    from io import StringIO

    from django.core.management import call_command
    path = tmp_path / 'e2e.env'
    path.write_text('KEEP_SETTING=unchanged\n')
    path.chmod(0o600)
    output = StringIO()
    call_command('provision_local_files_account', database=connection.settings_dict['NAME'],
                 credentials_file=str(path), stdout=output)
    before = path.read_bytes()
    assert b'KEEP_SETTING=unchanged\n' in before
    assert path.stat().st_mode & 0o777 == 0o600
    values = dict(line.split('=', 1) for line in path.read_text().splitlines())
    assert len(values['E2E_PASSWORD']) >= 20
    assert values['E2E_PASSWORD'] not in output.getvalue()
    call_command('provision_local_files_account', database=connection.settings_dict['NAME'],
                 credentials_file=str(path), stdout=output)
    assert path.read_bytes() == before
    assert User.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_command_rejects_nonprivate_file_before_database_write(local_environment, tmp_path):
    from django.core.management import call_command
    from django.core.management.base import CommandError
    path = tmp_path / 'e2e.env'
    path.touch(mode=0o644)
    with pytest.raises(CommandError, match='LOCAL_PRIVATE_CREDENTIAL_FILE_REQUIRED'):
        call_command('provision_local_files_account', database=connection.settings_dict['NAME'],
                     credentials_file=str(path))
    assert not Role.objects.filter(name='local_files_e2e').exists()


@pytest.mark.django_db(transaction=True)
def test_failed_identity_creation_rolls_back_role_and_registry(local_environment, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('injected audit failure')
    from apps.users.services import local_files_provisioning
    monkeypatch.setattr(local_files_provisioning, '_record_creation', fail)
    with pytest.raises(RuntimeError, match='injected audit failure'):
        provision()
    assert User.objects.count() == 0
    assert IamTenantRegistryEntry.objects.count() == 0
    assert not Role.objects.filter(name='local_files_e2e').exists()


@pytest.mark.django_db(transaction=True)
def test_membership_is_invisible_in_another_tenant(local_environment):
    result = provision()
    from apps.users.repositories.membership_repository import MembershipRepository
    user = User.objects.get(pk=result.user_id)
    other_tenant = uuid.uuid4()
    MembershipRepository.ensure_foundation(
        tenant_id=other_tenant, identity_id=user.identity_id,
        legacy_actor_user_id=user.pk, display_name='Isolated negative fixture',
    )
    with connection.cursor() as cursor:
        cursor.execute('SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user')
        assert cursor.fetchone() == (False, False)
    with tenant_rls_scope(other_tenant):
        assert not TenantMembership.objects.filter(tenant_id=result.tenant_id).exists()
        assert TenantMembership.objects.filter(tenant_id=other_tenant).count() == 1
    with tenant_rls_scope(result.tenant_id):
        assert not TenantMembership.objects.filter(tenant_id=other_tenant).exists()


@pytest.mark.django_db(transaction=True)
def test_operator_cannot_be_fabricated_through_user_service(local_environment):
    from apps.users.services.user_admin_service import create_user
    with pytest.raises(ValidationError, match='LOCAL_OPERATOR_BOUNDARY_REQUIRED'):
        create_user(tenant_id=uuid.uuid4(), email='fake@example.test', full_name='Fake',
                    roles=[], actor_id=None, operator_ref='local-cli:uid:1000')
    assert User.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_repeat_rejects_missing_binding_without_repair(local_environment):
    result = provision()
    before = User.objects.get(pk=result.user_id).password
    with tenant_rls_scope(result.tenant_id):
        RoleBinding.objects.filter(tenant_id=result.tenant_id).update(is_active=False)
    with pytest.raises(ValidationError, match='LOCAL_ACCOUNT_CONFLICT'):
        provision()
    assert User.objects.get(pk=result.user_id).password == before
    with tenant_rls_scope(result.tenant_id):
        assert not RoleBinding.objects.filter(tenant_id=result.tenant_id, is_active=True).exists()
