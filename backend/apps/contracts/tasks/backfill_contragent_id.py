"""
[ADR-0022] Release N+1 — Backfill contracts_contracts.contragent_id.

Strategy: JOIN finance_transactions → contragents_contragents via counterparty_inn.
Only fills where exactly ONE contragent matches the INN within the tenant
(avoids probabilistic mismatches on ambiguous INNs).

Uses raw SQL to avoid cross-domain ORM imports (ADR-0005).
Run via: celery call contracts.tasks.backfill_contragent_id.run_backfill_contragent_id
"""

from __future__ import annotations

import structlog
from celery.exceptions import SoftTimeLimitExceeded
from django.db import connection, transaction

from apps.common.tasks import BaseEnterpriseTask
from config.celery import app

logger = structlog.get_logger(__name__)

BATCH_SIZE = 5_000


@app.task(
    base=BaseEnterpriseTask,
    bind=True,
    queue="retention",
    max_retries=5,
    acks_late=True,
    soft_time_limit=3600,
    time_limit=3900,
    name="contracts.tasks.backfill_contragent_id.run_backfill_contragent_id",
)
def run_backfill_contragent_id(self) -> dict:
    """
    Iterates until no unmatched contracts remain or no progress is made.
    Idempotent: skips contracts already having contragent_id.
    """
    total_updated = 0
    iteration = 0

    try:
        while True:
            iteration += 1
            updated = _backfill_batch(BATCH_SIZE)
            total_updated += updated

            logger.info(
                "backfill_contragent_id_batch",
                task_id=self.request.id,
                iteration=iteration,
                batch_updated=updated,
                total_updated=total_updated,
            )

            if updated == 0:
                break

    except SoftTimeLimitExceeded:
        logger.warning(
            "backfill_contragent_id_time_limit",
            task_id=self.request.id,
            total_updated=total_updated,
            iteration=iteration,
        )
        raise self.retry(countdown=60) from None

    except Exception as exc:
        logger.exception(
            "backfill_contragent_id_error",
            task_id=self.request.id,
            total_updated=total_updated,
            iteration=iteration,
        )
        raise self.retry(exc=exc, countdown=60 * 2**self.request.retries) from exc

    remaining = _count_unmatched()
    logger.info(
        "backfill_contragent_id_complete",
        task_id=self.request.id,
        total_updated=total_updated,
        remaining_unmatched=remaining,
    )
    return {"total_updated": total_updated, "remaining_unmatched": remaining}


def _backfill_batch(batch_size: int) -> int:
    """
    Single batch UPDATE. Returns number of rows updated.

    Logic:
      1. Find contracts without contragent_id.
      2. For each, look up the INN from the most recent non-deleted transaction
         on that contract (ORDER BY id DESC ensures deterministic selection).
      3. Match INN → contragent_id only when exactly one contragent has that INN
         within the same tenant (COUNT = 1 guard prevents ambiguous matches).
      4. Limit to batch_size to avoid long-running transactions.
    """
    sql = """
        WITH candidates AS (
            SELECT
                c.id          AS contract_id,
                c.tenant_id,
                t.counterparty_inn
            FROM contracts_contracts c
            JOIN LATERAL (
                SELECT counterparty_inn
                FROM finance_transactions
                WHERE contract_id = c.id
                  AND tenant_id   = c.tenant_id
                  AND is_deleted  = FALSE
                  AND counterparty_inn != ''
                ORDER BY id DESC
                LIMIT 1
            ) t ON TRUE
            WHERE c.contragent_id IS NULL
              AND c.is_deleted     = FALSE
            LIMIT %(batch_size)s
            FOR UPDATE OF c SKIP LOCKED
        ),
        unique_matches AS (
            SELECT
                cand.contract_id,
                cg.id AS contragent_id
            FROM candidates cand
            JOIN contragents_contragents cg
              ON  cg.inn       = cand.counterparty_inn
              AND cg.tenant_id = cand.tenant_id
              AND cg.is_deleted = FALSE
            WHERE (
                SELECT COUNT(*)
                FROM contragents_contragents
                WHERE inn       = cand.counterparty_inn
                  AND tenant_id = cand.tenant_id
                  AND is_deleted = FALSE
            ) = 1
        )
        UPDATE contracts_contracts cc
        SET    contragent_id = um.contragent_id
        FROM   unique_matches um
        WHERE  cc.id = um.contract_id
    """
    # savepoint=False: FOR UPDATE SKIP LOCKED fails inside a subtransaction
    with transaction.atomic(savepoint=False):
        with connection.cursor() as cursor:
            cursor.execute(sql, {"batch_size": batch_size})
            return cursor.rowcount


def _count_unmatched() -> int:
    sql = """
        SELECT COUNT(*)
        FROM contracts_contracts
        WHERE contragent_id IS NULL
          AND is_deleted = FALSE
    """
    with connection.cursor() as cursor:
        cursor.execute(sql)
        row = cursor.fetchone()
        return row[0] if row else 0
