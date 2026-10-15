from django.conf import settings
from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class TenantMembership(BaseEnterpriseModel):
    class Status(models.TextChoices):
        DRAFT = "draft"
        INVITED = "invited"
        ACTIVE = "active"
        DISABLED = "disabled"

    identity_id = models.UUIDField(db_index=True)
    legacy_actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="legacy_actor_memberships",
        null=True,
    )
    display_name = models.CharField(max_length=255)
    position = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, db_index=True)
    authz_version = models.PositiveBigIntegerField(default=1)

    class Meta:
        db_table = "users_tenant_memberships"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "identity_id"],
                condition=models.Q(is_deleted=False),
                name="uniq_users_active_membership",
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "legacy_actor_user"],
                condition=models.Q(is_deleted=False),
                name="uniq_users_active_membership_actor",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["draft", "invited", "active", "disabled"]),
                name="ck_users_membership_status",
            ),
        ]


class TenantAuthorizationState(BaseEnterpriseModel):
    policy_version = models.PositiveBigIntegerField(default=1)

    class Meta:
        db_table = "users_tenant_authorization_state"
        constraints = [
            models.UniqueConstraint(fields=["tenant_id"], name="uniq_users_tenant_auth_state"),
            models.CheckConstraint(
                condition=models.Q(is_deleted=False),
                name="ck_users_tenant_auth_state_not_soft_deleted",
            ),
        ]


class TenantSessionGrant(BaseEnterpriseModel):
    session = models.ForeignKey(
        "users.IdentitySession",
        on_delete=models.PROTECT,
        related_name="tenant_grants",
    )
    membership = models.ForeignKey(
        TenantMembership,
        on_delete=models.PROTECT,
        related_name="session_grants",
    )
    context_version = models.PositiveBigIntegerField()
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    superseded_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "users_tenant_session_grants"
        constraints = [
            models.UniqueConstraint(
                fields=["session"],
                condition=models.Q(superseded_at__isnull=True, revoked_at__isnull=True),
                name="uniq_users_open_grant_per_session",
            ),
            models.CheckConstraint(
                condition=models.Q(is_deleted=False),
                name="ck_users_grant_not_soft_deleted",
            ),
        ]
