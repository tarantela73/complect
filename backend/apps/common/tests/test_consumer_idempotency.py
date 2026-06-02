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

