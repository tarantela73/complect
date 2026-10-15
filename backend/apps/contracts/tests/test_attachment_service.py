"""
Tests for ContractAttachment domain:
  - AttachmentService.attach: invariants (AVAILABLE-only, tenant isolation, duplicate)
  - AttachmentService.detach: legal hold guard, not-found
  - get_attachments_list: projection, deleted file skip, download_url logic
  - AttachmentRepository: soft_delete CAS, list_file_ids
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.common.contracts.file_service_protocol import FileMetaDTO
from apps.common.tenancy.context import set_tenant_context
from apps.contracts.models.attachment import ContractAttachment
from apps.contracts.models.contract import Contract, ContractStatus, ResourceSupplyType
from apps.contracts.queries.attachment_queries import get_attachments_list
from apps.contracts.repositories.attachment_repo import AttachmentRepository
from apps.contracts.services.attachment_service import AttachmentService


def _make_contract(tenant_id: uuid.UUID) -> Contract:
    set_tenant_context(tenant_id)
    c = Contract(
        contract_number=f"C-{uuid.uuid4().hex[:6].upper()}",
        title="Тест",
        status=ContractStatus.ACTIVE,
        resource_type=ResourceSupplyType.OTHER,
        total_amount=100_000,
        paid_amount=0,
        tenant_id=tenant_id,
    )
    c.save()
    return c


def _mock_file_service(
    *,
    file_id: uuid.UUID,
    tenant_id: uuid.UUID,
    status: str = "AVAILABLE",
    download_url: str | None = "https://s3.test/file",
) -> MagicMock:
    meta = FileMetaDTO(
        file_id=file_id,
        tenant_id=tenant_id,
        original_name="test.pdf",
        size_bytes=1024,
        status=status,
        download_url=download_url if status == "AVAILABLE" else None,
    )
    svc = MagicMock()
    svc.get_file_meta.return_value = meta
    return svc


def _mock_file_service_none() -> MagicMock:
    svc = MagicMock()
    svc.get_file_meta.return_value = None
    return svc


# ── AttachmentService.attach ──────────────────────────────────────────────────


@pytest.mark.django_db
class TestAttachmentServiceAttach:
    def setup_method(self):
        self.tenant_id = uuid.uuid4()
        self.actor_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)
        self.contract = _make_contract(self.tenant_id)

    def test_attach_available_file_succeeds(self):
        file_id = uuid.uuid4()
        svc = AttachmentService(_mock_file_service(file_id=file_id, tenant_id=self.tenant_id))
        svc.attach(
            contract_id=self.contract.id,
            file_id=file_id,
            actor_id=self.actor_id,
            tenant_id=self.tenant_id,
        )
        assert ContractAttachment.objects.filter(
            contract_id=self.contract.id, file_id=file_id, is_deleted=False
        ).exists()

    def test_attach_non_available_file_raises(self):
        for bad_status in ("PENDING_UPLOAD", "UPLOADED", "SCANNING", "QUARANTINED"):
            file_id = uuid.uuid4()
            svc = AttachmentService(_mock_file_service(file_id=file_id, tenant_id=self.tenant_id, status=bad_status))
            with pytest.raises(ValidationError, match="Прикреплять можно только AVAILABLE файлы"):
                svc.attach(
                    contract_id=self.contract.id,
                    file_id=file_id,
                    actor_id=self.actor_id,
                    tenant_id=self.tenant_id,
                )

    def test_attach_unknown_file_raises_permission_denied(self):
        svc = AttachmentService(_mock_file_service_none())
        with pytest.raises(PermissionDenied, match="FILE_NOT_FOUND"):
            svc.attach(
                contract_id=self.contract.id,
                file_id=uuid.uuid4(),
                actor_id=self.actor_id,
                tenant_id=self.tenant_id,
            )

    def test_attach_same_file_twice_raises(self):
        file_id = uuid.uuid4()
        svc = AttachmentService(_mock_file_service(file_id=file_id, tenant_id=self.tenant_id))
        svc.attach(
            contract_id=self.contract.id,
            file_id=file_id,
            actor_id=self.actor_id,
            tenant_id=self.tenant_id,
        )
        with pytest.raises(ValidationError, match="уже прикреплён"):
            svc.attach(
                contract_id=self.contract.id,
                file_id=file_id,
                actor_id=self.actor_id,
                tenant_id=self.tenant_id,
            )

    def test_attach_to_closed_contract_raises(self):
        self.contract.status = ContractStatus.CLOSED
        self.contract.save()
        file_id = uuid.uuid4()
        svc = AttachmentService(_mock_file_service(file_id=file_id, tenant_id=self.tenant_id))
        with pytest.raises(ValidationError, match="статусе CLOSED"):
            svc.attach(
                contract_id=self.contract.id,
                file_id=file_id,
                actor_id=self.actor_id,
                tenant_id=self.tenant_id,
            )

    def test_attach_to_legal_hold_status_raises(self):
        self.contract.status = ContractStatus.LEGAL_HOLD
        self.contract.save()
        file_id = uuid.uuid4()
        svc = AttachmentService(_mock_file_service(file_id=file_id, tenant_id=self.tenant_id))
        with pytest.raises(ValidationError, match="статусе LEGAL_HOLD"):
            svc.attach(
                contract_id=self.contract.id,
                file_id=file_id,
                actor_id=self.actor_id,
                tenant_id=self.tenant_id,
            )

    def test_attach_to_legal_hold_flag_raises(self):
        self.contract.is_legal_hold = True
        self.contract.save()
        file_id = uuid.uuid4()
        svc = AttachmentService(_mock_file_service(file_id=file_id, tenant_id=self.tenant_id))
        with pytest.raises(ValidationError, match="Прикрепление файла невозможно"):
            svc.attach(
                contract_id=self.contract.id,
                file_id=file_id,
                actor_id=self.actor_id,
                tenant_id=self.tenant_id,
            )

    def test_attach_to_nonexistent_contract_raises(self):
        file_id = uuid.uuid4()
        svc = AttachmentService(_mock_file_service(file_id=file_id, tenant_id=self.tenant_id))
        with pytest.raises(PermissionDenied):
            svc.attach(
                contract_id=uuid.uuid4(),
                file_id=file_id,
                actor_id=self.actor_id,
                tenant_id=self.tenant_id,
            )

    def test_reattach_after_detach_succeeds(self):
        """После soft-detach тот же файл можно прикрепить снова."""
        file_id = uuid.uuid4()
        svc = AttachmentService(_mock_file_service(file_id=file_id, tenant_id=self.tenant_id))
        svc.attach(
            contract_id=self.contract.id,
            file_id=file_id,
            actor_id=self.actor_id,
            tenant_id=self.tenant_id,
        )
        svc.detach(
            contract_id=self.contract.id,
            file_id=file_id,
            actor_id=self.actor_id,
        )
        svc.attach(
            contract_id=self.contract.id,
            file_id=file_id,
            actor_id=self.actor_id,
            tenant_id=self.tenant_id,
        )
        active = ContractAttachment.objects.filter(contract_id=self.contract.id, file_id=file_id, is_deleted=False)
        assert active.count() == 1


# ── AttachmentService.detach ──────────────────────────────────────────────────


@pytest.mark.django_db
class TestAttachmentServiceDetach:
    def setup_method(self):
        self.tenant_id = uuid.uuid4()
        self.actor_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)
        self.contract = _make_contract(self.tenant_id)

    def _attach(self, file_id: uuid.UUID) -> None:
        svc = AttachmentService(_mock_file_service(file_id=file_id, tenant_id=self.tenant_id))
        svc.attach(
            contract_id=self.contract.id,
            file_id=file_id,
            actor_id=self.actor_id,
            tenant_id=self.tenant_id,
        )

    def test_detach_marks_deleted(self):
        file_id = uuid.uuid4()
        self._attach(file_id)
        svc = AttachmentService(MagicMock())
        svc.detach(contract_id=self.contract.id, file_id=file_id, actor_id=self.actor_id)
        att = ContractAttachment.objects.get(contract_id=self.contract.id, file_id=file_id)
        assert att.is_deleted is True

    def test_detach_nonexistent_raises(self):
        svc = AttachmentService(MagicMock())
        with pytest.raises(PermissionDenied, match="ATTACHMENT_NOT_FOUND"):
            svc.detach(
                contract_id=self.contract.id,
                file_id=uuid.uuid4(),
                actor_id=self.actor_id,
            )

    def test_detach_from_closed_contract_raises(self):
        file_id = uuid.uuid4()
        self._attach(file_id)
        self.contract.status = ContractStatus.CLOSED
        self.contract.save()
        svc = AttachmentService(MagicMock())
        with pytest.raises(ValidationError, match="договор закрыт"):
            svc.detach(contract_id=self.contract.id, file_id=file_id, actor_id=self.actor_id)

    def test_detach_from_contract_legal_hold_flag_raises(self):
        file_id = uuid.uuid4()
        self._attach(file_id)
        self.contract.is_legal_hold = True
        self.contract.save()
        svc = AttachmentService(MagicMock())
        with pytest.raises(PermissionDenied, match="CONTRACT_LEGAL_HOLD"):
            svc.detach(contract_id=self.contract.id, file_id=file_id, actor_id=self.actor_id)

    def test_detach_from_legal_hold_status_raises(self):
        file_id = uuid.uuid4()
        self._attach(file_id)
        self.contract.status = ContractStatus.LEGAL_HOLD
        self.contract.save()
        svc = AttachmentService(MagicMock())
        with pytest.raises(PermissionDenied, match="CONTRACT_LEGAL_HOLD"):
            svc.detach(contract_id=self.contract.id, file_id=file_id, actor_id=self.actor_id)

    def test_detach_legal_hold_raises(self):
        file_id = uuid.uuid4()
        self._attach(file_id)
        ContractAttachment.objects.filter(contract_id=self.contract.id, file_id=file_id).update(is_legal_hold=True)
        svc = AttachmentService(MagicMock())
        with pytest.raises(PermissionDenied, match="ATTACHMENT_LEGAL_HOLD"):
            svc.detach(contract_id=self.contract.id, file_id=file_id, actor_id=self.actor_id)


# ── get_attachments_list ──────────────────────────────────────────────────────


@pytest.mark.django_db
class TestGetAttachmentsList:
    def setup_method(self):
        self.tenant_id = uuid.uuid4()
        self.actor_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)
        self.contract = _make_contract(self.tenant_id)

    def test_returns_empty_for_no_attachments(self):
        svc = MagicMock()
        result = get_attachments_list(self.contract.id, self.tenant_id, svc)
        assert result.count == 0
        assert result.results == []

    def test_returns_attachment_with_download_url(self):
        file_id = uuid.uuid4()
        AttachmentRepository.create(
            contract_id=self.contract.id,
            file_id=file_id,
            attached_by=self.actor_id,
        )
        mock_svc = _mock_file_service(
            file_id=file_id,
            tenant_id=self.tenant_id,
            status="AVAILABLE",
            download_url="https://s3.test/file",
        )
        result = get_attachments_list(self.contract.id, self.tenant_id, mock_svc)
        assert result.count == 1
        assert result.results[0].download_url == "https://s3.test/file"
        assert result.results[0].status == "AVAILABLE"

    def test_non_available_file_has_no_download_url(self):
        file_id = uuid.uuid4()
        AttachmentRepository.create(
            contract_id=self.contract.id,
            file_id=file_id,
            attached_by=self.actor_id,
        )
        mock_svc = _mock_file_service(
            file_id=file_id,
            tenant_id=self.tenant_id,
            status="SCANNING",
            download_url=None,
        )
        result = get_attachments_list(self.contract.id, self.tenant_id, mock_svc)
        assert result.results[0].download_url is None

    def test_deleted_file_skipped_in_list(self):
        """Если FileMetaDTO возвращает None (файл удалён), запись пропускается."""
        file_id = uuid.uuid4()
        AttachmentRepository.create(
            contract_id=self.contract.id,
            file_id=file_id,
            attached_by=self.actor_id,
        )
        null_svc = _mock_file_service_none()
        result = get_attachments_list(self.contract.id, self.tenant_id, null_svc)
        assert result.count == 0

    def test_cross_tenant_isolation(self):
        """Вложения другого тенанта не видны."""
        other_tenant = uuid.uuid4()
        file_id = uuid.uuid4()
        # Прикрепляем от имени other_tenant напрямую
        ContractAttachment.objects.create(
            contract=self.contract,
            file_id=file_id,
            attached_by=uuid.uuid4(),
            tenant_id=other_tenant,
        )
        mock_svc = _mock_file_service(file_id=file_id, tenant_id=self.tenant_id)
        result = get_attachments_list(self.contract.id, self.tenant_id, mock_svc)
        assert result.count == 0
