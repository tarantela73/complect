from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.db import models

from apps.common.models.base import BaseEnterpriseModel
from apps.payments.models.bank_statement import BankStatement


class LineParseStatus(models.TextChoices):
    PARSED = "parsed", "Распознана"
    FAILED = "failed", "Ошибка"
    SKIPPED = "skipped", "Пропущена"
    HEADER = "header", "Заголовок"


class MatchStatus(models.TextChoices):
    UNMATCHED = "UNMATCHED", "Не сопоставлена"
    PARTIAL = "PARTIAL", "Частично сопоставлена"
    MATCHED = "MATCHED", "Сопоставлена"
    AMBIGUOUS = "AMBIGUOUS", "Неоднозначное совпадение"


class BankStatementLine(BaseEnterpriseModel):
    """
    Одна строка (транзакция) банковской выписки.
    raw_line хранит исходный текст строки для отладки парсера.
    transaction_id и contract_id — loose UUID-ссылки без FK (Domain Isolation ADR-0005).
    """

    if TYPE_CHECKING:
        statement_id: uuid.UUID

    statement = models.ForeignKey(
        BankStatement,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    line_number = models.IntegerField()
    raw_line = models.TextField(blank=True, default="")

    parse_status = models.CharField(
        max_length=20,
        choices=LineParseStatus.choices,
        default=LineParseStatus.PARSED,
        db_index=True,
    )
    parse_error = models.TextField(blank=True, default="")

    transaction_date = models.DateField(null=True, blank=True)
    amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    direction = models.CharField(max_length=6, blank=True, default="")
    currency = models.CharField(max_length=3, blank=True, default="")
    description = models.TextField(blank=True, default="")
    counterparty_name = models.CharField(max_length=500, blank=True, default="")
    counterparty_inn = models.CharField(max_length=12, blank=True, default="")
    counterparty_account = models.CharField(max_length=50, blank=True, default="")
    reference = models.TextField(blank=True, default="")
    doc_number = models.CharField(max_length=50, blank=True, default="")
    doc_type = models.CharField(max_length=100, blank=True, default="")

    # Cross-domain loose refs (no DB FK — Domain Isolation ADR-0005)
    transaction_id = models.UUIDField(null=True, blank=True, db_index=True)
    contract_id = models.UUIDField(null=True, blank=True, db_index=True)

    # Reconciliation
    match_status = models.CharField(
        max_length=20,
        choices=MatchStatus.choices,
        default=MatchStatus.UNMATCHED,
        db_index=True,
    )
    match_confidence = models.SmallIntegerField(default=0)

    class Meta:
        app_label = "payments"
        db_table = "payments_bank_statement_lines"
        indexes = [
            models.Index(
                fields=["statement_id", "parse_status"],
                name="pay_bsl_status_idx",
            ),
            models.Index(
                fields=["tenant_id", "transaction_date"],
                name="pay_bsl_date_idx",
            ),
            models.Index(
                fields=["tenant_id", "match_status"],
                name="pay_bsl_match_idx",
            ),
        ]
