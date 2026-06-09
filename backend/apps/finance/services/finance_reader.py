from __future__ import annotations

import uuid

from apps.common.contracts.finance_contracts import FinanceReaderProtocol, LastPaymentDTO
from apps.finance.models.transaction import Transaction, TransactionStatus


class FinanceReaderService:
    """[ADR-0005] Implements FinanceReaderProtocol — injected into contracts Query Side."""

    def get_last_posted_payments(
        self,
        tenant_id: uuid.UUID,
        contract_id: uuid.UUID,
        limit: int = 5,
    ) -> list[LastPaymentDTO]:
        qs = (
            Transaction.objects.filter(
                tenant_id=tenant_id,
                contract_id=contract_id,
                status=TransactionStatus.POSTED,
                is_deleted=False,
            )
            .order_by("-id")[:limit]
        )
        return [
            LastPaymentDTO(
                transaction_id=tx.id,
                amount=tx.amount,
                value_date=tx.value_date,
            )
            for tx in qs
        ]


# Singleton — injected in contracts/api.py
_: FinanceReaderProtocol = FinanceReaderService()  # type: ignore[assignment]
default_finance_reader = FinanceReaderService()
