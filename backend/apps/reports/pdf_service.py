"""
PDF rendering service for reports — contract and contragent print forms.
ADR-0005 exception: cross-domain reads inside apps/reports/ are sanctioned.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from django.core.exceptions import PermissionDenied
from django.db import connection
from django.template.loader import render_to_string

from apps.common.tenancy.context_core import get_tenant_id
from apps.contracts.models.contract import Contract, ResourceSupplyType
from apps.contracts.repositories.contract_repo import ContractRepository


def _fmt_amount(kopecks: int) -> str:
    rub = kopecks / 100
    return f"{rub:,.2f} ₽".replace(",", " ")


STATUS_LABELS = {
    "DRAFT": "Черновик",
    "ACTIVE": "Активен",
    "LEGAL_HOLD": "Юр. удержание",
    "CLOSED": "Закрыт",
}

RESOURCE_LABELS = {
    "ELECTRICITY": "Электроснабжение",
    "GAS": "Газоснабжение",
    "WATER": "Водоснабжение",
    "HEAT": "Теплоснабжение",
    "OTHER": "Прочее",
}

CONTRAGENT_TYPE_LABELS = {
    "LEGAL_ENTITY": "Юридическое лицо",
    "ENTREPRENEUR": "ИП",
    "INDIVIDUAL": "Физическое лицо",
}


def render_contract_pdf(contract_id: uuid.UUID) -> bytes:
    """
    Renders a contract print form to PDF bytes.
    Raises PermissionDenied if contract not found or belongs to another tenant.
    """
    contract: Contract = ContractRepository.get_by_id(contract_id)

    contragent_name: str | None = None
    contragent_inn: str | None = None
    if contract.contragent_id:
        tenant_id = get_tenant_id()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name, inn FROM contragents_contragents WHERE id = %s AND tenant_id = %s AND is_deleted = false",
                [str(contract.contragent_id), str(tenant_id)],
            )
            row = cursor.fetchone()
            if row:
                contragent_name, contragent_inn = row

    context = {
        "contract": contract,
        "status_label": STATUS_LABELS.get(contract.status, contract.status),
        "resource_label": RESOURCE_LABELS.get(contract.resource_type, contract.resource_type),
        "contragent_name": contragent_name,
        "contragent_inn": contragent_inn,
        "total_amount": _fmt_amount(contract.total_amount),
        "paid_amount": _fmt_amount(contract.paid_amount),
        "remaining_amount": _fmt_amount(contract.total_amount - contract.paid_amount),
        "created_at": contract.created_at.strftime("%d.%m.%Y %H:%M") if contract.created_at else "—",
        "generated_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
    }
    html = render_to_string("reports/contract_pdf.html", context)

    from weasyprint import HTML
    return HTML(string=html).write_pdf()


def render_contragent_pdf(contragent_id: uuid.UUID) -> bytes:
    """
    Renders a contragent card to PDF bytes.
    Raises PermissionDenied if contragent not found or belongs to another tenant.
    """
    tenant_id = get_tenant_id()

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, name, inn, contragent_type, created_at FROM contragents_contragents "
            "WHERE id = %s AND tenant_id = %s AND is_deleted = false",
            [str(contragent_id), str(tenant_id)],
        )
        row = cursor.fetchone()

    if not row:
        raise PermissionDenied("Контрагент не найден.")

    ct_id, name, inn, ct_type, ct_created_at = row

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                COUNT(*)                     AS linked_contracts_count,
                COALESCE(SUM(total_amount), 0) AS total_contracts_amount,
                COALESCE(SUM(paid_amount), 0)  AS total_paid
            FROM contracts_contracts
            WHERE contragent_id = %s AND tenant_id = %s AND is_deleted = false
            """,
            [str(contragent_id), str(tenant_id)],
        )
        stats_row = cursor.fetchone()

    linked_contracts_count, total_contracts_amount, total_paid = stats_row or (0, 0, 0)

    context = {
        "contragent": type("Contragent", (), {"id": ct_id, "name": name, "inn": inn})(),
        "type_label": CONTRAGENT_TYPE_LABELS.get(ct_type, ct_type),
        "created_at": ct_created_at.strftime("%d.%m.%Y") if ct_created_at else "—",
        "linked_contracts_count": linked_contracts_count,
        "total_contracts_amount": _fmt_amount(int(total_contracts_amount)),
        "total_paid": _fmt_amount(int(total_paid)),
        "generated_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
    }
    html = render_to_string("reports/contragent_pdf.html", context)

    from weasyprint import HTML
    return HTML(string=html).write_pdf()
