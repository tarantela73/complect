from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass(slots=True, frozen=True)
class ParsedLine:
    line_number: int
    raw_line: str
    parse_status: str  # 'parsed' | 'failed' | 'skipped' | 'header'
    parse_error: str | None
    transaction_date: date | None
    amount: Decimal | None
    direction: str | None  # 'credit' | 'debit'
    currency: str | None
    description: str | None
    counterparty_name: str | None
    counterparty_inn: str | None
    counterparty_account: str | None
    reference: str | None
    doc_number: str | None
    doc_type: str | None


@dataclass(slots=True, frozen=True)
class ParsedStatement:
    account_number: str | None
    bank_name: str | None
    currency: str | None
    period_from: date | None
    period_to: date | None
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    lines: tuple[ParsedLine, ...]


@runtime_checkable
class BankStatementParser(Protocol):
    parser_class: str

    def can_parse(self, raw_text: str) -> bool:
        ...

    def parse(self, raw_text: str) -> ParsedStatement:
        """
        Parse never raises — individual line errors go into ParsedLine(parse_status='failed').
        Only raises ValueError if the file format is fundamentally unrecognisable.
        """
        ...
