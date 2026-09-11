import pyotp
import pytest
from django.core.exceptions import PermissionDenied

from apps.users.models import IdentityMergeHoldBlock, IdentityMergeOperation, User
from apps.users.services.auth_service import login
from apps.users.services.identity_collision_resolution import IdentityCollisionResolutionError
from apps.users.services.mfa_service import confirm_totp_setup, setup_totp


@pytest.mark.django_db(transaction=True)
def test_mfa_setup_and_confirm_do_not_mutate_held_identity(identity):
    User.objects.filter(pk=identity.pk).update(identity_legal_hold=True)
    identity.refresh_from_db(using="default")
    before = (identity.mfa_secret, identity.mfa_enabled, identity.security_version)

    with pytest.raises(PermissionDenied, match="LEGAL_HOLD_ACTIVE"):
        setup_totp(identity)
    identity.refresh_from_db(using="default")
    assert (identity.mfa_secret, identity.mfa_enabled, identity.security_version) == before

    User.objects.filter(pk=identity.pk).update(mfa_secret=pyotp.random_base32())
    identity.refresh_from_db(using="default")
    confirm_before = (identity.mfa_secret, identity.mfa_enabled, identity.security_version)
    with pytest.raises(PermissionDenied, match="LEGAL_HOLD_ACTIVE"):
        confirm_totp_setup(identity, pyotp.TOTP(identity.mfa_secret).now())

    identity.refresh_from_db(using="default")
    assert (identity.mfa_secret, identity.mfa_enabled, identity.security_version) == confirm_before


@pytest.mark.django_db(transaction=True)
def test_public_login_hides_hold_and_creates_no_refresh_state(identity, settings):
    User.objects.filter(pk=identity.pk).update(identity_legal_hold=True)
    before = list(settings.REDIS_CLIENT.scan_iter(match=f"auth:refresh:{identity.pk}:*"))

    with pytest.raises(PermissionDenied, match="INVALID_CREDENTIALS"):
        login(settings.REDIS_CLIENT, identity.username, "TemporaryPassword123!")

    after = list(settings.REDIS_CLIENT.scan_iter(match=f"auth:refresh:{identity.pk}:*"))
    assert after == before


@pytest.mark.django_db(transaction=True)
def test_cancel_merge_held_source_rolls_back_authority_and_commits_hold_evidence(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
):
    source, canonical = collision_pair.second, collision_pair.first
    merge_approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-HELD-CANCEL-MERGE",
    )
    operation = identity_collision_resolution_service.approve_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        approval=merge_approval,
    )
    cancellation = signed_reconciliation_approval.for_cancel_merge(
        operation_id=operation.id,
        expected_version=operation.version,
        reference="IAM-HELD-CANCEL",
    )
    User.objects.filter(pk=source.pk).update(identity_legal_hold=True)

    with pytest.raises(IdentityCollisionResolutionError, match="LEGAL_HOLD_ACTIVE"):
        identity_collision_resolution_service.cancel_merge(
            operation_id=operation.id,
            approval=cancellation,
        )

    operation.refresh_from_db(using="default")
    assert operation.state == IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE
    assert operation.version == 1
    assert IdentityMergeHoldBlock.objects.using("default").filter(operation=operation).count() == 1
