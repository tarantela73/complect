"""
Upload lifecycle: PENDING_UPLOAD → UPLOADED via UploadInitService + UploadCompleteService.
Verifies state transitions, Outbox emission, and idempotency guard.
"""
from __future__ import annotations

import hashlib
import uuid

import pytest
from django.core.exceptions import ValidationError

from apps.common.models.outbox import OutboxRecord
from apps.common.tenancy.context import set_tenant_context
from apps.files.models.file_record import FileStatus
from apps.files.repositories.file_repo import FileRepository
from apps.files.schemas.file_dto import UploadCompleteRequest, UploadInitRequest
from apps.files.services.upload_complete_service import UploadCompleteService
from apps.files.services.upload_init_service import UploadInitService

_DUMMY_SHA256 = hashlib.sha256(b"test").hexdigest()


@pytest.mark.django_db(transaction=True)
class TestUploadWorkflow:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.tenant_id = uuid.uuid4()
        self.actor_id = uuid.uuid4()
        self.correlation_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    def _init_request(self, name: str = "договор.pdf", size: int = 1024) -> UploadInitRequest:
        return UploadInitRequest(
            original_name=name,
            content_type="application/pdf",
            size_bytes=size,
        )

    def _complete_request(self, sha: str = _DUMMY_SHA256) -> UploadCompleteRequest:
        return UploadCompleteRequest(checksum_sha256=sha)

    # ------------------------------------------------------------------
    # upload-init
    # ------------------------------------------------------------------

    def test_init_creates_pending_record(self):
        result = UploadInitService.initialize_upload(actor_id=self.actor_id, request=self._init_request())

        record = FileRepository.get_by_id(result.file_id)
        assert record.status == FileStatus.PENDING_UPLOAD
        assert record.tenant_id == self.tenant_id
        assert record.uploaded_by == self.actor_id
        assert record.size_bytes == 1024

    def test_init_object_key_contains_tenant_and_file_id(self):
        result = UploadInitService.initialize_upload(actor_id=self.actor_id, request=self._init_request())

        assert str(self.tenant_id) in result.object_key
        assert str(result.file_id) in result.object_key

    def test_init_sanitises_filename(self):
        result = UploadInitService.initialize_upload(
            actor_id=self.actor_id,
            request=self._init_request(name="../../etc/passwd"),
        )
        assert "passwd" in result.object_key
        assert ".." not in result.object_key

    def test_init_rejects_oversized_file(self):
        with pytest.raises(ValidationError, match="превышает лимит"):
            UploadInitService.initialize_upload(
                actor_id=self.actor_id,
                request=UploadInitRequest(
                    original_name="big.zip",
                    content_type="application/zip",
                    size_bytes=600 * 1024 * 1024,
                ),
            )

    # ------------------------------------------------------------------
    # upload-complete
    # ------------------------------------------------------------------

    def test_complete_transitions_to_uploaded(self):
        init = UploadInitService.initialize_upload(actor_id=self.actor_id, request=self._init_request())

        record = UploadCompleteService.complete_upload(
            file_id=init.file_id,
            actor_id=self.actor_id,
            request=self._complete_request(),
            correlation_id=self.correlation_id,
        )

        assert record.status == FileStatus.UPLOADED
        assert record.checksum_sha256 == _DUMMY_SHA256

    def test_complete_emits_file_uploaded_outbox_event(self):
        init = UploadInitService.initialize_upload(actor_id=self.actor_id, request=self._init_request())

        UploadCompleteService.complete_upload(
            file_id=init.file_id,
            actor_id=self.actor_id,
            request=self._complete_request(),
            correlation_id=self.correlation_id,
        )

        event = OutboxRecord.objects.filter(event_type="files.FileUploaded").first()
        assert event is not None
        assert event.payload_json["file_id"] == str(init.file_id)
        assert event.payload_json["tenant_id"] == str(self.tenant_id)

    def test_complete_idempotency_guard_rejects_second_call(self):
        init = UploadInitService.initialize_upload(actor_id=self.actor_id, request=self._init_request())

        UploadCompleteService.complete_upload(
            file_id=init.file_id,
            actor_id=self.actor_id,
            request=self._complete_request(),
            correlation_id=self.correlation_id,
        )

        with pytest.raises(ValidationError, match="уже обработан"):
            UploadCompleteService.complete_upload(
                file_id=init.file_id,
                actor_id=self.actor_id,
                request=self._complete_request(),
                correlation_id=self.correlation_id,
            )

    def test_complete_rejects_unknown_file_id(self):
        from django.core.exceptions import PermissionDenied

        with pytest.raises(PermissionDenied):
            UploadCompleteService.complete_upload(
                file_id=uuid.uuid4(),
                actor_id=self.actor_id,
                request=self._complete_request(),
            )

    def test_tenant_isolation_between_uploads(self):
        """FileRecord created in tenant_A must not be visible to tenant_B."""
        init = UploadInitService.initialize_upload(actor_id=self.actor_id, request=self._init_request())

        tenant_b = uuid.uuid4()
        set_tenant_context(tenant_b)

        from django.core.exceptions import PermissionDenied

        with pytest.raises(PermissionDenied):
            FileRepository.get_by_id(init.file_id)
