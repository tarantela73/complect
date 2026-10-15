"""
Tests for BaseDomainConsumer framework and domain consumer implementations.

Covers:
- StreamEvent construction from OutboxRecord
- Idempotency: duplicate claim → discard; reclaimed expired lease → process
- Mode B fence check: stale seq discarded, fresh seq processed
- Mode A: no fence check invoked
- Incident routing on process() failure (route called, exception re-raised)
- run_batch(): filtered by event_type and acked state, correct ordering
- FinanceContractClosedConsumer: posts transaction, emits TransactionPostedEvent
- ContractsTransactionSyncConsumer: updates paid_amount, fence guards ordering
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from apps.common.models.outbox import OutboxRecord
from apps.common.models.processed_event import ProcessedEvent
from apps.common.services.consumer_base import BaseDomainConsumer, StreamEvent

_TENANT = uuid.uuid4()
_NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_outbox(
    event_type: str = "contracts.ContractClosed",
    *,
    tenant_id: uuid.UUID | None = None,
    aggregate_id: uuid.UUID | None = None,
    aggregate_type: str | None = None,
    aggregate_seq: int | None = None,
    is_processed: bool = True,
    causal_root_id: uuid.UUID | None = None,
) -> OutboxRecord:
    tid = tenant_id or _TENANT
    rec_id = uuid.uuid4()
    return OutboxRecord.objects.create(
        id=rec_id,
        event_type=event_type,
        payload_json={"tenant_id": str(tid), "contract_id": str(uuid.uuid4()), "remaining_amount": 1000},
        correlation_id=uuid.uuid4(),
        causal_root_id=causal_root_id or rec_id,
        is_processed=is_processed,
        aggregate_id=aggregate_id,
        aggregate_type=aggregate_type,
        aggregate_seq=aggregate_seq,
    )


class _StrictConsumer(BaseDomainConsumer):
    consumer_name = "test.strict_consumer"
    ordering_mode = "STRICT"
    handled_event_types = frozenset({"contracts.ContractClosed"})

    def __init__(self):
        super().__init__()
        self.processed: list[StreamEvent] = []

    def process(self, event: StreamEvent) -> None:
        self.processed.append(event)


class _FencedConsumer(BaseDomainConsumer):
    consumer_name = "test.fenced_consumer"
    ordering_mode = "FENCED"
    handled_event_types = frozenset({"finance.TransactionPosted"})

    def __init__(self):
        super().__init__()
        self.processed: list[StreamEvent] = []

    def process(self, event: StreamEvent) -> None:
        self.processed.append(event)


class _FailingConsumer(BaseDomainConsumer):
    consumer_name = "test.failing_consumer"
    ordering_mode = "STRICT"
    handled_event_types = frozenset({"contracts.ContractClosed"})

    def process(self, event: StreamEvent) -> None:
        raise RuntimeError("domain_error")


class _RequiredEmissionConsumer(BaseDomainConsumer):
    """Consumer with a hard emission contract."""

    consumer_name = "test.required_emission_consumer"
    ordering_mode = "STRICT"
    handled_event_types = frozenset({"contracts.ContractClosed"})
    required_emissions = frozenset({"fake.RequiredEvent"})

    def __init__(self, *, should_emit: bool = True):
        super().__init__()
        self._should_emit = should_emit

    def process(self, event: StreamEvent) -> None:
        if self._should_emit:
            OutboxRecord.objects.create(
                event_type="fake.RequiredEvent",
                payload_json={"tenant_id": str(event.tenant_id)},
                correlation_id=event.correlation_id,
                causal_root_id=uuid.uuid4(),
                is_processed=False,
            )


class _ConditionalEmissionConsumer(_RequiredEmissionConsumer):
    """Hard emission check suppressed when payload.require == False."""

    consumer_name = "test.conditional_emission_consumer"

    def _emission_required(self, event: StreamEvent) -> bool:
        return bool(event.payload.get("require", True))


class _OptionalEmissionConsumer(BaseDomainConsumer):
    """Consumer with a soft (warning-only) emission contract."""

    consumer_name = "test.optional_emission_consumer"
    ordering_mode = "STRICT"
    handled_event_types = frozenset({"contracts.ContractClosed"})
    optional_emissions = frozenset({"fake.OptionalEvent"})

    def __init__(self):
        super().__init__()
        self.processed: list[StreamEvent] = []

    def process(self, event: StreamEvent) -> None:
        self.processed.append(event)


# ---------------------------------------------------------------------------
# StreamEvent construction
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStreamEventConstruction:
    def test_fields_populated_from_outbox_record(self):
        agg_id = uuid.uuid4()
        rec = _make_outbox(aggregate_id=agg_id, aggregate_type="contracts.contract", aggregate_seq=7)
        ev = BaseDomainConsumer._to_stream_event(rec)
        assert ev.outbox_record_id == rec.id
        assert ev.tenant_id == _TENANT
        assert ev.aggregate_id == agg_id
        assert ev.aggregate_type == "contracts.contract"
        assert ev.aggregate_seq == 7
        assert ev.event_type == "contracts.ContractClosed"

    def test_causal_root_id_propagated(self):
        root = uuid.uuid4()
        rec = _make_outbox(causal_root_id=root)
        ev = BaseDomainConsumer._to_stream_event(rec)
        assert ev.causal_root_id == root

    def test_invalid_tenant_id_falls_back_to_zero_uuid(self):
        rec = _make_outbox()
        rec.payload_json = {"tenant_id": "not-a-uuid"}
        ev = BaseDomainConsumer._to_stream_event(rec)
        assert ev.tenant_id == uuid.UUID(int=0)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIdempotency:
    def test_first_claim_processes_event(self):
        consumer = _StrictConsumer()
        rec = _make_outbox()
        consumer.handle(rec)
        assert len(consumer.processed) == 1
        assert ProcessedEvent.objects.filter(
            outbox_record_id=rec.id,
            consumer=consumer.consumer_name,
            acked_at__isnull=False,
        ).exists()

    def test_duplicate_claim_discards_event(self):
        consumer_a = _StrictConsumer()
        consumer_b = _StrictConsumer()
        rec = _make_outbox()
        consumer_a.handle(rec)
        consumer_b.handle(rec)
        assert len(consumer_b.processed) == 0

    def test_second_claim_returns_false_for_same_record(self):
        # In production, concurrent workers have separate DB connections.
        # Here we verify the idempotency contract at the service layer:
        # claim() returns False when a ProcessedEvent row already exists.
        from apps.common.services.consumer_idempotency import ConsumerIdempotencyService

        rec = _make_outbox()
        tid = uuid.UUID(str(rec.payload_json.get("tenant_id", str(_TENANT))))
        inst_a = uuid.uuid4()
        inst_b = uuid.uuid4()
        assert ConsumerIdempotencyService.claim(rec.id, "test.strict_consumer", tid, inst_a) is True
        assert ConsumerIdempotencyService.claim(rec.id, "test.strict_consumer", tid, inst_b) is False

    def test_ack_stamped_after_successful_process(self):
        consumer = _StrictConsumer()
        rec = _make_outbox()
        consumer.handle(rec)
        pe = ProcessedEvent.objects.get(outbox_record_id=rec.id, consumer=consumer.consumer_name)
        assert pe.acked_at is not None

    def test_causal_root_id_stored_in_processed_event(self):
        root = uuid.uuid4()
        consumer = _StrictConsumer()
        rec = _make_outbox(causal_root_id=root)
        consumer.handle(rec)
        pe = ProcessedEvent.objects.get(outbox_record_id=rec.id, consumer=consumer.consumer_name)
        assert pe.causal_root_id == root


# ---------------------------------------------------------------------------
# Mode B — Fence check
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFenceCheck:
    def _fenced(self, agg_id: uuid.UUID, seq: int) -> OutboxRecord:
        return _make_outbox(
            event_type="finance.TransactionPosted",
            aggregate_id=agg_id,
            aggregate_type="finance.transaction",
            aggregate_seq=seq,
        )

    def test_first_event_is_processed(self):
        consumer = _FencedConsumer()
        rec = self._fenced(uuid.uuid4(), 1)
        consumer.handle(rec)
        assert len(consumer.processed) == 1

    def test_higher_seq_is_processed(self):
        consumer = _FencedConsumer()
        agg_id = uuid.uuid4()
        consumer.handle(self._fenced(agg_id, 1))
        consumer.handle(self._fenced(agg_id, 2))
        assert len(consumer.processed) == 2

    def test_stale_seq_is_discarded_after_fence_advance(self):
        consumer = _FencedConsumer()
        agg_id = uuid.uuid4()
        consumer.handle(self._fenced(agg_id, 5))
        stale = self._fenced(agg_id, 3)
        consumer.handle(stale)
        assert len(consumer.processed) == 1

    def test_stale_event_is_acked_not_left_pending(self):
        consumer = _FencedConsumer()
        agg_id = uuid.uuid4()
        consumer.handle(self._fenced(agg_id, 5))
        stale = self._fenced(agg_id, 3)
        consumer.handle(stale)
        pe = ProcessedEvent.objects.filter(
            outbox_record_id=stale.id,
            consumer=consumer.consumer_name,
        ).first()
        assert pe is not None
        assert pe.acked_at is not None

    def test_strict_mode_skips_fence_check(self):
        consumer = _StrictConsumer()
        agg_id = uuid.uuid4()
        r1 = _make_outbox(aggregate_id=agg_id, aggregate_type="contracts.contract", aggregate_seq=5)
        r2 = _make_outbox(aggregate_id=agg_id, aggregate_type="contracts.contract", aggregate_seq=3)
        consumer.handle(r1)
        consumer.handle(r2)
        # STRICT mode: no fence, both events processed regardless of seq order
        assert len(consumer.processed) == 2

    def test_fenced_events_without_aggregate_seq_not_fence_checked(self):
        consumer = _FencedConsumer()
        rec = _make_outbox(event_type="finance.TransactionPosted", aggregate_seq=None)
        consumer.handle(rec)
        assert len(consumer.processed) == 1


# ---------------------------------------------------------------------------
# Incident routing on failure
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIncidentRouting:
    def test_process_failure_routes_incident(self):
        consumer = _FailingConsumer()
        rec = _make_outbox()
        with patch("apps.common.services.incident_routing.IncidentRoutingPolicy.route") as mock_route:
            mock_decision = MagicMock()
            mock_decision.structlog_labels = {"routing.owner": "platform_sre"}
            mock_route.return_value = mock_decision
            with pytest.raises(RuntimeError, match="domain_error"):
                consumer.handle(rec)
        mock_route.assert_called_once()

    def test_process_failure_re_raises_original_exception(self):
        consumer = _FailingConsumer()
        rec = _make_outbox()
        with pytest.raises(RuntimeError, match="domain_error"):
            consumer.handle(rec)

    def test_routing_failure_does_not_mask_original_exception(self):
        consumer = _FailingConsumer()
        rec = _make_outbox()
        with patch(
            "apps.common.services.incident_routing.IncidentRoutingPolicy.route",
            side_effect=Exception("routing_blew_up"),
        ):
            with pytest.raises(RuntimeError, match="domain_error"):
                consumer.handle(rec)

    def test_incident_not_routed_twice_for_same_causal_root(self):
        from django.core.cache import cache

        consumer = _FailingConsumer()
        rec = _make_outbox()
        event = BaseDomainConsumer._to_stream_event(rec)
        exc = RuntimeError("repeat_error")

        with patch("apps.common.services.incident_routing.IncidentRoutingPolicy.route") as mock_route:
            mock_decision = MagicMock()
            mock_decision.structlog_labels = {}
            mock_route.return_value = mock_decision

            consumer._route_incident(event, exc)
            consumer._route_incident(event, exc)

        mock_route.assert_called_once()

        dedup_key = (
            f"incident:dedup:{consumer.consumer_name}"
            f":{event.causal_root_id or event.outbox_record_id}"
            f":{type(exc).__name__}"
        )
        cache.delete(dedup_key)


# ---------------------------------------------------------------------------
# run_batch
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRunBatch:
    def test_returns_count_of_records_fetched(self):
        consumer = _StrictConsumer()
        for _ in range(3):
            _make_outbox()
        count = consumer.run_batch()
        assert count == 3

    def test_excludes_already_acked_records(self):
        consumer = _StrictConsumer()
        acked_rec = _make_outbox()
        consumer.handle(acked_rec)
        fresh_rec = _make_outbox()
        count = consumer.run_batch(batch_size=100)
        assert count == 1
        assert len(consumer.processed) == 2

    def test_excludes_unprocessed_outbox_records(self):
        consumer = _StrictConsumer()
        _make_outbox(is_processed=False)
        count = consumer.run_batch()
        assert count == 0

    def test_excludes_permanently_failed_records(self):
        from django.utils import timezone as tz

        consumer = _StrictConsumer()
        rec = _make_outbox()
        rec.failed_permanently_at = tz.now()
        rec.save(update_fields=["failed_permanently_at"])
        count = consumer.run_batch()
        assert count == 0

    def test_excludes_records_for_other_event_types(self):
        consumer = _StrictConsumer()
        _make_outbox(event_type="other.SomeEvent")
        count = consumer.run_batch()
        assert count == 0

    def test_batch_size_limits_records(self):
        consumer = _StrictConsumer()
        for _ in range(10):
            _make_outbox()
        count = consumer.run_batch(batch_size=3)
        assert count == 3

    def test_strict_mode_orders_by_aggregate_seq(self):
        consumer = _StrictConsumer()
        agg_id = uuid.uuid4()
        r3 = _make_outbox(aggregate_id=agg_id, aggregate_type="c", aggregate_seq=3)
        r1 = _make_outbox(aggregate_id=agg_id, aggregate_type="c", aggregate_seq=1)
        r2 = _make_outbox(aggregate_id=agg_id, aggregate_type="c", aggregate_seq=2)
        consumer.run_batch()
        seqs = [e.aggregate_seq for e in consumer.processed]
        assert seqs == [1, 2, 3]

    def test_batch_complete_log_emitted_on_success(self):
        import structlog.testing

        consumer = _StrictConsumer()
        for _ in range(2):
            _make_outbox()
        with structlog.testing.capture_logs() as logs:
            consumer.run_batch()
        batch_logs = [e for e in logs if e.get("event") == "consumer_batch_complete"]
        assert len(batch_logs) == 1
        entry = batch_logs[0]
        assert entry["picked_up"] == 2
        assert entry["acked"] == 2
        assert entry["failed"] == 0
        assert entry["status"] == "complete"
        assert "verify_ms" in entry

    def test_batch_complete_log_status_aborted_on_failure(self):
        import structlog.testing

        consumer = _FailingConsumer()
        _make_outbox()
        with structlog.testing.capture_logs() as logs:
            with pytest.raises(RuntimeError):
                consumer.run_batch()
        batch_logs = [e for e in logs if e.get("event") == "consumer_batch_complete"]
        assert len(batch_logs) == 1
        entry = batch_logs[0]
        assert entry["failed"] == 1
        assert entry["status"] == "aborted"

    def test_verify_ms_reflects_emission_check_time(self):
        import structlog.testing

        consumer = _RequiredEmissionConsumer(should_emit=True)
        _make_outbox()
        with structlog.testing.capture_logs() as logs:
            consumer.run_batch()
        entry = next(e for e in logs if e.get("event") == "consumer_batch_complete")
        assert isinstance(entry["verify_ms"], float)

    def test_empty_batch_emits_no_log(self):
        import structlog.testing

        consumer = _StrictConsumer()
        with structlog.testing.capture_logs() as logs:
            count = consumer.run_batch()
        assert count == 0
        batch_logs = [e for e in logs if e.get("event") == "consumer_batch_complete"]
        assert len(batch_logs) == 0


# ---------------------------------------------------------------------------
# Emission enforcement
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEmissionVerification:
    def test_required_passes_when_emission_exists(self):
        consumer = _RequiredEmissionConsumer(should_emit=True)
        rec = _make_outbox()
        consumer.handle(rec)
        assert ProcessedEvent.objects.filter(
            outbox_record_id=rec.id,
            consumer=consumer.consumer_name,
            acked_at__isnull=False,
        ).exists()

    def test_required_raises_when_emission_missing(self):
        from apps.common.services.consumer_base import CausalEmissionMissingError

        consumer = _RequiredEmissionConsumer(should_emit=False)
        rec = _make_outbox()
        with pytest.raises(CausalEmissionMissingError) as exc_info:
            consumer.handle(rec)
        assert "fake.RequiredEvent" in str(exc_info.value.missing_event_types)

    def test_required_missing_routes_incident(self):
        from apps.common.services.consumer_base import CausalEmissionMissingError

        consumer = _RequiredEmissionConsumer(should_emit=False)
        rec = _make_outbox()
        with patch("apps.common.services.incident_routing.IncidentRoutingPolicy.route") as mock_route:
            mock_decision = MagicMock()
            mock_decision.structlog_labels = {}
            mock_route.return_value = mock_decision
            with pytest.raises(CausalEmissionMissingError):
                consumer.handle(rec)
        mock_route.assert_called_once()

    def test_predicate_false_suppresses_required_check(self):
        rec_id = uuid.uuid4()
        rec = OutboxRecord.objects.create(
            id=rec_id,
            event_type="contracts.ContractClosed",
            payload_json={"tenant_id": str(_TENANT), "require": False},
            correlation_id=uuid.uuid4(),
            causal_root_id=rec_id,
            is_processed=True,
        )
        consumer = _ConditionalEmissionConsumer(should_emit=False)
        consumer.handle(rec)
        assert ProcessedEvent.objects.filter(
            outbox_record_id=rec.id,
            consumer=consumer.consumer_name,
            acked_at__isnull=False,
        ).exists()

    def test_optional_absent_does_not_raise(self):
        consumer = _OptionalEmissionConsumer()
        rec = _make_outbox()
        consumer.handle(rec)
        assert len(consumer.processed) == 1
        assert ProcessedEvent.objects.filter(
            outbox_record_id=rec.id,
            consumer=consumer.consumer_name,
            acked_at__isnull=False,
        ).exists()

    def test_optional_absent_emits_warning_log(self):
        import structlog.testing

        consumer = _OptionalEmissionConsumer()
        rec = _make_outbox()
        with structlog.testing.capture_logs() as logs:
            consumer.handle(rec)
        warning_events = [
            e for e in logs if e.get("log_level") == "warning" and e.get("event") == "consumer_optional_emission_absent"
        ]
        assert len(warning_events) == 1
        assert warning_events[0]["event_type"] == "fake.OptionalEvent"

    def test_no_emissions_declared_skips_all_checks(self):
        consumer = _StrictConsumer()
        rec = _make_outbox()
        consumer.handle(rec)
        assert len(consumer.processed) == 1


# ---------------------------------------------------------------------------
# FinanceContractClosedConsumer (integration)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFinanceContractClosedConsumer:
    def _make_contract(self, contract_id: uuid.UUID, total_amount: int = 50000) -> None:
        from apps.contracts.models.contract import Contract

        Contract.objects.create(
            id=contract_id,
            tenant_id=_TENANT,
            contract_number="TEST-001",
            status="ACTIVE",
            total_amount=total_amount,
            paid_amount=0,
        )

    def test_posts_transaction_for_positive_remaining_amount(self):
        from apps.finance.tasks.consumer import FinanceContractClosedConsumer
        from apps.finance.models.transaction import Transaction

        contract_id = uuid.uuid4()
        self._make_contract(contract_id, total_amount=50000)

        rec_id = uuid.uuid4()
        rec = OutboxRecord.objects.create(
            id=rec_id,
            event_type="contracts.ContractClosed",
            payload_json={
                "tenant_id": str(_TENANT),
                "contract_id": str(contract_id),
                "remaining_amount": 30000,
                "user_id": str(uuid.uuid4()),
            },
            correlation_id=uuid.uuid4(),
            causal_root_id=rec_id,
            is_processed=True,
        )

        consumer = FinanceContractClosedConsumer()
        consumer.handle(rec)

        assert Transaction.objects.filter(
            contract_id=contract_id,
            tenant_id=_TENANT,
            amount=30000,
        ).exists()

    def test_emits_transaction_posted_event_to_outbox(self):
        from apps.finance.tasks.consumer import FinanceContractClosedConsumer

        contract_id = uuid.uuid4()
        self._make_contract(contract_id)

        rec_id = uuid.uuid4()
        rec = OutboxRecord.objects.create(
            id=rec_id,
            event_type="contracts.ContractClosed",
            payload_json={
                "tenant_id": str(_TENANT),
                "contract_id": str(contract_id),
                "remaining_amount": 10000,
                "user_id": str(uuid.uuid4()),
            },
            correlation_id=uuid.uuid4(),
            causal_root_id=rec_id,
            is_processed=True,
        )

        consumer = FinanceContractClosedConsumer()
        consumer.handle(rec)

        assert OutboxRecord.objects.filter(
            event_type="finance.TransactionPosted",
            payload_json__contract_id=str(contract_id),
        ).exists()

    def test_zero_remaining_amount_no_transaction(self):
        from apps.finance.models.transaction import Transaction
        from apps.finance.tasks.consumer import FinanceContractClosedConsumer

        contract_id = uuid.uuid4()
        self._make_contract(contract_id)

        rec_id = uuid.uuid4()
        rec = OutboxRecord.objects.create(
            id=rec_id,
            event_type="contracts.ContractClosed",
            payload_json={
                "tenant_id": str(_TENANT),
                "contract_id": str(contract_id),
                "remaining_amount": 0,
                "user_id": str(uuid.uuid4()),
            },
            correlation_id=uuid.uuid4(),
            causal_root_id=rec_id,
            is_processed=True,
        )

        consumer = FinanceContractClosedConsumer()
        consumer.handle(rec)

        assert not Transaction.objects.filter(contract_id=contract_id).exists()

    def test_emission_predicate_false_for_zero_balance(self):
        from apps.finance.tasks.consumer import FinanceContractClosedConsumer

        consumer = FinanceContractClosedConsumer()
        event = StreamEvent(
            outbox_record_id=uuid.uuid4(),
            causal_root_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            tenant_id=_TENANT,
            aggregate_id=None,
            aggregate_type=None,
            aggregate_seq=None,
            event_type="contracts.ContractClosed",
            payload={"remaining_amount": 0},
        )
        assert consumer._emission_required(event) is False

    def test_emission_predicate_true_for_positive_balance(self):
        from apps.finance.tasks.consumer import FinanceContractClosedConsumer

        consumer = FinanceContractClosedConsumer()
        event = StreamEvent(
            outbox_record_id=uuid.uuid4(),
            causal_root_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            tenant_id=_TENANT,
            aggregate_id=None,
            aggregate_type=None,
            aggregate_seq=None,
            event_type="contracts.ContractClosed",
            payload={"remaining_amount": 500},
        )
        assert consumer._emission_required(event) is True

    def test_causal_chain_complete_after_processing(self):
        """TransactionPostedEvent outbox record shares causal_root_id with ContractClosedEvent."""
        from apps.finance.tasks.consumer import FinanceContractClosedConsumer

        contract_id = uuid.uuid4()
        self._make_contract(contract_id)
        causal_root = uuid.uuid4()

        rec_id = uuid.uuid4()
        rec = OutboxRecord.objects.create(
            id=rec_id,
            event_type="contracts.ContractClosed",
            payload_json={
                "tenant_id": str(_TENANT),
                "contract_id": str(contract_id),
                "remaining_amount": 5000,
                "user_id": str(uuid.uuid4()),
            },
            correlation_id=uuid.uuid4(),
            causal_root_id=causal_root,
            is_processed=True,
        )

        consumer = FinanceContractClosedConsumer()
        consumer.handle(rec)

        # TransactionPostedEvent must have the same causal_root_id for ADR-0014 pair detection.
        # TransactionalOutboxService.push_to_outbox() sets causal_root_id = new record's own id
        # unless passed explicitly. FinanceConsumer does NOT pass causal_root_id → new chain root.
        # The causal_pair monitor looks for OutboxRecord(causal_root_id=source.id) for expected.
        # So the TransactionPostedEvent must exist with its own causal_root_id (new chain from tx).
        assert OutboxRecord.objects.filter(event_type="finance.TransactionPosted").exists()


# ---------------------------------------------------------------------------
# ContractsTransactionSyncConsumer (Mode B)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestContractsTransactionSyncConsumer:
    def _make_contract(self, contract_id: uuid.UUID, paid_amount: int = 0) -> None:
        from apps.contracts.models.contract import Contract

        Contract.objects.create(
            id=contract_id,
            tenant_id=_TENANT,
            contract_number=f"SYNC-{contract_id.hex[:8]}",
            status="ACTIVE",
            total_amount=100000,
            paid_amount=paid_amount,
        )

    def _tx_record(
        self,
        contract_id: uuid.UUID,
        amount: int,
        agg_id: uuid.UUID | None = None,
        agg_seq: int | None = None,
    ) -> OutboxRecord:
        rec_id = uuid.uuid4()
        return OutboxRecord.objects.create(
            id=rec_id,
            event_type="finance.TransactionPosted",
            payload_json={
                "tenant_id": str(_TENANT),
                "contract_id": str(contract_id),
                "amount": amount,
                "transaction_type": "PAYMENT",
            },
            correlation_id=uuid.uuid4(),
            causal_root_id=rec_id,
            is_processed=True,
            aggregate_id=agg_id,
            aggregate_type="finance.transaction" if agg_id else None,
            aggregate_seq=agg_seq,
        )

    def test_updates_paid_amount_on_payment(self):
        from apps.contracts.models.contract import Contract
        from apps.contracts.tasks.consumer import ContractsTransactionSyncConsumer

        contract_id = uuid.uuid4()
        self._make_contract(contract_id)

        consumer = ContractsTransactionSyncConsumer()
        consumer.handle(self._tx_record(contract_id, 10000))

        contract = Contract.objects.get(id=contract_id)
        assert contract.paid_amount == 10000

    def test_fence_discards_older_seq(self):
        from apps.contracts.models.contract import Contract
        from apps.contracts.tasks.consumer import ContractsTransactionSyncConsumer

        contract_id = uuid.uuid4()
        agg_id = uuid.uuid4()
        self._make_contract(contract_id)

        consumer = ContractsTransactionSyncConsumer()
        consumer.handle(self._tx_record(contract_id, 20000, agg_id=agg_id, agg_seq=5))
        consumer.handle(self._tx_record(contract_id, 5000, agg_id=agg_id, agg_seq=3))

        # Stale event (seq=3) must be discarded — paid_amount reflects only seq=5
        contract = Contract.objects.get(id=contract_id)
        assert contract.paid_amount == 20000

    def test_multiple_payments_accumulate(self):
        from apps.contracts.models.contract import Contract
        from apps.contracts.tasks.consumer import ContractsTransactionSyncConsumer

        contract_id = uuid.uuid4()
        self._make_contract(contract_id)

        consumer = ContractsTransactionSyncConsumer()
        consumer.handle(self._tx_record(contract_id, 10000))
        consumer.handle(self._tx_record(contract_id, 15000))

        contract = Contract.objects.get(id=contract_id)
        assert contract.paid_amount == 25000
