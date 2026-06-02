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
        assert ProcessedEvent.objects.filter(
            outbox_record_id=event_id, consumer="svc"
        ).exists()

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

        assert len(dispatch_calls) == 1, (
            f"Expected 1 wake-up for 5 rapid writes, got {len(dispatch_calls)}"
        )

