import base64
import os
import uuid
from datetime import timedelta

import jwt
import pyotp
import pytest
from django.contrib.auth.hashers import check_password
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.common.tenancy.context import tenant_rls_scope
from apps.users.contracts.auth_context_impl import UsersAuthContextValidator
from apps.users.models import (
    IamTenantRegistryEntry,
    IdentityMergeHoldBlock,
    IdentityMergeOperation,
    IdentityMergeRequeue,
    IdentityMergeRequeueResult,
    TenantMembership,
    User,
)
from apps.users.models_identity import MergeActionKind, MergeRequeueOutcome, MergeResumeMode
from apps.users.security.reconciliation_approval import canonical_action_bytes
from apps.users.services.auth_service import (
    REFRESH_TOKEN_REDIS_PREFIX,
    _make_access_token,
    _make_refresh_token,
    _store_refresh_token,
    login,
    logout,
    refresh_access_token,
)
from apps.users.services.deferred_merge_guard import DeferredMergeGuard
from apps.users.services.merge_convergence_service import MergeConvergenceService
from apps.users.services.mfa_service import verify_mfa_and_issue_tokens
from apps.users.tasks.identity_merge_redis_cleanup import process_identity_merge_redis_cleanup


@pytest.mark.django_db(transaction=True)
def test_owner_convergence_establishes_one_password_authority(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
    valid_runtime_merge_coverage_attestation,
):
    source, canonical = collision_pair.second, collision_pair.first
    secret = pyotp.random_base32()
    User.objects.filter(pk=canonical.pk).update(mfa_enabled=True, mfa_secret=secret)
    canonical.refresh_from_db()
    approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-CONVERGENCE",
    )
    operation = identity_collision_resolution_service.approve_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        approval=approval,
    )
    service = MergeConvergenceService()
    challenge = service.start(operation_id=operation.id, actor_pk=source.pk)
    with pytest.raises(PermissionDenied, match="MERGE_CHALLENGE_ACTIVE"):
        service.start(operation_id=operation.id, actor_pk=source.pk)
    service.complete(
        challenge_token=challenge.challenge_token,
        actor_pk=source.pk,
        new_password="Unique Convergence Password 42!",
        totp_code=pyotp.TOTP(secret).now(),
        idempotency_key=challenge.idempotency_key,
    )
    cleanup_result = process_identity_merge_redis_cleanup.run(batch_size=10)
    assert cleanup_result["completed"] == 1
    service.complete(
        challenge_token=challenge.challenge_token,
        actor_pk=source.pk,
        new_password="Unique Convergence Password 42!",
        totp_code=pyotp.TOTP(secret).now(),
        idempotency_key=challenge.idempotency_key,
    )
    source.refresh_from_db()
    canonical.refresh_from_db()
    operation.refresh_from_db()
    assert operation.state == IdentityMergeOperation.State.APPROVED_DEFERRED
    assert source.password == canonical.password
    assert check_password("Unique Convergence Password 42!", canonical.password)
    assert source.is_active is False
    assert source.normalized_email == operation.synthetic_normalized_email
    assert canonical.security_version == 2
    authority = DeferredMergeGuard.resolve_legacy_actor(source)
    assert authority.identity.pk == canonical.pk
    assert TenantMembership.objects.filter(legacy_actor_user=source).count() == 1


@pytest.mark.django_db(transaction=True)
def test_owner_convergence_can_enroll_canonical_encrypted_factor(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
    valid_runtime_merge_coverage_attestation,
    settings,
):
    source, canonical = collision_pair.second, collision_pair.first
    key = os.urandom(32)
    settings.IAM_MFA_CURRENT_KEY_ID = "test-mfa-key"
    settings.IAM_MFA_ENCRYPTION_KEYS = {"test-mfa-key": base64.b64encode(key).decode()}
    approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-CONVERGENCE-SETUP",
    )
    operation = identity_collision_resolution_service.approve_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        approval=approval,
    )
    service = MergeConvergenceService()
    challenge = service.start(operation_id=operation.id, actor_pk=source.pk)
    assert challenge.mfa_secret
    assert challenge.otpauth_url
    service.complete(
        challenge_token=challenge.challenge_token,
        actor_pk=source.pk,
        new_password="Another Unique Convergence Password 43!",
        totp_code=pyotp.TOTP(challenge.mfa_secret).now(),
        idempotency_key=challenge.idempotency_key,
    )
    operation.refresh_from_db()
    assert operation.state == IdentityMergeOperation.State.APPROVED_DEFERRED


