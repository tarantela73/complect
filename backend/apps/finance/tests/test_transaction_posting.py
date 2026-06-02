import uuid

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.common.tenancy.context import set_tenant_context
from apps.contracts.models.contract import Contract, ContractStatus, ResourceSupplyType
from apps.contracts.repositories.contract_repo import ContractRepository
from apps.finance.models.transaction import Transaction, TransactionStatus, TransactionType
from apps.finance.services.transaction_posting import TransactionPostingService


@pytest.mark.django_db(transaction=True)
class TestTransactionPostingService:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    def _make_contract(
        self,
        total: int = 10_000_00,
        paid: int = 0,
        status: str = ContractStatus.ACTIVE,
        is_legal_hold: bool = False,
    ) -> Contract:
        contract = Contract(
            contract_number=f"C-{uuid.uuid4().hex[:6].upper()}",
            title="Тестовый договор",
            status=status,
            resource_type=ResourceSupplyType.GAS,
            total_amount=total,
            paid_amount=paid,
            is_legal_hold=is_legal_hold,
        )
        return ContractRepository.create(contract)

    # --- PAYMENT ---

    def test_post_payment_updates_paid_amount(self):
        contract = self._make_contract(total=10_000_00, paid=0)

        tx = TransactionPostingService.post_payment(
            contract_id=contract.id,
            amount=5_000_00,
            posted_by=self.user_id,
        )

        assert tx.status == TransactionStatus.POSTED
        assert tx.transaction_type == TransactionType.PAYMENT
        assert tx.amount == 5_000_00

        refreshed = Contract.objects.get(id=contract.id)
        assert refreshed.paid_amount == 5_000_00

    def test_post_payment_zero_amount_raises(self):
        contract = self._make_contract()

        with pytest.raises(ValidationError, match="положительной"):
            TransactionPostingService.post_payment(
                contract_id=contract.id,
                amount=0,
                posted_by=self.user_id,
            )

    def test_post_payment_negative_amount_raises(self):
        contract = self._make_contract()

        with pytest.raises(ValidationError, match="положительной"):
            TransactionPostingService.post_payment(
                contract_id=contract.id,
                amount=-100,
                posted_by=self.user_id,
            )

    def test_post_payment_invalid_type_raises(self):
        contract = self._make_contract()

        with pytest.raises(ValidationError, match="Недопустимый тип"):
            TransactionPostingService.post_payment(
                contract_id=contract.id,
                amount=1_000_00,
                posted_by=self.user_id,
                transaction_type="BOGUS",
            )

    def test_post_payment_legal_hold_raises(self):
        contract = self._make_contract(is_legal_hold=True)

        with pytest.raises(PermissionDenied, match="юридическим удержанием"):
            TransactionPostingService.post_payment(
                contract_id=contract.id,
                amount=1_000_00,
                posted_by=self.user_id,
            )

    def test_post_payment_deleted_contract_raises(self):
        contract = self._make_contract()
        Contract.objects.filter(id=contract.id).update(is_deleted=True)

        with pytest.raises(PermissionDenied):
            TransactionPostingService.post_payment(
                contract_id=contract.id,
                amount=1_000_00,
                posted_by=self.user_id,
            )

    def test_post_payment_wrong_tenant_raises(self):
        contract = self._make_contract()
        set_tenant_context(uuid.uuid4())  # чужой тенант

        with pytest.raises(PermissionDenied):
            TransactionPostingService.post_payment(
                contract_id=contract.id,
                amount=1_000_00,
                posted_by=self.user_id,
            )

    # --- REFUND ---

    def test_post_refund_decreases_paid_amount(self):
        contract = self._make_contract(total=10_000_00, paid=8_000_00)

        TransactionPostingService.post_payment(
            contract_id=contract.id,
            amount=3_000_00,
            posted_by=self.user_id,
            transaction_type=TransactionType.REFUND,
        )

        refreshed = Contract.objects.get(id=contract.id)
        assert refreshed.paid_amount == 5_000_00

    def test_post_refund_exceeds_paid_amount_raises(self):
        contract = self._make_contract(total=10_000_00, paid=2_000_00)

        with pytest.raises(ValidationError, match="превышает оплаченную сумму"):
            TransactionPostingService.post_payment(
                contract_id=contract.id,
                amount=5_000_00,
                posted_by=self.user_id,
                transaction_type=TransactionType.REFUND,
            )

    # --- REVERSE ---

    def test_reverse_transaction_reverts_paid_amount(self):
        contract = self._make_contract(total=10_000_00, paid=0)
        tx = TransactionPostingService.post_payment(
            contract_id=contract.id,
            amount=4_000_00,
            posted_by=self.user_id,
        )

        TransactionPostingService.reverse_transaction(tx_id=tx.id, posted_by=self.user_id)

        refreshed_contract = Contract.objects.get(id=contract.id)
        refreshed_tx = Transaction.objects.get(id=tx.id)

        assert refreshed_tx.status == TransactionStatus.REVERSED
        assert refreshed_contract.paid_amount == 0

    def test_reverse_already_reversed_raises(self):
        contract = self._make_contract(total=10_000_00, paid=0)
        tx = TransactionPostingService.post_payment(
            contract_id=contract.id,
            amount=4_000_00,
            posted_by=self.user_id,
        )
        TransactionPostingService.reverse_transaction(tx_id=tx.id, posted_by=self.user_id)

        with pytest.raises(PermissionDenied, match="уже сторнирована"):
            TransactionPostingService.reverse_transaction(tx_id=tx.id, posted_by=self.user_id)

    def test_reverse_wrong_tenant_raises(self):
        contract = self._make_contract()
        tx = TransactionPostingService.post_payment(
            contract_id=contract.id,
            amount=1_000_00,
            posted_by=self.user_id,
        )
        set_tenant_context(uuid.uuid4())

        with pytest.raises(PermissionDenied):
            TransactionPostingService.reverse_transaction(tx_id=tx.id, posted_by=self.user_id)
