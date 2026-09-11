import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.users.models import (
    IdentityMergeHoldBlock,
    IdentityMergeRequeue,
    IdentityMergeRequeueResult,
)
from apps.users.models_identity import MergeActionKind, MergeRequeueOutcome, MergeResumeMode
from apps.users.security.reconciliation_approval import canonical_action_bytes
from apps.users.tasks.identity_merge_requeue import process_identity_merge_requeues


@pytest.mark.django_db(transaction=True)
def test_requeue_worker_writes_one_immutable_result(
    identity_merge_operation_factory,
    signed_reconciliation_approval,
    settings,
):
    operation = identity_merge_operation_factory(
        approval_expires_at=timezone.now() - timedelta(minutes=1),
    )
    digest = uuid.uuid4().hex * 2
    block = IdentityMergeHoldBlock.objects.create(
        operation=operation,
        subject_type="source_identity",
        subject_identity_id=operation.source_identity_id,
        hold_reference="released-test-hold",
        hold_digest=uuid.uuid4().hex * 2,
        action_digest=digest,
        lock_plan_digest=uuid.uuid4().hex * 2,
        hold_set_digest=uuid.uuid4().hex * 2,
        operation_version_snapshot=operation.version,
        request_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        observed_at=timezone.now(),
    )
    requeue_id = uuid.uuid4()
    fresh_digest = uuid.uuid4().hex * 2
    request_id = uuid.uuid4()
    correlation_id = uuid.uuid4()
    expires_at = timezone.now() + timedelta(minutes=10)
    settings.ENVIRONMENT = "test"
    settings.BUILD_COMMIT = "test-build"
    action = {
        "schema_version": 1,
        "purpose": "identity-merge-requeue",
        "environment": settings.ENVIRONMENT,
        "operation_id": str(operation.id),
        "hold_block_id": str(block.id),
        "prior_action_digest": digest,
        "fresh_action_digest": fresh_digest,
        "action_kind": MergeActionKind.EXPIRE_PENDING,
        "resume_mode": MergeResumeMode.AUTOMATIC,
        "expected_operation_version": operation.version,
        "request_id": str(request_id),
        "correlation_id": str(correlation_id),
        "expires_at": expires_at.isoformat(),
        "approval_reference": "IAM-REQUEUE",
    }
    signature = signed_reconciliation_approval.private_key.sign(canonical_action_bytes(action))
    requeue = IdentityMergeRequeue.objects.create(
        id=requeue_id,
        operation=operation,
        hold_block=block,
        prior_action_digest=digest,
        fresh_action_digest=fresh_digest,
        action_kind=MergeActionKind.EXPIRE_PENDING,
        resume_mode=MergeResumeMode.AUTOMATIC,
        action_schema_version=1,
        environment="test",
        execution_build_digest="test-build",
        approval_reference="IAM-REQUEUE",
        key_id=signed_reconciliation_approval.key_id,
        issued_at=timezone.now(),
        expires_at=expires_at,
        signature=signature,
        expected_operation_version=operation.version,
        requested_by="test-operator",
        request_id=request_id,
        correlation_id=correlation_id,
    )
    result = process_identity_merge_requeues.run(batch_size=10)
    assert result[MergeRequeueOutcome.APPLIED] == 1
    row = IdentityMergeRequeueResult.objects.using("default").get(requeue=requeue)
    assert row.outcome == MergeRequeueOutcome.APPLIED


@pytest.mark.django_db(transaction=True)
def test_requeue_worker_rejects_unverified_release_evidence(identity_merge_operation_factory, settings):
    operation = identity_merge_operation_factory()
    digest = uuid.uuid4().hex * 2
    block = IdentityMergeHoldBlock.objects.create(
        operation=operation,
        subject_type="source_identity",
        subject_identity_id=operation.source_identity_id,
        hold_reference="released-test-hold",
        hold_digest=uuid.uuid4().hex * 2,
        action_digest=digest,
        lock_plan_digest=uuid.uuid4().hex * 2,
        hold_set_digest=uuid.uuid4().hex * 2,
        operation_version_snapshot=operation.version,
        request_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        observed_at=timezone.now(),
    )
    settings.ENVIRONMENT = "test"
    settings.BUILD_COMMIT = "test-build"
    requeue = IdentityMergeRequeue.objects.create(
        operation=operation,
        hold_block=block,
        prior_action_digest=digest,
        fresh_action_digest=uuid.uuid4().hex * 2,
        action_kind=MergeActionKind.EXPIRE_PENDING,
        resume_mode=MergeResumeMode.AUTOMATIC,
        action_schema_version=1,
        environment="test",
        execution_build_digest="wrong-build",
        approval_reference="IAM-REQUEUE",
        key_id="unknown-key",
        issued_at=timezone.now(),
        expires_at=timezone.now() + timedelta(minutes=10),
        signature=b"invalid-signature",
        expected_operation_version=operation.version,
        requested_by="test-operator",
        request_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
    )

    result = process_identity_merge_requeues.run(batch_size=10)

    assert result[MergeRequeueOutcome.INVALID_RELEASE_EVIDENCE] == 1
    assert IdentityMergeRequeueResult.objects.using("default").get(requeue=requeue).outcome == (
        MergeRequeueOutcome.INVALID_RELEASE_EVIDENCE
    )
