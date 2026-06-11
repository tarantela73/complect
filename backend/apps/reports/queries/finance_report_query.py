"""
CQRS Query Side — cross-domain read for finance reports.
ADR-0005 exception: apps/reports/ is the sanctioned cross-domain read layer.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from django.db import connection

from apps.common.tenancy.context_core import get_tenant_id


def get_finance_report_rows(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    contract_id: Optional[uuid.UUID] = None,
    transaction_type: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    """
    Returns finance transaction rows with contract_number via JOIN.
    ADR-0005 exception: cross-domain JOIN inside apps/reports/ is sanctioned.
    Amounts stored as kopecks, returned as-is for Excel formatting.
    """
    tenant_id = get_tenant_id()

    where_clauses = ["t.tenant_id = %s", "t.is_deleted = false"]
    params: list = [str(tenant_id)]

    if date_from:
        where_clauses.append("t.value_date >= %s")
        params.append(date_from)
    if date_to:
        where_clauses.append("t.value_date <= %s")
        params.append(date_to)
    if contract_id:
        where_clauses.append("t.contract_id = %s")
        params.append(str(contract_id))
    if transaction_type:
        where_clauses.append("t.transaction_type = %s")
        params.append(transaction_type)
    if status:
        where_clauses.append("t.status = %s")
        params.append(status)

    where_sql = " AND ".join(where_clauses)

    sql = f"""
        SELECT
            t.value_date,
            c.contract_number,
            t.transaction_type,
            t.status,
            t.amount,
            t.counterparty_inn,
            t.external_ref
        FROM finance_transactions t
        LEFT JOIN contracts_contracts c
            ON c.id = t.contract_id AND c.is_deleted = false
        WHERE {where_sql}
        ORDER BY t.value_date DESC NULLS LAST, t.created_at DESC
    """  # noqa: S608 — tenant_id always injected, no user-controlled SQL fragments

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
