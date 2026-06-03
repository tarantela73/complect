"""
Contracts domain consumer — Mode B (FENCED, snapshot semantics).

Consumes finance.TransactionPosted events to maintain a denormalized local view
of the latest payment state per contract. Uses AggregateSequenceFenceService to
ensure snapshot idempotency: if seq=5 arrived before seq=4, seq=4 is discarded.

This is the canonical Mode B template. Copy and adapt for new snapshot consumers.
"""
import uuid

import structlog

from apps.common.events.finance import TransactionPostedEvent
from apps.common.services.consumer_base import BaseDomainConsumer, StreamEvent
from apps.common.tasks import BaseEnterpriseTask
from config.celery import app

logger = structlog.get_logger(__name__)


class ContractsTransactionSyncConsumer(BaseDomainConsumer):
    """
    Keeps contracts.contract.paid_amount in sync with finance.TransactionPosted events.

    Mode B (FENCED): AggregateSequenceFenceService.advance() prevents applying an older
    snapshot over a newer one. Safe with multiple concurrent workers — fence is atomic.
    """

    consumer_name = "contracts.transaction_sync_consumer"
    ordering_mode = "FENCED"
    handled_event_types = frozenset({TransactionPostedEvent.EVENT_TYPE})

    def process(self, event: StreamEvent) -> None:
        from apps.common.context.core import set_tenant_id

        payload = event.payload
        contract_id_raw = payload.get("contract_id")
        amount = int(payload.get("amount", 0))
        transaction_type = payload.get("transaction_type", "PAYMENT")

        if not contract_id_raw:
            logger.warning(
                "contracts_consumer_missing_contract_id",
                consumer=self.consumer_name,
                outbox_record_id=str(event.outbox_record_id),
            )
            return

        contract_id = uuid.UUID(contract_id_raw)
        set_tenant_id(event.tenant_id)

        # Snapshot update: last-write-wins within the fence window.
        # FENCED mode guarantees we never apply an older event over a newer one.
        from django.db.models import F

        from apps.contracts.models.contract import Contract

        delta = amount if transaction_type == "PAYMENT" else -amount
        updated = Contract.objects.filter(
            id=contract_id,
            tenant_id=event.tenant_id,
            is_deleted=False,
        ).update(paid_amount=F("paid_amount") + delta)

        if updated:
            logger.info(
                "contracts_consumer_paid_amount_synced",
                contract_id=str(contract_id),
                delta=delta,
                consumer=self.consumer_name,
            )
        else:
            logger.warning(
                "contracts_consumer_contract_not_found",
                contract_id=str(contract_id),
                consumer=self.consumer_name,
            )


@app.task(
    base=BaseEnterpriseTask,
    name="apps.contracts.tasks.consumer.run_contracts_consumer_task",
    queue="default",
    ignore_result=True,
)
def run_contracts_consumer_task() -> None:
    """
    Poll and process finance.TransactionPosted events for the Contracts domain.

    Mode B (FENCED): safe with default worker concurrency. Fence check inside
    BaseDomainConsumer.handle() discards stale snapshots atomically.
    """
    ContractsTransactionSyncConsumer().run_batch()
