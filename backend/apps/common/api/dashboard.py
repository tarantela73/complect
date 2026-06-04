from django.http import HttpRequest
from ninja import Router, Schema

from apps.common.api.permissions import require_permission
from apps.common.queries.outbox_queries import get_outbox_health
from apps.contracts.queries.contract_queries import get_contract_counts, get_stuck_drafts_count
from apps.contragents.queries.contragent_queries import get_contragent_count
from apps.files.repositories.file_repo import FileRepository

router = Router(tags=["dashboard"])


class ContractCountsSchema(Schema):
    total: int
    draft: int
    active: int
    legal_hold: int
    closed: int


class FileCountsSchema(Schema):
    total: int
    available: int
    scanning: int
    quarantined: int
    pending: int


class OperationalCountsSchema(Schema):
    stuck_drafts: int
    outbox_pending: int
    outbox_failed: int


class DashboardSummarySchema(Schema):
    contracts: ContractCountsSchema
    files: FileCountsSchema
    contragents_total: int
    operational: OperationalCountsSchema


@router.get("/", response={200: DashboardSummarySchema, 403: dict})
@require_permission("read", "dashboard")
def get_dashboard_summary(request: HttpRequest):
    health = get_outbox_health()
    return 200, DashboardSummarySchema(
        contracts=ContractCountsSchema(**get_contract_counts()),
        files=FileCountsSchema(**FileRepository.get_counts()),
        contragents_total=get_contragent_count(),
        operational=OperationalCountsSchema(
            stuck_drafts=get_stuck_drafts_count(),
            outbox_pending=health["outbox_pending"],
            outbox_failed=health["outbox_failed"],
        ),
    )
