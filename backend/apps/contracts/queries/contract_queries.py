"""
CQRS Query Side — read-only projections, no business logic, no writes.
Cursor pagination: WHERE id < cursor ORDER BY id DESC (ADR rule 4.11).
"""
import uuid
from datetime import timedelta
from typing import Optional

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import Count
from django.utils import timezone

from apps.contracts.models.contract import Contract, ContractStatus
from apps.contracts.repositories.contract_repo import ContractRepository
from apps.contracts.schemas.contract_dto import ContractResponseSchema


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
