import uuid
from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.common.events.contracts import ContractClosedEvent
from apps.common.models.processed_event import ProcessedEvent
from apps.common.services.consumer_idempotency import ConsumerIdempotencyService
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tasks.outbox_dispatcher import run_outbox_polling_dispatcher_task


def _claim(outbox_record_id=None, consumer="svc_a", tenant_id=None) -> bool:
    return ConsumerIdempotencyService.claim(
        outbox_record_id=outbox_record_id or uuid.uuid4(),
        consumer=consumer,
        tenant_id=tenant_id or uuid.uuid4(),
    )


@pytest.mark.django_db
class TestConsumerIdempotencyService:
    # ── first-time claim ──────────────────────────────────────────────────────

    def test_first_claim_returns_true(self):
        assert _claim() is True

    def test_first_claim_creates_db_record(self):
        event_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id)
        assert ProcessedEvent.objects.filter(outbox_record_id=event_id, consumer="svc").exists()

    # ── duplicate claim ───────────────────────────────────────────────────────

    def test_duplicate_claim_returns_false(self):
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        assert ConsumerIdempotencyService.claim(event_id, "svc", tenant_id) is True
        assert ConsumerIdempotencyService.claim(event_id, "svc", tenant_id) is False

    def test_duplicate_claim_does_not_raise(self):
        """IntegrityError on duplicate INSERT must never propagate to the caller."""
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id)
        try:
            ConsumerIdempotencyService.claim(event_id, "svc", tenant_id)
        except Exception as exc:
            pytest.fail(f"claim() raised unexpectedly: {exc}")

    # ── multi-consumer isolation ──────────────────────────────────────────────

    def test_different_consumers_are_independent(self):
        """Each consumer has its own claim slot for the same event."""
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        assert ConsumerIdempotencyService.claim(event_id, "consumer_a", tenant_id) is True
        assert ConsumerIdempotencyService.claim(event_id, "consumer_b", tenant_id) is True
        assert ProcessedEvent.objects.filter(outbox_record_id=event_id).count() == 2


# ── ACK tracking ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestConsumerAckTracking:
    def test_claim_leaves_acked_at_null(self):
        """Freshly claimed event is in-flight: acked_at must be NULL."""
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id)
        record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc")
        assert record.acked_at is None

    def test_ack_stamps_acked_at(self):
        """ack() must set acked_at to a non-null timestamp."""
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id)
        ConsumerIdempotencyService.ack(event_id, "svc")
        record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc")
        assert record.acked_at is not None

    def test_ack_returns_true_on_success(self):
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id)
        assert ConsumerIdempotencyService.ack(event_id, "svc") is True

    def test_ack_returns_false_without_prior_claim(self):
        """ack() on an unknown pair returns False — no claim to confirm."""
        assert ConsumerIdempotencyService.ack(uuid.uuid4(), "svc") is False

    def test_ack_idempotent_second_call_returns_false(self):
        """Second ack() on already-acked record is a no-op (acked_at filter misses)."""
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id)
        assert ConsumerIdempotencyService.ack(event_id, "svc") is True
        assert ConsumerIdempotencyService.ack(event_id, "svc") is False

    def test_ack_does_not_raise(self):
        """ack() must never propagate exceptions to the caller."""
        try:
            ConsumerIdempotencyService.ack(uuid.uuid4(), "svc")
        except Exception as exc:
            pytest.fail(f"ack() raised unexpectedly: {exc}")

    def test_ack_is_consumer_scoped(self):
        """ack() for consumer_a must not affect consumer_b's claim."""
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "consumer_a", tenant_id)
        ConsumerIdempotencyService.claim(event_id, "consumer_b", tenant_id)
        ConsumerIdempotencyService.ack(event_id, "consumer_a")
        b_record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="consumer_b")
        assert b_record.acked_at is None

    def test_full_lifecycle_claim_process_ack(self):
        """
        Full delivery guarantee lifecycle:
        claim() → process (simulated) → ack()
        Final state: acked_at IS NOT NULL.
        """
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()

        claimed = ConsumerIdempotencyService.claim(event_id, "svc", tenant_id)
        assert claimed is True

        # Simulate processing...
        processing_result = "ok"

        acked = ConsumerIdempotencyService.ack(event_id, "svc")
        assert acked is True
        assert processing_result == "ok"

        record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc")
        assert record.acked_at is not None


