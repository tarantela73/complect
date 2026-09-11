import pyotp
import pytest

from apps.users.models import IdentityMergeHoldBlock, IdentityMergeRedisCleanupRecord, User
from apps.users.services.merge_convergence_service import MergeConvergenceService
from apps.users.tasks.identity_merge_redis_cleanup import process_identity_merge_redis_cleanup


def _complete_merge(
    *,
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
):
    source, canonical = collision_pair.second, collision_pair.first
    secret = pyotp.random_base32()
    User.objects.filter(pk=canonical.pk).update(mfa_enabled=True, mfa_secret=secret)
    canonical.refresh_from_db(using="default")
    approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-CLEANUP",
    )
    operation = identity_collision_resolution_service.approve_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        approval=approval,
    )
    service = MergeConvergenceService()
    challenge = service.start(operation_id=operation.id, actor_pk=source.pk)
    service.complete(
        challenge_token=challenge.challenge_token,
        actor_pk=source.pk,
        new_password="Durable Cleanup Password 47!",
        totp_code=pyotp.TOTP(secret).now(),
        idempotency_key=challenge.idempotency_key,
    )
    return source, operation


@pytest.mark.django_db(transaction=True)
def test_redis_failure_leaves_durable_pending_cleanup(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
    valid_runtime_merge_coverage_attestation,
    settings,
):
    source, operation = _complete_merge(
        collision_pair=collision_pair,
        signed_reconciliation_approval=signed_reconciliation_approval,
        identity_collision_resolution_service=identity_collision_resolution_service,
    )

    class FailingRedis:
        def scan_iter(self, **kwargs):
            raise ConnectionError("unavailable")

    settings.REDIS_CLIENT = FailingRedis()
    result = process_identity_merge_redis_cleanup.run(batch_size=10)
    cleanup = IdentityMergeRedisCleanupRecord.objects.using("default").get(operation=operation)
    assert result["failed"] == 1
    assert cleanup.status == IdentityMergeRedisCleanupRecord.Status.PENDING
    assert cleanup.attempt_count == 1
    assert cleanup.last_failure_code == "REDIS_CLEANUP_FAILED"
    assert cleanup.actor_user_pks == sorted(cleanup.actor_user_pks)
    assert source.pk in cleanup.actor_user_pks


@pytest.mark.django_db(transaction=True)
def test_cleanup_hold_mutates_no_redis_and_commits_only_hold_evidence(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
    valid_runtime_merge_coverage_attestation,
    settings,
):
    source, operation = _complete_merge(
        collision_pair=collision_pair,
        signed_reconciliation_approval=signed_reconciliation_approval,
        identity_collision_resolution_service=identity_collision_resolution_service,
    )
    User.objects.filter(pk=source.pk).update(identity_legal_hold=True)

    class ForbiddenRedis:
        def scan_iter(self, **kwargs):
            raise AssertionError("Redis must not be read or mutated while held")

    settings.REDIS_CLIENT = ForbiddenRedis()
    result = process_identity_merge_redis_cleanup.run(batch_size=10)
    cleanup = IdentityMergeRedisCleanupRecord.objects.using("default").get(operation=operation)
    assert result["held"] == 1
    assert cleanup.status == IdentityMergeRedisCleanupRecord.Status.PENDING
    assert cleanup.attempt_count == 0
    assert IdentityMergeHoldBlock.objects.using("default").filter(operation=operation).count() == 1
