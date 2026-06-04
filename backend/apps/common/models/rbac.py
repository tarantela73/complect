import uuid

from django.db import models


class Permission(models.Model):
    """
    System-defined permission. Seeded once, never created by users.

    Keyed by (action, resource) — the canonical permission identifier used
    throughout PolicyEngine. action/resource strings are stable identifiers;
    changing them is a breaking change to all role assignments.
    """

    action = models.CharField(max_length=64)
    resource = models.CharField(max_length=64)

    class Meta:
        db_table = "common_rbac_permissions"
        unique_together = [("action", "resource")]
        indexes = [
            models.Index(fields=["action", "resource"], name="rbac_perm_action_resource_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.action}:{self.resource}"


class Role(models.Model):
    """
    System-defined role. Not tenant-scoped — tenant isolation is enforced at RoleBinding level.
    """

    name = models.CharField(max_length=64, unique=True)
    description = models.CharField(max_length=255, blank=True, default="")
    permissions = models.ManyToManyField(
        Permission,
        through="RolePermission",
        related_name="roles",
    )

    class Meta:
        db_table = "common_rbac_roles"
        indexes = [
            models.Index(fields=["name"], name="rbac_role_name_idx"),
        ]

    def __str__(self) -> str:
        return self.name


class RolePermission(models.Model):
    """Through table — Role ↔ Permission."""

    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_permissions")
    permission = models.ForeignKey(Permission, on_delete=models.CASCADE, related_name="role_permissions")

    class Meta:
        db_table = "common_rbac_role_permissions"
        unique_together = [("role", "permission")]


class RoleBinding(models.Model):
    """
    Tenant-scoped assignment of a Role to a user.

    A user can have multiple roles within the same tenant.
    The PolicyEngine reads all active bindings for (user_id, tenant_id)
    and unions their permission sets.

    is_active=False: soft revocation without deletion (audit trail preserved).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(db_index=True, editable=False)
    user_id = models.UUIDField(db_index=True, editable=False)
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="bindings")
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    granted_by = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "common_rbac_role_bindings"
        unique_together = [("tenant_id", "user_id", "role")]
        indexes = [
            models.Index(fields=["tenant_id", "user_id", "is_active"], name="rbac_binding_lookup_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}@{self.tenant_id} → {self.role}"
