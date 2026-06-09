import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError

from apps.common.models.rbac import Role, RoleBinding
from apps.users.models import User
from apps.users.services.auth_service import _pk_to_uuid4

TENANT_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def seed_roles(db):
    """Re-seed roles after each transaction=True flush."""
    from apps.common.models.rbac import Permission, Role, RolePermission

    for action, resource in [
        ("read", "contracts"), ("create", "contracts"), ("write", "contracts"),
        ("read", "users"), ("create", "users"), ("write", "users"),
        ("read", "files"), ("read", "finance"),
    ]:
        Permission.objects.get_or_create(action=action, resource=resource)

    admin, _ = Role.objects.get_or_create(name="admin")
    manager, _ = Role.objects.get_or_create(name="manager")
    viewer, _ = Role.objects.get_or_create(name="viewer")

    for action, resource in [("read", "contracts"), ("create", "contracts"),
                              ("write", "contracts"), ("read", "users"),
                              ("create", "users"), ("write", "users"),
                              ("read", "files"), ("read", "finance")]:
        perm = Permission.objects.get(action=action, resource=resource)
        RolePermission.objects.get_or_create(role=admin, permission=perm)

    for action, resource in [("read", "contracts"), ("read", "users"), ("read", "files")]:
        perm = Permission.objects.get(action=action, resource=resource)
        RolePermission.objects.get_or_create(role=manager, permission=perm)
        RolePermission.objects.get_or_create(role=viewer, permission=perm)


def _make_admin_user(tenant_id=TENANT_ID) -> tuple[User, uuid.UUID]:
    user = User.objects.create_user(
        username=f"admin_{uuid.uuid4().hex[:8]}@test.com",
        email=f"admin_{uuid.uuid4().hex[:8]}@test.com",
        password="AdminPass1!",
        tenant_id=tenant_id,
        first_name="Admin User",
    )
    admin_role = Role.objects.get(name="admin")
    uuid_id = uuid.UUID(_pk_to_uuid4(user.pk))
    RoleBinding.objects.create(
        tenant_id=tenant_id,
        user_id=uuid_id,
        role=admin_role,
        granted_by=uuid_id,
    )
    return user, uuid_id


@pytest.mark.django_db(transaction=True)
class TestCreateUser:
    def test_creates_user_with_correct_tenant(self):
        from apps.users.services.user_admin_service import create_user

        admin, actor_id = _make_admin_user()
        new_user, _ = create_user(
            tenant_id=TENANT_ID,
            email="new@example.com",
            full_name="New Person",
            roles=["viewer"],
            actor_id=actor_id,
        )
        assert new_user.tenant_id == TENANT_ID
        assert new_user.email == "new@example.com"
        assert new_user.first_name == "New Person"

    def test_returns_working_temp_password(self):
        from apps.users.services.user_admin_service import create_user

        admin, actor_id = _make_admin_user()
        new_user, temp_pwd = create_user(
            tenant_id=TENANT_ID,
            email="pwd_test@example.com",
            full_name="Pwd Test",
            roles=[],
            actor_id=actor_id,
        )
        assert check_password(temp_pwd, new_user.password)

    def test_assigns_requested_roles(self):
        from apps.users.services.user_admin_service import create_user

        admin, actor_id = _make_admin_user()
        new_user, _ = create_user(
            tenant_id=TENANT_ID,
            email="roles@example.com",
            full_name="Roles User",
            roles=["manager", "viewer"],
            actor_id=actor_id,
        )
        new_uuid = uuid.UUID(_pk_to_uuid4(new_user.pk))
        bound_roles = set(
            RoleBinding.objects.filter(
                tenant_id=TENANT_ID,
                user_id=new_uuid,
                is_active=True,
            ).values_list("role__name", flat=True)
        )
        assert bound_roles == {"manager", "viewer"}

    def test_duplicate_email_raises(self):
        from apps.users.services.user_admin_service import create_user

        admin, actor_id = _make_admin_user()
        create_user(
            tenant_id=TENANT_ID,
            email="dup@example.com",
            full_name="First",
            roles=[],
            actor_id=actor_id,
        )
        with pytest.raises(ValidationError, match="EMAIL_ALREADY_EXISTS"):
            create_user(
                tenant_id=TENANT_ID,
                email="dup@example.com",
                full_name="Second",
                roles=[],
                actor_id=actor_id,
            )

    def test_unknown_role_raises(self):
        from apps.users.services.user_admin_service import create_user

        admin, actor_id = _make_admin_user()
        with pytest.raises(ValidationError, match="UNKNOWN_ROLE"):
            create_user(
                tenant_id=TENANT_ID,
                email="unk@example.com",
                full_name="Unk",
                roles=["superuser"],
                actor_id=actor_id,
            )