@pytest.mark.django_db(transaction=True)
def test_convergence_invalidates_old_access_refresh_and_mfa_partial_artifacts(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
    valid_runtime_merge_coverage_attestation,
    settings,
):
    source, canonical = collision_pair.second, collision_pair.first
    secret = pyotp.random_base32()
    User.objects.filter(pk=canonical.pk).update(mfa_enabled=True, mfa_secret=secret)
    canonical.refresh_from_db()
    redis_client = settings.REDIS_CLIENT

    old_source_access = _make_access_token(source)
    old_canonical_access = _make_access_token(canonical)
    old_source_refresh, source_jti = _make_refresh_token(source)
    old_canonical_refresh, canonical_jti = _make_refresh_token(canonical)
    _store_refresh_token(redis_client, source.pk, source_jti)
    _store_refresh_token(redis_client, canonical.pk, canonical_jti)
    old_mfa_partial, _, mfa_required, _ = login(
        redis_client,
        canonical.username,
        "FirstPassword123!",
    )
    assert mfa_required is True

    approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-ARTIFACT-INVALIDATION",
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
        new_password="Artifact Invalidating Password 44!",
        totp_code=pyotp.TOTP(secret).now(),
        idempotency_key=challenge.idempotency_key,
    )
    cleanup_result = process_identity_merge_redis_cleanup.run(batch_size=10)
    assert cleanup_result["completed"] == 1

    validator = UsersAuthContextValidator()
    for old_access in (old_source_access, old_canonical_access):
        claims = jwt.decode(old_access, settings.SECRET_KEY, algorithms=["HS256"])
        with pytest.raises(PermissionDenied, match="INVALID_AUTH_CONTEXT"):
            validator.validate(claims)
    for old_refresh in (old_source_refresh, old_canonical_refresh):
        with pytest.raises(PermissionDenied, match="REFRESH_TOKEN_REVOKED"):
            refresh_access_token(redis_client, old_refresh)
    with pytest.raises(PermissionDenied, match="MFA_TOKEN_INVALID"):
        verify_mfa_and_issue_tokens(
            redis_client,
            old_mfa_partial,
            pyotp.TOTP(secret).now(),
        )
    assert redis_client.exists(f"{REFRESH_TOKEN_REDIS_PREFIX}{source.pk}:{source_jti}") == 0
    assert redis_client.exists(f"{REFRESH_TOKEN_REDIS_PREFIX}{canonical.pk}:{canonical_jti}") == 0

    new_partial, _, mfa_required, _ = login(
        redis_client,
        source.username,
        "Artifact Invalidating Password 44!",
    )
    assert mfa_required is True
    new_access, new_refresh = verify_mfa_and_issue_tokens(
        redis_client,
        new_partial,
        pyotp.TOTP(secret).now(),
    )
    validator.validate(jwt.decode(new_access, settings.SECRET_KEY, algorithms=["HS256"]))
    _rotated_access, rotated_refresh = refresh_access_token(redis_client, new_refresh)
    logout(redis_client, rotated_refresh)


