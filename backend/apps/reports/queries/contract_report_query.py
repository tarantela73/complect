"""
CQRS Query Side — cross-domain read for contract reports.
ADR-0005 exception: apps/reports/ is the sanctioned cross-domain read layer.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from django.db import connection

from apps.common.tenancy.context_core import get_tenant_id


def get_contracts_report_rows(
    status: Optional[str] = None,
    resource_type: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    contragent_id: Optional[uuid.UUID] = None,
) -> list[dict]:
    """
    Returns contract rows with contragent name/inn via SQL JOIN.
    Cross-domain read is an explicit ADR-0005 exception for apps/reports/.
    Amounts stored as integer kopecks, returned as float roubles.
    """
    tenant_id = get_tenant_id()

    where_clauses = ["c.tenant_id = %s", "c.is_deleted = false"]
    params: list = [str(tenant_id)]

    if status:
        where_clauses.append("c.status = %s")
        params.append(status)
    if resource_type:
        where_clauses.append("c.resource_type = %s")
        params.append(resource_type)
    if date_from:
        where_clauses.append("c.created_at::date >= %s")
        params.append(date_from)
    if date_to:
        where_clauses.append("c.created_at::date <= %s")
        params.append(date_to)
    if contragent_id:
        where_clauses.append("c.contragent_id = %s")
        params.append(str(contragent_id))

    where_sql = " AND ".join(where_clauses)

    sql = f"""
        SELECT
            c.contract_number,
            c.title,
            c.status,
            c.resource_type,
            ct.name   AS contragent_name,
            ct.inn    AS contragent_inn,
            c.total_amount,
            c.paid_amount,
            c.total_amount - c.paid_amount AS remaining_amount,
            c.created_at
        FROM contracts_contracts c
        LEFT JOIN contragents_contragents ct
            ON ct.id = c.contragent_id AND ct.is_deleted = false
        WHERE {where_sql}
        ORDER BY c.created_at DESC
    """  # noqa: S608 — tenant_id is always injected, no user-controlled SQL fragments

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]