# ── Ownership-bound ACK (instance_id) ────────────────────────────────────────


@pytest.mark.django_db
class TestConsumerAckOwnership:
    def test_claim_stores_instance_id(self):
        event_id, tenant_id, instance_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, instance_id=instance_id)
        record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc")
        assert record.claimed_by_instance_id == instance_id

    def test_ack_with_correct_instance_id_succeeds(self):
        event_id, tenant_id, instance_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, instance_id=instance_id)
        assert ConsumerIdempotencyService.ack(event_id, "svc", instance_id=instance_id) is True

    def test_ack_with_wrong_instance_id_fails(self):
        """instance B must not be able to ack what instance A claimed."""
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        instance_a, instance_b = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, instance_id=instance_a)
        assert ConsumerIdempotencyService.ack(event_id, "svc", instance_id=instance_b) is False

    def test_ack_without_instance_id_bypasses_ownership(self):
        """Legacy consumers without instance_id can still ack (backward compat)."""
        event_id, tenant_id, instance_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, instance_id=instance_id)
        # ack() without instance_id ignores claimed_by_instance_id filter
        assert ConsumerIdempotencyService.ack(event_id, "svc") is True

    def test_wrong_instance_ack_does_not_stamp_acked_at(self):
        """Failed ownership check must leave acked_at=NULL."""
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        instance_a, instance_b = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, instance_id=instance_a)
        ConsumerIdempotencyService.ack(event_id, "svc", instance_id=instance_b)
        record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc")
        assert record.acked_at is None


# ── Lease & Reclaim (ADR-0011) ────────────────────────────────────────────────


@pytest.mark.django_db
class TestConsumerLease:
    def test_claim_without_lease_argument_creates_default_lease(self):
        """
        Safe-by-default: claim() without lease_ttl must set lease_expires_at
        from settings.CONSUMER_LEASE_TTL_SECONDS. No more unrecoverable claims by accident.
        """
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id)
        record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc")
        assert record.lease_expires_at is not None

    def test_claim_with_explicit_none_creates_infinite_lease(self):
        """
        Unsafe opt-out: claim(lease_ttl=None) creates infinite lease — reclaim() cannot
        recover this claim. Requires explicit intent, not the default path.
        """
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, lease_ttl=None)
        record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc")
        assert record.lease_expires_at is None

    def test_claim_with_custom_lease_ttl_sets_lease_expires_at(self):
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, lease_ttl=60)
        record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc")
        assert record.lease_expires_at is not None

    def test_default_lease_expires_at_is_in_the_future(self):
        from django.utils import timezone as tz

        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id)
        record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc")
        assert record.lease_expires_at > tz.now()


