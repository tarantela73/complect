from collections.abc import Iterable

from django.db.models import F
from django.utils import timezone

from apps.users.models import (
    BackupCode,
    IdentityMergeOperation,
    IdentityMergeRequeue,
    IdentityMergeRequeueResult,
    IdentitySession,
    MfaCredential,
    PreAuthChallenge,
    RecoveryToken,
    User,
)


class MergeConvergenceCasConflictError(RuntimeError):
    pass


class MergeConvergenceRepository:
    @staticmethod
    def revoke_stale_challenges(*, challenge_ids, now, using: str = "default") -> None:
        if not challenge_ids:
            return
        credential_ids = list(
            MfaCredential.objects.using(using).filter(
                setup_challenge_id__in=challenge_ids,
                confirmed_at__isnull=True,
                revoked_at__isnull=True,
                is_legal_hold=False,
            ).values_list("id", flat=True),
        )
        if credential_ids:
            changed = MfaCredential.objects.using(using).filter(
                id__in=credential_ids,
                revoked_at__isnull=True,
                is_legal_hold=False,
            ).update(revoked_at=now, version=F("version") + 1, updated_at=now)
            if changed != len(credential_ids):
                raise MergeConvergenceCasConflictError("MERGE_CONTEXT_STALE")
        changed = PreAuthChallenge.objects.using(using).filter(
            id__in=challenge_ids,
            consumed_at__isnull=True,
            revoked_at__isnull=True,
            is_legal_hold=False,
        ).update(revoked_at=now, version=F("version") + 1, updated_at=now)
        if changed != len(challenge_ids):
            raise MergeConvergenceCasConflictError("MERGE_CONTEXT_STALE")

    @staticmethod
    def create_challenge(*, using: str = "default", **fields) -> PreAuthChallenge:
        return PreAuthChallenge.objects.using(using).create(**fields)

    @staticmethod
    def create_pending_factor(*, using: str = "default", **fields) -> MfaCredential:
        return MfaCredential.objects.using(using).create(**fields)

    @staticmethod
    def create_applied_requeue_result(
        *,
        requeue: IdentityMergeRequeue,
        observed_operation_version: int,
        now,
        using: str = "default",
    ) -> None:
        IdentityMergeRequeueResult.objects.using(using).create(
            requeue=requeue,
            outcome="applied",
            observed_operation_version=observed_operation_version,
            processed_at=now,
        )

    @staticmethod
    def store_password_authority(
        *,
        canonical: User,
        compatibility_sources: Iterable[User],
        encoded_password: str,
        using: str = "default",
    ) -> None:
        changed = User.objects.using(using).filter(
            pk=canonical.pk,
            identity_id=canonical.identity_id,
            security_version=canonical.security_version,
            identity_legal_hold=False,
        ).update(password=encoded_password, security_version=F("security_version") + 1)
        if changed != 1:
            raise MergeConvergenceCasConflictError("MERGE_CONTEXT_STALE")
        for source in compatibility_sources:
            changed = User.objects.using(using).filter(
                pk=source.pk,
                identity_id=source.identity_id,
                security_version=source.security_version,
                identity_legal_hold=False,
            ).update(password=encoded_password)
            if changed != 1:
                raise MergeConvergenceCasConflictError("MERGE_CONTEXT_STALE")

    @staticmethod
    def mark_source_compatibility(
        *,
        source: User,
        synthetic_normalized_email: str,
        using: str = "default",
    ) -> None:
        changed = User.objects.using(using).filter(
            pk=source.pk,
            identity_id=source.identity_id,
            security_version=source.security_version,
            identity_legal_hold=False,
        ).update(
            normalized_email=synthetic_normalized_email,
            is_active=False,
            security_status=User.SecurityStatus.LOCKED,
        )
        if changed != 1:
            raise MergeConvergenceCasConflictError("MERGE_CONTEXT_STALE")

    @staticmethod
    def activate_canonical(*, canonical: User, using: str = "default") -> None:
        changed = User.objects.using(using).filter(
            pk=canonical.pk,
            identity_id=canonical.identity_id,
            security_version=canonical.security_version + 1,
            identity_legal_hold=False,
        ).update(security_status=User.SecurityStatus.ACTIVE)
        if changed != 1:
            raise MergeConvergenceCasConflictError("MERGE_CONTEXT_STALE")

    @staticmethod
    def confirm_pending_factor(
        *,
        canonical_identity_id,
        challenge: PreAuthChallenge,
        now,
        using: str = "default",
    ) -> None:
        ids = list(
            MfaCredential.objects.using(using).filter(
                identity_id=canonical_identity_id,
                setup_challenge=challenge,
                confirmed_at__isnull=True,
                revoked_at__isnull=True,
                is_legal_hold=False,
            ).values_list("id", flat=True),
        )
        if not ids:
            return
        changed = MfaCredential.objects.using(using).filter(
            id__in=ids,
            confirmed_at__isnull=True,
            revoked_at__isnull=True,
            is_legal_hold=False,
        ).update(confirmed_at=now, version=F("version") + 1, updated_at=now)
        if changed != len(ids):
            raise MergeConvergenceCasConflictError("MERGE_CONTEXT_STALE")

    @staticmethod
    def revoke_graph_artifacts(*, identity_ids, challenge: PreAuthChallenge, now, using: str = "default") -> None:
        specifications = (
            (IdentitySession, {"revoked_at__isnull": True}),
            (BackupCode, {"revoked_at__isnull": True}),
            (RecoveryToken, {"revoked_at__isnull": True}),
        )
        for model, filters in specifications:
            ids = list(
                model.objects.using(using).filter(
                    identity_id__in=identity_ids,
                    is_legal_hold=False,
                    **filters,
                ).values_list("id", flat=True),
            )
            if not ids:
                continue
            changed = model.objects.using(using).filter(
                id__in=ids,
                is_legal_hold=False,
                **filters,
            ).update(revoked_at=now, version=F("version") + 1, updated_at=now)
            if changed != len(ids):
                raise MergeConvergenceCasConflictError("MERGE_CONTEXT_STALE")
        other_challenge_ids = list(
            PreAuthChallenge.objects.using(using).filter(
                identity_id__in=identity_ids,
                revoked_at__isnull=True,
                consumed_at__isnull=True,
                is_legal_hold=False,
            ).exclude(pk=challenge.pk).values_list("id", flat=True),
        )
        if other_challenge_ids:
            changed = PreAuthChallenge.objects.using(using).filter(
                id__in=other_challenge_ids,
                revoked_at__isnull=True,
                consumed_at__isnull=True,
                is_legal_hold=False,
            ).update(revoked_at=now, version=F("version") + 1, updated_at=now)
            if changed != len(other_challenge_ids):
                raise MergeConvergenceCasConflictError("MERGE_CONTEXT_STALE")

    @staticmethod
    def consume_challenge(*, challenge: PreAuthChallenge, now, using: str = "default") -> None:
        changed = PreAuthChallenge.objects.using(using).filter(
            pk=challenge.pk,
            version=challenge.version,
            consumed_at__isnull=True,
            revoked_at__isnull=True,
            is_legal_hold=False,
        ).update(
            consumed_at=now,
            mfa_verified_at=now,
            version=F("version") + 1,
            updated_at=now,
        )
        if changed != 1:
            raise MergeConvergenceCasConflictError("MERGE_CONTEXT_STALE")

    @staticmethod
    def transition_to_deferred(
        *,
        operation: IdentityMergeOperation,
        mode: str,
        now=None,
        using: str = "default",
    ) -> IdentityMergeOperation:
        now = now or timezone.now()
        changed = IdentityMergeOperation.objects.using(using).filter(
            pk=operation.pk,
            state=IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE,
            version=operation.version,
            credential_epoch=0,
            is_legal_hold=False,
        ).update(
            state=IdentityMergeOperation.State.APPROVED_DEFERRED,
            convergence_mode=mode,
            credential_epoch=1,
            credentials_changed_at=now,
            credentials_converged_at=now,
            version=F("version") + 1,
            updated_at=now,
        )
        if changed != 1:
            raise MergeConvergenceCasConflictError("MERGE_CONTEXT_STALE")
        operation.refresh_from_db(using=using)
        return operation
