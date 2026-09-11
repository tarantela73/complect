from celery import shared_task
from django.conf import settings
from django.db import transaction

from apps.users.models import IdentityMergeRedisCleanupRecord
from apps.users.models_identity import MergeActionKind
from apps.users.repositories.identity_merge_cleanup_repository import IdentityMergeCleanupRepository
from apps.users.security.identity_merge_legal_hold import (
    IdentityMergeLegalHoldActive,
    record_hold_evidence,
    require_no_legal_hold,
)
from apps.users.security.identity_merge_locking import IdentityMergeLockCoordinator


@shared_task(queue="retention")
def process_identity_merge_redis_cleanup(*, batch_size: int = 100) -> dict[str, int]:
    candidate_ids = list(
        IdentityMergeRedisCleanupRecord.objects.using("default")
        .filter(status=IdentityMergeRedisCleanupRecord.Status.PENDING)
        .order_by("id")
        .values_list("id", flat=True)[:batch_size],
    )
    completed = failed = held = 0
    for record_id in candidate_ids:
        hold_failure = None
        with transaction.atomic(using="default"):
            candidate = IdentityMergeRedisCleanupRecord.objects.using("default").select_related("operation").get(
                pk=record_id,
            )
            graph = IdentityMergeLockCoordinator().lock(user_pks=[candidate.operation.source_user_id])
            record = IdentityMergeRedisCleanupRecord.objects.using("default").select_for_update().get(pk=record_id)
            try:
                require_no_legal_hold(graph, using="default")
            except IdentityMergeLegalHoldActive as exc:
                hold_failure = (candidate.operation_id, exc.snapshot, candidate.operation.approval_digest)
            if hold_failure is None:
                redis_client = getattr(settings, "REDIS_CLIENT", None)
                if redis_client is None:
                    IdentityMergeCleanupRepository.record_failure(record, "REDIS_UNAVAILABLE")
                    failed += 1
                else:
                    try:
                        for actor_pk in record.actor_user_pks:
                            keys = list(redis_client.scan_iter(match=f"auth:refresh:{actor_pk}:*"))
                            if keys:
                                redis_client.delete(*keys)
                    except Exception:
                        IdentityMergeCleanupRepository.record_failure(record, "REDIS_CLEANUP_FAILED")
                        failed += 1
                    else:
                        if IdentityMergeCleanupRepository.complete(record):
                            completed += 1
        if hold_failure is not None:
            operation_id, snapshot, action_digest = hold_failure
            record_hold_evidence(
                operation_id=operation_id,
                snapshot=snapshot,
                action_digest=action_digest,
                action_kind=MergeActionKind.REDIS_CLEANUP,
                using="default",
            )
            held += 1
    return {"examined": len(candidate_ids), "completed": completed, "failed": failed, "held": held}
