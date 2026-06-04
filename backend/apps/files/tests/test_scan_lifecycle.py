"""
State machine + FilesConsumer scan pipeline tests.

State machine (FileRepository CAS guards):
  PENDING_UPLOAD → UPLOADED                        (mark_uploaded)
  UPLOADED       → SCANNING                        (mark_scanning)
  SCANNING       → AVAILABLE                       (mark_available)
  SCANNING       → QUARANTINED                     (mark_quarantined)
  QUARANTINED[scan_deferred_cb_open] → SCANNING    (mark_rescanning — CB recovery)

  All other transitions must silently return False (no rows updated).

Consumer pipeline:
  files.FileUploaded → AVAILABLE  + FileAvailableEvent   (CLEAN)
  files.FileUploaded → QUARANTINED + FileQuarantinedEvent (INFECTED)
  files.FileUploaded → QUARANTINED + FileQuarantinedEvent (ERROR)
  files.FileUploaded → QUARANTINED[scan_deferred_cb_open] + FileQuarantinedEvent (CB_FAST_FAIL)
  Duplicate event discarded (idempotency via ConsumerIdempotencyService).
  Causal emission verified (CausalEmissionMissingError on missing outcome event).
"""
from __future__ import annotations

import hashlib
import uuid
from unittest.mock import MagicMock

import pytest

from apps.common.models.outbox import OutboxRecord
from apps.common.tenancy.context import set_tenant_context
from apps.files.models.file_record import FileStatus
from apps.files.repositories.file_repo import FileRepository
from apps.files.schemas.file_dto import UploadCompleteRequest, UploadInitRequest
from apps.files.services.adaptive_scan_policy import _RESCAN_QUEUE_KEY
from apps.files.services.scan_engine import ScanResult, StubScanEngine
from apps.files.services.upload_complete_service import UploadCompleteService
from apps.files.services.upload_init_service import UploadInitService
from apps.files.tasks.consumer import FilesConsumer

