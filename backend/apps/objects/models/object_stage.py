import uuid

from django.db import models
from django.utils import timezone

from apps.common.models.base import BaseEnterpriseModel
from apps.objects.models.supply_object import SupplyObject


class ObjectStageKey(models.TextChoices):
    REQUEST_RECEIVED = "REQUEST_RECEIVED", "Получена заявка"
    SITE_VISIT = "SITE_VISIT", "Выезд на объект"
    COST_ESTIMATION = "COST_ESTIMATION", "Расчёт стоимости"
    CONTRACT_SIGNING = "CONTRACT_SIGNING", "Подписание договора"
    INITIAL_PAYMENT = "INITIAL_PAYMENT", "Первоначальная оплата"
    INSTALLATION = "INSTALLATION", "Монтаж"
    TESTING = "TESTING", "Испытания"
    FINAL_PAYMENT = "FINAL_PAYMENT", "Финальная оплата"
    ACT_SIGNING = "ACT_SIGNING", "Подписание акта"
    CLOSURE = "CLOSURE", "Закрытие"


class ObjectStageStatus(models.TextChoices):
    PENDING = "PENDING", "Ожидает"
    IN_PROGRESS = "IN_PROGRESS", "В работе"
    COMPLETED = "COMPLETED", "Завершён"
    SKIPPED = "SKIPPED", "Пропущен"


STAGE_ORDER = {
    ObjectStageKey.REQUEST_RECEIVED: 10,
    ObjectStageKey.SITE_VISIT: 20,
    ObjectStageKey.COST_ESTIMATION: 30,
    ObjectStageKey.CONTRACT_SIGNING: 40,
    ObjectStageKey.INITIAL_PAYMENT: 50,
    ObjectStageKey.INSTALLATION: 60,
    ObjectStageKey.TESTING: 70,
    ObjectStageKey.FINAL_PAYMENT: 80,
    ObjectStageKey.ACT_SIGNING: 90,
    ObjectStageKey.CLOSURE: 100,
}

OPTIONAL_STAGES = {
    ObjectStageKey.SITE_VISIT,
    ObjectStageKey.INSTALLATION,
    ObjectStageKey.TESTING,
}


class ObjectStage(BaseEnterpriseModel):
    object = models.ForeignKey(
        SupplyObject,
        on_delete=models.CASCADE,
        related_name="stages",
        db_index=True,
    )
    stage_key = models.CharField(max_length=30, choices=ObjectStageKey.choices)
    status = models.CharField(
        max_length=20,
        choices=ObjectStageStatus.choices,
        default=ObjectStageStatus.PENDING,
        db_index=True,
    )
    order = models.PositiveSmallIntegerField()
    is_optional = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by_id = models.UUIDField(null=True, blank=True)
    note = models.TextField(blank=True, default="")

    class Meta:
        db_table = "objects_object_stages"
        constraints = [
            models.UniqueConstraint(
                fields=["object_id", "stage_key"],
                name="objects_stage_unique_object_stage_key",
            ),
        ]
        ordering = ["order"]

    @classmethod
    def build_initial_stages(cls, supply_object: SupplyObject) -> list["ObjectStage"]:
        stages = []
        for key in ObjectStageKey:
            stages.append(
                cls(
                    object=supply_object,
                    tenant_id=supply_object.tenant_id,
                    stage_key=key,
                    status=ObjectStageStatus.PENDING,
                    order=STAGE_ORDER[key],
                    is_optional=key in OPTIONAL_STAGES,
                )
            )
        return stages
