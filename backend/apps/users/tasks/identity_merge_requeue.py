from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.users.models import IdentityMergeRequeue, IdentityMergeRequeueResult
from apps.users.models_identity import MergeActionKind, MergeRequeueOutcome, MergeResumeMode
from apps.users.security.identity_merge_locking import (
    IdentityMergeLockCoordinator,
    identity_merge_graph_has_legal_hold,
)
from apps.users.security.reconciliation_approval import (
    ReconciliationApproval,
    ReconciliationApprovalVerifier,
)


def _signed_action(requeue: IdentityMergeRequeue) -> dict[str, object]:
    return {
        "schema_version": requeue.action_schema_version,
        "purpose": "identity-merge-requeue",
        "environment": requeue.environment,
        "operation_id": str(requeue.operation_id),
        "hold_block_id": str(requeue.hold_block_id),
        "prior_action_digest": requeue.prior_action_digest,
        "fresh_action_digest": requeue.fresh_action_digest,
        "action_kind": requeue.action_kind,
        "resume_mode": requeue.resume_mode,
        "expected_operation_version": requeue.expected_operation_version,
        "request_id": str(requeue.request_id),
        "correlation_id": str(requeue.correlation_id),
        "expires_at": requeue.expires_at.isoformat(),
        "approval_reference": requeue.approval_reference,
    }


def has_valid_release_evidence(requeue: IdentityMergeRequeue) -> bool:
    build_commit = getattr(settings, "BUILD_COMMIT", "")
    if (
        requeue.action_schema_version != 1
        or requeue.environment != settings.ENVIRONMENT
        or not build_commit
        or requeue.execution_build_digest != build_commit
        or requeue.prior_action_digest != requeue.hold_block.action_digest
        or requeue.operation_id != requeue.hold_block.operation_id
        or requeue.issued_at >= requeue.expires_at
    ):
        return False
    return ReconciliationApprovalVerifier.verify(
        ReconciliationApproval(
            reference=requeue.approval_reference,
            key_id=requeue.key_id,
            signature=bytes(requeue.signature),
        ),
        _signed_action(requeue),
    )


@shared_task(queue="retention")
def process_identity_merge_requeues(*, batch_size: int = 100) -> dict[str, int]:
    candidate_ids = list(
        IdentityMergeRequeue.objects.using("default")
        .filter(
            identitymergerequeueresult__isnull=True,
            resume_mode=MergeResumeMode.AUTOMATIC,
        )
        .order_by("id")
        .values_list("id", flat=True)[:batch_size],
    )
    counts = {outcome: 0 for outcome, _label in MergeRequeueOutcome.choices}
    for requeue_id in candidate_ids:
        with transaction.atomic():
            candidate = IdentityMergeRequeue.objects.using("default").select_related("operation").get(pk=requeue_id)
            graph = IdentityMergeLockCoordinator().lock(user_pks=[candidate.operation.source_user_id])
            requeue = (
                IdentityMergeRequeue.objects.using("default")
                .select_for_update()
                .select_related("operation", "hold_block")
                .get(pk=requeue_id)
            )
            operation = next(row for row in graph.operations if row.id == requeue.operation_id)
            if requeue.expires_at <= timezone.now() or not has_valid_release_evidence(requeue):
                outcome = MergeRequeueOutcome.INVALID_RELEASE_EVIDENCE
            elif operation.version != requeue.expected_operation_version:
                outcome = MergeRequeueOutcome.VERSION_CONFLICT
            elif identity_merge_graph_has_legal_hold(graph):
                outcome = MergeRequeueOutcome.HOLD_STILL_ACTIVE
            elif requeue.action_kind == MergeActionKind.EXPIRE_PENDING:
                from apps.users.repositories.identity_merge_repository import IdentityMergeRepository

                applied = IdentityMergeRepository.expire_pending(operation, using="default")
                outcome = MergeRequeueOutcome.APPLIED if applied else MergeRequeueOutcome.VERSION_CONFLICT
                if applied:
                    operation.refresh_from_db(using="default")
                    IdentityMergeRepository.append_transition_evidence(
                        operation=operation,
                        action_kind=requeue.action_kind,
                        actor_ref=requeue.requested_by,
                        correlation_id=requeue.correlation_id,
                        using="default",
                    )
            else:
                outcome = MergeRequeueOutcome.INVALID_RELEASE_EVIDENCE
            IdentityMergeRequeueResult.objects.using("default").get_or_create(
                requeue=requeue,
                defaults={
                    "outcome": outcome,
                    "observed_operation_version": operation.version,
                    "processed_at": timezone.now(),
                },
            )
            counts[outcome] += 1
    return {"examined": len(candidate_ids), **counts}
