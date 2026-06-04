from __future__ import annotations

import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.common.cache.dashboard_cache import invalidate_dashboard
from apps.common.events.files import FileUploadedEvent
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tenancy.context_core import get_tenant_id
from apps.files.models.file_record import FileRecord, FileStatus
from apps.files.repositories.file_repo import FileRepository
from apps.files.schemas.file_dto import UploadCompleteRequest


class UploadCompleteService:
    """
    [COMMAND SIDE] Marks an upload as completed and emits files.FileUploaded.

    Transition: PENDING_UPLOAD → UPLOADED
    The CAS update (status filter) is the optimistic lock:
    only the first caller for a given file_id succeeds.

    On success the FileUploadedEvent enters the Outbox which triggers
    the ClamAV scan consumer (FilesConsumer) in the next iteration.
    """

    @staticmethod
    def complete_upload(
        *,
        file_id: uuid.UUID,
        actor_id: uuid.UUID,
        request: UploadCompleteRequest,
        correlation_id: uuid.UUID | None = None,
    ) -> FileRecord:
        tenant_id = get_tenant_id()
        corr_id = correlation_id or uuid.uuid4()

        # Read the record first to validate tenant scope and current status.
        record = FileRepository.get_by_id(file_id)

        if record.status != FileStatus.PENDING_UPLOAD:
            raise ValidationError(
                f"Файл {file_id} уже обработан (текущий статус: {record.status})."
            )

        with transaction.atomic():
            updated = FileRepository.mark_uploaded(
                file_id=file_id,
                checksum_sha256=request.checksum_sha256,
            )
            if not updated:
                raise PermissionDenied(
                    "Не удалось подтвердить загрузку: файл изменён параллельным запросом."
                )

            TransactionalOutboxService.push_to_outbox(
                FileUploadedEvent.create(
                    correlation_id=corr_id,
                    file_id=file_id,
                    tenant_id=tenant_id,
                    uploaded_by=actor_id,
                    object_key=record.object_key,
                    original_name=record.original_name,
                    content_type=record.content_type,
                    size_bytes=record.size_bytes,
                    checksum_sha256=request.checksum_sha256,
                )
            )
            _tid = tenant_id
            transaction.on_commit(lambda t=_tid: invalidate_dashboard(t))

        # Refresh from DB so the caller gets the updated status.
        return FileRepository.get_by_id(file_id)
