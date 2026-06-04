from django.http import HttpRequest
from ninja import Router, Schema

from apps.common.api.permissions import require_permission
from apps.contracts.queries.contract_queries import get_contract_counts
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


class DashboardSummarySchema(Schema):
    contracts: ContractCountsSchema
    files: FileCountsSchema
    contragents_total: int


@router.get("/", response={200: DashboardSummarySchema, 403: dict})
@require_permission("read", "dashboard")
def get_dashboard_summary(request: HttpRequest):
    return 200, DashboardSummarySchema(
        contracts=ContractCountsSchema(**get_contract_counts()),
        files=FileCountsSchema(**FileRepository.get_counts()),
        contragents_total=get_contragent_count(),
    )
