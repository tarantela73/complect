"""
Парсер формата «1C Client Bank Exchange» (КЛ → 1С).

Формат — стандарт ЦБ РФ для экспорта выписок из банк-клиента в 1С.
Файлы кодируются в Windows-1251 или UTF-8.
Структура: заголовок → СекцияРасчСчет/КонецРасчСчет → N × СекцияДокумент/КонецДокумента → КонецФайла.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from apps.payments.parsers.base import ParsedLine, ParsedStatement

_DATE_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")
_ENCODINGS = ("windows-1251", "utf-8", "cp866")

MARKER_START = "1CClientBankExchange"
MARKER_SECTION_ACCOUNT = "СекцияРасчСчет"
MARKER_END_ACCOUNT = "КонецРасчСчет"
MARKER_SECTION_DOC = "СекцияДокумент"
MARKER_END_DOC = "КонецДокумента"
MARKER_EOF = "КонецФайла"


def _decode(raw: str | bytes) -> str:
    if isinstance(raw, str):
        return raw
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("windows-1251", errors="replace")


def _parse_date(value: str) -> date | None:
    m = _DATE_RE.match(value.strip())
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _parse_amount(value: str) -> Decimal | None:
    try:
        normalized = value.strip().replace(" ", "").replace(",", ".")
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _parse_fields(lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in lines:
        if "=" in line:
            key, _, val = line.partition("=")
            key = key.strip()
            if key and key not in result:
                result[key] = val.strip()
    return result


class KlTo1CParser:
    parser_class = "KlTo1CParser"

    def can_parse(self, raw_text: str | bytes) -> bool:
        text = _decode(raw_text)
        first = text.lstrip().split("\n", 1)[0].strip()
        return first == MARKER_START

    def parse(self, raw_text: str | bytes) -> ParsedStatement:
        text = _decode(raw_text)
        if not self.can_parse(text):
            raise ValueError("Файл не является выпиской в формате 1C Client Bank Exchange.")

        lines_raw = text.splitlines()

        # ── Разбиваем на секции ──────────────────────────────────────────────
        header_lines: list[str] = []
        account_lines: list[str] = []
        doc_chunks: list[tuple[str, list[str]]] = []  # (doc_type, lines)

        mode = "header"
        current_doc_type = ""
        current_doc_lines: list[str] = []

        for line in lines_raw:
            stripped = line.strip()
            if stripped.startswith(MARKER_SECTION_DOC + "="):
                current_doc_type = stripped[len(MARKER_SECTION_DOC) + 1 :]
                current_doc_lines = []
                mode = "doc"
            elif stripped == MARKER_END_DOC:
                doc_chunks.append((current_doc_type, current_doc_lines))
                mode = "header"
            elif stripped == MARKER_SECTION_ACCOUNT:
                mode = "account"
            elif stripped == MARKER_END_ACCOUNT:
                mode = "header"
            elif stripped == MARKER_EOF:
                break
            elif mode == "header":
                header_lines.append(line)
            elif mode == "account":
                account_lines.append(line)
            elif mode == "doc":
                current_doc_lines.append(line)

        # ── Заголовок и счёт ────────────────────────────────────────────────
        account_fields = _parse_fields(account_lines)
        account_number = account_fields.get("РасчСчет", "").strip()
        period_from = _parse_date(account_fields.get("ДатаНачала", ""))
        period_to = _parse_date(account_fields.get("ДатаКонца", ""))
        opening_balance = _parse_amount(account_fields.get("НачальныйОстаток", ""))
        closing_balance = _parse_amount(account_fields.get("КонечныйОстаток", ""))

        # ── Документы ───────────────────────────────────────────────────────
        parsed_lines: list[ParsedLine] = []

        for doc_idx, (doc_type, doc_lines) in enumerate(doc_chunks):
            raw_block = "\n".join(doc_lines)
            try:
                pl = self._parse_document(doc_idx, doc_type, doc_lines, account_number)
            except Exception as exc:  # noqa: BLE001
                pl = ParsedLine(
                    line_number=doc_idx,
                    raw_line=raw_block,
                    parse_status="failed",
                    parse_error=str(exc),
                    transaction_date=None,
                    amount=None,
                    direction=None,
                    currency=None,
                    description=None,
                    counterparty_name=None,
                    counterparty_inn=None,
                    counterparty_account=None,
                    reference=None,
                    doc_number=None,
                    doc_type=doc_type or None,
                )
            parsed_lines.append(pl)

        return ParsedStatement(
            account_number=account_number or None,
            bank_name=None,
            currency="RUB",
            period_from=period_from,
            period_to=period_to,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            lines=tuple(parsed_lines),
        )

    def _parse_document(
        self,
        doc_idx: int,
        doc_type: str,
        doc_lines: list[str],
        our_account: str,
    ) -> ParsedLine:
        raw_block = "\n".join(doc_lines)
        fields = _parse_fields(doc_lines)

        # Определяем направление
        date_written = fields.get("ДатаСписано", "").strip()
        date_received = fields.get("ДатаПоступило", "").strip()

        if date_written:
            direction = "debit"
            tx_date = _parse_date(date_written)
        elif date_received:
            direction = "credit"
            tx_date = _parse_date(date_received)
        else:
            # Fallback: используем поле Дата документа
            direction = None
            tx_date = _parse_date(fields.get("Дата", ""))

        amount_str = fields.get("Сумма", "")
        amount = _parse_amount(amount_str)

        if amount is None:
            return ParsedLine(
                line_number=doc_idx,
                raw_line=raw_block,
                parse_status="failed",
                parse_error=f"Не удалось разобрать сумму: '{amount_str}'",
                transaction_date=tx_date,
                amount=None,
                direction=direction,
                currency="RUB",
                description=fields.get("НазначениеПлатежа", "") or None,
                counterparty_name=None,
                counterparty_inn=None,
                counterparty_account=None,
                reference=None,
                doc_number=fields.get("Номер") or None,
                doc_type=doc_type or None,
            )

        # Контрагент: для дебета — получатель, для кредита — плательщик
        if direction == "debit":
            cp_name = fields.get("Получатель1", "") or fields.get("Получатель", "")
            cp_inn = fields.get("ПолучательИНН", "")
            cp_account = fields.get("ПолучательРасчСчет", "") or fields.get("ПолучательСчет", "")
        else:
            cp_name = fields.get("Плательщик1", "") or fields.get("Плательщик", "")
            cp_inn = fields.get("ПлательщикИНН", "")
            cp_account = fields.get("ПлательщикРасчСчет", "") or fields.get("ПлательщикСчет", "")

        # Убираем ИНН из имени если он там есть (формат «ИНН, Имя»)
        cp_name = cp_name.strip()
        if cp_inn and cp_name.startswith(cp_inn):
            cp_name = cp_name[len(cp_inn) :].lstrip(", ").strip()

        # НазначениеПлатежа может быть разбито на НазначениеПлатежа1..6
        description_parts = [fields.get("НазначениеПлатежа", "")]
        for i in range(1, 7):
            part = fields.get(f"НазначениеПлатежа{i}", "")
            if part:
                description_parts.append(part)
        description = " ".join(p for p in description_parts if p).strip()

        return ParsedLine(
            line_number=doc_idx,
            raw_line=raw_block,
            parse_status="parsed",
            parse_error=None,
            transaction_date=tx_date,
            amount=amount,
            direction=direction,
            currency="RUB",
            description=description or None,
            counterparty_name=cp_name or None,
            counterparty_inn=cp_inn.strip() or None,
            counterparty_account=cp_account.strip() or None,
            reference=fields.get("Код", "").strip() or None,
            doc_number=fields.get("Номер", "").strip() or None,
            doc_type=doc_type or None,
        )
