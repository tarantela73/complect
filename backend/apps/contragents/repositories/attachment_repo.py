from __future__ import annotations

import uuid

import django.utils.timezone as tz
from django.db.models import F

from apps.common.tenancy.context_core import get_tenant_id
from apps.contragents.models.attachment import ContragentAttachment


class ContragentAttachmentRepository:
    @staticmethod
    def get_queryset(contragent_id: uuid.UUID):
        tenant_id = get_tenant_id()
        return ContragentAttachment.objects.filter(
            contragent_id=contragent_id,
            tenant_id=tenant_id,
            is_deleted=False,
        ).order_by("-created_at")

    @staticmethod
    def get_active(contragent_id: uuid.UUID, file_id: uuid.UUID) -> ContragentAttachment | None:
        tenant_id = get_tenant_id()
        return ContragentAttachment.objects.filter(
            contragent_id=contragent_id,
            file_id=file_id,
            tenant_id=tenant_id,
            is_deleted=False,
        ).first()

    @staticmethod
    def create(
        *,
        contragent_id: uuid.UUID,
        file_id: uuid.UUID,
        attached_by: uuid.UUID,
    ) -> ContragentAttachment:
        tenant_id = get_tenant_id()
        attachment = ContragentAttachment(
            contragent_id=contragent_id,
            file_id=file_id,
            attached_by=attached_by,
            tenant_id=tenant_id,
        )
        attachment.save()
        return attachment

    @staticmethod
    def soft_delete(attachment: ContragentAttachment, deleted_by: uuid.UUID) -> bool:
        if attachment.is_legal_hold or attachment.is_deleted:
            return False
        updated = ContragentAttachment.objects.filter(
            id=attachment.id,
            version=attachment.version,
            is_deleted=False,
            is_legal_hold=False,
        ).update(
            is_deleted=True,
            deleted_at=tz.now(),
            version=F("version") + 1,
            updated_at=tz.now(),
        )
        return updated == 1

    @staticmethod
    def list_file_ids(contragent_id: uuid.UUID) -> list[uuid.UUID]:
        tenant_id = get_tenant_id()
        return list(
            ContragentAttachment.objects.filter(
                contragent_id=contragent_id,
                tenant_id=tenant_id,
                is_deleted=False,
            )
            .order_by("-created_at")
            .values_list("file_id", flat=True)
        )
