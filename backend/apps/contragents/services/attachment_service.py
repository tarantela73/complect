from __future__ import annotations

import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.common.contracts.file_service_protocol import FileServiceProtocol
from apps.common.events.contragents import ContragentFileAttachedEvent, ContragentFileDetachedEvent
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tenancy.context_core import get_context
from apps.contragents.models.contragent import Contragent
from apps.contragents.repositories.attachment_repo import ContragentAttachmentRepository


class ContragentAttachmentService:
    def __init__(self, file_service: FileServiceProtocol) -> None:
        self._file_service = file_service

    def attach(
        self,
        *,
        contragent_id: uuid.UUID,
        file_id: uuid.UUID,
        actor_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> None:
        meta = self._file_service.get_file_meta(file_id, tenant_id)
        if meta is None:
            raise PermissionDenied("FILE_NOT_FOUND")
        if meta.status != "AVAILABLE":
            raise ValidationError("Файл недоступен: ещё не прошёл проверку или находится на карантине.")

        with transaction.atomic():
            contragent = Contragent.objects.filter(
                id=contragent_id,
                tenant_id=tenant_id,
                is_deleted=False,
            ).first()
            if contragent is None:
                raise PermissionDenied("CONTRAGENT_NOT_FOUND")
            if contragent.is_legal_hold:
                raise PermissionDenied("CONTRAGENT_LEGAL_HOLD")

            existing = ContragentAttachmentRepository.get_active(contragent_id, file_id)
            if existing is not None:
                raise ValidationError("Файл уже прикреплён к данному контрагенту.")

            ContragentAttachmentRepository.create(
                contragent_id=contragent_id,
                file_id=file_id,
                attached_by=actor_id,
            )

            ctx = get_context()
            event = ContragentFileAttachedEvent.create(
                correlation_id=ctx.correlation_id,
                contragent_id=contragent_id,
                user_id=actor_id,
                file_id=file_id,
            )
            TransactionalOutboxService.push_to_outbox(event)

    def detach(
        self,
        *,
        contragent_id: uuid.UUID,
        file_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> None:
        with transaction.atomic():
            attachment = ContragentAttachmentRepository.get_active(contragent_id, file_id)
            if attachment is None:
                raise PermissionDenied("ATTACHMENT_NOT_FOUND")
            if attachment.is_legal_hold:
                raise PermissionDenied("ATTACHMENT_LEGAL_HOLD")

            deleted = ContragentAttachmentRepository.soft_delete(attachment, actor_id)
            if not deleted:
                raise PermissionDenied("ATTACHMENT_NOT_FOUND")

            ctx = get_context()
            event = ContragentFileDetachedEvent.create(
                correlation_id=ctx.correlation_id,
                contragent_id=contragent_id,
                user_id=actor_id,
                file_id=file_id,
            )
            TransactionalOutboxService.push_to_outbox(event)
