"""
Recovery scanner integration tests.

Verifies that run_files_recovery_scan_task():
  - Skips when CB is OPEN.
  - Is a no-op on an empty rescan queue.
  - Transitions QUARANTINED[scan_deferred_cb_open] → AVAILABLE on CLEAN result.
  - Emits FileAvailable / FileQuarantined outbox events.
  - Keeps file QUARANTINED with final reason on INFECTED / ERROR.
  - Re-queues and rolls back on CB_FAST_FAIL during recovery probe.
  - Skips files not in the expected deferred state (idempotency guard).
"""

from __future__ import annotations

import hashlib
import uuid
from unittest.mock import MagicMock, patch

import pytest

from apps.common.models.outbox import OutboxRecord
from apps.common.tenancy.context import set_tenant_context
from apps.files.models.file_record import FileStatus
from apps.files.repositories.file_repo import FileRepository
from apps.files.schemas.file_dto import UploadCompleteRequest, UploadInitRequest
from apps.files.services.adaptive_scan_policy import _RESCAN_QUEUE_KEY, get_adaptive_scan_policy
from apps.files.services.clamav_circuit_breaker import _CB_FAILURES_KEY, _CB_OPEN_KEY
from apps.files.services.scan_engine import ScanResult
from apps.files.services.upload_complete_service import UploadCompleteService
from apps.files.services.upload_init_service import UploadInitService
from apps.files.tasks.recovery_scanner import run_files_recovery_scan_task

_DUMMY_SHA = hashlib.sha256(b"recovery").hexdigest()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_state():
    import redis
    from django.conf import settings
    from django.core.cache import cache

    cache.delete(_CB_OPEN_KEY)
    cache.delete(_CB_FAILURES_KEY)
    r = redis.Redis.from_url(settings.REDIS_CACHE_URL, decode_responses=True)
    r.delete(_RESCAN_QUEUE_KEY)
    yield
    cache.delete(_CB_OPEN_KEY)
    cache.delete(_CB_FAILURES_KEY)
    r.delete(_RESCAN_QUEUE_KEY)


