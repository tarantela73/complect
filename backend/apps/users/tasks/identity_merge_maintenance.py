from celery import shared_task
from django.db import transaction
from django.utils import timezone

from apps.users.models import IdentityMergeOperation
from apps.users.models_identity import MergeActionKind
from apps.users.repositories.identity_merge_repository import IdentityMergeRepository
from apps.users.security.identity_merge_legal_hold import (
    IdentityMergeLegalHoldActive,
    record_hold_evidence,
    require_no_legal_hold,
)
from apps.users.security.identity_merge_locking import (
    IdentityMergeLockCoordinator,
)


@shared_task(queue="retention")
def expire_pending_identity_merges(*, batch_size: int = 500) -> dict[str, int]:
    now = timezone.now()
    candidate_ids = list(
        IdentityMergeOperation.objects.using("default").filter(
            state=IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE,
            approval_expires_at__lte=now,
        ).order_by("id").values_list("id", flat=True)[:batch_size],
    )
    expired = held = skipped = 0
    coordinator = IdentityMergeLockCoordinator()
    for operation_id in candidate_ids:
        hold_failure = None
        with transaction.atomic():
            candidate = IdentityMergeOperation.objects.using("default").get(pk=operation_id)
            graph = coordinator.lock(user_pks=[candidate.source_user_id])
            operation = next(row for row in graph.operations if row.id == operation_id)
            try:
                require_no_legal_hold(graph, using="default")
            except IdentityMergeLegalHoldActive as exc:
                hold_failure = (exc.snapshot, operation.approval_digest)
            if hold_failure is None and operation.state == IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE:
                changed = int(IdentityMergeRepository.expire_pending(operation, using="default"))
                expired += changed
                skipped += int(changed == 0)
            elif hold_failure is None:
                skipped += 1
        if hold_failure is not None:
            snapshot, action_digest = hold_failure
            record_hold_evidence(
                operation_id=operation_id,
                snapshot=snapshot,
                action_digest=action_digest,
                action_kind=MergeActionKind.EXPIRE_PENDING,
                using="default",
            )
            held += 1
    return {"examined": len(candidate_ids), "expired": expired, "held": held, "skipped": skipped}
