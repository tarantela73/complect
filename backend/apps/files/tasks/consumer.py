"""
FilesConsumer — scans uploaded files via ScanEngine and transitions them to
AVAILABLE or QUARANTINED.

Pipeline per files.FileUploaded event:
  claim → mark_scanning → scan → mark_available / mark_quarantined
  → emit FileAvailableEvent / FileQuarantinedEvent → ack

Ordering mode: STRICT (no aggregate sequence; each file is independent).
Emission contract: exactly one of files.FileAvailable | files.FileQuarantined
  must appear in Outbox per correlation_id — enforced by _verify_emission override.
"""
from __future__ import annotations

import time
import uuid

import structlog
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from apps.common.events.files import FileAvailableEvent, FileQuarantinedEvent, FileUploadedEvent
from apps.common.services.consumer_base import (
    BaseDomainConsumer,
    CausalEmissionMissingError,
    StreamEvent,
)
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tasks import BaseEnterpriseTask
from apps.files.repositories.file_repo import FileRepository
from apps.files.services.adaptive_scan_policy import get_adaptive_scan_policy
from apps.files.services.clamav_scan_engine import get_scan_engine
from apps.files.services.scan_engine import ScanEngine, ScanResult, StubScanEngine
from apps.files.services.system_control_policy import get_system_control_policy
from config.celery import app

logger = structlog.get_logger(__name__)

_SCAN_OUTCOME_TYPES = frozenset(
    {FileAvailableEvent.EVENT_TYPE, FileQuarantinedEvent.EVENT_TYPE}
)

_WS_GROUP = "files.tenant.{tenant_id}"


