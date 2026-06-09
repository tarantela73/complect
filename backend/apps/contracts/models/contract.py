from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class ContractStatus(models.TextChoices):
    DRAFT = "DRAFT", "Черновик"
    ACTIVE = "ACTIVE", "Активен"
    LEGAL_HOLD = "LEGAL_HOLD", "Юридическое удержание"
    CLOSED = "CLOSED", "Закрыт"


class ResourceSupplyType(models.TextChoices):
    ELECTRICITY = "ELECTRICITY", "Электроснабжение"
    GAS = "GAS", "Газоснабжение"
    WATER = "WATER", "Водоснабжение"
    HEAT = "HEAT", "Теплоснабжение"
    OTHER = "OTHER", "Другие виды снабжения"


class Contract(BaseEnterpriseModel):
    # Номер договора индексируется для быстрого точного поиска (WHERE contract_number = ...)
    contract_number = models.CharField(max_length=100, db_index=True)
    title = models.CharField(max_length=255)

    # Статус — ключевое поле для бизнес-логики и фиксации Error Budget, индекс обязателен
    status = models.CharField(
        max_length=50,
        choices=ContractStatus.choices,
        default=ContractStatus.DRAFT,
        db_index=True,
    )

    resource_type = models.CharField(
        max_length=30,
        choices=ResourceSupplyType.choices,
        default=ResourceSupplyType.OTHER,
        db_index=True,
    )
    total_amount = models.BigIntegerField()
    paid_amount = models.BigIntegerField(default=0)

    # [ADR-0022] UUID reference без ORM FK — cross-domain link к contragents-домену.
    # Nullable: исторические договоры заполняются через Celery-backfill (Release N+1).
    contragent_id = models.UUIDField(null=True, blank=True)

    search_vector = models.GeneratedField(
        expression=(
            SearchVector("contract_number", config="simple", weight="A")
            + SearchVector("title", config="pg_catalog.russian", weight="B")
        ),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        db_table = "contracts_contracts"
        indexes = [
            models.Index(
                fields=["tenant_id", "contragent_id", "status", "is_deleted", "-id"],
                name="idx_contract_tenant_contragent",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "resource_type", "contract_number"],
                name="unique_tenant_resource_contract",
            )
        ]
