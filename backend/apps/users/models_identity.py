import uuid

from django.conf import settings
from django.db import models


class IamTenantRegistryEntry(models.Model):
    tenant_id = models.UUIDField(primary_key=True, editable=False)
    registered_at = models.DateTimeField(auto_now_add=True)
    version = models.PositiveBigIntegerField(default=1, editable=False)
    source_code = models.CharField(max_length=32, editable=False)

    class Meta:
        db_table = "users_iam_tenant_registry"


class GlobalSecurityRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.PositiveBigIntegerField(default=1, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_legal_hold = models.BooleanField(default=False, db_index=True)

    class Meta:
        abstract = True


class MergeHoldSubjectType(models.TextChoices):
    OPERATION = "operation"
    SOURCE_IDENTITY = "source_identity"
    CANONICAL_IDENTITY = "canonical_identity"
    MEMBERSHIP = "membership"
    SESSION = "session"
    GRANT = "grant"


class MergeRequeueOutcome(models.TextChoices):
    APPLIED = "applied"
    HOLD_STILL_ACTIVE = "hold_still_active"
    VERSION_CONFLICT = "version_conflict"
    INVALID_RELEASE_EVIDENCE = "invalid_release_evidence"


class MergeActionKind(models.TextChoices):
    OWNER_CONVERGENCE = "owner_convergence"
    DORMANT_CONVERGENCE = "dormant_convergence"
    EXPIRE_PENDING = "expire_pending"
    CANCEL_PENDING = "cancel_pending"
    MFA_SETUP = "mfa_setup"
    MFA_CONFIRM = "mfa_confirm"
    REDIS_CLEANUP = "redis_cleanup"


class MergeResumeMode(models.TextChoices):
    AUTOMATIC = "automatic"
    OWNER_AUTHENTICATED = "owner_authenticated"
    OPERATOR_REEXECUTE = "operator_reexecute"


class IdentityMergeOperation(GlobalSecurityRecord):
    class State(models.TextChoices):
        APPROVED_PENDING_CONVERGENCE = "approved_pending_convergence"
        APPROVED_DEFERRED = "approved_deferred"
        READY_TO_FINALIZE = "ready_to_finalize"
        FINALIZED = "finalized"
        CANCELLED = "cancelled"
        EXPIRED = "expired"

    class ConvergenceMode(models.TextChoices):
        OWNER_CONFIRMED = "owner_confirmed"
        DORMANT_FAIL_CLOSED = "dormant_fail_closed"

    source_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="source_merge_operations",
    )
    source_identity_id = models.UUIDField()
    canonical_identity_id = models.UUIDField(db_index=True)
    state = models.CharField(max_length=40, choices=State.choices)
    convergence_mode = models.CharField(max_length=32, choices=ConvergenceMode.choices, null=True)
    approval_reference = models.CharField(max_length=128)
    approval_digest = models.CharField(max_length=64)
    approval_key_id = models.CharField(max_length=64)
    approval_signature = models.BinaryField()
    approved_at = models.DateTimeField()
    approval_expires_at = models.DateTimeField()
    synthetic_normalized_email = models.EmailField(max_length=254)
    credential_epoch = models.PositiveBigIntegerField(default=0)
    credentials_changed_at = models.DateTimeField(null=True)
    credentials_converged_at = models.DateTimeField(null=True)
    finalized_at = models.DateTimeField(null=True)
    retention_until = models.DateTimeField()
    legal_hold_reference = models.CharField(max_length=128, blank=True, default="")
    legal_hold_applied_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "users_identity_merge_operations"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(source_identity_id=models.F("canonical_identity_id")),
                name="ck_users_merge_source_not_canonical",
            ),
            models.UniqueConstraint(
                fields=["source_user"],
                condition=~models.Q(state__in=["cancelled", "expired"]),
                name="uniq_users_live_merge_source",
            ),
            models.UniqueConstraint(
                fields=["canonical_identity_id"],
                condition=models.Q(state="approved_pending_convergence"),
                name="uniq_users_pending_merge_per_canonical",
            ),
            models.UniqueConstraint(
                fields=["synthetic_normalized_email"],
                condition=~models.Q(state__in=["cancelled", "expired"]),
                name="uniq_users_live_merge_synthetic_email",
            ),
        ]


