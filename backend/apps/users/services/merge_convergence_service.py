import base64
import hashlib
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

import pyotp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.common.tenancy.context import tenant_rls_scope
from apps.users.infrastructure.iam_tenant_inventory import DurableIamTenantInventoryProvider
from apps.users.models import (
    IdentityMergeHoldBlock,
    IdentityMergeOperation,
    IdentityMergeRequeue,
    IdentityMergeRequeueResult,
    IdentitySession,
    MfaCredential,
    PreAuthChallenge,
    RecoveryToken,
    TenantGrantSwitchOperation,
    TenantMembership,
    TenantSessionGrant,
    User,
)
from apps.users.models_identity import MergeActionKind, MergeResumeMode
from apps.users.repositories.identity_merge_cleanup_repository import IdentityMergeCleanupRepository
from apps.users.repositories.identity_merge_repository import IdentityMergeRepository
from apps.users.repositories.membership_repository import MembershipRepository
from apps.users.repositories.merge_convergence_repository import (
    MergeConvergenceCasConflictError,
    MergeConvergenceRepository,
)
from apps.users.security.deferred_merge_execution_policy import DeferredMergeExecutionPolicy
from apps.users.security.identity_merge_legal_hold import (
    IdentityMergeLegalHoldActive,
    record_hold_evidence,
    require_no_legal_hold,
)
from apps.users.security.identity_merge_locking import (
    IdentityMergeLockCoordinator,
    identity_merge_graph_has_legal_hold,
)


class MergeFactorVerifier(Protocol):
    def verify(self, identity: User, code: str) -> bool: ...


def _mfa_keyring() -> dict[str, bytes]:
    configured = getattr(settings, "IAM_MFA_ENCRYPTION_KEYS", {})
    if isinstance(configured, str):
        configured = json.loads(configured or "{}")
    return {
        key_id: base64.b64decode(value) if isinstance(value, str) else value
        for key_id, value in configured.items()
    }


class CanonicalTotpVerifier:
    def verify(self, identity: User, code: str) -> bool:
        credential = (
            MfaCredential.objects.filter(identity_id=identity.identity_id, revoked_at__isnull=True)
            .order_by("id")
            .first()
        )
        if credential is not None:
            key = _mfa_keyring().get(credential.key_id)
            if key is None:
                return False
            try:
                secret = AESGCM(key).decrypt(
                    bytes(credential.nonce),
                    bytes(credential.encrypted_secret),
                    identity.identity_id.bytes,
                ).decode()
            except (ValueError, UnicodeDecodeError):
                return False
            return pyotp.TOTP(secret).verify(code, valid_window=1)
        return bool(
            identity.mfa_enabled
            and identity.mfa_secret
            and pyotp.TOTP(identity.mfa_secret).verify(code, valid_window=1)
        )


@dataclass(frozen=True, slots=True)
class MergeConvergenceChallenge:
    challenge_token: str
    idempotency_key: uuid.UUID
    expires_at: object
    mfa_secret: str | None = None
    otpauth_url: str | None = None


class MergeConvergenceError(PermissionDenied):
    pass


