"""
CQRS Query Side — cross-domain read for payments/bank statement reports.
ADR-0005 exception: apps/reports/ is the sanctioned cross-domain read layer.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from django.db import connection

from apps.common.tenancy.context_core import get_tenant_id


def get_payments_report_rows(
    statement_id: Optional[uuid.UUID] = None,
    match_status: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list[dict]:
    """
    Returns bank statement line rows joined with statement account_number.
    ADR-0005 exception: cross-domain JOIN inside apps/reports/ is sanctioned.
    """
    tenant_id = get_tenant_id()

    where_clauses = ["l.tenant_id = %s", "l.is_deleted = false"]
    params: list = [str(tenant_id)]

    if statement_id:
        where_clauses.append("l.statement_id = %s")
        params.append(str(statement_id))
    if match_status:
        where_clauses.append("l.match_status = %s")
        params.append(match_status)
    if date_from:
        where_clauses.append("l.transaction_date >= %s")
        params.append(date_from)
    if date_to:
        where_clauses.append("l.transaction_date <= %s")
        params.append(date_to)

    where_sql = " AND ".join(where_clauses)

    sql = f"""
        SELECT
            l.transaction_date,
            l.amount,
            l.direction,
            l.counterparty_name,
            l.counterparty_inn,
            l.description,
            l.reference,
            l.match_status,
            l.match_confidence,
            l.contract_id,
            s.account_number
        FROM payments_bank_statement_lines l
        JOIN payments_bank_statements s ON s.id = l.statement_id
        WHERE {where_sql}
          AND l.parse_status = 'parsed'
        ORDER BY l.transaction_date DESC NULLS LAST, l.created_at DESC
    """  # noqa: S608 — tenant_id always injected

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
