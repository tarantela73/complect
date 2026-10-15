"""Persistence of pre-authorized request snapshots; no supported hold writer yet."""

from uuid import UUID

from django.db.models import F
from django.utils import timezone

from apps.users.models_cleanup_protocol import IdentityHoldRequest
from apps.users.security.identity_cleanup_admission import CleanupAdmissionError, cleanup_catalog_phase


class IdentityHoldRequestRepository:
    @staticmethod
    def request(
        *,
        idempotency_key: UUID,
        identity_ids: tuple[UUID, ...],
        actor_pks: tuple[int, ...],
        tenant_ids: tuple[UUID, ...],
        targets: tuple[dict, ...],
        requester_reference: str,
        reason_code: str,
        request_id: UUID,
        correlation_id: UUID,
        using: str = "default",
    ) -> IdentityHoldRequest:
        """Caller must authorize and resolve this exact snapshot before invoking it.

        This is not a Legal Hold installation API. There are no runtime callers.
        """
        payload = dict(
            identity_ids=sorted(set(identity_ids)),
            actor_pks=sorted(set(actor_pks)),
            tenant_ids=sorted(set(tenant_ids)),
            targets=list(targets),
            requester_reference=requester_reference,
            reason_code=reason_code,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        with cleanup_catalog_phase(using=using):
            existing = IdentityHoldRequest.objects.using(using).filter(idempotency_key=idempotency_key).first()
            if existing is not None:
                if any(getattr(existing, name) != value for name, value in payload.items()):
                    raise CleanupAdmissionError("HOLD_IDEMPOTENCY_PAYLOAD_CONFLICT")
                return existing
            created = IdentityHoldRequest.objects.using(using).create(
                idempotency_key=idempotency_key,
                **payload,
            )
            created.refresh_from_db(using=using, fields=["payload_digest"])
            return created

    @staticmethod
    def activate(*, request_id: UUID, expected_version: int, using: str = "default") -> None:
        raise NotImplementedError("HOLD_ACTIVATION_NOT_IMPLEMENTED")

    @staticmethod
    def release_requested(
        *,
        request_id: UUID,
        expected_version: int,
        decision_reference: str,
        actor_reference: str,
        using: str = "default",
    ) -> bool:
        """Pre-authorized pending-request metadata only; never releases an active hold.

        The admission/resume barrier survives. No runtime caller is installed.
        """
        with cleanup_catalog_phase(using=using):
            return (
                IdentityHoldRequest.objects.using(using)
                .filter(
                    pk=request_id,
                    version=expected_version,
                    state="REQUESTED",
                )
                .update(
                    state="RELEASED",
                    version=F("version") + 1,
                    updated_at=timezone.now(),
                    release_evidence={"decision_reference": decision_reference, "actor_reference": actor_reference},
                )
                == 1
            )
