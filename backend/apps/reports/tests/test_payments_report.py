"""Tests for payments report query and Excel export endpoint (U5)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from apps.common.tenancy.context import set_tenant_context
from apps.common.tenancy.context_core import RequestContext, set_context
from apps.payments.models.bank_statement import BankStatement, BankStatementStatus
from apps.payments.models.bank_statement_line import BankStatementLine, LineParseStatus, MatchStatus
from apps.reports.queries.payments_report_query import get_payments_report_rows


def _ctx(tenant_id: uuid.UUID) -> None:
    set_context(
        RequestContext(
            tenant_id=tenant_id,
            user_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
        )
    )
    set_tenant_context(tenant_id)


def _make_statement(tenant_id: uuid.UUID) -> BankStatement:
    return BankStatement.objects.create(
        tenant_id=tenant_id,
        file_name="test.txt",
        file_format="kl_to_1c",
        raw_text="",
        status=BankStatementStatus.IMPORTED,
        account_number="40702810000000001234",
    )


def _make_line(
    statement: BankStatement,
    tenant_id: uuid.UUID,
    match_status: str = "UNMATCHED",
    transaction_date: date | None = None,
    parse_status: str = "parsed",
) -> BankStatementLine:
    return BankStatementLine.objects.create(
        statement=statement,
        tenant_id=tenant_id,
        line_number=1,
        parse_status=parse_status,
        transaction_date=transaction_date or date(2026, 1, 15),
        amount=Decimal("1500.00"),
        direction="D",
        counterparty_name="ООО Тест",
        counterparty_inn="7700000001",
        description="Оплата по счёту",
        reference="REF-001",
        match_status=match_status,
        match_confidence=90,
    )


@pytest.mark.django_db
class TestPaymentsReportQuery:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.tenant_id = uuid.uuid4()
        _ctx(self.tenant_id)
        self.statement = _make_statement(self.tenant_id)

    def test_returns_parsed_lines(self):
        _make_line(self.statement, self.tenant_id)
        _make_line(self.statement, self.tenant_id)

        rows = get_payments_report_rows()
        assert len(rows) == 2

    def test_match_status_filter(self):
        _make_line(self.statement, self.tenant_id, match_status="UNMATCHED")
        _make_line(self.statement, self.tenant_id, match_status="MATCHED")

        rows = get_payments_report_rows(match_status="UNMATCHED")
        assert len(rows) == 1
        assert rows[0]["match_status"] == "UNMATCHED"

    def test_statement_id_filter(self):
        other_statement = _make_statement(self.tenant_id)
        _make_line(self.statement, self.tenant_id)
        _make_line(other_statement, self.tenant_id)

        rows = get_payments_report_rows(statement_id=self.statement.id)
        assert len(rows) == 1

    def test_date_from_filter(self):
        _make_line(self.statement, self.tenant_id, transaction_date=date(2026, 1, 1))
        _make_line(self.statement, self.tenant_id, transaction_date=date(2026, 3, 1))

        rows = get_payments_report_rows(date_from=date(2026, 2, 1))
        assert len(rows) == 1

    def test_tenant_isolation(self):
        other_tenant = uuid.uuid4()
        other_stmt = _make_statement(other_tenant)
        _make_line(self.statement, self.tenant_id)
        _make_line(other_stmt, other_tenant)

        rows = get_payments_report_rows()
        assert len(rows) == 1

    def test_non_parsed_lines_excluded(self):
        _make_line(self.statement, self.tenant_id, parse_status="parsed")
        _make_line(self.statement, self.tenant_id, parse_status="failed")

        rows = get_payments_report_rows()
        assert len(rows) == 1

    def test_empty_result(self):
        rows = get_payments_report_rows()
        assert rows == []
