import uuid

import pytest
from django.core.exceptions import ValidationError

from apps.common.models.outbox import OutboxRecord
from apps.common.tenancy.context import set_tenant_context
from apps.contracts.models.attachment import AttachmentType, ContractAttachment
from apps.contracts.models.contract import Contract, ContractStatus, ResourceSupplyType
from apps.contracts.policies.closure_policy import ContractClosurePolicyViolation
from apps.contracts.repositories.contract_repo import ContractRepository
from apps.contracts.services.contract_closure import ContractClosureService


@pytest.mark.django_db(transaction=True)
class TestContractClosureServiceSuite:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.correlation_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    def _create_test_contract(self, status: str, total: int, paid: int, is_legal_hold: bool = False) -> Contract:
        contract = Contract(
            contract_number=f"CONT-{uuid.uuid4().hex[:6].upper()}",
            title="Тестовый договор снабжения электричества",
            status=status,
            resource_type=ResourceSupplyType.ELECTRICITY,
            total_amount=total,
            paid_amount=paid,
            is_legal_hold=is_legal_hold,
        )
        return ContractRepository.create(contract)

    def _attach(self, contract: Contract, attachment_type: str) -> None:
        ContractAttachment.objects.create(
            contract=contract,
            file_id=uuid.uuid4(),
            attached_by=self.user_id,
            tenant_id=self.tenant_id,
            attachment_type=attachment_type,
        )

    def test_cannot_close_contract_with_debt(self):
        """Payment incomplete blocks closure even when all documents present."""
        contract = self._create_test_contract(ContractStatus.ACTIVE, 10000000, 5000000)
        self._attach(contract, AttachmentType.CONTRACT_SIGNED)
        self._attach(contract, AttachmentType.ACT_COMPLETED)
        with pytest.raises(ContractClosurePolicyViolation) as exc_info:
            ContractClosureService.execute_contract_closure(contract.id, self.user_id, self.correlation_id)
        assert exc_info.value.closure_detail["paid"] is False
        assert exc_info.value.closure_detail["has_signed"] is True
        assert exc_info.value.closure_detail["has_act"] is True

    def test_cannot_close_contract_under_legal_hold(self):
        """Legal hold blocks closure regardless of other conditions."""
        contract = self._create_test_contract(ContractStatus.ACTIVE, 100000, 100000, is_legal_hold=True)
        with pytest.raises(ValidationError, match="Юридическое удержание активировано"):
            ContractClosureService.execute_contract_closure(contract.id, self.user_id, self.correlation_id)

    def test_cannot_close_already_closed_contract(self):
        """Already-closed contract raises a distinct error."""
        contract = self._create_test_contract(ContractStatus.CLOSED, 100000, 100000)
        with pytest.raises(ValidationError, match="Договор уже имеет статус 'Закрыт'"):
            ContractClosureService.execute_contract_closure(contract.id, self.user_id, self.correlation_id)

    def test_cannot_close_without_required_documents(self):
        """Missing CONTRACT_SIGNED and ACT_COMPLETED blocks closure."""
        contract = self._create_test_contract(ContractStatus.ACTIVE, 500000, 500000)
        with pytest.raises(ContractClosurePolicyViolation) as exc_info:
            ContractClosureService.execute_contract_closure(contract.id, self.user_id, self.correlation_id)
        detail = exc_info.value.closure_detail
        assert detail["paid"] is True
        assert detail["has_signed"] is False
        assert detail["has_act"] is False

    def test_cannot_close_missing_act(self):
        """CONTRACT_SIGNED present but ACT_COMPLETED missing blocks closure."""
        contract = self._create_test_contract(ContractStatus.ACTIVE, 500000, 500000)
        self._attach(contract, AttachmentType.CONTRACT_SIGNED)
        with pytest.raises(ContractClosurePolicyViolation) as exc_info:
            ContractClosureService.execute_contract_closure(contract.id, self.user_id, self.correlation_id)
        detail = exc_info.value.closure_detail
        assert detail["has_signed"] is True
        assert detail["has_act"] is False

    def test_cannot_close_missing_signed(self):
        """ACT_COMPLETED present but CONTRACT_SIGNED missing blocks closure."""
        contract = self._create_test_contract(ContractStatus.ACTIVE, 500000, 500000)
        self._attach(contract, AttachmentType.ACT_COMPLETED)
        with pytest.raises(ContractClosurePolicyViolation) as exc_info:
            ContractClosureService.execute_contract_closure(contract.id, self.user_id, self.correlation_id)
        detail = exc_info.value.closure_detail
        assert detail["has_signed"] is False
        assert detail["has_act"] is True

    def test_soft_deleted_required_document_does_not_count(self):
        """A soft-deleted CONTRACT_SIGNED does not satisfy the closure gate."""
        contract = self._create_test_contract(ContractStatus.ACTIVE, 500000, 500000)
        attachment = ContractAttachment.objects.create(
            contract=contract,
            file_id=uuid.uuid4(),
            attached_by=self.user_id,
            tenant_id=self.tenant_id,
            attachment_type=AttachmentType.CONTRACT_SIGNED,
            is_deleted=True,
        )
        self._attach(contract, AttachmentType.ACT_COMPLETED)
        with pytest.raises(ContractClosurePolicyViolation) as exc_info:
            ContractClosureService.execute_contract_closure(contract.id, self.user_id, self.correlation_id)
        assert exc_info.value.closure_detail["has_signed"] is False
        assert attachment.is_deleted is True

    def test_successful_closure_mutates_status_and_emits_outbox_event(self):
        """All three conditions met → contract transitions to CLOSED + OutboxRecord emitted."""
        contract = self._create_test_contract(ContractStatus.ACTIVE, 500000, 500000)
        self._attach(contract, AttachmentType.CONTRACT_SIGNED)
        self._attach(contract, AttachmentType.ACT_COMPLETED)

        ContractClosureService.execute_contract_closure(contract.id, self.user_id, self.correlation_id)

        updated_contract = ContractRepository.get_by_id(contract.id)
        assert updated_contract.status == ContractStatus.CLOSED
        assert updated_contract.version == 2

        outbox_event = OutboxRecord.objects.filter(correlation_id=self.correlation_id).first()
        assert outbox_event is not None
        assert outbox_event.event_type == "contracts.ContractClosed"
        assert outbox_event.payload_json["contract_id"] == str(contract.id)
        assert outbox_event.payload_json["remaining_amount"] == 0
