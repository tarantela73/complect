"""
Payments domain Celery tasks.

reconcile_statement_task — asynchronously runs the reconciliation algorithm
for a confirmed BankStatement (status=IMPORTING → IMPORTED | PARSE_FAILED).

Queue: finance (high-priority, per queue topology rule 4.15).
"""
from __future__ import annotations

import uuid

import structlog
from celery.exceptions import MaxRetriesExceededError
from django.db import transaction

from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tasks import BaseEnterpriseTask
from config.celery import app

logger = structlog.get_logger(__name__)


@app.task(
    base=BaseEnterpriseTask,
    name="apps.payments.tasks.reconcile_statement_task",
    queue="finance",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    ignore_result=True,
    acks_late=True,
)
def reconcile_statement_task(
    self,
    *,
    stmt_id: str,
    tenant_id: str,
    confirmed_by: str,
    correlation_id: str,
) -> None:
    from apps.common.context.core import set_tenant_id
    from apps.common.tenancy.context import set_tenant_context
    from apps.finance.services.transaction_finder import get_transaction_finder
    from apps.payments.events import ReconciliationCompletedEvent
    from apps.payments.models.bank_statement import BankStatement, BankStatementStatus
    from apps.payments.models.bank_statement_line import MatchStatus
    from apps.payments.services.matching_service import MatchingService

    _stmt_id = uuid.UUID(stmt_id)
    _tenant_id = uuid.UUID(tenant_id)
    _correlation_id = uuid.UUID(correlation_id)

    set_tenant_id(_tenant_id)
    set_tenant_context(_tenant_id)

    log = logger.bind(stmt_id=stmt_id, tenant_id=tenant_id, task_id=self.request.id)

    try:
        with transaction.atomic():
            try:
                stmt = BankStatement.objects.select_for_update(nowait=True).get(
                    id=_stmt_id,
                    tenant_id=_tenant_id,
                )
            except BankStatement.DoesNotExist:
                log.warning("reconcile_task_statement_not_found")
                return

            if stmt.status != BankStatementStatus.IMPORTING:
                log.info("reconcile_task_skipped", status=stmt.status)
                return

            counters = MatchingService(get_transaction_finder()).match_statement(stmt)

            matched_count = counters.get(MatchStatus.MATCHED, 0)

            BankStatement.objects.filter(id=_stmt_id, tenant_id=_tenant_id).update(
                status=BankStatementStatus.IMPORTED,
                matched_lines=matched_count,
            )

            TransactionalOutboxService.push_to_outbox(
                ReconciliationCompletedEvent.create(
                    correlation_id=_correlation_id,
                    tenant_id=_tenant_id,
                    statement_id=_stmt_id,
                    total_lines=stmt.total_lines or 0,
                    matched_lines=matched_count,
                    partial_lines=counters.get(MatchStatus.PARTIAL, 0),
                    ambiguous_lines=counters.get(MatchStatus.AMBIGUOUS, 0),
                    unmatched_lines=counters.get(MatchStatus.UNMATCHED, 0),
                )
            )

        log.info(
            "reconcile_task_completed",
            matched=matched_count,
            total=stmt.total_lines,
        )

    except Exception as exc:
        log.error("reconcile_task_error", error=str(exc), retries=self.request.retries)
        try:
            raise self.retry(exc=exc) from exc
        except MaxRetriesExceededError:
            _mark_failed(_stmt_id, _tenant_id, _correlation_id, exc)
            log.error("reconcile_task_exhausted", error=str(exc))


def _mark_failed(
    stmt_id: uuid.UUID,
    tenant_id: uuid.UUID,
    correlation_id: uuid.UUID,
    exc: Exception,
) -> None:
    from apps.payments.events import ReconciliationFailedEvent
    from apps.payments.models.bank_statement import BankStatement, BankStatementStatus

    try:
        with transaction.atomic():
            BankStatement.objects.filter(id=stmt_id, tenant_id=tenant_id).update(
                status=BankStatementStatus.PARSE_FAILED,
                parse_error=f"Сопоставление не выполнено: {exc}",
            )
            TransactionalOutboxService.push_to_outbox(
                ReconciliationFailedEvent.create(
                    correlation_id=correlation_id,
                    tenant_id=tenant_id,
                    statement_id=stmt_id,
                    error=str(exc),
                )
            )
    except Exception as inner:
        logger.error("reconcile_mark_failed_error", error=str(inner))
