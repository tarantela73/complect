"""
Tests for Contract 360° endpoint layer:
  - get_contract_detail_360: happy path, 0 payments, quarantined files, missing files, wrong-tenant isolation
  - ContractDetailSchema: snapshot for 360° fields presence
  - Cross-tenant: PermissionDenied propagates so API layer returns 404 (not 403)
"""
from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import PermissionDenied

from apps.common.contracts.file_service_protocol import FileMetaDTO
from apps.common.contracts.finance_contracts import LastPaymentDTO
from apps.common.tenancy.context import set_tenant_context
from apps.common.tenancy.context_core import RequestContext, set_context
from apps.contracts.models.contract import Contract, ContractStatus, ResourceSupplyType
from apps.contracts.queries.contract_queries import get_contract_detail_360
from apps.contracts.schemas.contract_dto import ContractDetailSchema


def _make_contract(tenant_id: uuid.UUID, total: int = 100_000, paid: int = 0) -> Contract:
    set_tenant_context(tenant_id)
    c = Contract(
        contract_number=f"C-{uuid.uuid4().hex[:6].upper()}",
        title="Тест 360°",
        status=ContractStatus.ACTIVE,
        resource_type=ResourceSupplyType.ELECTRICITY,
        total_amount=total,
        paid_amount=paid,
        tenant_id=tenant_id,
    )
    c.save()
    return c


def _stub_finance_reader(payments: list[LastPaymentDTO]) -> MagicMock:
    reader = MagicMock()
    reader.get_last_posted_payments.return_value = payments
    return reader


def _stub_file_service(metas: dict[uuid.UUID, FileMetaDTO]) -> MagicMock:
    svc = MagicMock()
    svc.get_file_metas_batch.return_value = metas
    return svc


