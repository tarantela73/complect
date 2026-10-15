import hashlib
import hmac
import json
import uuid
from collections.abc import Iterable
from uuid import UUID

from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

from apps.common.services.outbox_service import TransactionalOutboxService
from apps.users.events import IdentityMergeAuthorityChangedEvent
from apps.users.models import IdentityMergeOperation, IdentitySecurityAuditRecord


class IdentityMergeRepository:
    @staticmethod
    def create_pending(*, using: str = "default", **fields) -> IdentityMergeOperation:
        return IdentityMergeOperation.objects.using(using).create(**fields)

    @staticmethod
    def cancel_pending(operation: IdentityMergeOperation, *, using: str = "default") -> bool:
        return (
            IdentityMergeOperation.objects.using(using).filter(
                pk=operation.pk,
                state=IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE,
                version=operation.version,
                is_legal_hold=False,
            ).update(
                state=IdentityMergeOperation.State.CANCELLED,
                version=F("version") + 1,
                updated_at=timezone.now(),
            )
            == 1
        )

    @staticmethod
    def append_transition_evidence(
        *,
        operation: IdentityMergeOperation,
        action_kind: str,
        actor_ref: str,
        correlation_id: uuid.UUID,
        using: str = "default",
    ) -> None:
        now = timezone.now()
        subject_hmac = hmac.new(
            settings.SECRET_KEY.encode(),
            f"identity-merge-source:{operation.source_identity_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        IdentitySecurityAuditRecord.objects.using(using).create(
            identity_id=operation.source_identity_id,
            event_type="identity.merge_authority_changed",
            actor_type="system",
            actor_ref=actor_ref,
            reason_code=action_kind.upper(),
            subject_hmac=subject_hmac,
            request_id=uuid.uuid4(),
            correlation_id=correlation_id,
            metadata={"operation_id": str(operation.id), "operation_version": operation.version},
            occurred_at=now,
        )
        TransactionalOutboxService.push_to_outbox(
            IdentityMergeAuthorityChangedEvent(
                event_id=uuid.uuid4(),
                event_version=1,
                occurred_at=now,
                correlation_id=correlation_id,
                operation_id=operation.id,
                action_kind=action_kind,
                operation_version=operation.version,
            ),
        )

    @staticmethod
    def expire_pending(operation: IdentityMergeOperation, *, using: str = "default") -> bool:
        if operation.approval_expires_at > timezone.now():
            return False
        return (
            IdentityMergeOperation.objects.using(using).filter(
                pk=operation.pk,
                state=IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE,
                version=operation.version,
                is_legal_hold=False,
            ).update(
                state=IdentityMergeOperation.State.EXPIRED,
                version=F("version") + 1,
                updated_at=timezone.now(),
            )
            == 1
        )

    @staticmethod
    def participant_operations(identity_id: UUID | None, *, using: str = "default"):
        if identity_id is None:
            return IdentityMergeOperation.objects.using(using).none()
        return IdentityMergeOperation.objects.using(using).filter(
            Q(source_identity_id=identity_id) | Q(canonical_identity_id=identity_id),
        )

    @staticmethod
    def participant_digest(operations: Iterable[IdentityMergeOperation]) -> str:
        payload = [
            {"id": str(operation.id), "state": operation.state, "version": operation.version}
            for operation in sorted(operations, key=lambda row: row.id.bytes)
        ]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def live_pending_for(identity_id: UUID, *, using: str = "default"):
        return IdentityMergeRepository.participant_operations(identity_id, using=using).filter(
            state=IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE,
        )
