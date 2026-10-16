from __future__ import annotations

import json
import os
import secrets
from io import StringIO

import pytest
from django.conf import settings
from django.core import management
from django.core.management.base import CommandError
from django.db import connection, transaction

from apps.common.models.rbac import Permission, Role
from apps.common.tenancy.context import tenant_rls_scope
from apps.users.services import local_files_provisioning
from apps.users.services.local_files_provisioning import require_local_environment

pytestmark = pytest.mark.django_db(transaction=True)


def _password() -> str:
    return secrets.token_urlsafe(24)


@pytest.fixture(autouse=True)
def seeded_roles():
    for action in ("read", "create", "delete"):
        Permission.objects.get_or_create(resource="files", action=action)
    for name in ("director", "manager", "accountant", "foreman"):
        Role.objects.get_or_create(name=name)


def _call(monkeypatch):
    monkeypatch.setenv("COMPLECT_LOCAL_TEST_PASSWORD", _password())
    monkeypatch.setattr(local_files_provisioning, "require_local_environment", lambda _database: None)
    output = StringIO()
    management.call_command("create_test_users", database="disposable-test-db", stdout=output)
    return output.getvalue()


def test_command_requires_managed_local_database(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(CommandError, match="LOCAL_ENVIRONMENT_REQUIRED"):
        management.call_command("create_test_users", database=connection.settings_dict["NAME"])


def test_command_uses_generated_disposable_password(monkeypatch):
    monkeypatch.setenv("COMPLECT_LOCAL_TEST_PASSWORD", _password())
    monkeypatch.setattr(local_files_provisioning, "require_local_environment", lambda _database: None)
    output = StringIO()
    with pytest.raises(CommandError, match="LOCAL_TEST_USER_PROVISIONING_FAILED"):
        management.call_command("create_test_users", database="disposable-test-db", stdout=output)


def test_authorized_call_provisions_all_fixed_users(monkeypatch):
    output = _call(monkeypatch)
    assert "Created: 4" in output


def test_public_create_user_without_operator_context_is_rejected():
    import uuid

    from apps.users.services.user_admin_service import create_user
    with pytest.raises(Exception, match="LOCAL_OPERATOR_BOUNDARY_REQUIRED"):
        create_user(tenant_id=uuid.uuid4(), email="unauthorized@example.test", full_name="Unauthorized",
                    roles=[], actor_id=None, operator_ref=f"local-cli:uid:{os.getuid()}",
                    initial_password=_password())


def test_nonlocal_environment_is_rejected_without_side_effects(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(CommandError, match="LOCAL_ENVIRONMENT_REQUIRED"):
        management.call_command("create_test_users", database="disposable-test-db")


def test_real_local_environment_guard_accepts_disposable_restricted_database(monkeypatch, tmp_path):
    state_dir = tmp_path / "dev-state"
    state_dir.mkdir()
    (state_dir / "schema.json").write_text(json.dumps({"state": "ready"}))
    monkeypatch.setenv("DEV_STATE_DIR", str(state_dir))
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.local")
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(settings, "SETTINGS_MODULE", "config.settings.local", raising=False)
    require_local_environment(connection.settings_dict["NAME"])


def test_repeat_preserves_existing_identity_and_roles(monkeypatch):
    first = _call(monkeypatch)
    second = _call(monkeypatch)
    assert "Created: 4" in first
    assert "Existing: 4" in second


def test_username_email_conflict_is_rejected_before_any_write(monkeypatch):
    import uuid

    from django.contrib.auth.hashers import make_password

    from apps.users.models import User
    from apps.users.repositories.identity_creation_repository import create_identity
    with transaction.atomic():
        create_identity(username="dir_test", email="dir_test@test.com", password=make_password(_password()),
                        identity_id=uuid.uuid4(), normalized_email="dir_test@test.com",
                        email_normalization_version=1, security_version=1,
                        security_status=User.SecurityStatus.MFA_SETUP_REQUIRED, identity_legal_hold=False)
    with pytest.raises(CommandError, match="FIXED_TEST_USER_CONFLICT:dir_test"):
        _call(monkeypatch)


def test_existing_identity_in_other_tenant_is_not_replayed_into_public_site(monkeypatch):
    import uuid

    from django.contrib.auth.hashers import make_password

    from apps.users.models import TenantMembership, User
    from apps.users.repositories.identity_creation_repository import create_identity
    from apps.users.repositories.membership_repository import MembershipRepository

    foreign_tenant = uuid.uuid4()
    with transaction.atomic():
        identity = create_identity(
            username="dir_test",
            email="dir_test@test.com",
            password=make_password(_password()),
            tenant_id=foreign_tenant,
            identity_id=uuid.uuid4(),
            normalized_email="dir_test@test.com",
            email_normalization_version=1,
            security_version=1,
            security_status=User.SecurityStatus.MFA_SETUP_REQUIRED,
            identity_legal_hold=False,
        )
    MembershipRepository.ensure_foundation(
        tenant_id=foreign_tenant,
        identity_id=identity.identity_id,
        legacy_actor_user_id=identity.pk,
        display_name="Foreign tenant fixture",
    )

    with pytest.raises(CommandError, match="FIXED_TEST_USER_CONFLICT:dir_test"):
        _call(monkeypatch)

    assert User.objects.filter(username="dir_test").count() == 1
    with tenant_rls_scope(foreign_tenant):
        assert TenantMembership.objects.filter(
            tenant_id=foreign_tenant,
            identity_id=identity.identity_id,
            legacy_actor_user_id=identity.pk,
        ).count() == 1


def test_late_service_failure_rolls_back_the_whole_batch(monkeypatch):
    original_create = local_files_provisioning._create_account
    calls = 0

    def fail_on_third(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("synthetic late provisioning failure")
        return original_create(*args, **kwargs)

    monkeypatch.setattr(local_files_provisioning, "_create_account", fail_on_third)
    with pytest.raises(CommandError):
        _call(monkeypatch)
    assert not local_files_provisioning.User.objects.filter(
        username__in=[row[0] for row in local_files_provisioning._TEST_USERS],
    ).exists()


def test_unsupported_force_option_is_rejected():
    with pytest.raises(TypeError, match="Unknown option.*force"):
        management.call_command("create_test_users", force=True, database="disposable-test-db")