class MergeConvergenceService:
    def __init__(
        self,
        *,
        factor_verifier: MergeFactorVerifier | None = None,
        policy: type[DeferredMergeExecutionPolicy] = DeferredMergeExecutionPolicy,
        using: str = "default",
    ) -> None:
        self.factor_verifier = factor_verifier or CanonicalTotpVerifier()
        self.policy = policy
        self.using = using
        self.coordinator = IdentityMergeLockCoordinator()

    def _graph_has_legal_hold(self, graph) -> bool:
        return identity_merge_graph_has_legal_hold(graph, using=self.using)

    def start(self, *, operation_id: uuid.UUID, actor_pk: int) -> MergeConvergenceChallenge:
        self.policy.require_enabled()
        raw_token = secrets.token_urlsafe(32)
        token_digest = hashlib.sha256(raw_token.encode()).hexdigest()
        idempotency_key = uuid.uuid4()
        expires_at = timezone.now() + timedelta(minutes=5)
        with transaction.atomic(using=self.using):
            try:
                candidate = IdentityMergeOperation.objects.using(self.using).get(pk=operation_id)
            except IdentityMergeOperation.DoesNotExist:
                raise MergeConvergenceError("MERGE_OPERATION_NOT_FOUND") from None
            graph = self.coordinator.lock(user_pks=[candidate.source_user_id], using=self.using)
            try:
                operation = next(row for row in graph.operations if row.id == operation_id)
            except StopIteration:
                raise MergeConvergenceError("MERGE_OPERATION_NOT_FOUND") from None
            if operation.source_user_id != actor_pk:
                raise MergeConvergenceError("MERGE_SOURCE_AUTHENTICATION_REQUIRED")
            if operation.state != IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE:
                raise MergeConvergenceError("MERGE_OPERATION_NOT_PENDING")
            if operation.approval_expires_at <= timezone.now():
                raise MergeConvergenceError("MERGE_APPROVAL_EXPIRED")
            source = next((row for row in graph.users if row.pk == operation.source_user_id), None)
            canonical = next(
                (row for row in graph.users if row.identity_id == operation.canonical_identity_id),
                None,
            )
            if source is None:
                raise MergeConvergenceError("MERGE_SOURCE_IDENTITY_INVALID")
            if canonical is None:
                raise MergeConvergenceError("MERGE_CANONICAL_IDENTITY_INVALID")
            if operation.is_legal_hold or self._graph_has_legal_hold(graph):
                raise MergeConvergenceError("LEGAL_HOLD_ACTIVE")
            active_challenge_ids = list(PreAuthChallenge.objects.using(self.using).filter(
                merge_operation=operation,
                flow_kind=PreAuthChallenge.FlowKind.MERGE_CONVERGENCE,
                consumed_at__isnull=True,
                revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
            ).values_list("id", flat=True))
            has_hold_history = IdentityMergeHoldBlock.objects.using(self.using).filter(operation=operation).exists()
            if active_challenge_ids and not has_hold_history:
                raise MergeConvergenceError("MERGE_CHALLENGE_ACTIVE")
            stale_challenge_ids = active_challenge_ids + list(
                PreAuthChallenge.objects.using(self.using)
                .filter(
                    merge_operation=operation,
                    flow_kind=PreAuthChallenge.FlowKind.MERGE_CONVERGENCE,
                    consumed_at__isnull=True,
                    revoked_at__isnull=True,
                    expires_at__lte=timezone.now(),
                )
                .values_list("id", flat=True),
            )
            now = timezone.now()
            if stale_challenge_ids:
                try:
                    MergeConvergenceRepository.revoke_stale_challenges(
                        challenge_ids=stale_challenge_ids,
                        now=now,
                        using=self.using,
                    )
                except MergeConvergenceCasConflictError:
                    raise MergeConvergenceError("MERGE_CONTEXT_STALE") from None
            challenge = MergeConvergenceRepository.create_challenge(
                using=self.using,
                identity_id=source.identity_id,
                flow_kind=PreAuthChallenge.FlowKind.MERGE_CONVERGENCE,
                token_digest=token_digest,
                security_version_snapshot=source.security_version,
                merge_operation=operation,
                merge_operation_version_snapshot=operation.version,
                canonical_security_version_snapshot=canonical.security_version,
                idempotency_key=idempotency_key,
                expires_at=expires_at,
            )
            provisioning_secret = None
            provisioning_url = None
            has_factor = MfaCredential.objects.using(self.using).filter(
                identity_id=canonical.identity_id,
                revoked_at__isnull=True,
                confirmed_at__isnull=False,
            ).exists()
            if not has_factor and not (canonical.mfa_enabled and canonical.mfa_secret):
                key_id = getattr(settings, "IAM_MFA_CURRENT_KEY_ID", "")
                key = _mfa_keyring().get(key_id)
                if not key or len(key) != 32:
                    raise MergeConvergenceError("MFA_ENCRYPTION_KEY_UNAVAILABLE")
                provisioning_secret = pyotp.random_base32()
                nonce = os.urandom(12)
                ciphertext = AESGCM(key).encrypt(
                    nonce,
                    provisioning_secret.encode(),
                    canonical.identity_id.bytes,
                )
                MergeConvergenceRepository.create_pending_factor(
                    using=self.using,
                    identity_id=canonical.identity_id,
                    setup_challenge=challenge,
                    encrypted_secret=ciphertext,
                    nonce=nonce,
                    key_id=key_id,
                )
                provisioning_url = pyotp.TOTP(provisioning_secret).provisioning_uri(
                    name=canonical.email or canonical.username,
                    issuer_name="ERP Комплект",
                )
        return MergeConvergenceChallenge(
            raw_token,
            idempotency_key,
            expires_at,
            provisioning_secret,
            provisioning_url,
        )

    def complete(
        self,
        *,
        challenge_token: str,
        actor_pk: int,
        new_password: str,
        totp_code: str,
        idempotency_key: uuid.UUID,
        requeue_id: uuid.UUID | None = None,
    ) -> None:
        self.policy.require_enabled()
        if not 12 <= len(new_password) <= 128:
            raise MergeConvergenceError("PASSWORD_POLICY_FAILED")
        digest = hashlib.sha256(challenge_token.encode()).hexdigest()
        operation_id = None
        hold_snapshot = None
        hold_action_digest = ""
        try:
            with transaction.atomic(using=self.using):
                try:
                    candidate_challenge = PreAuthChallenge.objects.using(self.using).select_related(
                        "merge_operation",
                    ).get(token_digest=digest)
                except PreAuthChallenge.DoesNotExist:
                    raise MergeConvergenceError("MERGE_CHALLENGE_INVALID") from None
                operation_id = candidate_challenge.merge_operation_id
                candidate_operation = candidate_challenge.merge_operation
                if candidate_operation is None or candidate_operation.source_user_id != actor_pk:
                    raise MergeConvergenceError("MERGE_SOURCE_AUTHENTICATION_REQUIRED")
                if candidate_challenge.idempotency_key != idempotency_key:
                    raise MergeConvergenceError("MERGE_IDEMPOTENCY_MISMATCH")
                graph = self.coordinator.lock(user_pks=[actor_pk], using=self.using)
                try:
                    operation = next(row for row in graph.operations if row.id == operation_id)
                    challenge = PreAuthChallenge.objects.using(self.using).get(pk=candidate_challenge.pk)
                except (StopIteration, PreAuthChallenge.DoesNotExist):
                    raise MergeConvergenceError("MERGE_CONTEXT_STALE") from None
                if (
                    challenge.merge_operation_id != operation.id
                    or challenge.idempotency_key != idempotency_key
                    or operation.source_user_id != actor_pk
                ):
                    raise MergeConvergenceError("MERGE_CONTEXT_STALE")
                try:
                    require_no_legal_hold(graph, using=self.using)
                except IdentityMergeLegalHoldActive as exc:
                    hold_snapshot = exc.snapshot
                    hold_action_digest = operation.approval_digest
                    raise
                requeue = None
                has_hold_history = IdentityMergeHoldBlock.objects.using(self.using).filter(operation=operation).exists()
                if has_hold_history:
                    if requeue_id is None:
                        raise MergeConvergenceError("MERGE_REQUEUE_REQUIRED")
                    try:
                        requeue = IdentityMergeRequeue.objects.using(self.using).select_for_update().get(
                            pk=requeue_id,
                            operation=operation,
                            action_kind=MergeActionKind.OWNER_CONVERGENCE,
                            resume_mode=MergeResumeMode.OWNER_AUTHENTICATED,
                        )
                    except IdentityMergeRequeue.DoesNotExist:
                        raise MergeConvergenceError("MERGE_REQUEUE_INVALID") from None
                    from apps.users.tasks.identity_merge_requeue import has_valid_release_evidence

                    if (
                        IdentityMergeRequeueResult.objects.using(self.using).filter(requeue=requeue).exists()
                        or not has_valid_release_evidence(requeue)
                    ):
                        raise MergeConvergenceError("MERGE_REQUEUE_INVALID")
                if (
                    challenge.consumed_at is not None
                    and challenge.mfa_verified_at is not None
                    and operation.state == IdentityMergeOperation.State.APPROVED_DEFERRED
                ):
                    return
                if operation.approval_expires_at <= timezone.now():
                    raise MergeConvergenceError("MERGE_APPROVAL_EXPIRED")
                if (
                    challenge.flow_kind != PreAuthChallenge.FlowKind.MERGE_CONVERGENCE
                    or challenge.consumed_at is not None
                    or challenge.revoked_at is not None
                    or challenge.expires_at <= timezone.now()
                ):
                    raise MergeConvergenceError("MERGE_CHALLENGE_INVALID")
                source = next(row for row in graph.users if row.pk == actor_pk)
                canonical = next(
                    (row for row in graph.users if row.identity_id == operation.canonical_identity_id),
                    None,
                )
                if canonical is None:
                    raise MergeConvergenceError("MERGE_CANONICAL_IDENTITY_INVALID")
                if operation.state != IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE:
                    if operation.state == IdentityMergeOperation.State.APPROVED_DEFERRED:
                        return
                    raise MergeConvergenceError("MERGE_OPERATION_NOT_PENDING")
                if (
                    operation.version != challenge.merge_operation_version_snapshot
                    or source.security_version != challenge.security_version_snapshot
                    or canonical.security_version != challenge.canonical_security_version_snapshot
                ):
                    raise MergeConvergenceError("MERGE_CONTEXT_STALE")
                if not self.factor_verifier.verify(canonical, totp_code):
                    raise MergeConvergenceError("MFA_CODE_INVALID")
                try:
                    validate_password(new_password, user=canonical)
                except ValidationError:
                    raise MergeConvergenceError("PASSWORD_POLICY_FAILED") from None
                encoded_password = make_password(new_password)
                deferred_sources = [
                    user
                    for user in graph.users
                    if user.pk != canonical.pk
                ]
                now = timezone.now()
                try:
                    MergeConvergenceRepository.store_password_authority(
                        canonical=canonical,
                        compatibility_sources=deferred_sources,
                        encoded_password=encoded_password,
                        using=self.using,
                    )
                    MergeConvergenceRepository.mark_source_compatibility(
                        source=source,
                        synthetic_normalized_email=operation.synthetic_normalized_email,
                        using=self.using,
                    )
                    MergeConvergenceRepository.confirm_pending_factor(
                        canonical_identity_id=canonical.identity_id,
                        challenge=challenge,
                        now=now,
                        using=self.using,
                    )
                    MergeConvergenceRepository.activate_canonical(canonical=canonical, using=self.using)
                except MergeConvergenceCasConflictError:
                    raise MergeConvergenceError("MERGE_CONTEXT_STALE") from None
                source_actor_pks = [participant.pk for participant in deferred_sources]
                for tenant_id in graph.tenant_ids:
                    MembershipRepository.move_actor_memberships_to_identity(
                        tenant_id=tenant_id,
                        legacy_actor_user_ids=source_actor_pks,
                        canonical_identity_id=canonical.identity_id,
                        using=self.using,
                    )
                try:
                    MergeConvergenceRepository.revoke_graph_artifacts(
                        identity_ids=graph.identity_ids,
                        challenge=challenge,
                        now=now,
                        using=self.using,
                    )
                    MergeConvergenceRepository.consume_challenge(
                        challenge=challenge,
                        now=now,
                        using=self.using,
                    )
                    operation = MergeConvergenceRepository.transition_to_deferred(
                        operation=operation,
                        mode=IdentityMergeOperation.ConvergenceMode.OWNER_CONFIRMED,
                        now=now,
                        using=self.using,
                    )
                except MergeConvergenceCasConflictError:
                    raise MergeConvergenceError("MERGE_CONTEXT_STALE") from None
                IdentityMergeRepository.append_transition_evidence(
                    operation=operation,
                    action_kind=MergeActionKind.OWNER_CONVERGENCE,
                    actor_ref="owner_authenticated",
                    correlation_id=requeue.correlation_id if requeue is not None else challenge.idempotency_key,
                    using=self.using,
                )
                cleanup = IdentityMergeCleanupRepository.ensure_pending(
                    operation=operation,
                    actor_user_pks=[user.pk for user in graph.users],
                    using=self.using,
                )
                if requeue is not None:
                    MergeConvergenceRepository.create_applied_requeue_result(
                        requeue=requeue,
                        observed_operation_version=operation.version,
                        now=now,
                        using=self.using,
                    )
                transaction.on_commit(lambda: self._schedule_legacy_refresh_cleanup(cleanup.pk))
        except IdentityMergeLegalHoldActive:
            if operation_id is not None and hold_snapshot is not None:
                record_hold_evidence(
                    operation_id=operation_id,
                    snapshot=hold_snapshot,
                    action_digest=hold_action_digest,
                    action_kind=MergeActionKind.OWNER_CONVERGENCE,
                    using=self.using,
                )
            raise MergeConvergenceError("LEGAL_HOLD_ACTIVE") from None

    @staticmethod
    def _schedule_legacy_refresh_cleanup(_record_id) -> None:
        from apps.users.tasks.identity_merge_redis_cleanup import process_identity_merge_redis_cleanup

        try:
            process_identity_merge_redis_cleanup.apply_async(kwargs={"batch_size": 100})
        except Exception:
            pass

    def complete_dormant(self, *, operation_id: uuid.UUID) -> IdentityMergeOperation:
        try:
            return self._complete_dormant(operation_id=operation_id)
        except IdentityMergeLegalHoldActive as exc:
            operation = IdentityMergeOperation.objects.using(self.using).only("approval_digest").get(pk=operation_id)
            record_hold_evidence(
                operation_id=operation_id,
                snapshot=exc.snapshot,
                action_digest=operation.approval_digest,
                action_kind=MergeActionKind.DORMANT_CONVERGENCE,
                using=self.using,
            )
            raise MergeConvergenceError("LEGAL_HOLD_ACTIVE") from None

    def _complete_dormant(self, *, operation_id: uuid.UUID) -> IdentityMergeOperation:
        self.policy.require_enabled()
        with transaction.atomic(using=self.using):
            candidate = IdentityMergeOperation.objects.using(self.using).get(pk=operation_id)
            graph = self.coordinator.lock(user_pks=[candidate.source_user_id], using=self.using)
            operation = next(row for row in graph.operations if row.id == candidate.id)
            if operation.state != IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE:
                raise MergeConvergenceError("MERGE_OPERATION_NOT_PENDING")
            if operation.approval_expires_at <= timezone.now():
                raise MergeConvergenceError("MERGE_APPROVAL_EXPIRED")
            allowed_statuses = {
                User.SecurityStatus.LOCKED,
                User.SecurityStatus.PENDING_CREDENTIALS,
                User.SecurityStatus.MFA_SETUP_REQUIRED,
            }
            if any(user.is_active or user.security_status not in allowed_statuses for user in graph.users):
                raise MergeConvergenceError("DORMANT_PROFILE_NOT_SATISFIED")
            require_no_legal_hold(graph, using=self.using)
            now = timezone.now()
            if IdentitySession.objects.using(self.using).filter(
                identity_id__in=graph.identity_ids,
                revoked_at__isnull=True,
                expires_at__gt=now,
            ).exists():
                raise MergeConvergenceError("DORMANT_PROFILE_NOT_SATISFIED")
            if PreAuthChallenge.objects.using(self.using).filter(
                identity_id__in=graph.identity_ids,
                revoked_at__isnull=True,
                consumed_at__isnull=True,
                expires_at__gt=now,
            ).exists():
                raise MergeConvergenceError("DORMANT_PROFILE_NOT_SATISFIED")
            if RecoveryToken.objects.using(self.using).filter(
                identity_id__in=graph.identity_ids,
                revoked_at__isnull=True,
                consumed_at__isnull=True,
                expires_at__gt=now,
            ).exists():
                raise MergeConvergenceError("DORMANT_PROFILE_NOT_SATISFIED")
            graph_session_ids = list(
                IdentitySession.objects.using(self.using)
                .filter(identity_id__in=graph.identity_ids)
                .values_list("id", flat=True),
            )
            if TenantGrantSwitchOperation.objects.using(self.using).filter(
                session_id__in=graph_session_ids,
                status__in=[
                    TenantGrantSwitchOperation.Status.PENDING,
                    TenantGrantSwitchOperation.Status.DB_COMMITTED,
                ],
            ).exists():
                raise MergeConvergenceError("DORMANT_PROFILE_NOT_SATISFIED")
            canonical = next(user for user in graph.users if user.identity_id == operation.canonical_identity_id)
            source = next(user for user in graph.users if user.pk == operation.source_user_id)
            actor_pks = [user.pk for user in graph.users]
            membership_sets = []
            for tenant_id in DurableIamTenantInventoryProvider(using=self.using).iter_tenant_ids():
                with tenant_rls_scope(tenant_id, using=self.using):
                    memberships = TenantMembership.objects.using(self.using).select_for_update().filter(
                        tenant_id=tenant_id,
                        legacy_actor_user_id__in=actor_pks,
                        is_deleted=False,
                    )
                    allowed_membership_statuses = [
                        TenantMembership.Status.DISABLED,
                        TenantMembership.Status.DRAFT,
                    ]
                    if memberships.exclude(status__in=allowed_membership_statuses).exists():
                        raise MergeConvergenceError("DORMANT_PROFILE_NOT_SATISFIED")
                    membership_ids = list(memberships.values_list("id", flat=True))
                    if TenantSessionGrant.objects.using(self.using).filter(
                        tenant_id=tenant_id,
                        membership_id__in=membership_ids,
                        superseded_at__isnull=True,
                        revoked_at__isnull=True,
                        expires_at__gt=now,
                    ).exists():
                        raise MergeConvergenceError("DORMANT_PROFILE_NOT_SATISFIED")
                    membership_sets.append((tenant_id, membership_ids))
            redis_client = getattr(settings, "REDIS_CLIENT", None)
            if redis_client is not None:
                for user in graph.users:
                    if next(redis_client.scan_iter(match=f"auth:refresh:{user.pk}:*"), None) is not None:
                        raise MergeConvergenceError("DORMANT_PROFILE_NOT_SATISFIED")
            for tenant_id, _membership_ids in membership_sets:
                MembershipRepository.move_actor_memberships_to_identity(
                    tenant_id=tenant_id,
                    legacy_actor_user_ids=actor_pks,
                    canonical_identity_id=canonical.identity_id,
                    using=self.using,
                )
            try:
                MergeConvergenceRepository.mark_source_compatibility(
                    source=source,
                    synthetic_normalized_email=operation.synthetic_normalized_email,
                    using=self.using,
                )
                operation = MergeConvergenceRepository.transition_to_deferred(
                    operation=operation,
                    mode=IdentityMergeOperation.ConvergenceMode.DORMANT_FAIL_CLOSED,
                    now=now,
                    using=self.using,
                )
            except MergeConvergenceCasConflictError:
                raise MergeConvergenceError("MERGE_CONTEXT_STALE") from None
            IdentityMergeRepository.append_transition_evidence(
                operation=operation,
                action_kind=MergeActionKind.DORMANT_CONVERGENCE,
                actor_ref="dormant_operator",
                correlation_id=uuid.uuid4(),
                using=self.using,
            )
            return operation
