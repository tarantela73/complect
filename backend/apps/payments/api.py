import uuid
from typing import TYPE_CHECKING, Optional

from django.core.exceptions import PermissionDenied
from django.http import HttpRequest as DjangoHttpRequest
from ninja import File, Query, Router, Schema
from ninja.files import UploadedFile

from apps.common.api.errors import make_error_response

if TYPE_CHECKING:
    from apps.common.api.auth import AuthenticatedHttpRequest as HttpRequest
else:
    HttpRequest = DjangoHttpRequest
from apps.common.api.permissions import require_permission
from apps.common.contracts.finance_contracts import TxCandidateDTO
from apps.common.repository.sre_engine import ConcurrentModificationError
from apps.payments.models import BankStatement, BankStatementLine, BankStatementStatus, MatchStatus
from apps.payments.services.bank_statement_service import BankStatementService
from apps.payments.services.manual_match_service import ManualMatchService

router = Router(tags=["payments"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class BankStatementLineOut(Schema):
    id: uuid.UUID
    line_number: int
    parse_status: str
    parse_error: str
    transaction_date: Optional[str] = None
    amount: Optional[str] = None
    direction: str
    currency: str
    description: str
    counterparty_name: str
    counterparty_inn: str
    counterparty_account: str
    reference: str
    doc_number: str
    doc_type: str
    match_status: str
    match_confidence: int
    transaction_id: Optional[uuid.UUID] = None
    contract_id: Optional[uuid.UUID] = None
    created_at: str

    @staticmethod
    def from_model(obj: BankStatementLine) -> "BankStatementLineOut":
        return BankStatementLineOut(
            id=obj.id,
            line_number=obj.line_number,
            parse_status=obj.parse_status,
            parse_error=obj.parse_error,
            transaction_date=obj.transaction_date.isoformat() if obj.transaction_date else None,
            amount=str(obj.amount) if obj.amount is not None else None,
            direction=obj.direction,
            currency=obj.currency,
            description=obj.description,
            counterparty_name=obj.counterparty_name,
            counterparty_inn=obj.counterparty_inn,
            counterparty_account=obj.counterparty_account,
            reference=obj.reference,
            doc_number=obj.doc_number,
            doc_type=obj.doc_type,
            match_status=obj.match_status,
            match_confidence=obj.match_confidence,
            transaction_id=obj.transaction_id,
            contract_id=obj.contract_id,
            created_at=obj.created_at.isoformat(),
        )


class BankStatementOut(Schema):
    id: uuid.UUID
    file_name: str
    file_format: str
    status: str
    parse_error: str
    account_number: str
    currency: str
    period_from: Optional[str] = None
    period_to: Optional[str] = None
    opening_balance: Optional[str] = None
    closing_balance: Optional[str] = None
    total_lines: Optional[int] = None
    parsed_lines: Optional[int] = None
    failed_lines: Optional[int] = None
    matched_lines: Optional[int] = None
    imported_at: Optional[str] = None
    created_at: str

    @staticmethod
    def from_model(obj: BankStatement) -> "BankStatementOut":
        return BankStatementOut(
            id=obj.id,
            file_name=obj.file_name,
            file_format=obj.file_format,
            status=obj.status,
            parse_error=obj.parse_error,
            account_number=obj.account_number,
            currency=obj.currency,
            period_from=obj.period_from.isoformat() if obj.period_from else None,
            period_to=obj.period_to.isoformat() if obj.period_to else None,
            opening_balance=str(obj.opening_balance) if obj.opening_balance is not None else None,
            closing_balance=str(obj.closing_balance) if obj.closing_balance is not None else None,
            total_lines=obj.total_lines,
            parsed_lines=obj.parsed_lines,
            failed_lines=obj.failed_lines,
            matched_lines=obj.matched_lines,
            imported_at=obj.imported_at.isoformat() if obj.imported_at else None,
            created_at=obj.created_at.isoformat(),
        )


class BankStatementDetailOut(BankStatementOut):
    lines: list[BankStatementLineOut]

    @staticmethod
    def from_model_with_lines(obj: BankStatement) -> "BankStatementDetailOut":  # type: ignore[override]
        base = BankStatementOut.from_model(obj)
        lines = [BankStatementLineOut.from_model(ln) for ln in obj.lines.all().order_by("line_number")]
        return BankStatementDetailOut(**base.model_dump(), lines=lines)


class BankStatementListOut(Schema):
    items: list[BankStatementOut]
    next_cursor: Optional[uuid.UUID] = None


class ReviewLineOut(Schema):
    id: uuid.UUID
    statement_id: uuid.UUID
    statement_file_name: str
    statement_period_from: Optional[str] = None
    statement_period_to: Optional[str] = None
    statement_account_number: str
    line_number: int
    transaction_date: Optional[str] = None
    amount: Optional[str] = None
    direction: str
    currency: str
    description: str
    counterparty_name: str
    counterparty_inn: str
    match_status: str
    match_confidence: int
    transaction_id: Optional[uuid.UUID] = None
    created_at: str

    @staticmethod
    def from_model(obj: BankStatementLine) -> "ReviewLineOut":
        stmt = obj.statement
        return ReviewLineOut(
            id=obj.id,
            statement_id=stmt.id,
            statement_file_name=stmt.file_name,
            statement_period_from=stmt.period_from.isoformat() if stmt.period_from else None,
            statement_period_to=stmt.period_to.isoformat() if stmt.period_to else None,
            statement_account_number=stmt.account_number,
            line_number=obj.line_number,
            transaction_date=obj.transaction_date.isoformat() if obj.transaction_date else None,
            amount=str(obj.amount) if obj.amount is not None else None,
            direction=obj.direction,
            currency=obj.currency,
            description=obj.description,
            counterparty_name=obj.counterparty_name,
            counterparty_inn=obj.counterparty_inn,
            match_status=obj.match_status,
            match_confidence=obj.match_confidence,
            transaction_id=obj.transaction_id,
            created_at=obj.created_at.isoformat(),
        )


class ReviewQueueOut(Schema):
    items: list[ReviewLineOut]
    total_count: int
    unmatched_count: int
    ambiguous_count: int
    partial_count: int


class ManualMatchIn(Schema):
    transaction_id: uuid.UUID


class TxCandidateOut(Schema):
    transaction_id: uuid.UUID
    contract_id: uuid.UUID
    amount_kopecks: int
    external_ref: str
    counterparty_inn: str
    value_date: Optional[str] = None

    @staticmethod
    def from_dto(dto: TxCandidateDTO) -> "TxCandidateOut":
        return TxCandidateOut(
            transaction_id=dto.transaction_id,
            contract_id=dto.contract_id,
            amount_kopecks=dto.amount_kopecks,
            external_ref=dto.external_ref,
            counterparty_inn=dto.counterparty_inn,
            value_date=dto.value_date.isoformat() if dto.value_date else None,
        )


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_tenant_id(request: HttpRequest) -> uuid.UUID:
    return uuid.UUID(str(request.jwt_tenant_id))


def _get_user_id(request: HttpRequest) -> uuid.UUID:
    return uuid.UUID(str(request.jwt_user_id))


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "/statements/",
    response={201: BankStatementOut, 400: dict, 403: dict},
    summary="Загрузить банковскую выписку",
)
@require_permission("write", "payments")
def upload_statement(request: HttpRequest, file: UploadedFile = File(...)):  # noqa: B008
    tenant_id = _get_tenant_id(request)
    content = file.read()
    if not content:
        return 400, make_error_response("EMPTY_FILE", "Файл пустой.")

    stmt = BankStatementService.upload_and_parse(
        tenant_id=tenant_id,
        file_name=file.name or "statement.txt",
        raw_content=content,
    )
    return 201, BankStatementOut.from_model(stmt)


@router.get(
    "/statements/",
    response={200: BankStatementListOut, 403: dict},
    summary="Список выписок",
)
@require_permission("read", "payments")
def list_statements(
    request: HttpRequest,
    cursor: Optional[uuid.UUID] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
):
    tenant_id = _get_tenant_id(request)
    qs = BankStatement.objects.filter(tenant_id=tenant_id, is_deleted=False).order_by("-created_at", "-id")

    if status:
        qs = qs.filter(status=status)

    if cursor:
        anchor = BankStatement.objects.filter(id=cursor, tenant_id=tenant_id).values("created_at").first()
        if anchor:
            qs = qs.filter(created_at__lt=anchor["created_at"])

    items = list(qs[: limit + 1])
    next_cursor = items[limit].id if len(items) > limit else None
    return 200, BankStatementListOut(
        items=[BankStatementOut.from_model(s) for s in items[:limit]],
        next_cursor=next_cursor,
    )


@router.get(
    "/statements/{stmt_id}/",
    response={200: BankStatementDetailOut, 403: dict, 404: dict},
    summary="Детали выписки с транзакциями",
)
@require_permission("read", "payments")
def get_statement(request: HttpRequest, stmt_id: uuid.UUID):
    tenant_id = _get_tenant_id(request)
    try:
        stmt = BankStatement.objects.get(id=stmt_id, tenant_id=tenant_id, is_deleted=False)
    except BankStatement.DoesNotExist:
        return 404, make_error_response("STATEMENT_NOT_FOUND", "Выписка не найдена.")
    return 200, BankStatementDetailOut.from_model_with_lines(stmt)


@router.post(
    "/statements/{stmt_id}/confirm/",
    response={200: BankStatementOut, 403: dict, 404: dict, 422: dict},
    summary="Подтвердить импорт выписки",
)
@require_permission("write", "payments")
def confirm_statement(request: HttpRequest, stmt_id: uuid.UUID):
    tenant_id = _get_tenant_id(request)
    try:
        stmt = BankStatement.objects.get(id=stmt_id, tenant_id=tenant_id, is_deleted=False)
    except BankStatement.DoesNotExist:
        return 404, make_error_response("STATEMENT_NOT_FOUND", "Выписка не найдена.")

    try:
        stmt = BankStatementService.confirm_import(stmt, confirmed_by=_get_user_id(request))
    except ValueError as exc:
        return 422, make_error_response("INVALID_STATE", str(exc))

    return 200, BankStatementOut.from_model(stmt)


@router.post(
    "/statements/{stmt_id}/cancel/",
    response={200: BankStatementOut, 403: dict, 404: dict, 422: dict},
    summary="Отменить импорт выписки",
)
@require_permission("write", "payments")
def cancel_statement(request: HttpRequest, stmt_id: uuid.UUID):
    tenant_id = _get_tenant_id(request)
    try:
        stmt = BankStatement.objects.get(id=stmt_id, tenant_id=tenant_id, is_deleted=False)
    except BankStatement.DoesNotExist:
        return 404, make_error_response("STATEMENT_NOT_FOUND", "Выписка не найдена.")

    try:
        stmt = BankStatementService.cancel(stmt)
    except ValueError as exc:
        return 422, make_error_response("INVALID_STATE", str(exc))

    return 200, BankStatementOut.from_model(stmt)


_REVIEW_STATUSES = [MatchStatus.UNMATCHED, MatchStatus.AMBIGUOUS, MatchStatus.PARTIAL]


@router.get(
    "/review/",
    response={200: ReviewQueueOut, 403: dict},
    summary="Очередь сверки — строки требующие ручной обработки",
)
@require_permission("read", "payments")
def get_review_queue(
    request: HttpRequest,
    match_status: Optional[str] = Query(None),
):
    tenant_id = _get_tenant_id(request)

    base_qs = BankStatementLine.objects.filter(
        tenant_id=tenant_id,
        is_deleted=False,
        statement__status=BankStatementStatus.IMPORTED,
        statement__is_deleted=False,
        match_status__in=_REVIEW_STATUSES,
    )

    unmatched_count = base_qs.filter(match_status=MatchStatus.UNMATCHED).count()
    ambiguous_count = base_qs.filter(match_status=MatchStatus.AMBIGUOUS).count()
    partial_count = base_qs.filter(match_status=MatchStatus.PARTIAL).count()
    total_count = unmatched_count + ambiguous_count + partial_count

    qs = base_qs.select_related("statement")
    if match_status and match_status in [s.value for s in _REVIEW_STATUSES]:
        qs = qs.filter(match_status=match_status)

    qs = qs.order_by("-transaction_date", "line_number")

    return 200, ReviewQueueOut(
        items=[ReviewLineOut.from_model(ln) for ln in qs[:500]],
        total_count=total_count,
        unmatched_count=unmatched_count,
        ambiguous_count=ambiguous_count,
        partial_count=partial_count,
    )


def _get_line(stmt_id: uuid.UUID, line_id: uuid.UUID, tenant_id: uuid.UUID) -> BankStatementLine | None:
    try:
        return BankStatementLine.objects.using("default").get(
            id=line_id,
            statement_id=stmt_id,
            tenant_id=tenant_id,
            is_deleted=False,
        )
    except BankStatementLine.DoesNotExist:
        return None


@router.post(
    "/statements/{stmt_id}/lines/{line_id}/match/",
    response={200: BankStatementLineOut, 403: dict, 404: dict, 409: dict},
    summary="Ручная привязка строки к транзакции",
)
@require_permission("write", "payments")
def attach_match(request: HttpRequest, stmt_id: uuid.UUID, line_id: uuid.UUID, data: ManualMatchIn):
    tenant_id = _get_tenant_id(request)
    line = _get_line(stmt_id, line_id, tenant_id)
    if line is None:
        return 404, make_error_response("LINE_NOT_FOUND", "Строка выписки не найдена.")
    try:
        line = ManualMatchService.attach(
            tenant_id=tenant_id,
            line=line,
            transaction_id=data.transaction_id,
            confirmed_by=_get_user_id(request),
        )
    except ConcurrentModificationError:
        return 409, make_error_response(
            "CONCURRENT_MODIFICATION", "Строка выписки была изменена параллельным запросом."
        )
    except PermissionDenied:
        return 403, make_error_response("TRANSACTION_NOT_FOUND", "Транзакция не найдена в рамках тенанта.")
    return 200, BankStatementLineOut.from_model(line)


@router.delete(
    "/statements/{stmt_id}/lines/{line_id}/match/",
    response={200: BankStatementLineOut, 403: dict, 404: dict, 409: dict},
    summary="Снять привязку строки с транзакции",
)
@require_permission("write", "payments")
def detach_match(request: HttpRequest, stmt_id: uuid.UUID, line_id: uuid.UUID):
    tenant_id = _get_tenant_id(request)
    line = _get_line(stmt_id, line_id, tenant_id)
    if line is None:
        return 404, make_error_response("LINE_NOT_FOUND", "Строка выписки не найдена.")
    try:
        line = ManualMatchService.detach(
            tenant_id=tenant_id,
            line=line,
            confirmed_by=_get_user_id(request),
        )
    except ConcurrentModificationError:
        return 409, make_error_response(
            "CONCURRENT_MODIFICATION", "Строка выписки была изменена параллельным запросом."
        )
    return 200, BankStatementLineOut.from_model(line)


@router.get(
    "/statements/{stmt_id}/lines/{line_id}/candidates/",
    response={200: list[TxCandidateOut], 403: dict, 404: dict},
    summary="Кандидаты для ручного сопоставления строки",
)
@require_permission("read", "payments")
def get_line_candidates(request: HttpRequest, stmt_id: uuid.UUID, line_id: uuid.UUID):
    tenant_id = _get_tenant_id(request)
    line = _get_line(stmt_id, line_id, tenant_id)
    if line is None:
        return 404, make_error_response("LINE_NOT_FOUND", "Строка выписки не найдена.")
    candidates = ManualMatchService.get_candidates(tenant_id=tenant_id, line=line)
    return 200, [TxCandidateOut.from_dto(c) for c in candidates]
