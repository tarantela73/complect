from datetime import timedelta

import pytest
from django.utils import timezone

from apps.users.models import IdentityMergeHoldBlock, IdentityMergeOperation, MfaCredential, User
from apps.users.tasks.identity_merge_maintenance import expire_pending_identity_merges


@pytest.mark.django_db(transaction=True)
def test_expired_pending_merge_transitions_without_changing_identity_authority(
    identity_merge_operation_factory,
):
    operation = identity_merge_operation_factory(
        approval_expires_at=timezone.now() - timedelta(minutes=1),
    )
    before = list(
        User.objects.using("default")
        .filter(pk__in=[
            identity_merge_operation_factory.source.pk,
            identity_merge_operation_factory.canonical.pk,
        ])
        .order_by("pk")
        .values_list("pk", "password", "normalized_email", "is_active", "security_version"),
    )

    result = expire_pending_identity_merges.run(batch_size=10)

    operation.refresh_from_db(using="default")
    after = list(
        User.objects.using("default")
        .filter(pk__in=[
            identity_merge_operation_factory.source.pk,
            identity_merge_operation_factory.canonical.pk,
        ])
        .order_by("pk")
        .values_list("pk", "password", "normalized_email", "is_active", "security_version"),
    )
    assert result["expired"] == 1
    assert operation.state == IdentityMergeOperation.State.EXPIRED
    assert after == before


@pytest.mark.django_db(transaction=True)
def test_expiry_worker_observes_related_security_hold_without_mutating_operation(
    identity_merge_operation_factory,
):
    operation = identity_merge_operation_factory(
        approval_expires_at=timezone.now() - timedelta(minutes=1),
    )
    MfaCredential.objects.create(
        identity_id=operation.canonical_identity_id,
        encrypted_secret=b"held-ciphertext",
        nonce=b"held-nonce12",
        key_id="held-key",
        confirmed_at=timezone.now(),
        is_legal_hold=True,
    )

    result = expire_pending_identity_merges.run(batch_size=10)

    operation.refresh_from_db(using="default")
    assert result["held"] == 1
    assert result["expired"] == 0
    assert operation.state == IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE
    assert IdentityMergeHoldBlock.objects.using("default").filter(operation=operation).count() == 1
