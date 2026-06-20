from __future__ import annotations

import uuid

import django.utils.timezone as tz
from django.core.exceptions import PermissionDenied
from django.db.models import Count, F

from apps.common.tenancy.context_core import get_tenant_id
from apps.files.models.file_record import FileRecord, FileStatus


class FileRepository:
    @staticmethod
    def get_queryset():
        tenant_id = get_tenant_id()
        return FileRecord.objects.filter(tenant_id=tenant_id, is_deleted=False)

    @classmethod
    def get_by_id(cls, file_id: uuid.UUID) -> FileRecord:
        # Command-side authoritative read-back (rule 4.12): always read primary so a
        # fetch immediately following a CAS write (mark_*) cannot see stale state from
        # the async replica. Query-side reads use get_queryset()/get_counts() directly.
        record = cls.get_queryset().using("default").filter(id=file_id).first()
        if not record:
            raise PermissionDenied("Файл не найден или недоступен для текущего тенанта.")
        return record

    @staticmethod
    def create_pending(
        *,
        file_id: uuid.UUID,
        object_key: str,
        original_name: str,
        content_type: str,
        size_bytes: int,
        uploaded_by: uuid.UUID,
        storage_provider: str,
    ) -> FileRecord:
        tenant_id = get_tenant_id()
        record = FileRecord(
            id=file_id,
            tenant_id=tenant_id,
            object_key=object_key,
            original_name=original_name,
            content_type=content_type,
            size_bytes=size_bytes,
            checksum_sha256="",
            storage_provider=storage_provider,
            status=FileStatus.PENDING_UPLOAD,
            uploaded_by=uploaded_by,
        )
        record.save()
        return record

    @staticmethod
    def mark_uploaded(file_id: uuid.UUID, checksum_sha256: str) -> bool:
        tenant_id = get_tenant_id()
        updated = FileRecord.objects.filter(
            id=file_id,
            tenant_id=tenant_id,
            status=FileStatus.PENDING_UPLOAD,
            is_deleted=False,
            is_legal_hold=False,
        ).update(
            status=FileStatus.UPLOADED,
            checksum_sha256=checksum_sha256,
            version=F("version") + 1,
            updated_at=tz.now(),
        )
        return updated == 1

    @staticmethod
    def mark_scanning(file_id: uuid.UUID) -> bool:
        tenant_id = get_tenant_id()
        updated = FileRecord.objects.filter(
            id=file_id,
            tenant_id=tenant_id,
            status=FileStatus.UPLOADED,
            is_deleted=False,
        ).update(
            status=FileStatus.SCANNING,
            version=F("version") + 1,
            updated_at=tz.now(),
        )
        return updated == 1

    @staticmethod
    def mark_available(file_id: uuid.UUID, scanned_at=None) -> bool:
        tenant_id = get_tenant_id()
        updated = FileRecord.objects.filter(
            id=file_id,
            tenant_id=tenant_id,
            status=FileStatus.SCANNING,
            is_deleted=False,
        ).update(
            status=FileStatus.AVAILABLE,
            scanned_at=scanned_at or tz.now(),
            version=F("version") + 1,
            updated_at=tz.now(),
        )
        return updated == 1

    @staticmethod
    def mark_rescanning(file_id: uuid.UUID) -> bool:
        """CAS: QUARANTINED[scan_deferred_cb_open] → SCANNING for CB recovery rescan."""
        tenant_id = get_tenant_id()
        updated = FileRecord.objects.filter(
            id=file_id,
            tenant_id=tenant_id,
            status=FileStatus.QUARANTINED,
            quarantine_reason="scan_deferred_cb_open",
            is_deleted=False,
            is_legal_hold=False,
        ).update(
            status=FileStatus.SCANNING,
            quarantine_reason="",
            version=F("version") + 1,
            updated_at=tz.now(),
        )
        return updated == 1

    @staticmethod
    def mark_quarantined(file_id: uuid.UUID, reason: str, scanned_at=None) -> bool:
        tenant_id = get_tenant_id()
        updated = FileRecord.objects.filter(
            id=file_id,
            tenant_id=tenant_id,
            status=FileStatus.SCANNING,
            is_deleted=False,
        ).update(
            status=FileStatus.QUARANTINED,
            quarantine_reason=reason,
            scanned_at=scanned_at or tz.now(),
            version=F("version") + 1,
            updated_at=tz.now(),
        )
        return updated == 1

    @staticmethod
    def get_counts() -> dict:
        qs = FileRepository.get_queryset()
        total = qs.count()
        by_status = {row["status"]: row["cnt"] for row in qs.values("status").annotate(cnt=Count("id"))}
        return {
            "total": total,
            "available": by_status.get(FileStatus.AVAILABLE, 0),
            "scanning": by_status.get(FileStatus.SCANNING, 0),
            "quarantined": by_status.get(FileStatus.QUARANTINED, 0),
            "pending": by_status.get(FileStatus.PENDING_UPLOAD, 0) + by_status.get(FileStatus.UPLOADED, 0),
        }

    @staticmethod
    def soft_delete(file_id: uuid.UUID) -> bool:
        tenant_id = get_tenant_id()
        updated = FileRecord.objects.filter(
            id=file_id,
            tenant_id=tenant_id,
            is_deleted=False,
            is_legal_hold=False,
        ).update(
            is_deleted=True,
            version=F("version") + 1,
            updated_at=tz.now(),
        )
        return updated == 1
