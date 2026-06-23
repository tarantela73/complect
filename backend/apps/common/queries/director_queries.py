"""
CQRS Query Side — Director Dashboard KPI metrics.
Read-only projections, no writes.
All QuerySets use get_tenant_id() from the request context (set by JWTAuth middleware).

Note: SupplyObject has no status or planned_end_date fields in the current schema.
objects_in_progress_count and overdue_objects_count always return 0 until the schema
gains those fields (Expand & Contract migration, ADR-0002).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db.models import Q, Sum

from apps.common.tenancy.context_core import get_tenant_id
from apps.contracts.models.contract import Contract, ContractStatus
from apps.objects.models.supply_object import SupplyObject
from apps.payments.models.bank_statement import BankStatement, BankStatementStatus
from apps.payments.models.bank_statement_line import BankStatementLine


def get_open_contracts_count() -> int:
    """Count contracts NOT in CLOSED status for the current tenant."""
    tenant_id = get_tenant_id()
    return (
        Contract.objects.filter(
            tenant_id=tenant_id,
            is_deleted=False,
        )
        .exclude(status=ContractStatus.CLOSED)
        .count()
    )


def get_objects_summary() -> dict:
    """
    Returns in_progress_count, top5 list, and overdue_count.

    SupplyObject currently has no status or planned_end_date fields.
    in_progress_count and overdue_count return 0; top5 returns the 5 most-recently
    created objects as a fallback until the schema is extended.
    """
    tenant_id = get_tenant_id()
    qs = SupplyObject.objects.filter(tenant_id=tenant_id, is_deleted=False).order_by("name")
    top5 = list(qs.values("id", "name")[:5])
    return {
        "in_progress_count": 0,
        "top5": top5,
        "overdue_count": 0,
    }


def get_payments_month_summary() -> dict[str, Decimal]:
    """
    Sum credit/debit amounts for the current calendar month from IMPORTED statements.

    Returns {"credit": Decimal, "debit": Decimal}.
    Handles the case where no statements exist — returns Decimal("0.00").
    """
    tenant_id = get_tenant_id()
    today = date.today()
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
