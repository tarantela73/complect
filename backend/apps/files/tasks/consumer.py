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

import uuid

import structlog

from apps.common.events.files import FileAvailableEvent, FileQuarantinedEvent, FileUploadedEvent
from apps.common.services.consumer_base import (
    BaseDomainConsumer,
    CausalEmissionMissingError,
    StreamEvent,
)
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tasks import BaseEnterpriseTask
from apps.files.repositories.file_repo import FileRepository
from apps.files.services.clamav_scan_engine import get_scan_engine
from apps.files.services.scan_engine import ScanEngine, ScanResult, StubScanEngine
from config.celery import app

logger = structlog.get_logger(__name__)

_SCAN_OUTCOME_TYPES = frozenset(
    {FileAvailableEvent.EVENT_TYPE, FileQuarantinedEvent.EVENT_TYPE}
)


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
        result = self.scan_engine.scan(record)

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
                logger.info(
                    "files_consumer_file_quarantined",
                    file_id=str(file_id),
                    consumer=self.consumer_name,
                )
            elif result == ScanResult.ERROR:
                # Error during scan: quarantine to avoid stuck SCANNING state.
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
                logger.info(
                    "files_consumer_file_available",
                    file_id=str(file_id),
                    consumer=self.consumer_name,
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
    """
    FilesConsumer(scan_engine=get_scan_engine()).run_batch()