class IdentityMergeHoldBlock(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(IdentityMergeOperation, on_delete=models.PROTECT)
    subject_type = models.CharField(max_length=32, choices=MergeHoldSubjectType.choices)
    subject_identity_id = models.UUIDField()
    hold_reference = models.CharField(max_length=128)
    hold_digest = models.CharField(max_length=64)
    action_digest = models.CharField(max_length=64)
    action_kind = models.CharField(
        max_length=32,
        choices=MergeActionKind.choices,
        default=MergeActionKind.OWNER_CONVERGENCE,
    )
    lock_plan_digest = models.CharField(max_length=64)
    hold_set_digest = models.CharField(max_length=64)
    operation_version_snapshot = models.PositiveBigIntegerField()
    request_id = models.UUIDField()
    correlation_id = models.UUIDField()
    observed_at = models.DateTimeField()

    class Meta:
        db_table = "users_identity_merge_hold_blocks"
        constraints = [
            models.UniqueConstraint(
                fields=["operation", "subject_type", "subject_identity_id", "hold_digest"],
                name="uniq_users_merge_hold_observation",
            ),
        ]


class IdentityMergeRequeue(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation = models.ForeignKey(IdentityMergeOperation, on_delete=models.PROTECT)
    hold_block = models.ForeignKey(IdentityMergeHoldBlock, on_delete=models.PROTECT)
    prior_action_digest = models.CharField(max_length=64)
    fresh_action_digest = models.CharField(max_length=64)
    action_kind = models.CharField(
        max_length=32,
        choices=MergeActionKind.choices,
        default=MergeActionKind.OWNER_CONVERGENCE,
    )
    resume_mode = models.CharField(
        max_length=32,
        choices=MergeResumeMode.choices,
        default=MergeResumeMode.OWNER_AUTHENTICATED,
    )
    action_schema_version = models.PositiveSmallIntegerField()
    environment = models.CharField(max_length=32)
    execution_build_digest = models.CharField(max_length=64)
    approval_reference = models.CharField(max_length=128)
    key_id = models.CharField(max_length=64)
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    signature = models.BinaryField()
    expected_operation_version = models.PositiveBigIntegerField()
    requested_by = models.CharField(max_length=128)
    request_id = models.UUIDField()
    correlation_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "users_identity_merge_requeues"
        constraints = [
            models.UniqueConstraint(
                fields=["hold_block", "fresh_action_digest"],
                name="uniq_users_merge_release_requeue",
            ),
        ]


class IdentityMergeRequeueResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requeue = models.OneToOneField(IdentityMergeRequeue, on_delete=models.PROTECT)
    outcome = models.CharField(max_length=32, choices=MergeRequeueOutcome.choices)
    observed_operation_version = models.PositiveBigIntegerField()
    processed_at = models.DateTimeField()

    class Meta:
        db_table = "users_identity_merge_requeue_results"


class IdentityMergeRedisCleanupRecord(GlobalSecurityRecord):
    class Status(models.TextChoices):
        PENDING = "pending"
        COMPLETED = "completed"

    operation = models.OneToOneField(IdentityMergeOperation, on_delete=models.PROTECT)
    actor_user_pks = models.JSONField(default=list)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveIntegerField(default=0)
    last_failure_code = models.CharField(max_length=64, blank=True, default="")
    completed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "users_identity_merge_redis_cleanup"


class PreAuthChallenge(GlobalSecurityRecord):
    class FlowKind(models.TextChoices):
        MFA_VERIFY = "mfa_verify"
        MFA_SETUP = "mfa_setup"
        MERGE_CONVERGENCE = "merge_convergence"

    identity_id = models.UUIDField(db_index=True)
    flow_kind = models.CharField(max_length=32, choices=FlowKind.choices)
    token_digest = models.CharField(max_length=64, unique=True)
    security_version_snapshot = models.PositiveBigIntegerField()
    merge_operation = models.ForeignKey(IdentityMergeOperation, on_delete=models.PROTECT, null=True)
    merge_operation_version_snapshot = models.PositiveBigIntegerField(null=True)
    canonical_security_version_snapshot = models.PositiveBigIntegerField(null=True)
    idempotency_key = models.UUIDField(null=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    mfa_verified_at = models.DateTimeField(null=True)
    consumed_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "users_pre_auth_challenges"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        flow_kind="merge_convergence",
                        merge_operation__isnull=False,
                        merge_operation_version_snapshot__isnull=False,
                        canonical_security_version_snapshot__isnull=False,
                        idempotency_key__isnull=False,
                    )
                    | models.Q(
                        flow_kind__in=["mfa_verify", "mfa_setup"],
                        merge_operation__isnull=True,
                        merge_operation_version_snapshot__isnull=True,
                        canonical_security_version_snapshot__isnull=True,
                        idempotency_key__isnull=True,
                    )
                ),
                name="ck_users_preauth_merge_binding",
            ),
            models.UniqueConstraint(
                fields=["merge_operation", "idempotency_key"],
                condition=models.Q(flow_kind="merge_convergence"),
                name="uniq_users_merge_convergence_challenge_key",
            ),
        ]


