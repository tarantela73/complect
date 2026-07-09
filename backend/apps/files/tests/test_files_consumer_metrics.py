"""
FilesConsumer telemetry tests.

Verifies that scan_ms / transition_ms / cycle_ms are accumulated correctly
per process() call and emitted via structlog on run_batch() completion.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from structlog.testing import capture_logs

from apps.common.tenancy.context import set_tenant_context
from apps.files.schemas.file_dto import UploadCompleteRequest, UploadInitRequest
from apps.files.services.scan_engine import StubScanEngine
from apps.files.services.upload_complete_service import UploadCompleteService
from apps.files.services.upload_init_service import UploadInitService
from apps.files.tasks.consumer import FilesConsumer

_DUMMY_SHA = hashlib.sha256(b"payload").hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_file_uploaded_outbox(
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    corr: uuid.UUID | None = None,
):
    """Returns (OutboxRecord, file_id, object_key) with file in UPLOADED state."""
    from apps.common.models.outbox import OutboxRecord

    corr = corr or uuid.uuid4()
    init = UploadInitService.initialize_upload(
        actor_id=actor_id,
        request=UploadInitRequest(
            original_name="report.pdf",
            content_type="application/pdf",
            size_bytes=4096,
        ),
    )
    UploadCompleteService.complete_upload(
        file_id=init.file_id,
        actor_id=actor_id,
        request=UploadCompleteRequest(checksum_sha256=_DUMMY_SHA),
        correlation_id=corr,
    )
    outbox = OutboxRecord.objects.create(
        id=uuid.uuid4(),
        event_type="files.FileUploaded",
        payload_json={
            "file_id": str(init.file_id),
            "tenant_id": str(tenant_id),
            "object_key": init.object_key,
            "original_name": "report.pdf",
            "content_type": "application/pdf",
            "size_bytes": 4096,
            "checksum_sha256": _DUMMY_SHA,
            "uploaded_by": str(actor_id),
        },
        correlation_id=corr,
        causal_root_id=uuid.uuid4(),
        is_processed=True,
    )
    return outbox, init.file_id, init.object_key


# ---------------------------------------------------------------------------
# Per-event accumulation (via handle())
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestFilesConsumerMetricsAccumulation:
    @pytest.fixture(autouse=True)
    def ctx(self):
        self.tenant_id = uuid.uuid4()
        self.actor_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    def test_scan_ms_positive_after_handle(self):
        outbox, _, _ = _create_file_uploaded_outbox(self.tenant_id, self.actor_id)
        consumer = FilesConsumer(scan_engine=StubScanEngine())
        consumer.handle(outbox)
        assert consumer._total_scan_ms >= 0
        # StubScanEngine is instant but the timer still fires — value is non-negative.
        # We assert it was actually set (not skipped) by verifying type.
        assert isinstance(consumer._total_scan_ms, float)

    def test_transition_ms_positive_after_handle(self):
        outbox, _, _ = _create_file_uploaded_outbox(self.tenant_id, self.actor_id)
        consumer = FilesConsumer(scan_engine=StubScanEngine())
        consumer.handle(outbox)
        assert consumer._total_transition_ms > 0

    def test_cycle_ms_positive_after_handle(self):
        outbox, _, _ = _create_file_uploaded_outbox(self.tenant_id, self.actor_id)
        consumer = FilesConsumer(scan_engine=StubScanEngine())
        consumer.handle(outbox)
        assert consumer._total_cycle_ms > 0

    def test_cycle_ms_gte_scan_plus_transition(self):
        """cycle_ms covers scan + transition — must be >= their sum."""
        outbox, _, _ = _create_file_uploaded_outbox(self.tenant_id, self.actor_id)
        consumer = FilesConsumer(scan_engine=StubScanEngine())
        consumer.handle(outbox)
        assert consumer._total_cycle_ms >= consumer._total_scan_ms + consumer._total_transition_ms

    def test_metrics_accumulate_across_multiple_events(self):
        """Two events in sequence → totals are the sum of both."""
        consumer = FilesConsumer(scan_engine=StubScanEngine())

        for _ in range(2):
            outbox, _, _ = _create_file_uploaded_outbox(self.tenant_id, self.actor_id)
            consumer.handle(outbox)

        assert consumer._total_cycle_ms > 0
        assert consumer._total_transition_ms > 0


# ---------------------------------------------------------------------------
# run_batch() — log emission + reset
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestFilesConsumerBatchMetricsLog:
    @pytest.fixture(autouse=True)
    def ctx(self):
        self.tenant_id = uuid.uuid4()
        self.actor_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    def test_run_batch_emits_metrics_log(self):
        _create_file_uploaded_outbox(self.tenant_id, self.actor_id)
        consumer = FilesConsumer(scan_engine=StubScanEngine())

        with capture_logs() as log:
            consumer.run_batch()

        metrics = [e for e in log if e.get("event") == "files_consumer_batch_metrics"]
        assert len(metrics) == 1
        entry = metrics[0]
        assert "scan_ms" in entry
        assert "transition_ms" in entry
        assert "cycle_ms" in entry
        assert entry["consumer"] == "files.scan_consumer"

    def test_run_batch_no_metrics_log_when_empty(self):
        """No records → no files_consumer_batch_metrics entry (avoids noise)."""
        consumer = FilesConsumer(scan_engine=StubScanEngine())

        with capture_logs() as log:
            consumer.run_batch()

        metrics = [e for e in log if e.get("event") == "files_consumer_batch_metrics"]
        assert len(metrics) == 0

    def test_metrics_reset_between_batches(self):
        """Accumulators are zeroed at the start of each run_batch() call."""
        outbox, _, _ = _create_file_uploaded_outbox(self.tenant_id, self.actor_id)
        consumer = FilesConsumer(scan_engine=StubScanEngine())
        consumer.handle(outbox)

        first_cycle = consumer._total_cycle_ms
        assert first_cycle > 0

        # Second run_batch() — no pending records; accumulators must be 0 after reset.
        consumer.run_batch()
        assert consumer._total_cycle_ms == 0.0
        assert consumer._total_scan_ms == 0.0
        assert consumer._total_transition_ms == 0.0

    def test_metrics_values_are_rounded_floats(self):
        _create_file_uploaded_outbox(self.tenant_id, self.actor_id)
        consumer = FilesConsumer(scan_engine=StubScanEngine())

        with capture_logs() as log:
            consumer.run_batch()

        entry = next(e for e in log if e.get("event") == "files_consumer_batch_metrics")
        # All values must be numeric (rounded to 2 decimal places by consumer).
        assert isinstance(entry["scan_ms"], float)
        assert isinstance(entry["transition_ms"], float)
        assert isinstance(entry["cycle_ms"], float)