def _setup_deferred_file(tenant_id: uuid.UUID, actor_id: uuid.UUID) -> uuid.UUID:
    """Create a FileRecord in QUARANTINED[scan_deferred_cb_open] state."""
    init = UploadInitService.initialize_upload(
        actor_id=actor_id,
        request=UploadInitRequest(
            original_name="recovery.pdf",
            content_type="application/pdf",
            size_bytes=1024,
        ),
    )
    UploadCompleteService.complete_upload(
        file_id=init.file_id,
        actor_id=actor_id,
        request=UploadCompleteRequest(checksum_sha256=_DUMMY_SHA),
        correlation_id=uuid.uuid4(),
    )
    FileRepository.mark_scanning(init.file_id)
    FileRepository.mark_quarantined(init.file_id, reason="scan_deferred_cb_open")
    return init.file_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestRecoveryScannerTask:
    @pytest.fixture(autouse=True)
    def ctx(self):
        self.tenant_id = uuid.uuid4()
        self.actor_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    def test_skips_entirely_when_circuit_open(self):
        from django.core.cache import cache

        cache.set(_CB_OPEN_KEY, 1, timeout=60)

        file_id = _setup_deferred_file(self.tenant_id, self.actor_id)
        policy = get_adaptive_scan_policy()
        policy.push_rescan(file_id=file_id, tenant_id=self.tenant_id)

        run_files_recovery_scan_task()

        assert policy.rescan_queue_size() == 1
        assert FileRepository.get_by_id(file_id).status == FileStatus.QUARANTINED

    def test_empty_queue_is_no_op(self):
        engine = MagicMock()
        with patch("apps.files.tasks.recovery_scanner.get_scan_engine", return_value=engine):
            run_files_recovery_scan_task()
        engine.scan.assert_not_called()

    def test_clean_scan_makes_file_available(self):
        file_id = _setup_deferred_file(self.tenant_id, self.actor_id)
        policy = get_adaptive_scan_policy()
        policy.push_rescan(file_id=file_id, tenant_id=self.tenant_id)

        engine = MagicMock()
        engine.scan.return_value = ScanResult.CLEAN
        with patch("apps.files.tasks.recovery_scanner.get_scan_engine", return_value=engine):
            run_files_recovery_scan_task()

        assert FileRepository.get_by_id(file_id).status == FileStatus.AVAILABLE
        assert OutboxRecord.objects.filter(
            event_type="files.FileAvailable",
            payload_json__file_id=str(file_id),
        ).exists()

    def test_infected_keeps_quarantined_with_virus_reason(self):
        file_id = _setup_deferred_file(self.tenant_id, self.actor_id)
        policy = get_adaptive_scan_policy()
        policy.push_rescan(file_id=file_id, tenant_id=self.tenant_id)

        engine = MagicMock()
        engine.scan.return_value = ScanResult.INFECTED
        with patch("apps.files.tasks.recovery_scanner.get_scan_engine", return_value=engine):
            run_files_recovery_scan_task()

        record = FileRepository.get_by_id(file_id)
        assert record.status == FileStatus.QUARANTINED
        assert record.quarantine_reason == "virus_detected"
        assert OutboxRecord.objects.filter(
            event_type="files.FileQuarantined",
            payload_json__file_id=str(file_id),
        ).exists()

    def test_scan_error_keeps_quarantined_with_error_reason(self):
        file_id = _setup_deferred_file(self.tenant_id, self.actor_id)
        policy = get_adaptive_scan_policy()
        policy.push_rescan(file_id=file_id, tenant_id=self.tenant_id)

        engine = MagicMock()
        engine.scan.return_value = ScanResult.ERROR
        with patch("apps.files.tasks.recovery_scanner.get_scan_engine", return_value=engine):
            run_files_recovery_scan_task()

        record = FileRepository.get_by_id(file_id)
        assert record.status == FileStatus.QUARANTINED
        assert record.quarantine_reason == "scan_error"

    def test_cb_fast_fail_requeues_and_restores_deferred_state(self):
        file_id = _setup_deferred_file(self.tenant_id, self.actor_id)
        policy = get_adaptive_scan_policy()
        policy.push_rescan(file_id=file_id, tenant_id=self.tenant_id)

        engine = MagicMock()
        engine.scan.return_value = ScanResult.CB_FAST_FAIL
        with patch("apps.files.tasks.recovery_scanner.get_scan_engine", return_value=engine):
            run_files_recovery_scan_task()

        record = FileRepository.get_by_id(file_id)
        assert record.status == FileStatus.QUARANTINED
        assert record.quarantine_reason == "scan_deferred_cb_open"
        assert policy.rescan_queue_size() == 1

    def test_file_not_in_deferred_state_is_skipped(self):
        """
        mark_rescanning CAS requires quarantine_reason='scan_deferred_cb_open'.
        A file in AVAILABLE state must be left untouched.
        """
        init = UploadInitService.initialize_upload(
            actor_id=self.actor_id,
            request=UploadInitRequest(
                original_name="other.pdf",
                content_type="application/pdf",
                size_bytes=512,
            ),
        )
        UploadCompleteService.complete_upload(
            file_id=init.file_id,
            actor_id=self.actor_id,
            request=UploadCompleteRequest(checksum_sha256=_DUMMY_SHA),
            correlation_id=uuid.uuid4(),
        )
        FileRepository.mark_scanning(init.file_id)
        FileRepository.mark_available(init.file_id)

        policy = get_adaptive_scan_policy()
        policy.push_rescan(file_id=init.file_id, tenant_id=self.tenant_id)

        engine = MagicMock()
        engine.scan.return_value = ScanResult.CLEAN
        with patch("apps.files.tasks.recovery_scanner.get_scan_engine", return_value=engine):
            run_files_recovery_scan_task()

        assert FileRepository.get_by_id(init.file_id).status == FileStatus.AVAILABLE
        engine.scan.assert_not_called()