@pytest.mark.django_db(transaction=True)
def test_dormant_convergence_requires_the_complete_fail_closed_profile(
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
    source.refresh_from_db()
    canonical.refresh_from_db()
    approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-DORMANT-CONVERGENCE",
    )
    operation = identity_collision_resolution_service.approve_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        approval=approval,
    )
    for participant in (source, canonical):
        stale_keys = list(settings.REDIS_CLIENT.scan_iter(match=f"auth:refresh:{participant.pk}:*"))
        if stale_keys:
            settings.REDIS_CLIENT.delete(*stale_keys)

    result = MergeConvergenceService().complete_dormant(operation_id=operation.id)

    source.refresh_from_db()
    canonical.refresh_from_db()
    assert result.state == IdentityMergeOperation.State.APPROVED_DEFERRED
    assert result.convergence_mode == IdentityMergeOperation.ConvergenceMode.DORMANT_FAIL_CLOSED
    assert source.is_active is False
    assert canonical.is_active is False
    assert canonical.security_status == User.SecurityStatus.LOCKED


@pytest.mark.django_db(transaction=True)
def test_hold_creates_separate_evidence_and_changes_no_held_state(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
    valid_runtime_merge_coverage_attestation,
):
    source, canonical = collision_pair.second, collision_pair.first
    secret = pyotp.random_base32()
    User.objects.filter(pk=canonical.pk).update(mfa_enabled=True, mfa_secret=secret)
    canonical.refresh_from_db()
    approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-HELD-CONVERGENCE",
    )
    operation = identity_collision_resolution_service.approve_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        approval=approval,
    )
    service = MergeConvergenceService()
    challenge = service.start(operation_id=operation.id, actor_pk=source.pk)
    User.objects.filter(pk=source.pk).update(identity_legal_hold=True)
    before = User.objects.values_list(
        "pk",
        "password",
        "normalized_email",
        "security_version",
        "is_active",
    ).filter(pk__in=[source.pk, canonical.pk]).order_by("pk")
    before = list(before)

    with pytest.raises(PermissionDenied, match="LEGAL_HOLD_ACTIVE"):
        service.complete(
            challenge_token=challenge.challenge_token,
            actor_pk=source.pk,
            new_password="Held Convergence Password 45!",
            totp_code=pyotp.TOTP(secret).now(),
            idempotency_key=challenge.idempotency_key,
        )

    after = list(
        User.objects.values_list(
            "pk",
            "password",
            "normalized_email",
            "security_version",
            "is_active",
        ).filter(pk__in=[source.pk, canonical.pk]).order_by("pk"),
    )
    operation.refresh_from_db()
    assert after == before
    assert operation.state == IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE
    assert IdentityMergeHoldBlock.objects.filter(operation=operation).count() == 1


@pytest.mark.django_db(transaction=True)
def test_owner_convergence_moves_every_discovered_tenant_membership(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
    valid_runtime_merge_coverage_attestation,
):
    source, canonical = collision_pair.second, collision_pair.first
    extra_tenant = uuid.uuid4()
    IamTenantRegistryEntry.objects.create(tenant_id=extra_tenant, source_code="convergence_test")
    with tenant_rls_scope(extra_tenant, using="default"):
        extra_membership = TenantMembership.objects.create(
            tenant_id=extra_tenant,
            identity_id=source.identity_id,
            legacy_actor_user=source,
            display_name="Extra footprint",
            status=TenantMembership.Status.ACTIVE,
        )
    secret = pyotp.random_base32()
    User.objects.filter(pk=canonical.pk).update(mfa_enabled=True, mfa_secret=secret)
    canonical.refresh_from_db(using="default")
    approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-MULTI-TENANT-CONVERGENCE",
    )
    operation = identity_collision_resolution_service.approve_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        approval=approval,
    )
    challenge = MergeConvergenceService().start(operation_id=operation.id, actor_pk=source.pk)
    MergeConvergenceService().complete(
        challenge_token=challenge.challenge_token,
        actor_pk=source.pk,
        new_password="Multi Tenant Convergence Password 46!",
        totp_code=pyotp.TOTP(secret).now(),
        idempotency_key=challenge.idempotency_key,
    )

    with tenant_rls_scope(extra_tenant, using="default"):
        extra_membership.refresh_from_db(using="default")
    assert extra_membership.identity_id == canonical.identity_id


