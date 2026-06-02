import uuid

import pytest

from apps.common.tenancy.context import set_tenant_context
from apps.contracts.models.contract import Contract, ContractStatus, ResourceSupplyType
from apps.contracts.repositories.contract_repo import ContractRepository
from apps.finance.models.transaction import Transaction, TransactionStatus, TransactionType
from apps.finance.queries.transaction_queries import get_transactions_page
from apps.finance.services.transaction_posting import TransactionPostingService


def _make_contract(tenant_id: uuid.UUID, total: int = 10_000_00) -> Contract:
    set_tenant_context(tenant_id)
    contract = Contract(
        contract_number=f"C-{uuid.uuid4().hex[:6].upper()}",
        title="Тестовый договор",
        status=ContractStatus.ACTIVE,
        resource_type=ResourceSupplyType.GAS,
        total_amount=total,
        paid_amount=0,
    )
    return ContractRepository.create(contract)


def _post(contract_id: uuid.UUID, amount: int, user_id: uuid.UUID) -> Transaction:
    return TransactionPostingService.post_payment(
        contract_id=contract_id,
        amount=amount,
        posted_by=user_id,
    )


@pytest.mark.django_db(transaction=True)
class TestTransactionQueries:
    @pytest.fixture(autouse=True)
    def setup_context(self):
        self.tenant_id = uuid.uuid4()
        self.other_tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    # --- Tenant isolation ---

    def test_cursor_pagination_isolates_by_tenant(self):
        contract = _make_contract(self.tenant_id)

        # 3 транзакции текущего тенанта
        set_tenant_context(self.tenant_id)
        for amount in [1_00, 2_00, 3_00]:
            _post(contract.id, amount, self.user_id)

        # 2 транзакции чужого тенанта
        other_contract = _make_contract(self.other_tenant_id)
        set_tenant_context(self.other_tenant_id)
        for amount in [9_00, 8_00]:
            _post(other_contract.id, amount, self.user_id)

        set_tenant_context(self.tenant_id)
        result = get_transactions_page(limit=20)

        assert result["count"] == 3
        returned_ids = {r.id for r in result["results"]}
        other_txs = Transaction.objects.filter(tenant_id=self.other_tenant_id)
        for other_tx in other_txs:
            assert other_tx.id not in returned_ids

    # --- Cursor pagination ---

    def test_cursor_pagination_next_cursor_set(self):
        contract = _make_contract(self.tenant_id)
        set_tenant_context(self.tenant_id)
        for amount in [1_00, 2_00, 3_00]:
            _post(contract.id, amount, self.user_id)

        page1 = get_transactions_page(limit=2)

        assert page1["count"] == 2
        assert page1["next_cursor"] is not None

        page2 = get_transactions_page(cursor=uuid.UUID(page1["next_cursor"]), limit=2)

        assert page2["count"] == 1
        assert page2["next_cursor"] is None

    def test_cursor_pagination_no_overlap(self):
        contract = _make_contract(self.tenant_id)
        set_tenant_context(self.tenant_id)
        for amount in [1_00, 2_00, 3_00, 4_00]:
            _post(contract.id, amount, self.user_id)

        page1 = get_transactions_page(limit=2)
        page2 = get_transactions_page(cursor=uuid.UUID(page1["next_cursor"]), limit=2)

        ids_p1 = {r.id for r in page1["results"]}
        ids_p2 = {r.id for r in page2["results"]}
        assert ids_p1.isdisjoint(ids_p2)

    # --- Фильтрация по contract_id ---

    def test_filter_by_contract_id(self):
        c1 = _make_contract(self.tenant_id)
        c2 = _make_contract(self.tenant_id)
        set_tenant_context(self.tenant_id)

        _post(c1.id, 1_00, self.user_id)
        _post(c1.id, 2_00, self.user_id)
        _post(c2.id, 3_00, self.user_id)

        result = get_transactions_page(contract_id=c1.id, limit=20)

        assert result["count"] == 2
        for tx in result["results"]:
            assert tx.contract_id == c1.id

    def test_filter_by_contract_id_returns_empty_for_other_tenant(self):
        other_contract = _make_contract(self.other_tenant_id)
        set_tenant_context(self.other_tenant_id)
        _post(other_contract.id, 5_00, self.user_id)

        set_tenant_context(self.tenant_id)
        result = get_transactions_page(contract_id=other_contract.id, limit=20)

        assert result["count"] == 0

    # --- Фильтрация по статусу ---

    def test_filter_by_status(self):
        contract = _make_contract(self.tenant_id)
        set_tenant_context(self.tenant_id)

        tx = _post(contract.id, 1_00, self.user_id)
        _post(contract.id, 2_00, self.user_id)

        TransactionPostingService.reverse_transaction(tx_id=tx.id, posted_by=self.user_id)

        result = get_transactions_page(status=TransactionStatus.REVERSED, limit=20)
        assert result["count"] == 1
        assert result["results"][0].id == tx.id

    # --- DTO shape ---

    def test_dto_amount_rub_computed(self):
        contract = _make_contract(self.tenant_id)
        set_tenant_context(self.tenant_id)
        _post(contract.id, 1_50_00, self.user_id)

        result = get_transactions_page(limit=1)

        dto = result["results"][0]
        assert dto.amount == 1_50_00
        assert abs(dto.amount_rub - 150.0) < 0.001
