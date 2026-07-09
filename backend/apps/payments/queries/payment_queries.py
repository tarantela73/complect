"""
CQRS Query Side — Payments read-only projections.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from django.db.models import Q, Sum

from apps.payments.models.bank_statement import BankStatement, BankStatementStatus
from apps.payments.models.bank_statement_line import BankStatementLine


def get_payments_month_summary(tenant_id: uuid.UUID) -> dict[str, Decimal]:
    """
    Sum credit/debit amounts for the current calendar month from IMPORTED statements.

    Returns {"credit": Decimal, "debit": Decimal}.
    """
    today = datetime.now(tz=UTC).date()
    month_start = today.replace(day=1)

    imported_statement_ids = BankStatement.objects.filter(
        tenant_id=tenant_id,
        status=BankStatementStatus.IMPORTED,
        is_deleted=False,
    ).values_list("id", flat=True)

    base_qs = BankStatementLine.objects.filter(
        tenant_id=tenant_id,
        statement_id__in=imported_statement_ids,
        transaction_date__gte=month_start,
        transaction_date__lte=today,
        is_deleted=False,
    )

    agg = base_qs.aggregate(
        credit_sum=Sum("amount", filter=Q(direction="credit")),
        debit_sum=Sum("amount", filter=Q(direction="debit")),
    )

    return {
        "credit": agg["credit_sum"] or Decimal("0.00"),
        "debit": agg["debit_sum"] or Decimal("0.00"),
    }