def _broadcast_file_status(file_id: uuid.UUID, status: str, tenant_id: uuid.UUID) -> None:
    """Fanout file status change to all WebSocket clients of the tenant."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    try:
        async_to_sync(channel_layer.group_send)(
            _WS_GROUP.format(tenant_id=tenant_id),
            {
                "type": "files_file_status_changed",
                "file_id": str(file_id),
                "status": status,
            },
        )
    except Exception as exc:
        logger.warning("ws_broadcast_failed", file_id=str(file_id), status=status, error=str(exc))


class FilesConsumer(BaseDomainConsumer):
    """
    Processes files.FileUploaded events: transitions FileRecord through
    UPLOADED → SCANNING → AVAILABLE | QUARANTINED and emits scan outcome events.
    """

    consumer_name = "files.scan_consumer"
    ordering_mode = "STRICT"
    handled_event_types = frozenset({FileUploadedEvent.EVENT_TYPE})

    # Non-None triggers _verify_emission; overridden below for OR logic.
    required_emissions: frozenset[str] = frozenset()

    def __init__(self, scan_engine: ScanEngine | None = None) -> None:
        super().__init__()
        self.scan_engine: ScanEngine = scan_engine or StubScanEngine()
        self._total_scan_ms: float = 0.0
        self._total_transition_ms: float = 0.0
        self._total_cycle_ms: float = 0.0

    def process(self, event: StreamEvent) -> None:
        from django.db import transaction

        from apps.common.context.core import set_tenant_id

        set_tenant_id(event.tenant_id)

        file_id_raw = event.payload.get("file_id")
        if not file_id_raw:
            logger.warning(
                "files_consumer_missing_file_id",
                consumer=self.consumer_name,
                outbox_record_id=str(event.outbox_record_id),
            )
            return

        file_id = uuid.UUID(file_id_raw)

        # CAS: UPLOADED → SCANNING. Returns False if already in another state
        # (duplicate event from a retried worker).
        scanning = FileRepository.mark_scanning(file_id)
        if not scanning:
            logger.info(
                "files_consumer_mark_scanning_skipped",
                file_id=str(file_id),
                consumer=self.consumer_name,
            )
            return

        record = FileRepository.get_by_id(file_id)

        t0_cycle = time.perf_counter()

        t0_scan = time.perf_counter()
        result = self.scan_engine.scan(record)
        self._total_scan_ms += (time.perf_counter() - t0_scan) * 1000

        t0_transition = time.perf_counter()
        _fid, _tid = file_id, event.tenant_id
        with transaction.atomic():
            if result == ScanResult.INFECTED:
                FileRepository.mark_quarantined(file_id, reason="virus_detected")
                TransactionalOutboxService.push_to_outbox(
                    FileQuarantinedEvent.create(
                        correlation_id=event.correlation_id,
                        file_id=file_id,
                        tenant_id=event.tenant_id,
                        object_key=record.object_key,
                        quarantine_reason="virus_detected",
                    )
                )
                transaction.on_commit(
                    lambda fid=_fid, tid=_tid: _broadcast_file_status(fid, "QUARANTINED", tid)
                )
                logger.info(
                    "files_consumer_file_quarantined",
                    file_id=str(file_id),
                    consumer=self.consumer_name,
                )
            elif result == ScanResult.CB_FAST_FAIL:
                # CB open — scan deferred; quarantine now, re-scan when CB recovers.
                FileRepository.mark_quarantined(file_id, reason="scan_deferred_cb_open")
                TransactionalOutboxService.push_to_outbox(
                    FileQuarantinedEvent.create(
                        correlation_id=event.correlation_id,
                        file_id=file_id,
                        tenant_id=event.tenant_id,
                        object_key=record.object_key,
                        quarantine_reason="scan_deferred_cb_open",
                    )
                )
                transaction.on_commit(
                    lambda fid=_fid, tid=_tid: _broadcast_file_status(fid, "QUARANTINED", tid)
                )
                transaction.on_commit(
                    lambda fid=_fid, tid=_tid: get_adaptive_scan_policy().push_rescan(
                        file_id=fid, tenant_id=tid
                    )
                )
                logger.warning(
                    "files_consumer_scan_deferred_cb",
                    file_id=str(file_id),
                    consumer=self.consumer_name,
                )
            elif result == ScanResult.ERROR:
                # Scan attempted but failed; quarantine permanently.
                FileRepository.mark_quarantined(file_id, reason="scan_error")
                TransactionalOutboxService.push_to_outbox(
                    FileQuarantinedEvent.create(
                        correlation_id=event.correlation_id,
                        file_id=file_id,
                        tenant_id=event.tenant_id,
                        object_key=record.object_key,
                        quarantine_reason="scan_error",
                    )
                )
                transaction.on_commit(
                    lambda fid=_fid, tid=_tid: _broadcast_file_status(fid, "QUARANTINED", tid)
                )
                logger.warning(
                    "files_consumer_scan_error_quarantined",
                    file_id=str(file_id),
                    consumer=self.consumer_name,
                )
            else:
                FileRepository.mark_available(file_id)
                TransactionalOutboxService.push_to_outbox(
                    FileAvailableEvent.create(
                        correlation_id=event.correlation_id,
                        file_id=file_id,
                        tenant_id=event.tenant_id,
                        object_key=record.object_key,
                        checksum_sha256=record.checksum_sha256,
                    )
                )
                transaction.on_commit(
                    lambda fid=_fid, tid=_tid: _broadcast_file_status(fid, "AVAILABLE", tid)
                )
                logger.info(
                    "files_consumer_file_available",
                    file_id=str(file_id),
                    consumer=self.consumer_name,
                )

        self._total_transition_ms += (time.perf_counter() - t0_transition) * 1000
        self._total_cycle_ms += (time.perf_counter() - t0_cycle) * 1000

    def run_batch(self, batch_size: int = 100) -> int:
        self._total_scan_ms = 0.0
        self._total_transition_ms = 0.0
        self._total_cycle_ms = 0.0
        _picked_up = 0
        try:
            _picked_up = super().run_batch(batch_size)
            return _picked_up
        finally:
            if self._total_cycle_ms:
                logger.info(
                    "files_consumer_batch_metrics",
                    consumer=self.consumer_name,
                    scan_ms=round(self._total_scan_ms, 2),
                    transition_ms=round(self._total_transition_ms, 2),
                    cycle_ms=round(self._total_cycle_ms, 2),
                )
                if _picked_up > 0:
                    get_system_control_policy().record_scan_latency(
                        self._total_scan_ms / _picked_up
                    )

    def _verify_emission(self, event: StreamEvent) -> None:
        """
        Hard contract: after processing files.FileUploaded, exactly one of
        files.FileAvailable or files.FileQuarantined must be in Outbox.

        OR logic — cannot be expressed via required_emissions frozenset.
        """
        from apps.common.models.outbox import OutboxRecord

        emitted = OutboxRecord.objects.filter(
            correlation_id=event.correlation_id,
            event_type__in=_SCAN_OUTCOME_TYPES,
        ).exists()

        if not emitted:
            raise CausalEmissionMissingError(
                consumer=self.consumer_name,
                outbox_record_id=event.outbox_record_id,
                correlation_id=event.correlation_id,
                missing_event_types=_SCAN_OUTCOME_TYPES,
            )


@app.task(
    base=BaseEnterpriseTask,
    name="apps.files.tasks.consumer.run_files_consumer_task",
    queue="files",
    ignore_result=True,
)
def run_files_consumer_task() -> None:
    """
    Poll and process files.FileUploaded events for the Files domain.

    STRICT mode: no fence check needed — each file is an independent aggregate.
    Queue: files (low priority, see Celery topology rule 4.15).
    ScanEngine is selected by FILES_SCAN_ENGINE setting: STUB (dev) or CLAMAV (prod).
    Batch size is controlled by SystemControlPolicy (adaptive, Redis-backed).
    """
    batch_size = get_system_control_policy().get_batch_size()
    FilesConsumer(scan_engine=get_scan_engine()).run_batch(batch_size)