class IdentitySession(GlobalSecurityRecord):
    identity_id = models.UUIDField(db_index=True)
    session_family_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    security_version_snapshot = models.PositiveBigIntegerField()
    device_metadata = models.JSONField(default=dict)
    refresh_generation = models.PositiveBigIntegerField(default=0)
    current_refresh_digest = models.CharField(max_length=64, blank=True, default="")
    refresh_key_id = models.CharField(max_length=64, blank=True, default="")
    revoked_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "users_identity_sessions"


class MfaCredential(GlobalSecurityRecord):
    identity_id = models.UUIDField(db_index=True)
    setup_challenge = models.OneToOneField(
        PreAuthChallenge,
        null=True,
        on_delete=models.PROTECT,
        related_name="pending_mfa_credential",
    )
    credential_type = models.CharField(max_length=16, default="totp")
    encrypted_secret = models.BinaryField()
    nonce = models.BinaryField()
    key_id = models.CharField(max_length=64)
    confirmed_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "users_mfa_credentials"
        constraints = [
            models.UniqueConstraint(
                fields=["identity_id"],
                condition=models.Q(credential_type="totp", revoked_at__isnull=True),
                name="uniq_users_unrevoked_totp_per_identity",
            ),
        ]


class BackupCode(GlobalSecurityRecord):
    identity_id = models.UUIDField(db_index=True)
    code_hash = models.CharField(max_length=64, unique=True)
    key_id = models.CharField(max_length=64)
    used_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "users_backup_codes"


class RecoveryOperation(GlobalSecurityRecord):
    class Status(models.TextChoices):
        PENDING_DELIVERY = "pending_delivery"
        DELIVERING = "delivering"
        DELIVERED = "delivered"
        COMPLETED = "completed"
        SUPERSEDED = "superseded"
        EXPIRED = "expired"
        FAILED = "failed"

    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING_DELIVERY)
    delivery_ciphertext = models.BinaryField(null=True)
    delivery_nonce = models.BinaryField(null=True)
    delivery_key_id = models.CharField(max_length=64, blank=True, default="")
    delivery_attempt_id = models.UUIDField(null=True, unique=True)
    delivery_lease_expires_at = models.DateTimeField(null=True)
    delivery_attempt_count = models.PositiveIntegerField(default=0)
    provider_message_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    request_id = models.UUIDField()
    correlation_id = models.UUIDField(db_index=True)
    causal_root_id = models.UUIDField(db_index=True)
    delivered_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "users_recovery_operations"
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=["pending_delivery", "delivering"],
                        delivery_ciphertext__isnull=False,
                        delivery_nonce__isnull=False,
                    )
                    & ~models.Q(delivery_key_id="")
                    | models.Q(
                        status__in=["delivered", "completed", "superseded", "expired", "failed"],
                        delivery_ciphertext__isnull=True,
                        delivery_nonce__isnull=True,
                        delivery_key_id="",
                    )
                ),
                name="ck_users_recovery_operation_envelope_state",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="completed", delivered_at__isnull=False, completed_at__isnull=False)
                    | (~models.Q(status="completed") & models.Q(completed_at__isnull=True))
                ),
                name="ck_users_recovery_operation_completion_state",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="delivering",
                        delivery_attempt_id__isnull=False,
                        delivery_lease_expires_at__isnull=False,
                    )
                    | (
                        ~models.Q(status="delivering")
                        & models.Q(delivery_attempt_id__isnull=True, delivery_lease_expires_at__isnull=True)
                    )
                ),
                name="ck_users_recovery_operation_lease_state",
            ),
            models.CheckConstraint(
                condition=(~models.Q(status="delivered") | models.Q(delivered_at__isnull=False)),
                name="ck_users_recovery_operation_delivered_state",
            ),
        ]


