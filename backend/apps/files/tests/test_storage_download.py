"""
StorageBackend integration + DownloadService business-rule tests.

Coverage:
  UploadInitService — storage injection, presigned PUT URL shape
  DownloadService   — AVAILABLE-only guard, storage injection, tenant isolation
"""

from __future__ import annotations

import hashlib
import uuid
from unittest.mock import MagicMock

import pytest

from apps.common.tenancy.context import set_tenant_context
from apps.files.repositories.file_repo import FileRepository
from apps.files.schemas.file_dto import UploadCompleteRequest, UploadInitRequest
from apps.files.services.download_service import DownloadService
from apps.files.services.scan_engine import StubScanEngine
from apps.files.services.storage_backend import StubStorageBackend
from apps.files.services.upload_complete_service import UploadCompleteService
from apps.files.services.upload_init_service import UploadInitService
from apps.files.tasks.consumer import FilesConsumer

_DUMMY_SHA = hashlib.sha256(b"payload").hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_available_file(tenant_id: uuid.UUID, actor_id: uuid.UUID) -> tuple[uuid.UUID, str]:
    """Returns (file_id, object_key) for a file in AVAILABLE state."""
    from apps.common.models.outbox import OutboxRecord

    corr = uuid.uuid4()
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
    FilesConsumer(scan_engine=StubScanEngine()).handle(outbox)
    return init.file_id, init.object_key


# ---------------------------------------------------------------------------
# UploadInitService — storage injection
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestUploadInitStorageInjection:
    @pytest.fixture(autouse=True)
    def ctx(self):
        self.tenant_id = uuid.uuid4()
        self.actor_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    def test_upload_init_calls_generate_upload_url(self):
        mock_storage = MagicMock()
        mock_storage.generate_upload_url.return_value = "https://s3.example.com/presigned-put"

        result = UploadInitService.initialize_upload(
            actor_id=self.actor_id,
            request=UploadInitRequest(
                original_name="invoice.pdf",
                content_type="application/pdf",
                size_bytes=2048,
            ),
            storage=mock_storage,
        )

        mock_storage.generate_upload_url.assert_called_once()
        call_kwargs = mock_storage.generate_upload_url.call_args.kwargs
        assert call_kwargs["object_key"] == result.object_key
        assert call_kwargs["content_type"] == "application/pdf"
        assert call_kwargs["size_bytes"] == 2048
        assert result.upload_url == "https://s3.example.com/presigned-put"

    def test_stub_storage_returns_deterministic_url(self):
        result = UploadInitService.initialize_upload(
            actor_id=self.actor_id,
            request=UploadInitRequest(
                original_name="doc.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                size_bytes=512,
            ),
            storage=StubStorageBackend(),
        )

        assert result.upload_url.startswith("/stub/upload/")
        assert result.object_key in result.upload_url


# ---------------------------------------------------------------------------
# DownloadService — status guards
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDownloadServiceStatusGuard:
    @pytest.fixture(autouse=True)
    def ctx(self):
        self.tenant_id = uuid.uuid4()
        self.actor_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    def test_available_file_returns_download_url(self):
        file_id, object_key = _make_available_file(self.tenant_id, self.actor_id)
        result = DownloadService.get_download_url(
            file_id=file_id,
            storage=StubStorageBackend(),
        )
        assert result.file_id == file_id
        assert result.download_url.startswith("/stub/download/")
        assert object_key in result.download_url
        assert result.expires_in > 0

    def test_available_file_uses_injected_storage(self):
        file_id, _ = _make_available_file(self.tenant_id, self.actor_id)
        mock_storage = MagicMock()
        mock_storage.generate_download_url.return_value = "https://cdn.example.com/signed"

        result = DownloadService.get_download_url(
            file_id=file_id,
            storage=mock_storage,
        )

        mock_storage.generate_download_url.assert_called_once()
        call_kwargs = mock_storage.generate_download_url.call_args.kwargs
        record = FileRepository.get_by_id(file_id)
        assert call_kwargs["object_key"] == record.object_key
        assert result.download_url == "https://cdn.example.com/signed"

    @pytest.mark.parametrize(
        "transition_to",
        [
            "pending",
            "uploaded",
            "scanning",
            "quarantined",
        ],
    )
    def test_non_available_status_raises(self, transition_to):
        from django.core.exceptions import ValidationError

        init = UploadInitService.initialize_upload(
            actor_id=self.actor_id,
            request=UploadInitRequest(
                original_name="f.pdf",
                content_type="application/pdf",
                size_bytes=100,
            ),
        )
        file_id = init.file_id

        if transition_to == "pending":
            pass  # already PENDING_UPLOAD
        elif transition_to == "uploaded":
            FileRepository.mark_uploaded(file_id, checksum_sha256=_DUMMY_SHA)
        elif transition_to == "scanning":
            FileRepository.mark_uploaded(file_id, checksum_sha256=_DUMMY_SHA)
            FileRepository.mark_scanning(file_id)
        elif transition_to == "quarantined":
            FileRepository.mark_uploaded(file_id, checksum_sha256=_DUMMY_SHA)
            FileRepository.mark_scanning(file_id)
            FileRepository.mark_quarantined(file_id, reason="test")

        with pytest.raises(ValidationError, match="недоступен для скачивания"):
            DownloadService.get_download_url(
                file_id=file_id,
                storage=StubStorageBackend(),
            )

    def test_download_custom_expires_in_propagated(self):
        file_id, _ = _make_available_file(self.tenant_id, self.actor_id)
        result = DownloadService.get_download_url(
            file_id=file_id,
            storage=StubStorageBackend(),
            expires_in=7200,
        )
        assert result.expires_in == 7200


# ---------------------------------------------------------------------------
# DownloadService — tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDownloadTenantIsolation:
    def test_tenant_b_cannot_download_tenant_a_file(self):
        from django.core.exceptions import PermissionDenied

        tenant_a = uuid.uuid4()
        actor_a = uuid.uuid4()
        set_tenant_context(tenant_a)
        file_id, _ = _make_available_file(tenant_a, actor_a)

        tenant_b = uuid.uuid4()
        set_tenant_context(tenant_b)

        with pytest.raises(PermissionDenied):
            DownloadService.get_download_url(
                file_id=file_id,
                storage=StubStorageBackend(),
            )

    def test_quarantined_in_tenant_a_not_downloadable_by_tenant_b(self):
        from django.core.exceptions import PermissionDenied, ValidationError

        tenant_a = uuid.uuid4()
        actor_a = uuid.uuid4()
        set_tenant_context(tenant_a)
        init = UploadInitService.initialize_upload(
            actor_id=actor_a,
            request=UploadInitRequest(
                original_name="malware.exe",
                content_type="application/octet-stream",
                size_bytes=512,
            ),
        )
        FileRepository.mark_uploaded(init.file_id, checksum_sha256=_DUMMY_SHA)
        FileRepository.mark_scanning(init.file_id)
        FileRepository.mark_quarantined(init.file_id, reason="virus_detected")

        # Same tenant — business rule (ValidationError)
        with pytest.raises(ValidationError):
            DownloadService.get_download_url(
                file_id=init.file_id,
                storage=StubStorageBackend(),
            )

        # Different tenant — tenant isolation (PermissionDenied)
        set_tenant_context(uuid.uuid4())
        with pytest.raises(PermissionDenied):
            DownloadService.get_download_url(
                file_id=init.file_id,
                storage=StubStorageBackend(),
            )
