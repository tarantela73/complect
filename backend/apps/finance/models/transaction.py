
from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class TransactionType(models.TextChoices):
    PAYMENT = "PAYMENT", "Оплата"
    REFUND = "REFUND", "Возврат"
    CORRECTION = "CORRECTION", "Корректировка"


class TransactionStatus(models.TextChoices):
    PENDING = "PENDING", "Ожидает обработки"
    POSTED = "POSTED", "Проведена"
    REVERSED = "REVERSED", "Сторнирована"
    FAILED = "FAILED", "Ошибка"


class Transaction(BaseEnterpriseModel):
    """
    Финансовая транзакция по договору.
    Сумма хранится в копейках (BigIntegerField) для точной арифметики без float.
    Мутации только через SELECT FOR UPDATE — см. TransactionPostingService.
    """

    contract_id = models.UUIDField(db_index=True)

    transaction_type = models.CharField(
        max_length=20,
        choices=TransactionType.choices,
        default=TransactionType.PAYMENT,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=TransactionStatus.choices,
        default=TransactionStatus.PENDING,
        db_index=True,
    )

    # Сумма в копейках (целое, без float-ошибок)
    amount = models.BigIntegerField()

    # Внешний идентификатор платежа (банк, 1С, SWIFT)
    external_ref = models.CharField(max_length=255, blank=True, default="", db_index=True)

    # Дата валютирования (когда деньги фактически прошли по счёту)
    value_date = models.DateField(null=True, blank=True, db_index=True)

    # ИНН контрагента — для reconciliation без JOIN в contracts/contragents
    counterparty_inn = models.CharField(max_length=12, blank=True, default="", db_index=True)

    # Пользователь, инициировавший транзакцию
    posted_by = models.UUIDField(null=True, blank=True)

    class Meta:
        app_label = "finance"
        db_table = "finance_transactions"
        indexes = [
            models.Index(fields=["tenant_id", "contract_id"], name="idx_tx_tenant_contract"),
            models.Index(fields=["tenant_id", "status"], name="idx_tx_tenant_status"),
            models.Index(fields=["tenant_id", "id"], name="idx_tx_tenant_cursor"),
            models.Index(fields=["tenant_id", "counterparty_inn"], name="idx_tx_tenant_inn"),
            models.Index(fields=["tenant_id", "value_date"], name="idx_tx_tenant_vdate"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="tx_amount_positive",
            ),
            # Prevents duplicate postings from event replay: same external_ref within
            # a tenant is always the same payment. Empty string excluded — not all
            # transactions have an external reference.
            models.UniqueConstraint(
                fields=["tenant_id", "external_ref"],
                condition=~models.Q(external_ref=""),
                name="finance_tx_external_ref_uq",
            ),
        ]
