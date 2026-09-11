import hashlib
import hmac
import uuid
from datetime import timedelta
from enum import StrEnum

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.users.models import IdentityMergeOperation, IdentitySecurityAuditRecord, TenantMembership, User
from apps.users.models_identity import MergeActionKind
from apps.users.repositories.identity_collision_repository import IdentityCollisionRepository
from apps.users.repositories.identity_merge_repository import IdentityMergeRepository
from apps.users.repositories.membership_repository import MembershipRepository
from apps.users.security.identity_merge_legal_hold import (
    IdentityMergeLegalHoldActive,
    record_hold_evidence,
    require_no_legal_hold,
)
from apps.users.security.identity_merge_locking import IdentityMergeLockCoordinator
from apps.users.security.reconciliation_approval import (
    ReconciliationApproval,
    ReconciliationApprovalVerifier,
    canonical_action_digest,
    decode_signature,
)
from apps.users.services.email_normalization import is_reserved_merge_email, normalize_email
from apps.users.services.identity_foundation import ensure_identity_foundation
from apps.users.services.pii_access_audit_service import PiiAccessAuditService


class IdentityCollisionResolutionErrorCode(StrEnum):
    MERGE_OPERATION_ACTIVE = "MERGE_OPERATION_ACTIVE"
    APPROVAL_CONTEXT_STALE = "APPROVAL_CONTEXT_STALE"
    LEGAL_HOLD_ACTIVE = "LEGAL_HOLD_ACTIVE"
    MERGE_GRAPH_CHANGED_RETRY = "MERGE_GRAPH_CHANGED_RETRY"
    INVALID_APPROVAL = "INVALID_APPROVAL"
    INVALID_COLLISION = "INVALID_COLLISION"
    RESERVED_EMAIL = "RESERVED_EMAIL"
    MERGE_SOURCE_REALIZED = "MERGE_SOURCE_REALIZED"
    CONCURRENT_UPDATE = "CONCURRENT_UPDATE"
    CANONICAL_CONVERGENCE_BUSY = "CANONICAL_CONVERGENCE_BUSY"
    NOT_FOUND = "NOT_FOUND"


class IdentityCollisionResolutionError(RuntimeError):
    def __init__(self, code: IdentityCollisionResolutionErrorCode):
        self.code = code
        super().__init__(code.value)