_DUMMY_SHA = hashlib.sha256(b"payload").hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_uploaded_file(tenant_id: uuid.UUID, actor_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (file_id, correlation_id) of a UPLOADED file with its FileUploaded outbox event."""
    corr = uuid.uuid4()
    init = UploadInitService.initialize_upload(
        actor_id=actor_id,
        request=UploadInitRequest(
            original_name="scan_me.pdf",
            content_type="application/pdf",
            size_bytes=2048,
        ),
    )
    UploadCompleteService.complete_upload(
        file_id=init.file_id,
        actor_id=actor_id,
        request=UploadCompleteRequest(checksum_sha256=_DUMMY_SHA),
        correlation_id=corr,
    )
    return init.file_id, corr


def _make_outbox_for_file(file_id: uuid.UUID, tenant_id: uuid.UUID, corr: uuid.UUID) -> OutboxRecord:
    rec_id = uuid.uuid4()
    return OutboxRecord.objects.create(
        id=rec_id,
        event_type="files.FileUploaded",
        payload_json={
            "file_id": str(file_id),
            "tenant_id": str(tenant_id),
            "object_key": f"{tenant_id}/{file_id}/scan_me.pdf",
            "original_name": "scan_me.pdf",
            "content_type": "application/pdf",
            "size_bytes": 2048,
            "checksum_sha256": _DUMMY_SHA,
            "uploaded_by": str(uuid.uuid4()),
        },
        correlation_id=corr,
        causal_root_id=rec_id,
        is_processed=True,
    )


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestFileStatusMachine:
    @pytest.fixture(autouse=True)
    def ctx(self):
        self.tenant_id = uuid.uuid4()
        self.actor_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    # -- Valid transitions --

    def test_pending_to_uploaded(self):
        file_id, _ = _setup_uploaded_file(self.tenant_id, self.actor_id)
        record = FileRepository.get_by_id(file_id)
        assert record.status == FileStatus.UPLOADED

    def test_uploaded_to_scanning(self):
        file_id, _ = _setup_uploaded_file(self.tenant_id, self.actor_id)
        result = FileRepository.mark_scanning(file_id)
        assert result is True
        assert FileRepository.get_by_id(file_id).status == FileStatus.SCANNING

    def test_scanning_to_available(self):
        file_id, _ = _setup_uploaded_file(self.tenant_id, self.actor_id)
        FileRepository.mark_scanning(file_id)
        result = FileRepository.mark_available(file_id)
        assert result is True
        assert FileRepository.get_by_id(file_id).status == FileStatus.AVAILABLE

    def test_scanning_to_quarantined(self):
        file_id, _ = _setup_uploaded_file(self.tenant_id, self.actor_id)
        FileRepository.mark_scanning(file_id)
        result = FileRepository.mark_quarantined(file_id, reason="virus_detected")
        assert result is True
        record = FileRepository.get_by_id(file_id)
        assert record.status == FileStatus.QUARANTINED
        assert record.quarantine_reason == "virus_detected"

    # -- Invalid transitions (must return False) --

    def test_pending_upload_cannot_skip_to_scanning(self):
        init = UploadInitService.initialize_upload(
            actor_id=self.actor_id,
            request=UploadInitRequest(
                original_name="f.pdf", content_type="application/pdf", size_bytes=100
            ),
        )
        result = FileRepository.mark_scanning(init.file_id)
        assert result is False
        assert FileRepository.get_by_id(init.file_id).status == FileStatus.PENDING_UPLOAD

    def test_available_cannot_go_back_to_scanning(self):
        file_id, _ = _setup_uploaded_file(self.tenant_id, self.actor_id)
        FileRepository.mark_scanning(file_id)
        FileRepository.mark_available(file_id)
        result = FileRepository.mark_scanning(file_id)
        assert result is False
        assert FileRepository.get_by_id(file_id).status == FileStatus.AVAILABLE

    def test_quarantined_to_scanning_via_rescanning(self):
        file_id, _ = _setup_uploaded_file(self.tenant_id, self.actor_id)
        FileRepository.mark_scanning(file_id)
        FileRepository.mark_quarantined(file_id, reason="scan_deferred_cb_open")
        result = FileRepository.mark_rescanning(file_id)
        assert result is True
        assert FileRepository.get_by_id(file_id).status == FileStatus.SCANNING

    def test_mark_rescanning_requires_deferred_cb_reason(self):
        file_id, _ = _setup_uploaded_file(self.tenant_id, self.actor_id)
        FileRepository.mark_scanning(file_id)
        FileRepository.mark_quarantined(file_id, reason="virus_detected")
        result = FileRepository.mark_rescanning(file_id)
        assert result is False
        assert FileRepository.get_by_id(file_id).status == FileStatus.QUARANTINED

    def test_quarantined_cannot_go_to_available(self):
        file_id, _ = _setup_uploaded_file(self.tenant_id, self.actor_id)
        FileRepository.mark_scanning(file_id)
        FileRepository.mark_quarantined(file_id, reason="test")
        result = FileRepository.mark_available(file_id)
        assert result is False
        assert FileRepository.get_by_id(file_id).status == FileStatus.QUARANTINED

    def test_pending_upload_cannot_jump_to_available(self):
        init = UploadInitService.initialize_upload(
            actor_id=self.actor_id,
            request=UploadInitRequest(
                original_name="f.pdf", content_type="application/pdf", size_bytes=100
            ),
        )
        result = FileRepository.mark_available(init.file_id)
        assert result is False


# ---------------------------------------------------------------------------
# Consumer pipeline
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestFilesConsumerPipeline:
    @pytest.fixture(autouse=True)
    def ctx(self):
        self.tenant_id = uuid.uuid4()
        self.actor_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    def test_clean_scan_produces_available_and_event(self):
        file_id, corr = _setup_uploaded_file(self.tenant_id, self.actor_id)
        record = _make_outbox_for_file(file_id, self.tenant_id, corr)

        FilesConsumer(scan_engine=StubScanEngine()).handle(record)

        assert FileRepository.get_by_id(file_id).status == FileStatus.AVAILABLE
        assert OutboxRecord.objects.filter(
            correlation_id=corr, event_type="files.FileAvailable"
        ).exists()

    def test_infected_scan_produces_quarantined_and_event(self):
        file_id, corr = _setup_uploaded_file(self.tenant_id, self.actor_id)
        record = _make_outbox_for_file(file_id, self.tenant_id, corr)

        engine = MagicMock()
        engine.scan.return_value = ScanResult.INFECTED
        FilesConsumer(scan_engine=engine).handle(record)

        repo_record = FileRepository.get_by_id(file_id)
        assert repo_record.status == FileStatus.QUARANTINED
        assert repo_record.quarantine_reason == "virus_detected"
        assert OutboxRecord.objects.filter(
            correlation_id=corr, event_type="files.FileQuarantined"
        ).exists()

    def test_scan_error_quarantines_with_scan_error_reason(self):
        file_id, corr = _setup_uploaded_file(self.tenant_id, self.actor_id)
        record = _make_outbox_for_file(file_id, self.tenant_id, corr)

        engine = MagicMock()
        engine.scan.return_value = ScanResult.ERROR
        FilesConsumer(scan_engine=engine).handle(record)

        repo_record = FileRepository.get_by_id(file_id)
        assert repo_record.status == FileStatus.QUARANTINED
        assert repo_record.quarantine_reason == "scan_error"

    def test_cb_fast_fail_quarantines_with_deferred_reason(self):
        import redis
        from django.conf import settings

        r = redis.Redis.from_url(settings.REDIS_CACHE_URL, decode_responses=True)
        r.delete(_RESCAN_QUEUE_KEY)

        file_id, corr = _setup_uploaded_file(self.tenant_id, self.actor_id)
        record = _make_outbox_for_file(file_id, self.tenant_id, corr)

        engine = MagicMock()
        engine.scan.return_value = ScanResult.CB_FAST_FAIL
        FilesConsumer(scan_engine=engine).handle(record)

        repo_record = FileRepository.get_by_id(file_id)
        assert repo_record.status == FileStatus.QUARANTINED
        assert repo_record.quarantine_reason == "scan_deferred_cb_open"
        assert OutboxRecord.objects.filter(
            correlation_id=corr, event_type="files.FileQuarantined"
        ).exists()

        r.delete(_RESCAN_QUEUE_KEY)

    def test_duplicate_event_is_discarded(self):
        file_id, corr = _setup_uploaded_file(self.tenant_id, self.actor_id)
        record = _make_outbox_for_file(file_id, self.tenant_id, corr)

        consumer = FilesConsumer(scan_engine=StubScanEngine())
        consumer.handle(record)
        # Second handle — idempotency claim will discard
        consumer.handle(record)

        # Only one FileAvailable event emitted
        assert OutboxRecord.objects.filter(
            correlation_id=corr, event_type="files.FileAvailable"
        ).count() == 1

    def test_causal_emission_verified_after_process(self):
        """Consumer raises CausalEmissionMissingError when scan outcome event absent."""
        from unittest.mock import patch

        from apps.common.services.consumer_base import CausalEmissionMissingError
        from apps.common.services.outbox_service import TransactionalOutboxService

        file_id, corr = _setup_uploaded_file(self.tenant_id, self.actor_id)
        record = _make_outbox_for_file(file_id, self.tenant_id, corr)

        # Patch push_to_outbox so the outcome event is never written to DB,
        # then verify _verify_emission raises CausalEmissionMissingError.
        with patch.object(TransactionalOutboxService, "push_to_outbox", return_value=None):
            with pytest.raises(CausalEmissionMissingError):
                FilesConsumer(scan_engine=StubScanEngine()).handle(record)

    def test_full_lifecycle_pending_to_available(self):
        """End-to-end: PENDING_UPLOAD → UPLOADED → SCANNING → AVAILABLE."""
        file_id, corr = _setup_uploaded_file(self.tenant_id, self.actor_id)

        record = FileRepository.get_by_id(file_id)
        assert record.status == FileStatus.UPLOADED

        outbox = _make_outbox_for_file(file_id, self.tenant_id, corr)
        FilesConsumer(scan_engine=StubScanEngine()).handle(outbox)

        final = FileRepository.get_by_id(file_id)
        assert final.status == FileStatus.AVAILABLE
        assert final.scanned_at is not None