@pytest.mark.django_db
class TestConsumerReclaim:
    def test_reclaim_expired_lease_returns_true(self):
        """Worker B can take over after Worker A's lease expires."""
        from datetime import timedelta

        from django.utils import timezone as tz

        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        instance_a, instance_b = uuid.uuid4(), uuid.uuid4()

        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, instance_id=instance_a, lease_ttl=300)
        # Simulate lease expiry: set lease_expires_at to the past
        ProcessedEvent.objects.filter(outbox_record_id=event_id, consumer="svc").update(
            lease_expires_at=tz.now() - timedelta(seconds=1)
        )

        assert ConsumerIdempotencyService.reclaim(event_id, "svc", tenant_id, instance_id=instance_b) is True

    def test_reclaim_transfers_ownership(self):
        """After reclaim, claimed_by_instance_id is updated to the new owner."""
        from datetime import timedelta

        from django.utils import timezone as tz

        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        instance_a, instance_b = uuid.uuid4(), uuid.uuid4()

        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, instance_id=instance_a, lease_ttl=300)
        ProcessedEvent.objects.filter(outbox_record_id=event_id, consumer="svc").update(
            lease_expires_at=tz.now() - timedelta(seconds=1)
        )
        ConsumerIdempotencyService.reclaim(event_id, "svc", tenant_id, instance_id=instance_b)

        record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc")
        assert record.claimed_by_instance_id == instance_b

    def test_reclaim_sets_new_lease_expires_at(self):
        """reclaim() must extend the lease to a new future deadline."""
        from datetime import timedelta

        from django.utils import timezone as tz

        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, lease_ttl=300)
        ProcessedEvent.objects.filter(outbox_record_id=event_id, consumer="svc").update(
            lease_expires_at=tz.now() - timedelta(seconds=1)
        )
        ConsumerIdempotencyService.reclaim(event_id, "svc", tenant_id, lease_ttl=300)

        record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc")
        assert record.lease_expires_at > tz.now()

    def test_reclaim_valid_lease_returns_false(self):
        """reclaim() on a still-valid lease must not steal ownership."""
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        instance_a, instance_b = uuid.uuid4(), uuid.uuid4()

        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, instance_id=instance_a, lease_ttl=300)
        assert ConsumerIdempotencyService.reclaim(event_id, "svc", tenant_id, instance_id=instance_b) is False

    def test_reclaim_does_not_affect_valid_owner(self):
        """Failed reclaim must leave the original owner and lease untouched."""

        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        instance_a, instance_b = uuid.uuid4(), uuid.uuid4()

        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, instance_id=instance_a, lease_ttl=300)
        before = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc").lease_expires_at

        ConsumerIdempotencyService.reclaim(event_id, "svc", tenant_id, instance_id=instance_b)

        record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc")
        assert record.claimed_by_instance_id == instance_a
        assert record.lease_expires_at == before

    def test_reclaim_infinite_lease_returns_false(self):
        """Explicit infinite-lease records (lease_ttl=None) are immune to reclaim()."""
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, lease_ttl=None)
        assert ConsumerIdempotencyService.reclaim(event_id, "svc", tenant_id) is False

    def test_reclaim_acked_record_returns_false(self):
        """Already-acked records must not be reclaimed."""
        from datetime import timedelta

        from django.utils import timezone as tz

        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, lease_ttl=300)
        ProcessedEvent.objects.filter(outbox_record_id=event_id, consumer="svc").update(
            lease_expires_at=tz.now() - timedelta(seconds=1)
        )
        ConsumerIdempotencyService.ack(event_id, "svc")

        assert ConsumerIdempotencyService.reclaim(event_id, "svc", tenant_id) is False

    def test_reclaim_concurrent_workers_only_one_succeeds(self):
        """
        Two concurrent reclaim() calls must result in exactly one success.
        Verified via sequential DB simulation: second call sees updated lease_expires_at.
        """
        from datetime import timedelta

        from django.utils import timezone as tz

        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        instance_a, instance_b, instance_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, instance_id=instance_a, lease_ttl=300)
        ProcessedEvent.objects.filter(outbox_record_id=event_id, consumer="svc").update(
            lease_expires_at=tz.now() - timedelta(seconds=1)
        )

        result_b = ConsumerIdempotencyService.reclaim(event_id, "svc", tenant_id, instance_id=instance_b)
        result_c = ConsumerIdempotencyService.reclaim(event_id, "svc", tenant_id, instance_id=instance_c)

        assert result_b is True
        assert result_c is False  # lease already renewed by B


