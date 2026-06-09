
from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class BankStatementStatus(models.TextChoices):
    PENDING = "pending", "Загружен"
    PARSED = "parsed", "Распознан"
    PARSE_FAILED = "parse_failed", "Ошибка распознавания"
    IMPORTING = "importing", "Импортируется"
    IMPORTED = "imported", "Импортирован"
    CANCELLED = "cancelled", "Отменён"


class BankStatement(BaseEnterpriseModel):
    """
    Загруженная банковская выписка.
    raw_text хранит исходный текст файла для повторной обработки при изменении парсера.
    """

    file_name = models.CharField(max_length=255)
    file_format = models.CharField(
        max_length=30,
        default="kl_to_1c",
    )
    raw_text = models.TextField()

    status = models.CharField(
        max_length=30,
        choices=BankStatementStatus.choices,
        default=BankStatementStatus.PENDING,
        db_index=True,
    )
    parser_class = models.CharField(max_length=100, blank=True, default="")
    parse_error = models.TextField(blank=True, default="")

    account_number = models.CharField(max_length=50, blank=True, default="")
    bank_name = models.CharField(max_length=255, blank=True, default="")
    currency = models.CharField(max_length=3, blank=True, default="")
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)
    opening_balance = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    closing_balance = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)

    total_lines = models.IntegerField(null=True, blank=True)
    parsed_lines = models.IntegerField(null=True, blank=True)
    failed_lines = models.IntegerField(null=True, blank=True)
    matched_lines = models.IntegerField(null=True, blank=True)

    imported_by = models.UUIDField(null=True, blank=True)
    imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "payments"
        db_table = "payments_bank_statements"
        indexes = [
            models.Index(
                fields=["tenant_id", "status", "created_at"],
                name="pay_bs_status_idx",
            ),
        ]