class IdentityCollisionResolutionService:
    def __init__(
        self,
        *,
        verifier: type[ReconciliationApprovalVerifier] = ReconciliationApprovalVerifier,
        coordinator: IdentityMergeLockCoordinator | None = None,
        using: str = "default",
    ) -> None:
        self.verifier = verifier
        self.coordinator = coordinator or IdentityMergeLockCoordinator()
        self.using = using

    def _audit_before_pii(self, *, user_pks: list[int], reference: str) -> None:
        PiiAccessAuditService.append_batch_and_commit(
            subjects=IdentityCollisionRepository.enumerate_subjects(user_pks=user_pks, using=self.using),
            actor_type="operator",
            actor_ref=reference,
            reason_code="IDENTITY_COLLISION_RESOLUTION",
            request_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            using=self.using,
        )

    def _audit(self, *, user: User, event_type: str, reference: str) -> None:
        subject_hmac = hmac.new(
            settings.SECRET_KEY.encode(),
            f"identity-user-pk:{user.pk}".encode(),
            hashlib.sha256,
        ).hexdigest()
        IdentitySecurityAuditRecord.objects.using(self.using).create(
            identity_id=user.identity_id,
            event_type=event_type,
            actor_type="operator",
            actor_ref=reference,
            reason_code="IDENTITY_EMAIL_RECONCILIATION",
            subject_hmac=subject_hmac,
            request_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            metadata={"approval_reference": reference},
            occurred_at=timezone.now(),
        )

    def _participant_digest(self, user: User, operations=None) -> str:
        if operations is None:
            operations = IdentityMergeRepository.participant_operations(user.identity_id, using=self.using)
        return IdentityMergeRepository.participant_digest(operations)

    @staticmethod
    def _would_create_cycle(*, source_identity_id, canonical_identity_id, operations) -> bool:
        next_identity = {
            operation.source_identity_id: operation.canonical_identity_id
            for operation in operations
            if operation.state not in {IdentityMergeOperation.State.CANCELLED, IdentityMergeOperation.State.EXPIRED}
        }
        cursor = canonical_identity_id
        visited = set()
        while cursor in next_identity:
            if cursor in visited:
                return True
            visited.add(cursor)
            cursor = next_identity[cursor]
            if cursor == source_identity_id:
                return True
        return False

    def build_correct_email_action(self, *, user: User, new_email: str, reference: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "purpose": "identity-correct-email",
            "approval_reference": reference,
            "user_pk": user.pk,
            "normalized_email": normalize_email(new_email).value,
            "expected_security_version": user.security_version or 1,
            "participant_operations_digest": self._participant_digest(user),
        }

    @staticmethod
    def build_merge_action(*, source: User, canonical: User, reference: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "purpose": "identity-merge-approval",
            "approval_reference": reference,
            "source_user_pk": source.pk,
            "canonical_user_pk": canonical.pk,
            "source_security_version": source.security_version or 1,
            "canonical_security_version": canonical.security_version or 1,
        }

    @staticmethod
    def build_cancel_action(*, operation: IdentityMergeOperation, reference: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "purpose": "identity-merge-cancel",
            "approval_reference": reference,
            "operation_id": str(operation.id),
            "expected_version": operation.version,
        }

    def _verify(self, approval: ReconciliationApproval, current_action: dict[str, object]) -> None:
        if approval.action is not None and approval.action != current_action:
            raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.APPROVAL_CONTEXT_STALE)
        if not self.verifier.verify(approval, approval.action or current_action):
            raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.APPROVAL_CONTEXT_STALE)

    def correct_email(
        self,
        *,
        user_pk: int,
        new_email: str,
        approval: ReconciliationApproval,
    ) -> User:
        if is_reserved_merge_email(new_email):
            raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.RESERVED_EMAIL)
        normalized = normalize_email(new_email)
        self._audit_before_pii(user_pks=[user_pk], reference=approval.reference)
        with transaction.atomic(using=self.using):
            graph = self.coordinator.lock(user_pks=[user_pk], using=self.using)
            try:
                user = next(row for row in graph.users if row.pk == user_pk)
            except StopIteration:
                raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.NOT_FOUND) from None
            try:
                require_no_legal_hold(graph, using=self.using)
            except IdentityMergeLegalHoldActive:
                raise IdentityCollisionResolutionError(
                    IdentityCollisionResolutionErrorCode.LEGAL_HOLD_ACTIVE,
                ) from None
            realized_source = [
                operation
                for operation in graph.operations
                if operation.source_user_id == user.pk
                and operation.state
                in {
                    IdentityMergeOperation.State.APPROVED_DEFERRED,
                    IdentityMergeOperation.State.READY_TO_FINALIZE,
                    IdentityMergeOperation.State.FINALIZED,
                }
            ]
            if realized_source:
                raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.MERGE_SOURCE_REALIZED)
            current_action = self.build_correct_email_action(
                user=user,
                new_email=new_email,
                reference=approval.reference,
            )
            self._verify(approval, current_action)
            if any(
                operation.state == IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE
                for operation in graph.operations
            ):
                raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.MERGE_OPERATION_ACTIVE)
            foundation = ensure_identity_foundation(user, using=self.using)
            if foundation.changed:
                graph = self.coordinator.lock(user_pks=[user_pk], using=self.using)
                user = next(row for row in graph.users if row.pk == user_pk)
            expected_version = foundation.security_version
            updates: dict[str, object] = {
                "email": new_email.strip(),
                "normalized_email": normalized.value,
                "email_normalization_version": normalized.version,
                "security_version": F("security_version") + 1,
            }
            if not user.username or user.username == user.email:
                updates["username"] = new_email.strip()
            changed = IdentityCollisionRepository.correct_email_cas(
                user=user,
                expected_security_version=expected_version,
                updates=updates,
                using=self.using,
            )
            if not changed:
                raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.CONCURRENT_UPDATE)
            user.refresh_from_db(using=self.using)
            if user.tenant_id:
                MembershipRepository.ensure_foundation(
                    tenant_id=user.tenant_id,
                    identity_id=user.identity_id,
                    legacy_actor_user_id=user.pk,
                    display_name=user.get_full_name() or user.username,
                    status=TenantMembership.Status.ACTIVE if user.is_active else TenantMembership.Status.DISABLED,
                    using=self.using,
                )
            self._audit(user=user, event_type="identity.email_corrected", reference=approval.reference)
            return user

    def approve_merge(
        self,
        *,
        source_pk: int,
        canonical_pk: int,
        approval: ReconciliationApproval,
    ) -> IdentityMergeOperation:
        if source_pk == canonical_pk:
            raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.INVALID_COLLISION)
        self._audit_before_pii(user_pks=[source_pk, canonical_pk], reference=approval.reference)
        with transaction.atomic(using=self.using):
            graph = self.coordinator.lock(user_pks=[source_pk, canonical_pk], using=self.using)
            users = {user.pk: user for user in graph.users}
            if source_pk not in users or canonical_pk not in users:
                raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.NOT_FOUND)
            source, canonical = users[source_pk], users[canonical_pk]
            try:
                require_no_legal_hold(graph, using=self.using)
            except IdentityMergeLegalHoldActive:
                raise IdentityCollisionResolutionError(
                    IdentityCollisionResolutionErrorCode.LEGAL_HOLD_ACTIVE,
                ) from None
            source_foundation = ensure_identity_foundation(source, using=self.using)
            canonical_foundation = ensure_identity_foundation(canonical, using=self.using)
            if source_foundation.changed or canonical_foundation.changed:
                graph = self.coordinator.lock(user_pks=[source_pk, canonical_pk], using=self.using)
                users = {user.pk: user for user in graph.users}
            source, canonical = users[source_pk], users[canonical_pk]
            if normalize_email(source.email or source.username).value != normalize_email(
                canonical.email or canonical.username,
            ).value:
                raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.INVALID_COLLISION)
            action = self.build_merge_action(source=source, canonical=canonical, reference=approval.reference)
            self._verify(approval, action)
            existing = IdentityMergeOperation.objects.using(self.using).filter(
                Q(source_user=source)
                & ~Q(state__in=[IdentityMergeOperation.State.CANCELLED, IdentityMergeOperation.State.EXPIRED]),
            ).first()
            if existing and existing.approval_digest == canonical_action_digest(action):
                return existing
            if existing:
                raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.MERGE_OPERATION_ACTIVE)
            pending_for_canonical = next(
                (
                    operation
                    for operation in graph.operations
                    if operation.canonical_identity_id == canonical.identity_id
                    and operation.state == IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE
                ),
                None,
            )
            if pending_for_canonical is not None:
                raise IdentityCollisionResolutionError(
                    IdentityCollisionResolutionErrorCode.CANONICAL_CONVERGENCE_BUSY,
                )
            if any(
                operation.state == IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE
                for operation in graph.operations
            ):
                raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.MERGE_OPERATION_ACTIVE)
            if self._would_create_cycle(
                source_identity_id=source.identity_id,
                canonical_identity_id=canonical.identity_id,
                operations=graph.operations,
            ):
                raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.INVALID_COLLISION)
            now = timezone.now()
            operation = IdentityMergeRepository.create_pending(
                using=self.using,
                source_user=source,
                source_identity_id=source_foundation.identity_id,
                canonical_identity_id=canonical_foundation.identity_id,
                state=IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE,
                approval_reference=approval.reference,
                approval_digest=canonical_action_digest(action),
                approval_key_id=approval.key_id,
                approval_signature=decode_signature(approval.signature),
                approved_at=now,
                approval_expires_at=now + timedelta(hours=24),
                synthetic_normalized_email=f"merged+{source_foundation.identity_id}@identity.invalid",
                retention_until=now + timedelta(days=365),
            )
            for user in (source, canonical):
                if user.tenant_id:
                    MembershipRepository.ensure_foundation(
                        tenant_id=user.tenant_id,
                        identity_id=user.identity_id,
                        legacy_actor_user_id=user.pk,
                        display_name=user.get_full_name() or user.username,
                        status=TenantMembership.Status.ACTIVE if user.is_active else TenantMembership.Status.DISABLED,
                        using=self.using,
                    )
            self._audit(user=source, event_type="identity.merge_approved", reference=approval.reference)
            return operation

    def cancel_merge(
        self,
        *,
        operation_id: uuid.UUID,
        approval: ReconciliationApproval,
    ) -> IdentityMergeOperation:
        hold_failure = None
        try:
            with transaction.atomic(using=self.using):
                try:
                    candidate = IdentityMergeOperation.objects.using(self.using).get(pk=operation_id)
                except IdentityMergeOperation.DoesNotExist:
                    raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.NOT_FOUND) from None
                graph = self.coordinator.lock(user_pks=[candidate.source_user_id], using=self.using)
                operation = next(row for row in graph.operations if row.id == candidate.id)
                action = self.build_cancel_action(operation=operation, reference=approval.reference)
                if operation.state == IdentityMergeOperation.State.CANCELLED:
                    action["expected_version"] = operation.version - 1
                    self._verify(approval, action)
                    return operation
                self._verify(approval, action)
                try:
                    require_no_legal_hold(graph, using=self.using)
                except IdentityMergeLegalHoldActive as exc:
                    hold_failure = (exc.snapshot, operation.approval_digest)
                    raise
                if operation.state != IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE:
                    raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.APPROVAL_CONTEXT_STALE)
                if not IdentityMergeRepository.cancel_pending(operation, using=self.using):
                    raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.CONCURRENT_UPDATE)
                operation.refresh_from_db(using=self.using)
                self._audit(
                    user=operation.source_user,
                    event_type="identity.merge_cancelled",
                    reference=approval.reference,
                )
                return operation
        except IdentityMergeLegalHoldActive:
            if hold_failure is not None:
                snapshot, action_digest = hold_failure
                record_hold_evidence(
                    operation_id=operation_id,
                    snapshot=snapshot,
                    action_digest=action_digest,
                    action_kind=MergeActionKind.CANCEL_PENDING,
                    using=self.using,
                )
            raise IdentityCollisionResolutionError(IdentityCollisionResolutionErrorCode.LEGAL_HOLD_ACTIVE) from None
