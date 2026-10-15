from __future__ import annotations

import uuid
from datetime import datetime, timezone

from django.db import transaction

from apps.common.context.core import get_correlation_id
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.payments.events import ReconciliationStartedEvent
from apps.payments.models import BankStatement, BankStatementLine, BankStatementStatus
from apps.payments.parsers.kl_to_1c import KlTo1CParser

_PARSER = KlTo1CParser()


class BankStatementService:
    @staticmethod
    def upload_and_parse(
        tenant_id: uuid.UUID,
        file_name: str,
        raw_content: bytes,
    ) -> BankStatement:
        """
        Decode raw file bytes, detect format, parse, persist statement + lines atomically.
        Returns the persisted BankStatement (status=parsed or parse_failed).
        """
        raw_text = _decode_file(raw_content)
        file_format = _detect_format(raw_text)

        with transaction.atomic():
            stmt = BankStatement.objects.create(
                tenant_id=tenant_id,
                file_name=file_name,
                file_format=file_format,
                raw_text=raw_text,
                status=BankStatementStatus.PENDING,
            )

            if not _PARSER.can_parse(raw_text):
                stmt.status = BankStatementStatus.PARSE_FAILED
                stmt.parse_error = "Неизвестный формат файла. Поддерживается: 1C Client Bank Exchange (КЛ→1С)."
                BankStatement.objects.filter(id=stmt.id).update(
                    status=stmt.status,
                    parse_error=stmt.parse_error,
                )
                return stmt

            try:
                result = _PARSER.parse(raw_text)
            except Exception as exc:  # noqa: BLE001
                stmt.status = BankStatementStatus.PARSE_FAILED
                stmt.parse_error = str(exc)
                BankStatement.objects.filter(id=stmt.id).update(
                    status=stmt.status,
                    parse_error=stmt.parse_error,
                )
                return stmt

            parsed_count = sum(1 for ln in result.lines if ln.parse_status == "parsed")
            failed_count = sum(1 for ln in result.lines if ln.parse_status == "failed")

            BankStatement.objects.filter(id=stmt.id).update(
                status=BankStatementStatus.PARSED,
                parser_class=_PARSER.parser_class,
                account_number=result.account_number or "",
                currency=result.currency or "",
                period_from=result.period_from,
                period_to=result.period_to,
                opening_balance=result.opening_balance,
                closing_balance=result.closing_balance,
                total_lines=len(result.lines),
                parsed_lines=parsed_count,
                failed_lines=failed_count,
            )

            BankStatementLine.objects.bulk_create(
                [
                    BankStatementLine(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        statement=stmt,
                        line_number=pl.line_number,
                        raw_line=pl.raw_line,
                        parse_status=pl.parse_status,
                        parse_error=pl.parse_error or "",
                        transaction_date=pl.transaction_date,
                        amount=pl.amount,
                        direction=pl.direction or "",
                        currency=pl.currency or "",
                        description=pl.description or "",
                        counterparty_name=pl.counterparty_name or "",
                        counterparty_inn=pl.counterparty_inn or "",
                        counterparty_account=pl.counterparty_account or "",
                        reference=pl.reference or "",
                        doc_number=pl.doc_number or "",
                        doc_type=pl.doc_type or "",
                    )
                    for pl in result.lines
                ]
            )

        stmt.refresh_from_db()
        return stmt

    @staticmethod
    def confirm_import(
        stmt: BankStatement,
        confirmed_by: uuid.UUID,
    ) -> BankStatement:
        if stmt.status != BankStatementStatus.PARSED:
            raise ValueError(f"Импорт возможен только из статуса 'parsed'. Текущий: {stmt.status}.")

        correlation_id = _safe_correlation_id()

        with transaction.atomic():
            BankStatement.objects.filter(id=stmt.id, tenant_id=stmt.tenant_id).update(
                status=BankStatementStatus.IMPORTING,
                imported_by=confirmed_by,
                imported_at=datetime.now(timezone.utc),
            )
            stmt.refresh_from_db()

            TransactionalOutboxService.push_to_outbox(
                ReconciliationStartedEvent.create(
                    correlation_id=correlation_id,
                    tenant_id=stmt.tenant_id,
                    statement_id=stmt.id,
                    confirmed_by=confirmed_by,
                )
            )

            def _dispatch() -> None:
                from apps.payments.tasks import reconcile_statement_task  # noqa: PLC0415

                reconcile_statement_task.apply_async(
                    kwargs={
                        "stmt_id": str(stmt.id),
                        "tenant_id": str(stmt.tenant_id),
                        "confirmed_by": str(confirmed_by),
                        "correlation_id": str(correlation_id),
                    },
                )

            transaction.on_commit(_dispatch)

        return stmt

    @staticmethod
    def cancel(stmt: BankStatement) -> BankStatement:
        if stmt.status not in (BankStatementStatus.PARSED, BankStatementStatus.PARSE_FAILED):
            raise ValueError(f"Отмена невозможна из статуса {stmt.status}.")
        BankStatement.objects.filter(id=stmt.id).update(status=BankStatementStatus.CANCELLED)
        stmt.refresh_from_db()
        return stmt


# ── Helpers ──────────────────────────────────────────────────────────────────

_ENCODINGS = ("windows-1251", "utf-8", "cp866")


def _safe_correlation_id() -> uuid.UUID:
    try:
        return get_correlation_id()
    except Exception:  # noqa: BLE001
        return uuid.uuid4()


def _decode_file(content: bytes) -> str:
    for enc in _ENCODINGS:
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("windows-1251", errors="replace")


def _detect_format(text: str) -> str:
    first_line = text.lstrip().split("\n", 1)[0].strip()
    if first_line == "1CClientBankExchange":
        return "kl_to_1c"
    return "unknown"
