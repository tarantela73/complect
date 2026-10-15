import pyotp
import pytest
from django.db import transaction

from apps.common.tenancy.context import tenant_rls_scope
from apps.users.models import IdentityMergeHoldBlock, MfaCredential, TenantMembership, User
from apps.users.security.identity_merge_legal_hold import collect_legal_hold_snapshot
from apps.users.security.identity_merge_locking import IdentityMergeLockCoordinator
from apps.users.services.merge_convergence_service import MergeConvergenceError, MergeConvergenceService


@pytest.mark.django_db(transaction=True)
def test_owner_hold_block_preserves_exact_locked_security_subject_and_is_idempotent(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
    valid_runtime_merge_coverage_attestation,
):
    source, canonical = collision_pair.second, collision_pair.first
    secret = pyotp.random_base32()
    User.objects.filter(pk=canonical.pk).update(mfa_enabled=True, mfa_secret=secret)
    canonical.refresh_from_db(using="default")
    approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-EXACT-OWNER-HOLD",
    )
    operation = identity_collision_resolution_service.approve_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        approval=approval,
    )
    service = MergeConvergenceService()
    challenge = service.start(operation_id=operation.id, actor_pk=source.pk)
    MfaCredential.objects.create(
        identity_id=canonical.identity_id,
        encrypted_secret=b"held-factor",
        nonce=b"held-nonce12",
        key_id="held-key",
        confirmed_at=canonical.date_joined,
        is_legal_hold=True,
    )
    with transaction.atomic(using="default"):
        graph = IdentityMergeLockCoordinator().lock(user_pks=[source.pk], using="default")
        expected = collect_legal_hold_snapshot(graph, using="default")

    kwargs = {
        "challenge_token": challenge.challenge_token,
        "actor_pk": source.pk,
        "new_password": "Exact Held Owner Password 52!",
        "totp_code": pyotp.TOTP(secret).now(),
        "idempotency_key": challenge.idempotency_key,
    }
    for _attempt in range(2):
        with pytest.raises(MergeConvergenceError, match="LEGAL_HOLD_ACTIVE"):
            service.complete(**kwargs)

    blocks = IdentityMergeHoldBlock.objects.using("default").filter(operation=operation)
    assert blocks.count() == 1
    block = blocks.get()
    assert block.subject_identity_id == expected.subjects[0].identity_id
    assert block.subject_type == expected.subjects[0].subject_type
    assert block.hold_digest == expected.digest
    assert block.hold_set_digest == expected.digest
    assert block.lock_plan_digest == expected.lock_plan_digest
    assert block.operation_version_snapshot == expected.operation_version(operation.id)


@pytest.mark.django_db(transaction=True)
def test_dormant_membership_hold_commits_exact_snapshot_evidence_and_no_authority_change(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
    valid_runtime_merge_coverage_attestation,
    settings,
):
    source, canonical = collision_pair.second, collision_pair.first
    User.objects.filter(pk__in=[source.pk, canonical.pk]).update(
        is_active=False,
        security_status=User.SecurityStatus.LOCKED,
    )
    source.refresh_from_db(using="default")
    canonical.refresh_from_db(using="default")
    approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-EXACT-DORMANT-HOLD",
    )
    operation = identity_collision_resolution_service.approve_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        approval=approval,
    )
    with tenant_rls_scope(source.tenant_id, using="default"):
        membership = TenantMembership.objects.using("default").get(legacy_actor_user=source)
        TenantMembership.objects.using("default").filter(pk=membership.pk).update(is_legal_hold=True)
    for participant in (source, canonical):
        keys = list(settings.REDIS_CLIENT.scan_iter(match=f"auth:refresh:{participant.pk}:*"))
        if keys:
            settings.REDIS_CLIENT.delete(*keys)
    with transaction.atomic(using="default"):
        graph = IdentityMergeLockCoordinator().lock(user_pks=[source.pk], using="default")
        expected = collect_legal_hold_snapshot(graph, using="default")
    before = (operation.state, operation.version, source.password, canonical.password)

    with pytest.raises(MergeConvergenceError, match="LEGAL_HOLD_ACTIVE"):
        MergeConvergenceService().complete_dormant(operation_id=operation.id)

    operation.refresh_from_db(using="default")
    source.refresh_from_db(using="default")
    canonical.refresh_from_db(using="default")
    assert (operation.state, operation.version, source.password, canonical.password) == before
    block = IdentityMergeHoldBlock.objects.using("default").get(operation=operation)
    assert block.subject_identity_id == expected.subjects[0].identity_id
    assert block.subject_type == expected.subjects[0].subject_type
    assert block.hold_digest == expected.digest
    assert block.hold_set_digest == expected.digest
    assert block.lock_plan_digest == expected.lock_plan_digest
    assert block.operation_version_snapshot == expected.operation_version(operation.id)