@pytest.mark.django_db
class TestGetContractDetail360:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.tenant_id = uuid.uuid4()
        set_context(RequestContext(
            tenant_id=self.tenant_id,
            user_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
        ))

    def test_happy_path_returns_360_fields(self):
        contract = _make_contract(self.tenant_id, total=500_000, paid=200_000)

        payment = LastPaymentDTO(
            transaction_id=uuid.uuid4(),
            amount=50_000,
            value_date=date(2026, 4, 1),
        )
        file_id = uuid.uuid4()
        meta = FileMetaDTO(
            file_id=file_id,
            tenant_id=self.tenant_id,
            original_name="doc.pdf",
            size_bytes=12345,
            status="AVAILABLE",
            download_url="https://s3.test/doc.pdf",
        )

        patch_target = "apps.contracts.queries.contract_queries.AttachmentRepository.list_file_ids"
        with patch(patch_target, return_value=[file_id]):
            result = get_contract_detail_360(
                contract.id,
                _stub_finance_reader([payment]),
                _stub_file_service({file_id: meta}),
            )

        assert result.id == contract.id
        assert result.total_amount == 500_000
        assert result.paid_amount == 200_000
        assert result.remaining_amount == 300_000
        assert len(result.last_payments) == 1
        assert result.last_payments[0].amount == 50_000
        assert result.last_payments[0].value_date == date(2026, 4, 1)
        assert result.attachments_count == 1
        assert result.files_available == 1
        assert result.files_quarantined == 0
        assert result.files_unresolvable == 0

    def test_zero_payments_returns_empty_list(self):
        contract = _make_contract(self.tenant_id)

        with patch("apps.contracts.queries.contract_queries.AttachmentRepository.list_file_ids", return_value=[]):
            result = get_contract_detail_360(
                contract.id,
                _stub_finance_reader([]),
                _stub_file_service({}),
            )

        assert result.last_payments == []
        assert result.attachments_count == 0
        assert result.files_available == 0
        assert result.files_quarantined == 0
        assert result.files_unresolvable == 0

    def test_quarantined_file_counted_separately(self):
        contract = _make_contract(self.tenant_id)
        fid_available = uuid.uuid4()
        fid_quarantined = uuid.uuid4()

        metas = {
            fid_available: FileMetaDTO(
                file_id=fid_available, tenant_id=self.tenant_id,
                original_name="ok.pdf", size_bytes=100, status="AVAILABLE", download_url="https://s3/ok",
            ),
            fid_quarantined: FileMetaDTO(
                file_id=fid_quarantined, tenant_id=self.tenant_id,
                original_name="bad.exe", size_bytes=200, status="QUARANTINED", download_url=None,
            ),
        }

        with patch("apps.contracts.queries.contract_queries.AttachmentRepository.list_file_ids",
                   return_value=[fid_available, fid_quarantined]):
            result = get_contract_detail_360(
                contract.id,
                _stub_finance_reader([]),
                _stub_file_service(metas),
            )

        assert result.attachments_count == 2
        assert result.files_available == 1
        assert result.files_quarantined == 1
        assert result.files_unresolvable == 0

    def test_missing_file_not_in_batch_result(self):
        contract = _make_contract(self.tenant_id)
        orphan_fid = uuid.uuid4()
        real_fid = uuid.uuid4()

        real_meta = FileMetaDTO(
            file_id=real_fid, tenant_id=self.tenant_id,
            original_name="real.pdf", size_bytes=500, status="AVAILABLE", download_url="https://s3/r",
        )

        with patch("apps.contracts.queries.contract_queries.AttachmentRepository.list_file_ids",
                   return_value=[orphan_fid, real_fid]):
            result = get_contract_detail_360(
                contract.id,
                _stub_finance_reader([]),
                _stub_file_service({real_fid: real_meta}),
            )

        assert result.attachments_count == 2
        assert result.files_available == 1
        assert result.files_quarantined == 0
        assert result.files_unresolvable == 1

    def test_wrong_tenant_raises_permission_denied(self):
        other_tenant = uuid.uuid4()
        set_tenant_context(other_tenant)
        contract = _make_contract(other_tenant)

        set_tenant_context(self.tenant_id)

        with pytest.raises(PermissionDenied):
            get_contract_detail_360(
                contract.id,
                _stub_finance_reader([]),
                _stub_file_service({}),
            )

    def test_nonexistent_contract_raises_permission_denied(self):
        with pytest.raises(PermissionDenied):
            get_contract_detail_360(
                uuid.uuid4(),
                _stub_finance_reader([]),
                _stub_file_service({}),
            )

    def test_remaining_amount_computed_correctly(self):
        contract = _make_contract(self.tenant_id, total=750_000, paid=250_000)

        with patch("apps.contracts.queries.contract_queries.AttachmentRepository.list_file_ids", return_value=[]):
            result = get_contract_detail_360(
                contract.id,
                _stub_finance_reader([]),
                _stub_file_service({}),
            )

        assert result.remaining_amount == 500_000

    def test_batch_file_service_called_once_not_n_plus_1(self):
        contract = _make_contract(self.tenant_id)
        file_ids = [uuid.uuid4() for _ in range(5)]
        svc = _stub_file_service({})

        with patch("apps.contracts.queries.contract_queries.AttachmentRepository.list_file_ids",
                   return_value=file_ids):
            get_contract_detail_360(contract.id, _stub_finance_reader([]), svc)

        svc.get_file_metas_batch.assert_called_once()
        assert svc.get_file_meta.call_count == 0


class TestContractDetailSchemaSnapshot:
    def test_360_fields_present_in_schema(self):
        raw = {
            "id": uuid.uuid4(),
            "contract_number": "N-001",
            "title": "Тест",
            "status": "ACTIVE",
            "resource_type": "GAS",
            "total_amount": 100_000,
            "paid_amount": 40_000,
            "last_payments": [],
            "attachments_count": 3,
            "files_available": 2,
            "files_quarantined": 0,
            "files_unresolvable": 1,
        }
        dto = ContractDetailSchema(**raw)
        dumped = dto.model_dump()

        assert "last_payments" in dumped
        assert "attachments_count" in dumped
        assert "files_available" in dumped
        assert "files_quarantined" in dumped
        assert "files_unresolvable" in dumped
        assert dumped["remaining_amount"] == 60_000
        assert dumped["files_unresolvable"] == 1

    def test_last_payment_schema_fields(self):
        tid = uuid.uuid4()
        raw = {
            "id": uuid.uuid4(),
            "contract_number": "N-002",
            "title": "Платежи",
            "status": "ACTIVE",
            "resource_type": "WATER",
            "total_amount": 200_000,
            "paid_amount": 100_000,
            "last_payments": [
                {"transaction_id": tid, "amount": 50_000, "value_date": "2026-04-01"},
            ],
        }
        dto = ContractDetailSchema(**raw)
        payment = dto.last_payments[0]

        assert payment.transaction_id == tid
        assert payment.amount == 50_000
        assert payment.value_date == date(2026, 4, 1)
