import uuid
from typing import Optional

from django.http import HttpRequest
from ninja import File, Query, Router, Schema
from ninja.files import UploadedFile

from apps.common.api.errors import make_error_response
from apps.common.api.permissions import require_permission
from apps.payments.models import BankStatement, BankStatementLine
from apps.payments.services.bank_statement_service import BankStatementService

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
