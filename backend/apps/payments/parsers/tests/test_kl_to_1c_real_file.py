"""
Regression tests for KlTo1CParser against a synthetic 1CClientBankExchange v1.03 fixture.

Fixture mimics a real VTB statement with:
  - Account 40702810601320000266
  - Period 01.06.2026–08.06.2026
  - 4 transactions: 2 debit (ЕНП, зарплата), 2 credit (СОСТРА, ЭТАЛОН)
  - One amount with spaces as thousands separator (edge case)
  - Multi-part НазначениеПлатежа (НазначениеПлатежа + НазначениеПлатежа1)
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.payments.parsers.kl_to_1c import KlTo1CParser

# ---------------------------------------------------------------------------
# Synthetic fixture: realistic 1CClientBankExchange Windows-1251 bytes
# ---------------------------------------------------------------------------
_FIXTURE_TEXT = """\
1CClientBankExchange
ВерсияФормата=1.03
Кодировка=Windows
Отправитель=ВТБ (ПАО)
ДатаНачала=01.06.2026
ДатаКонца=08.06.2026
РасчСчет=40702810601320000266
СекцияРасчСчет
ДатаНачала=01.06.2026
ДатаКонца=08.06.2026
РасчСчет=40702810601320000266
НачальныйОстаток=1143258,28
ВсегоПоступило=66183,00
ВсегоСписано=1180669,09
КонечныйОстаток=28772,19
КонецРасчСчет

СекцияДокумент=Платёжное поручение
Номер=123
Дата=05.06.2026
Сумма=50000,00
ПлательщикСчет=40702810601320000266
ДатаСписано=05.06.2026
Плательщик1=ООО Комплект
ПлательщикИНН=7733888171
Получатель1=ИФНС России №33
ПолучательИНН=7733506810
НазначениеПлатежа=ЕНП единый налоговый платёж за июнь 2026
КонецДокумента

СекцияДокумент=Платёжное поручение
Номер=124
Дата=06.06.2026
Сумма=1 130 669,09
ПлательщикСчет=40702810601320000266
ДатаСписано=06.06.2026
Плательщик1=ООО Комплект
ПлательщикИНН=7733888171
Получатель1=Петров Иван Сергеевич
ПолучательИНН=773300001
НазначениеПлатежа=Заработная плата за май 2026
НазначениеПлатежа1= сотрудникам согласно ведомости №5
КонецДокумента

СекцияДокумент=Платёжное поручение
Номер=47
Дата=02.06.2026
Сумма=33183,00
ПолучательСчет=40702810601320000266
ДатаПоступило=02.06.2026
Плательщик1=ООО СОСТРА
ПлательщикИНН=7720123456
Получатель1=ООО Комплект
ПолучательИНН=7733888171
НазначениеПлатежа=Оплата по договору №12 от 15.05.2026
КонецДокумента

СекцияДокумент=Платёжное поручение
Номер=89
Дата=04.06.2026
Сумма=33000,00
ПолучательСчет=40702810601320000266
ДатаПоступило=04.06.2026
Плательщик1=ООО ЭТАЛОН
ПлательщикИНН=7710654321
Получатель1=ООО Комплект
ПолучательИНН=7733888171
НазначениеПлатежа=Аванс по договору №8 от 01.04.2026
КонецДокумента

