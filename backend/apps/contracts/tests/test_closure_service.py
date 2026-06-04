import uuid

import pytest
from django.core.exceptions import ValidationError

from apps.common.models.outbox import OutboxRecord
from apps.common.tenancy.context import set_tenant_context
from apps.contracts.models.attachment import ContractAttachment
from apps.contracts.models.contract import Contract, ContractStatus, ResourceSupplyType
from apps.contracts.repositories.contract_repo import ContractRepository
from apps.contracts.services.contract_closure import ContractClosureService


@pytest.mark.django_db(transaction=True)
class TestContractClosureServiceSuite:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        """Взводит обязательный tenant-контекст для текущей тестовой сессии [4.12]."""
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

    def test_cannot_close_contract_with_debt(self):
        """[CASE 1: DEBT BLOCKER] Проверяет блокировку закрытия при наличии задолженности."""
        contract = self._create_test_contract(ContractStatus.ACTIVE, 10000000, 5000000)
        ContractAttachment.objects.create(
            contract=contract,
            file_id=uuid.uuid4(),
            attached_by=self.user_id,
            tenant_id=self.tenant_id,
        )
        with pytest.raises(ValidationError, match="обнаружена непогашенная дебиторская задолженность"):
            ContractClosureService.execute_contract_closure(contract.id, self.user_id, self.correlation_id)

    def test_cannot_close_contract_under_legal_hold(self):
        """[CASE 2: LEGAL HOLD BLOCKER] Проверяет блокировку закрытия при включенном удержании юристов."""
        contract = self._create_test_contract(ContractStatus.ACTIVE, 100000, 100000, is_legal_hold=True)

        with pytest.raises(ValidationError, match="Юридическое удержание активировано"):
            ContractClosureService.execute_contract_closure(contract.id, self.user_id, self.correlation_id)

    def test_cannot_close_already_closed_contract(self):
        """[CASE 3: ALREADY CLOSED BLOCKER] Проверяет защиту от повторного закрытия."""
        contract = self._create_test_contract(ContractStatus.CLOSED, 100000, 100000)

        with pytest.raises(ValidationError, match="Договор уже имеет статус 'Закрыт'"):
            ContractClosureService.execute_contract_closure(contract.id, self.user_id, self.correlation_id)

    def test_cannot_close_contract_without_attachments(self):
        """[CASE 5: NO ATTACHMENTS BLOCKER] Договор без вложений нельзя закрыть."""
        contract = self._create_test_contract(ContractStatus.ACTIVE, 500000, 500000)
        with pytest.raises(ValidationError, match="валидные файлы"):
            ContractClosureService.execute_contract_closure(contract.id, self.user_id, self.correlation_id)

    def test_successful_closure_mutates_status_and_emits_outbox_event(self):
        """[CASE 4-5: SUCCESS CONTOUR] Проверяет перевод статуса и генерацию Outbox записи."""
        contract = self._create_test_contract(ContractStatus.ACTIVE, 500000, 500000)
        ContractAttachment.objects.create(
            contract=contract,
            file_id=uuid.uuid4(),
            attached_by=self.user_id,
            tenant_id=self.tenant_id,
        )

        ContractClosureService.execute_contract_closure(contract.id, self.user_id, self.correlation_id)

        updated_contract = ContractRepository.get_by_id(contract.id)
        assert updated_contract.status == ContractStatus.CLOSED
        assert updated_contract.version == 2

        outbox_event = OutboxRecord.objects.filter(correlation_id=self.correlation_id).first()
        assert outbox_event is not None
        assert outbox_event.event_type == "contracts.ContractClosed"
        assert outbox_event.payload_json["contract_id"] == str(contract.id)
        assert outbox_event.payload_json["remaining_amount"] == 0
