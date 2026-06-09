"""
CQRS Query Side — read-only projections, no business logic, no writes.
Cursor pagination: WHERE id < cursor ORDER BY id DESC (ADR rule 4.11).
"""
import uuid
from datetime import timedelta
from typing import Optional

import structlog
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import Count
from django.utils import timezone

from apps.common.contracts.file_service_protocol import FileServiceProtocol
from apps.common.contracts.finance_contracts import FinanceReaderProtocol
from apps.common.tenancy.context_core import get_context
from apps.contracts.models.contract import Contract, ContractStatus
from apps.contracts.repositories.attachment_repo import AttachmentRepository
from apps.contracts.repositories.contract_repo import ContractRepository
from apps.contracts.schemas.contract_dto import ContractDetailSchema, ContractResponseSchema, LastPaymentSchema

logger = structlog.get_logger(__name__)


def _to_dto(contract: Contract) -> ContractResponseSchema:
    return ContractResponseSchema(
        id=contract.id,
        contract_number=contract.contract_number,
        title=contract.title,
        status=contract.status,
        resource_type=contract.resource_type,
        total_amount=contract.total_amount,
        paid_amount=contract.paid_amount,
        remaining_amount=contract.total_amount - contract.paid_amount,
        created_at=contract.created_at,
        contragent_id=contract.contragent_id,
    )


def get_contracts_page(
    cursor: Optional[uuid.UUID] = None,
    limit: int = 20,
    status: Optional[str] = None,
    resource_type: Optional[str] = None,
    q: Optional[str] = None,
) -> dict:
    """
    Returns a cursor-paginated page of contracts for the current tenant.
    Uses replica routing automatically (read replica DB, see db/router.py).
    """
    qs = ContractRepository.get_queryset()

    if q:
        search_query = SearchQuery(q, config="pg_catalog.russian", search_type="plain")
        qs = qs.filter(search_vector=search_query).order_by(
            SearchRank("search_vector", search_query).desc(), "-id"
        )
    else:
        qs = qs.order_by("-id")

    if cursor:
        qs = qs.filter(id__lt=cursor)

    if status:
        qs = qs.filter(status=status)

    if resource_type:
        qs = qs.filter(resource_type=resource_type)

    # Fetch limit+1 to determine if there are more pages
    rows = list(qs[: limit + 1])
    has_next = len(rows) > limit
    rows = rows[:limit]

    next_cursor = str(rows[-1].id) if has_next and rows else None

    return {
        "results": [_to_dto(c) for c in rows],
        "next_cursor": next_cursor,
        "count": len(rows),
    }


def get_contract_detail(contract_id: uuid.UUID) -> ContractResponseSchema:
    """Raises PermissionDenied if not found or wrong tenant."""
    contract = ContractRepository.get_by_id(contract_id)
    return _to_dto(contract)


def get_contract_detail_360(
    contract_id: uuid.UUID,
    finance_reader: FinanceReaderProtocol,
    file_service: FileServiceProtocol,
) -> ContractDetailSchema:
    """
    360° contract view: base fields + last payments + attachment counts.
    Cross-domain reads go through Protocols (ADR-0005).
    """
    ctx = get_context()
    tenant_id = ctx.tenant_id
    contract = ContractRepository.get_by_id(contract_id)

    last_payments = finance_reader.get_last_posted_payments(tenant_id, contract_id, limit=5)

    # Rule 5.8 — audit log for financial PII read
    logger.info(
        "contract_payments_pii_read",
        why="contract_360_view",
        contract_id=str(contract_id),
        payment_count=len(last_payments),
        tenant_id=str(ctx.tenant_id),
        user_id=str(ctx.user_id),
        request_id=str(ctx.request_id),
        correlation_id=str(ctx.correlation_id),
    )

    file_ids = AttachmentRepository.list_file_ids(contract_id)
    file_metas_by_id = file_service.get_file_metas_batch(file_ids, tenant_id)
    valid_metas = list(file_metas_by_id.values())

    return ContractDetailSchema(
        id=contract.id,
        contract_number=contract.contract_number,
        title=contract.title,
        status=contract.status,
        resource_type=contract.resource_type,
        total_amount=contract.total_amount,
        paid_amount=contract.paid_amount,
        remaining_amount=contract.total_amount - contract.paid_amount,
        created_at=contract.created_at,
        last_payments=[
            LastPaymentSchema(
                transaction_id=p.transaction_id,
                amount=p.amount,
                value_date=p.value_date,
            )
            for p in last_payments
        ],
        attachments_count=len(file_ids),
        files_available=sum(1 for m in valid_metas if m.status == "AVAILABLE"),
        files_quarantined=sum(1 for m in valid_metas if m.status == "QUARANTINED"),
        files_unresolvable=len(file_ids) - len(valid_metas),
    )


def get_contract_counts() -> dict:
    qs = ContractRepository.get_queryset()
    total = qs.count()
    by_status = {row["status"]: row["cnt"] for row in qs.values("status").annotate(cnt=Count("id"))}
    return {
        "total": total,
        "draft": by_status.get("DRAFT", 0),
        "active": by_status.get("ACTIVE", 0),
        "legal_hold": by_status.get("LEGAL_HOLD", 0),
        "closed": by_status.get("CLOSED", 0),
    }


def get_stuck_drafts_count(threshold_days: int = 30) -> int:
    cutoff = timezone.now() - timedelta(days=threshold_days)
    return (
        ContractRepository.get_queryset()
        .filter(status=ContractStatus.DRAFT, created_at__lt=cutoff)
        .count()
    )