@pytest.mark.django_db(transaction=True)
def test_owner_requeue_is_applied_only_with_fresh_authenticated_convergence(
    collision_pair,
    signed_reconciliation_approval,
    identity_collision_resolution_service,
    valid_runtime_merge_coverage_attestation,
    settings,
):
    source, canonical = collision_pair.second, collision_pair.first
    secret = pyotp.random_base32()
    User.objects.filter(pk=canonical.pk).update(mfa_enabled=True, mfa_secret=secret)
    canonical.refresh_from_db(using="default")
    approval = signed_reconciliation_approval.for_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        reference="IAM-OWNER-REQUEUE",
    )
    operation = identity_collision_resolution_service.approve_merge(
        source_pk=source.pk,
        canonical_pk=canonical.pk,
        approval=approval,
    )
    blocked_challenge = MergeConvergenceService().start(operation_id=operation.id, actor_pk=source.pk)
    User.objects.filter(pk=source.pk).update(identity_legal_hold=True)
    with pytest.raises(PermissionDenied, match="LEGAL_HOLD_ACTIVE"):
        MergeConvergenceService().complete(
            challenge_token=blocked_challenge.challenge_token,
            actor_pk=source.pk,
            new_password="Blocked Owner Password 48!",
            totp_code=pyotp.TOTP(secret).now(),
            idempotency_key=blocked_challenge.idempotency_key,
        )
    block = IdentityMergeHoldBlock.objects.using("default").get(operation=operation)
    User.objects.filter(pk=source.pk).update(identity_legal_hold=False)
    expires_at = timezone.now() + timedelta(minutes=10)
    request_id, correlation_id = uuid.uuid4(), uuid.uuid4()
    fresh_digest = uuid.uuid4().hex * 2
    action = {
        "schema_version": 1,
        "purpose": "identity-merge-requeue",
        "environment": settings.ENVIRONMENT,
        "operation_id": str(operation.id),
        "hold_block_id": str(block.id),
        "prior_action_digest": block.action_digest,
        "fresh_action_digest": fresh_digest,
        "action_kind": MergeActionKind.OWNER_CONVERGENCE,
        "resume_mode": MergeResumeMode.OWNER_AUTHENTICATED,
        "expected_operation_version": operation.version,
        "request_id": str(request_id),
        "correlation_id": str(correlation_id),
        "expires_at": expires_at.isoformat(),
        "approval_reference": "IAM-OWNER-REQUEUE-RELEASE",
    }
    requeue = IdentityMergeRequeue.objects.create(
        operation=operation,
        hold_block=block,
        prior_action_digest=block.action_digest,
        fresh_action_digest=fresh_digest,
        action_kind=MergeActionKind.OWNER_CONVERGENCE,
        resume_mode=MergeResumeMode.OWNER_AUTHENTICATED,
        action_schema_version=1,
        environment=settings.ENVIRONMENT,
        execution_build_digest=settings.BUILD_COMMIT,
        approval_reference="IAM-OWNER-REQUEUE-RELEASE",
        key_id=signed_reconciliation_approval.key_id,
        issued_at=timezone.now(),
        expires_at=expires_at,
        signature=signed_reconciliation_approval.private_key.sign(canonical_action_bytes(action)),
        expected_operation_version=operation.version,
        requested_by="test-operator",
        request_id=request_id,
        correlation_id=correlation_id,
    )
    fresh_challenge = MergeConvergenceService().start(operation_id=operation.id, actor_pk=source.pk)
    MergeConvergenceService().complete(
        challenge_token=fresh_challenge.challenge_token,
        actor_pk=source.pk,
        new_password="Fresh Owner Password 49!",
        totp_code=pyotp.TOTP(secret).now(),
        idempotency_key=fresh_challenge.idempotency_key,
        requeue_id=requeue.id,
    )
    result = IdentityMergeRequeueResult.objects.using("default").get(requeue=requeue)
    assert result.outcome == MergeRequeueOutcome.APPLIED