@pytest.mark.django_db(transaction=True)
class TestDisableUser:
    def test_disables_user(self):
        from apps.users.services.user_admin_service import disable_user

        admin, actor_id = _make_admin_user()
        target = User.objects.create_user(
            username="target@example.com",
            email="target@example.com",
            password="Pass1!",
            tenant_id=TENANT_ID,
        )
        disable_user(tenant_id=TENANT_ID, user_id=target.pk, actor_id=actor_id)
        target.refresh_from_db()
        assert not target.is_active

    def test_self_protection_raises(self):
        from apps.users.services.user_admin_service import disable_user

        admin, actor_id = _make_admin_user()
        with pytest.raises(ValidationError, match="CANNOT_DISABLE_SELF"):
            disable_user(tenant_id=TENANT_ID, user_id=admin.pk, actor_id=actor_id)

    def test_last_admin_raises(self):
        from apps.users.services.user_admin_service import disable_user

        admin, _ = _make_admin_user()
        # Different actor (not self) attempting to disable the sole admin
        other_actor = uuid.uuid4()
        with pytest.raises(ValidationError, match="LAST_ADMIN_LOCK"):
            disable_user(tenant_id=TENANT_ID, user_id=admin.pk, actor_id=other_actor)


@pytest.mark.django_db(transaction=True)
class TestEnableUser:
    def test_enables_user(self):
        from apps.users.services.user_admin_service import enable_user

        admin, actor_id = _make_admin_user()
        target = User.objects.create_user(
            username="disabled@example.com",
            email="disabled@example.com",
            password="Pass1!",
            tenant_id=TENANT_ID,
            is_active=False,
        )
        enable_user(tenant_id=TENANT_ID, user_id=target.pk, actor_id=actor_id)
        target.refresh_from_db()
        assert target.is_active


@pytest.mark.django_db(transaction=True)
class TestAssignRoles:
    def test_replaces_all_roles(self):
        from apps.users.services.user_admin_service import assign_roles

        admin, actor_id = _make_admin_user()
        target = User.objects.create_user(
            username="reassign@example.com",
            email="reassign@example.com",
            password="Pass1!",
            tenant_id=TENANT_ID,
        )
        target_uuid = uuid.UUID(_pk_to_uuid4(target.pk))
        viewer_role = Role.objects.get(name="viewer")
        RoleBinding.objects.create(
            tenant_id=TENANT_ID, user_id=target_uuid, role=viewer_role, granted_by=actor_id
        )

        assign_roles(
            tenant_id=TENANT_ID,
            user_id=target.pk,
            roles=["manager"],
            actor_id=actor_id,
        )

        active = set(
            RoleBinding.objects.filter(
                tenant_id=TENANT_ID, user_id=target_uuid, is_active=True
            ).values_list("role__name", flat=True)
        )
        assert active == {"manager"}

    def test_last_admin_lock_raises(self):
        from apps.users.services.user_admin_service import assign_roles

        admin, _ = _make_admin_user()
        other_actor = uuid.uuid4()  # Different actor, not the sole admin
        with pytest.raises(ValidationError, match="LAST_ADMIN_LOCK"):
            assign_roles(
                tenant_id=TENANT_ID,
                user_id=admin.pk,
                roles=["manager"],
                actor_id=other_actor,
            )

    def test_self_admin_removal_raises(self):
        from apps.users.services.user_admin_service import assign_roles

        admin, actor_id = _make_admin_user()
        # Add a second admin so last-admin guard doesn't trigger first
        admin2, _ = _make_admin_user()

        with pytest.raises(ValidationError, match="CANNOT_REMOVE_OWN_ADMIN"):
            assign_roles(
                tenant_id=TENANT_ID,
                user_id=admin.pk,
                roles=["manager"],
                actor_id=actor_id,
            )

    def test_invalidates_policy_cache(self):
        from apps.common.security.policy_engine import PolicyEngine
        from apps.users.services.user_admin_service import assign_roles

        admin, actor_id = _make_admin_user()
        target = User.objects.create_user(
            username="cache@example.com",
            email="cache@example.com",
            password="Pass1!",
            tenant_id=TENANT_ID,
        )

        with patch.object(PolicyEngine, "invalidate") as mock_inv:
            assign_roles(
                tenant_id=TENANT_ID,
                user_id=target.pk,
                roles=["viewer"],
                actor_id=actor_id,
            )

        target_uuid = uuid.UUID(_pk_to_uuid4(target.pk))
        mock_inv.assert_called_once_with(tenant_id=TENANT_ID, actor_id=target_uuid)