@pytest.mark.django_db
class TestConsumerHeartbeat:
    def test_heartbeat_extends_lease(self):
        """heartbeat() must push lease_expires_at further into the future."""

        event_id, tenant_id, instance_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, instance_id=instance_id, lease_ttl=300)
        before = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc").lease_expires_at

        result = ConsumerIdempotencyService.heartbeat(event_id, "svc", instance_id, lease_ttl=300)

        record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc")
        assert result is True
        assert record.lease_expires_at >= before

    def test_heartbeat_wrong_instance_returns_false(self):
        """Only the current owner can heartbeat."""
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        instance_a, instance_b = uuid.uuid4(), uuid.uuid4()

        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, instance_id=instance_a, lease_ttl=300)
        assert ConsumerIdempotencyService.heartbeat(event_id, "svc", instance_b) is False

    def test_heartbeat_expired_lease_returns_false(self):
        """heartbeat() on a lease expired beyond the grace window must fail.

        Grace window = TTL/10. With TTL=300s the window is 30s.
        Setting lease_expires_at = now() - 31s puts it strictly outside the grace
        window, so the heartbeat filter finds no matching row and returns False.
        A 1s-expired lease would still be within the grace window and succeed —
        that is intentional behaviour (absorbs clock skew/jitter at the boundary).
        """
        from datetime import timedelta

        from django.utils import timezone as tz

        ttl = 300
        grace = ttl // 10  # 30s

        event_id, tenant_id, instance_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, instance_id=instance_id, lease_ttl=ttl)
        ProcessedEvent.objects.filter(outbox_record_id=event_id, consumer="svc").update(
            lease_expires_at=tz.now() - timedelta(seconds=grace + 1)
        )

        assert ConsumerIdempotencyService.heartbeat(event_id, "svc", instance_id, lease_ttl=ttl) is False

    def test_heartbeat_does_not_raise(self):
        """heartbeat() must never propagate exceptions."""
        try:
            ConsumerIdempotencyService.heartbeat(uuid.uuid4(), "svc", uuid.uuid4())
        except Exception as exc:
            pytest.fail(f"heartbeat() raised unexpectedly: {exc}")


# ── Causal root propagation (ADR-0015/0016) ───────────────────────────────────


@pytest.mark.django_db
class TestClaimCausalRootPropagation:
    def test_claim_stores_causal_root_id(self):
        """causal_root_id passed to claim() must be persisted on ProcessedEvent."""
        event_id, tenant_id, root_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id, causal_root_id=root_id)
        record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc")
        assert record.causal_root_id == root_id

    def test_claim_without_causal_root_id_stores_null(self):
        """Backward compat: claim() without causal_root_id stores NULL."""
        event_id, tenant_id = uuid.uuid4(), uuid.uuid4()
        ConsumerIdempotencyService.claim(event_id, "svc", tenant_id)
        record = ProcessedEvent.objects.get(outbox_record_id=event_id, consumer="svc")
        assert record.causal_root_id is None

    def test_repair_flow_causal_root_differs_from_outbox_record_id(self):
        """
        Repair/compensation events: causal_root_id == original root, not the repair record id.
        This validates the two-identity model (ADR-0015): execution identity ≠ causal identity.
        """
        original_root_id = uuid.uuid4()
        repair_event_id = uuid.uuid4()  # different from original_root_id
        tenant_id = uuid.uuid4()

        ConsumerIdempotencyService.claim(repair_event_id, "svc", tenant_id, causal_root_id=original_root_id)
        record = ProcessedEvent.objects.get(outbox_record_id=repair_event_id, consumer="svc")
        assert record.causal_root_id == original_root_id
        assert record.outbox_record_id != record.causal_root_id


# ── Coalescing wake-up (fix 5.1) ─────────────────────────────────────────────
# Requires transaction=True so that transaction.on_commit fires after each write.


@pytest.mark.django_db(transaction=True)
class TestOutboxCoalescing:
    def test_bulk_push_fires_single_wake_up(self):
        """
        N rapid outbox writes in the same coalesce window must produce exactly
        one dispatcher wake-up, not N apply_async calls.
        """
        cache.delete("outbox:dispatch:wakeup")
        cache.delete("outbox:dispatch:inflight")

        dispatch_calls: list = []

        def _make_event():
            return ContractClosedEvent.create(
                correlation_id=uuid.uuid4(),
                contract_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                remaining_amount=0,
            )

        with patch.object(
            run_outbox_polling_dispatcher_task,
            "apply_async",
            side_effect=lambda *a, **kw: dispatch_calls.append(1),
        ):
            for _ in range(5):
                TransactionalOutboxService.push_to_outbox(_make_event())

        assert len(dispatch_calls) == 1, f"Expected 1 wake-up for 5 rapid writes, got {len(dispatch_calls)}"