КонецФайла
"""

FIXTURE_CONTENT: bytes = _FIXTURE_TEXT.encode("windows-1251")


@pytest.fixture(scope="module")
def parser() -> KlTo1CParser:
    return KlTo1CParser()


@pytest.fixture(scope="module")
def statement(parser: KlTo1CParser):
    return parser.parse(FIXTURE_CONTENT)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestKlTo1CParserRealFile:
    def test_can_parse_recognizes_valid_file(self, parser: KlTo1CParser) -> None:
        assert parser.can_parse(FIXTURE_CONTENT) is True

    def test_can_parse_rejects_garbage(self, parser: KlTo1CParser) -> None:
        assert parser.can_parse(b"Some random text") is False

    def test_parses_account_number(self, statement) -> None:
        assert statement.account_number == "40702810601320000266"

    def test_parses_currency(self, statement) -> None:
        assert statement.currency == "RUB"

    def test_parses_period_from(self, statement) -> None:
        assert statement.period_from == date(2026, 6, 1)

    def test_parses_period_to(self, statement) -> None:
        assert statement.period_to == date(2026, 6, 8)

    def test_parses_opening_balance(self, statement) -> None:
        assert statement.opening_balance == Decimal("1143258.28")

    def test_parses_closing_balance(self, statement) -> None:
        assert statement.closing_balance == Decimal("28772.19")

    def test_parses_all_documents(self, statement) -> None:
        assert len(statement.lines) == 4

    def test_all_documents_parsed_successfully(self, statement) -> None:
        failed = [ln for ln in statement.lines if ln.parse_status != "parsed"]
        assert failed == [], f"Failed lines: {[(ln.line_number, ln.parse_error) for ln in failed]}"

    # ── Direction detection ──────────────────────────────────────────────────

    def test_debit_direction_enp(self, statement) -> None:
        # doc #123 — ЕНП, ДатаСписано set → debit
        line = next(ln for ln in statement.lines if ln.doc_number == "123")
        assert line.direction == "debit"

    def test_debit_direction_salary(self, statement) -> None:
        # doc #124 — зарплата, ДатаСписано set → debit
        line = next(ln for ln in statement.lines if ln.doc_number == "124")
        assert line.direction == "debit"

    def test_credit_direction_sostra(self, statement) -> None:
        # doc #47 — СОСТРА, ДатаПоступило set → credit
        line = next(ln for ln in statement.lines if ln.doc_number == "47")
        assert line.direction == "credit"

    def test_credit_direction_etalon(self, statement) -> None:
        # doc #89 — ЭТАЛОН, ДатаПоступило set → credit
        line = next(ln for ln in statement.lines if ln.doc_number == "89")
        assert line.direction == "credit"

    # ── Amount parsing ───────────────────────────────────────────────────────

    def test_amount_plain_decimal(self, statement) -> None:
        line = next(ln for ln in statement.lines if ln.doc_number == "123")
        assert line.amount == Decimal("50000.00")

    def test_amount_with_spaces_thousands_separator(self, statement) -> None:
        # Сумма=1 130 669,09 — spaces as thousands separator
        line = next(ln for ln in statement.lines if ln.doc_number == "124")
        assert line.amount == Decimal("1130669.09")

    def test_amounts_are_decimal_not_float(self, statement) -> None:
        for ln in statement.lines:
            if ln.amount is not None:
                assert isinstance(ln.amount, Decimal), f"Line {ln.line_number} amount is not Decimal"

    # ── Transaction dates ────────────────────────────────────────────────────

    def test_debit_transaction_date(self, statement) -> None:
        line = next(ln for ln in statement.lines if ln.doc_number == "123")
        assert line.transaction_date == date(2026, 6, 5)

    def test_credit_transaction_date(self, statement) -> None:
        line = next(ln for ln in statement.lines if ln.doc_number == "47")
        assert line.transaction_date == date(2026, 6, 2)

    # ── Counterparty extraction ──────────────────────────────────────────────

    def test_counterparty_extraction_debit(self, statement) -> None:
        # debit → counterparty is the receiver (Получатель1)
        line = next(ln for ln in statement.lines if ln.doc_number == "123")
        assert line.counterparty_name == "ИФНС России №33"
        assert line.counterparty_inn == "7733506810"

    def test_counterparty_extraction_credit(self, statement) -> None:
        # credit → counterparty is the sender (Плательщик1)
        line = next(ln for ln in statement.lines if ln.doc_number == "47")
        assert line.counterparty_name == "ООО СОСТРА"
        assert line.counterparty_inn == "7720123456"

    def test_counterparty_credit_etalon(self, statement) -> None:
        line = next(ln for ln in statement.lines if ln.doc_number == "89")
        assert line.counterparty_name == "ООО ЭТАЛОН"
        assert line.counterparty_inn == "7710654321"

    # ── Multi-part НазначениеПлатежа ─────────────────────────────────────────

    def test_multipart_payment_purpose(self, statement) -> None:
        # doc #124: НазначениеПлатежа + НазначениеПлатежа1 must be concatenated
        line = next(ln for ln in statement.lines if ln.doc_number == "124")
        assert line.description is not None
        assert "Заработная плата за май 2026" in line.description
        assert "сотрудникам согласно ведомости №5" in line.description

    def test_single_payment_purpose(self, statement) -> None:
        line = next(ln for ln in statement.lines if ln.doc_number == "123")
        assert line.description == "ЕНП единый налоговый платёж за июнь 2026"

    # ── Doc type ─────────────────────────────────────────────────────────────

    def test_doc_type(self, statement) -> None:
        for ln in statement.lines:
            assert ln.doc_type == "Платёжное поручение"
