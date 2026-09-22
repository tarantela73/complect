from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class SecurityStatus(models.TextChoices):
        PENDING_CREDENTIALS = "pending_credentials"
        MFA_SETUP_REQUIRED = "mfa_setup_required"
        ACTIVE = "active"
        LOCKED = "locked"

    tenant_id = models.UUIDField(db_index=True, null=True, blank=True)

    # Release N expand fields.  They deliberately remain nullable, unindexed
    # and non-unique until reconciliation/backfill and the N+2 contract gate.
    identity_id = models.UUIDField(null=True, blank=True, editable=False)
    normalized_email = models.EmailField(max_length=254, null=True, blank=True, editable=False)
    email_normalization_version = models.PositiveSmallIntegerField(null=True, editable=False)
    security_version = models.PositiveBigIntegerField(null=True, editable=False)
    security_status = models.CharField(
        max_length=32,
        choices=SecurityStatus.choices,
        null=True,
        editable=False,
    )
    identity_legal_hold = models.BooleanField(null=True, editable=False)

    mfa_enabled = models.BooleanField(default=False)
    mfa_secret = models.CharField(max_length=64, blank=True, default="")

    device_fingerprint = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        db_table = "users_users"
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"
        indexes = [
            models.Index(fields=["tenant_id"], name="idx_users_tenant"),
            models.Index(fields=["tenant_id", "username"], name="idx_users_tenant_username"),
        ]


# Keep the long-standing `apps.users.models` import surface stable while the
# implementation is split into global and tenant-scoped IAM modules.
from apps.users.models_cleanup_protocol import (  # noqa: E402, F401
    IdentityCleanupAdmission,
    IdentityCleanupAdmissionSubject,
    IdentityCleanupLifecycleEvent,
    IdentityHoldLifecycleEvent,
    IdentityHoldRequest,
    IdentityHoldRequestSubject,
)
from apps.users.models_identity import (  # noqa: E402, F401
    BackupCode,
    IamTenantRegistryEntry,
    IdentityMergeHoldBlock,
    IdentityMergeOperation,
    IdentityMergeRedisCleanupRecord,
    IdentityMergeRequeue,
    IdentityMergeRequeueResult,
    IdentitySecurityAuditRecord,
    IdentitySession,
    MfaCredential,
    PreAuthChallenge,
    RecoveryOperation,
    RecoveryToken,
    SessionReconciliationRecord,
    TenantGrantSwitchOperation,
)
from apps.users.models_membership import (  # noqa: E402, F401
    TenantAuthorizationState,
    TenantMembership,
    TenantSessionGrant,
)
