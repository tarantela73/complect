"""Finance-owned persisted prerequisites for manual-match integration tests."""

import uuid

from apps.finance.models.transaction import Transaction, TransactionStatus
from apps.finance.services.transaction_finder import get_transaction_finder


def make_matchable_transaction_id(tenant_id: uuid.UUID) -> uuid.UUID:
    transaction = Transaction.objects.create(
        tenant_id=tenant_id,
        contract_id=uuid.uuid4(),
        amount=150_000,
        status=TransactionStatus.PENDING,
    )
    candidate = get_transaction_finder().find_by_id(tenant_id, transaction.id)
    assert candidate is not None
    assert candidate.transaction_id == transaction.id
    assert candidate.amount_kopecks == 150_000
    return transaction.id
