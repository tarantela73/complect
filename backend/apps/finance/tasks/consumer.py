"""
Finance domain consumer — Mode A (STRICT ordering).

Processes contracts.ContractClosed events and posts the corresponding finance transaction.
Completes the causal pair: contracts.ContractClosed → finance.TransactionPosted (ADR-0014).

Ordering guarantee: single-concurrency Celery worker on the 'finance' queue (ADR-0012
Slot Locking Invariant). run_batch() orders by (aggregate_id, aggregate_seq); combined
with concurrency=1 this is equivalent to Mode A serial dispatch.
"""
import uuid

import structlog

from apps.common.events.contracts import ContractClosedEvent
from apps.common.events.finance import TransactionPostedEvent
from apps.common.services.consumer_base import BaseDomainConsumer, StreamEvent
from apps.common.tasks import BaseEnterpriseTask
from config.celery import app

logger = structlog.get_logger(__name__)


class FinanceContractClosedConsumer(BaseDomainConsumer):
    consumer_name = "finance.contract_closed_consumer"
    ordering_mode = "STRICT"
    handled_event_types = frozenset({ContractClosedEvent.EVENT_TYPE})
    required_emissions = frozenset({TransactionPostedEvent.EVENT_TYPE})

    def _emission_required(self, event: StreamEvent) -> bool:
        return int(event.payload.get("remaining_amount", 0)) > 0

    def process(self, event: StreamEvent) -> None:
        from apps.common.context.core import set_tenant_id
        from apps.finance.services.transaction_posting import TransactionPostingService

        payload = event.payload
        contract_id = uuid.UUID(payload["contract_id"])
        remaining_amount = int(payload.get("remaining_amount", 0))
        user_raw = payload.get("user_id")
        user_id = uuid.UUID(user_raw) if user_raw else uuid.UUID(int=0)

        set_tenant_id(event.tenant_id)

        if remaining_amount <= 0:
            # Contract closed with zero balance — no transaction needed.
            # TransactionPostedEvent is NOT emitted; the causal pair SLO monitor
            # will observe an OBSERVABLE divergence until the accounting period closes.
            logger.info(
                "finance_consumer_zero_balance_contract_closed",
                contract_id=str(contract_id),
                consumer=self.consumer_name,
                outbox_record_id=str(event.outbox_record_id),
            )
            return

        TransactionPostingService.post_payment(
            contract_id=contract_id,
            amount=remaining_amount,
            posted_by=user_id,
            external_ref=f"outbox:{event.outbox_record_id}",
            correlation_id=event.correlation_id,
        )

        logger.info(
            "finance_consumer_transaction_posted",
            contract_id=str(contract_id),
            amount=remaining_amount,
            consumer=self.consumer_name,
            causal_root_id=str(event.causal_root_id) if event.causal_root_id else "",
        )


@app.task(
    base=BaseEnterpriseTask,
    name="apps.finance.tasks.consumer.run_finance_consumer_task",
    queue="finance",
    ignore_result=True,
)
def run_finance_consumer_task() -> None:
    """
    Poll and process contracts.ContractClosed events for the Finance domain.

    Must run with Celery worker concurrency=1 on the 'finance' queue to enforce
    Mode A (STRICT) per-aggregate ordering invariant (ADR-0012).
    """
    FinanceContractClosedConsumer().run_batch()
