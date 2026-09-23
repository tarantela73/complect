"""Inactive DB foundation for the proposed durable cleanup admission protocol.

No worker, hold writer or authority entrypoint creates these records yet.
"""

import uuid

from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models


class IdentityCleanupAdmission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cleanup = models.ForeignKey("users.IdentityMergeRedisCleanupRecord", on_delete=models.PROTECT, db_index=False)
    operation = models.ForeignKey("users.IdentityMergeOperation", on_delete=models.PROTECT, db_index=False)
    expected_cleanup_version = models.PositiveBigIntegerField()
    expected_operation_version = models.PositiveBigIntegerField()
    identity_ids = ArrayField(models.UUIDField())
    actor_pks = ArrayField(models.PositiveBigIntegerField())
    tenant_ids = ArrayField(models.UUIDField(), default=list)
    owner_incarnation = models.UUIDField()
    owner_hash = models.CharField(max_length=64)
    protocol_version = models.PositiveSmallIntegerField(default=1)
    build_id = models.CharField(max_length=128)
    resource_epoch = models.CharField(max_length=128)
    payload_digest = models.CharField(max_length=64, default="", editable=False)
    owner_proof = models.CharField(max_length=64, null=True, editable=False)
    receipt = models.JSONField(null=True, editable=False)
    receipt_digest = models.CharField(max_length=64, blank=True, default="")
    outcome = models.CharField(max_length=32, blank=True, default="")
    state = models.CharField(max_length=16, default="OPEN")
    version = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "users_identity_cleanup_admissions"
        constraints = [
            models.UniqueConstraint(
                fields=["cleanup"], condition=~models.Q(state="TERMINAL"), name="uniq_cleanup_live_owner"
            )
        ]
        indexes = [
            models.Index(fields=["cleanup"], name="cleanup_admission_cleanup_idx"),
            models.Index(fields=["operation"], name="cleanup_admission_op_idx"),
            GinIndex(fields=["identity_ids"], condition=~models.Q(state="TERMINAL"), name="cleanup_live_identity_gin"),
            GinIndex(fields=["actor_pks"], condition=~models.Q(state="TERMINAL"), name="cleanup_live_actor_gin"),
            GinIndex(fields=["tenant_ids"], condition=~models.Q(state="TERMINAL"), name="cleanup_live_tenant_gin"),
        ]


class IdentityHoldRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    idempotency_key = models.UUIDField()
    identity_ids = ArrayField(models.UUIDField(), default=list)
    actor_pks = ArrayField(models.PositiveBigIntegerField(), default=list)
    tenant_ids = ArrayField(models.UUIDField(), default=list)
    targets = models.JSONField()
    requester_reference = models.CharField(max_length=128)
    reason_code = models.CharField(max_length=64)
    request_id = models.UUIDField()
    correlation_id = models.UUIDField()
    payload_digest = models.CharField(max_length=64, default="", editable=False)
    release_evidence = models.JSONField(null=True, editable=False)
    state = models.CharField(max_length=16, default="REQUESTED")
    version = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "users_identity_hold_requests"
        constraints = [models.UniqueConstraint(fields=["idempotency_key"], name="uniq_hold_request_idempotency")]
        indexes = [
            GinIndex(fields=["identity_ids"], name="hold_request_identity_gin"),
            GinIndex(fields=["actor_pks"], name="hold_request_actor_gin"),
            GinIndex(fields=["tenant_ids"], name="hold_request_tenant_gin"),
        ]


class IdentityCleanupLifecycleEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    admission = models.ForeignKey(IdentityCleanupAdmission, on_delete=models.PROTECT, db_index=False)
    version = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=32)
    evidence = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "users_identity_cleanup_lifecycle"
        constraints = [models.UniqueConstraint(fields=["admission", "version"], name="uniq_cleanup_lifecycle_version")]


class IdentityCleanupAdmissionSubject(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    admission = models.ForeignKey(IdentityCleanupAdmission, on_delete=models.PROTECT, db_index=False)
    kind = models.CharField(max_length=8)
    value = models.CharField(max_length=36)

    class Meta:
        db_table = "users_identity_cleanup_admission_subjects"
        constraints = [
            models.UniqueConstraint(fields=["admission", "kind", "value"], name="uniq_cleanup_admission_subject")
        ]


class IdentityHoldRequestSubject(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(IdentityHoldRequest, on_delete=models.PROTECT, db_index=False)
    kind = models.CharField(max_length=8)
    value = models.CharField(max_length=36)

    class Meta:
        db_table = "users_identity_hold_request_subjects"
        constraints = [models.UniqueConstraint(fields=["request", "kind", "value"], name="uniq_hold_request_subject")]


class IdentityHoldLifecycleEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(IdentityHoldRequest, on_delete=models.PROTECT, db_index=False)
    version = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=32)
    evidence = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "users_identity_hold_lifecycle"
        constraints = [models.UniqueConstraint(fields=["request", "version"], name="uniq_hold_lifecycle_version")]
