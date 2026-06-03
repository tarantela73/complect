import uuid
from datetime import timedelta

import structlog
from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone as tz

from apps.common.models.processed_event import ProcessedEvent

logger = structlog.get_logger(__name__)

_LEASE_TTL: int = getattr(settings, "CONSUMER_LEASE_TTL_SECONDS", 300)

# Sentinel: distinguishes "caller passed no lease_ttl" from "caller passed None".
# Default (no arg) → use settings.CONSUMER_LEASE_TTL_SECONDS (safe-by-default).
# Explicit None   → infinite lease (unsafe opt-out for legacy consumers).
_UNSET = object()


class ConsumerIdempotencyService:
    """
    Distributed delivery guarantee for at-least-once event consumers.

    Lifecycle:
        claimed  → claim() returns True, acked_at=NULL  (processing started)
        acked    → ack() stamps acked_at                 (processing confirmed)
        duplicate→ claim() returns False                 (call reclaim() to check expiry)
        reclaimed→ reclaim() returns True                (expired lease taken over)

    Ownership model:
        When instance_id is provided to claim(), ack() requires the same instance_id.
        Prevents cross-instance ACK: instance B cannot confirm work claimed by A.
        Without instance_id, ACK is unbound (legacy consumers).

    Lease model (ADR-0011):
        Lease is enabled by default (safe-by-default, unsafe-by-opt-in).
        claim() without lease_ttl uses settings.CONSUMER_LEASE_TTL_SECONDS.
        claim(lease_ttl=None) creates an infinite lease — explicit opt-out only.
        After expiry, reclaim() atomically transfers ownership to another instance.

    The INSERT + IntegrityError pattern is race-free: the DB unique constraint
    on (outbox_record_id, consumer) is the only true gate.
    """

    @staticmethod
    def claim(
        outbox_record_id: uuid.UUID,
        consumer: str,
        tenant_id: uuid.UUID,
        instance_id: uuid.UUID | None = None,
        lease_ttl: int | None = _UNSET,  # type: ignore[assignment]
    ) -> bool:
        """
        Attempt to claim processing rights for an event.

        Returns True  — first claim: proceed with processing.
        Returns False — already claimed: call reclaim() to check if lease expired.
        Never raises.

        lease_ttl not provided → default TTL from settings (recoverable by default).
        lease_ttl=None         → infinite lease, reclaim() cannot recover this claim.
        lease_ttl=N            → custom TTL in seconds.
        """
        if lease_ttl is _UNSET:
            effective_ttl: int | None = _LEASE_TTL
        else:
            effective_ttl = lease_ttl  # type: ignore[assignment]

        lease_expires_at = tz.now() + timedelta(seconds=effective_ttl) if effective_ttl else None
        try:
            ProcessedEvent.objects.create(
                outbox_record_id=outbox_record_id,
                consumer=consumer,
                tenant_id=tenant_id,
                claimed_by_instance_id=instance_id,
                lease_expires_at=lease_expires_at,
            )
            return True
        except IntegrityError:
            logger.debug(
                "consumer_event_already_claimed",
                outbox_record_id=str(outbox_record_id),
                consumer=consumer,
            )
            return False

    @staticmethod
    def reclaim(
        outbox_record_id: uuid.UUID,
        consumer: str,
        tenant_id: uuid.UUID,
        instance_id: uuid.UUID | None = None,
        lease_ttl: int | None = None,
    ) -> bool:
        """
        Atomically take over an expired lease on an unacked event.

        Returns True  — lease was expired; ownership transferred to this instance.
        Returns False — no expired lease found (valid claim or already acked).
        Never raises.

        Race-safe: UPDATE WHERE lease_expires_at < now() is atomic at the DB level.
        Two concurrent workers: the first UPDATE sets a new lease_expires_at; the
        second finds the condition no longer satisfied → 0 rows → False.

        Only works on records with lease_expires_at IS NOT NULL. Infinite-lease claims
        (lease_expires_at=NULL) are unaffected — they require manual recovery.
        """
        try:
            ttl = lease_ttl if lease_ttl is not None else _LEASE_TTL
            now = tz.now()
            updated = ProcessedEvent.objects.filter(
                outbox_record_id=outbox_record_id,
                consumer=consumer,
                acked_at__isnull=True,
                lease_expires_at__isnull=False,
                lease_expires_at__lt=now,
            ).update(
                claimed_by_instance_id=instance_id,
                lease_expires_at=now + timedelta(seconds=ttl),
            )
            if updated:
                logger.info(
                    "consumer_event_reclaimed",
                    outbox_record_id=str(outbox_record_id),
                    consumer=consumer,
                    instance_id=str(instance_id) if instance_id else None,
                )
            return bool(updated)
        except Exception as exc:
            logger.error(
                "consumer_event_reclaim_failed",
                outbox_record_id=str(outbox_record_id),
                consumer=consumer,
                error=str(exc),
            )
            return False

    @staticmethod
    def heartbeat(
        outbox_record_id: uuid.UUID,
        consumer: str,
        instance_id: uuid.UUID,
        lease_ttl: int | None = None,
    ) -> bool:
        """
        Extend the lease for a long-running consumer.

        Call every lease_ttl/3 seconds during processing to prevent false reclaim.
        Only the current claim owner can heartbeat (instance_id verified).

        Returns True  — lease extended.
        Returns False — lease already expired or acked (may have been reclaimed).
        Never raises.
        """
        try:
            ttl = lease_ttl if lease_ttl is not None else _LEASE_TTL
            now = tz.now()
            updated = ProcessedEvent.objects.filter(
                outbox_record_id=outbox_record_id,
                consumer=consumer,
                claimed_by_instance_id=instance_id,
                acked_at__isnull=True,
                lease_expires_at__gt=now,
            ).update(lease_expires_at=now + timedelta(seconds=ttl))
            if not updated:
                logger.warning(
                    "consumer_event_heartbeat_missed",
                    outbox_record_id=str(outbox_record_id),
                    consumer=consumer,
                    instance_id=str(instance_id),
                )
            return bool(updated)
        except Exception as exc:
            logger.error(
                "consumer_event_heartbeat_failed",
                outbox_record_id=str(outbox_record_id),
                consumer=consumer,
                error=str(exc),
            )
            return False

    @staticmethod
    def ack(
        outbox_record_id: uuid.UUID,
        consumer: str,
        instance_id: uuid.UUID | None = None,
    ) -> bool:
        """
        Confirm successful completion of event processing.

        Returns True  — acked (acked_at stamped).
        Returns False — no matching claim, instance_id mismatch, or already acked.
        Never raises.

        Idempotent: second ack() returns False (acked_at filter misses stamped record).
        Ownership: if instance_id provided, only the claiming instance can ack.
        """
        try:
            qs = ProcessedEvent.objects.filter(
                outbox_record_id=outbox_record_id,
                consumer=consumer,
                acked_at__isnull=True,
            )
            if instance_id is not None:
                qs = qs.filter(claimed_by_instance_id=instance_id)
            updated = qs.update(acked_at=tz.now())
            if updated:
                logger.debug(
                    "consumer_event_acked",
                    outbox_record_id=str(outbox_record_id),
                    consumer=consumer,
                )
            return bool(updated)
        except Exception as exc:
            logger.error(
                "consumer_event_ack_failed",
                outbox_record_id=str(outbox_record_id),
                consumer=consumer,
                error=str(exc),
            )
            return False