class RecoveryToken(GlobalSecurityRecord):
    class Purpose(models.TextChoices):
        PASSWORD_RESET = "password_reset"
        MFA_RESET = "mfa_reset"

    identity_id = models.UUIDField(db_index=True)
    operation = models.OneToOneField(RecoveryOperation, on_delete=models.PROTECT, related_name="token")
    verifier_hash = models.CharField(max_length=64, unique=True)
    key_id = models.CharField(max_length=64)
    purpose = models.CharField(max_length=32, choices=Purpose.choices)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "users_recovery_tokens"
        constraints = [
            models.UniqueConstraint(
                fields=["identity_id", "purpose"],
                condition=models.Q(consumed_at__isnull=True, revoked_at__isnull=True),
                name="uniq_users_open_recovery_per_identity_purpose",
            ),
            models.CheckConstraint(
                condition=models.Q(purpose__in=["password_reset", "mfa_reset"]),
                name="ck_users_recovery_token_purpose",
            ),
            models.CheckConstraint(
                condition=models.Q(consumed_at__isnull=True) | models.Q(revoked_at__isnull=True),
                name="ck_users_recovery_not_consumed_and_revoked",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("created_at")),
                name="ck_users_recovery_expiry_after_issue",
            ),
        ]


class TenantGrantSwitchOperation(GlobalSecurityRecord):
    class Status(models.TextChoices):
        PENDING = "pending"
        DB_COMMITTED = "db_committed"
        COMPLETED = "completed"
        FAILED = "failed"

    operation_id = models.UUIDField(unique=True, db_index=True)
    session = models.ForeignKey(IdentitySession, on_delete=models.PROTECT, related_name="switch_operations")
    request_fingerprint = models.CharField(max_length=64)
    expected_current_grant_id = models.UUIDField(null=True)
    expected_current_tenant_id = models.UUIDField(null=True)
    expected_context_version = models.PositiveBigIntegerField(null=True)
    target_tenant_id = models.UUIDField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    result_grant_id = models.UUIDField(null=True)
    result_tenant_id = models.UUIDField(null=True)
    result_context_version = models.PositiveBigIntegerField(null=True)
    expected_refresh_generation = models.PositiveBigIntegerField(null=True)
    expected_refresh_digest = models.CharField(max_length=64, blank=True, default="")
    result_refresh_generation = models.PositiveBigIntegerField(null=True)
    result_refresh_digest = models.CharField(max_length=64, blank=True, default="")
    response_ciphertext = models.BinaryField(null=True)
    response_nonce = models.BinaryField(null=True)
    response_key_id = models.CharField(max_length=64, blank=True, default="")
    response_expires_at = models.DateTimeField(null=True, db_index=True)
    failure_code = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "users_tenant_grant_switch_operations"


class SessionReconciliationRecord(GlobalSecurityRecord):
    class Kind(models.TextChoices):
        REFRESH_ROTATION = "refresh_rotation"
        TENANT_SWITCH = "tenant_switch"
        SESSION_FAMILY_CLEANUP = "session_family_cleanup"

    session = models.ForeignKey(IdentitySession, on_delete=models.PROTECT, related_name="reconciliation_records")
    kind = models.CharField(max_length=32, choices=Kind.choices)
    operation_id = models.UUIDField(db_index=True)
    switch_operation = models.OneToOneField(
        TenantGrantSwitchOperation,
        null=True,
        to_field="operation_id",
        db_column="switch_operation_id",
        on_delete=models.PROTECT,
        related_name="reconciliation_record",
    )
    expected_db_state = models.JSONField()
    expected_redis_state = models.JSONField()
    failure_reason = models.CharField(max_length=128)
    attempt_count = models.PositiveIntegerField(default=0)
    resolved_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "users_session_reconciliation_records"
        constraints = [
            models.UniqueConstraint(
                fields=["session", "kind", "operation_id"],
                name="uniq_users_session_reconciliation_kind_operation",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind="tenant_switch",
                        switch_operation__isnull=False,
                        operation_id=models.F("switch_operation_id"),
                    )
                    | models.Q(
                        kind__in=["refresh_rotation", "session_family_cleanup"],
                        switch_operation__isnull=True,
                    )
                ),
                name="ck_users_reconciliation_switch_binding",
            ),
        ]


class IdentitySecurityAuditRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    identity_id = models.UUIDField(null=True, db_index=True)
    session_id = models.UUIDField(null=True, db_index=True)
    event_type = models.CharField(max_length=128, db_index=True)
    actor_type = models.CharField(max_length=32)
    actor_ref = models.CharField(max_length=128)
    reason_code = models.CharField(max_length=64)
    subject_hmac = models.CharField(max_length=64, blank=True, default="")
    request_id = models.UUIDField()
    correlation_id = models.UUIDField(db_index=True)
    metadata = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(db_index=True)
    is_legal_hold = models.BooleanField(default=False, db_index=True)

    class Meta:
        db_table = "users_identity_security_audit"
