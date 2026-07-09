"""
FilesConsumer recovery scanner.

Processes files that were deferred (quarantine_reason='scan_deferred_cb_open')
when the ClamAV circuit breaker was OPEN. Runs periodically; skips if CB is
still open so it does not hammer a degraded daemon.

Flow per deferred file:
  QUARANTINED[scan_deferred_cb_open] → SCANNING → AVAILABLE | QUARANTINED[final]
"""

from __future__ import annotations

import uuid

import structlog

from apps.common.tasks import BaseEnterpriseTask
from apps.files.services.adaptive_scan_policy import AdaptiveScanPolicy, get_adaptive_scan_policy
from apps.files.services.clamav_scan_engine import get_scan_engine
from apps.files.services.scan_engine import ScanEngine
from config.celery import app

logger = structlog.get_logger(__name__)


def _rescan_one(
    file_id: uuid.UUID,
    tenant_id: uuid.UUID,
    scan_engine: ScanEngine,
    policy: AdaptiveScanPolicy,
) -> None:
    from django.db import transaction

    from apps.common.context.core import set_tenant_id
    from apps.common.events.files import FileAvailableEvent, FileQuarantinedEvent
    from apps.common.services.outbox_service import TransactionalOutboxService
    from apps.files.repositories.file_repo import FileRepository
    from apps.files.services.scan_engine import ScanResult

    set_tenant_id(tenant_id)

    if not FileRepository.mark_rescanning(file_id):
        logger.info(
            "files_recovery_scan_skip_unexpected_state",
            file_id=str(file_id),
        )
        return

    record = FileRepository.get_by_id(file_id)
    result = scan_engine.scan(record)
    correlation_id = uuid.uuid4()

    if result == ScanResult.CB_FAST_FAIL:
        # CB tripped again during probe — defer again and re-queue after commit.
        with transaction.atomic():
            FileRepository.mark_quarantined(file_id, reason="scan_deferred_cb_open")
            _fid, _tid = file_id, tenant_id
            transaction.on_commit(lambda fid=_fid, tid=_tid: policy.push_rescan(file_id=fid, tenant_id=tid))
        logger.warning("files_recovery_scan_requeued_cb_still_open", file_id=str(file_id))
        return

    with transaction.atomic():
        if result == ScanResult.INFECTED:
            FileRepository.mark_quarantined(file_id, reason="virus_detected")
            TransactionalOutboxService.push_to_outbox(
                FileQuarantinedEvent.create(
                    correlation_id=correlation_id,
                    file_id=file_id,
                    tenant_id=tenant_id,
                    object_key=record.object_key,
                    quarantine_reason="virus_detected",
                    uploaded_by=record.uploaded_by,
                )
            )
            logger.info("files_recovery_scan_infected", file_id=str(file_id))
        elif result == ScanResult.ERROR:
            FileRepository.mark_quarantined(file_id, reason="scan_error")
            TransactionalOutboxService.push_to_outbox(
                FileQuarantinedEvent.create(
                    correlation_id=correlation_id,
                    file_id=file_id,
                    tenant_id=tenant_id,
                    object_key=record.object_key,
                    quarantine_reason="scan_error",
                    uploaded_by=record.uploaded_by,
                )
            )
            logger.warning("files_recovery_scan_error", file_id=str(file_id))
        else:  # CLEAN
            FileRepository.mark_available(file_id)
            TransactionalOutboxService.push_to_outbox(
                FileAvailableEvent.create(
                    correlation_id=correlation_id,
                    file_id=file_id,
                    tenant_id=tenant_id,
                    object_key=record.object_key,
                    checksum_sha256=record.checksum_sha256,
                )
            )
            logger.info("files_recovery_scan_available", file_id=str(file_id))


@app.task(
    base=BaseEnterpriseTask,
    name="apps.files.tasks.recovery_scanner.run_files_recovery_scan_task",
    queue="files",
    ignore_result=True,
)
def run_files_recovery_scan_task() -> None:
    """
    Poll the rescan queue and re-scan files deferred while ClamAV CB was open.

    Skips immediately if CB is still OPEN — avoids hammering a degraded daemon.
    Batch size is bounded by FILES_RECOVERY_SCAN_BATCH_SIZE (default 50).
    """
    from django.conf import settings

    from apps.files.services.clamav_circuit_breaker import get_circuit_breaker

    if get_circuit_breaker().is_open():
        logger.info("files_recovery_scan_skipped_cb_open")
        return

    policy = get_adaptive_scan_policy()
    batch_size = getattr(settings, "FILES_RECOVERY_SCAN_BATCH_SIZE", 50)
    batch = policy.pop_rescan_batch(batch_size)
    if not batch:
        return

    scan_engine = get_scan_engine()
    logger.info("files_recovery_scan_batch_start", count=len(batch))

    for item in batch:
        try:
            _rescan_one(
                file_id=uuid.UUID(item["file_id"]),
                tenant_id=uuid.UUID(item["tenant_id"]),
                scan_engine=scan_engine,
                policy=policy,
            )
        except Exception:
            logger.exception(
                "files_recovery_scan_item_error",
                file_id=item.get("file_id"),
            )
