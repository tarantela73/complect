# backend/apps/contragents/models/contragent.py
from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class ContragentType(models.TextChoices):
    LEGAL_ENTITY = "LEGAL_ENTITY", "Юридическое лицо"
    ENTREPRENEUR = "ENTREPRENEUR", "Индивидуальный предприниматель"
    INDIVIDUAL = "INDIVIDUAL", "Физическое лицо"


class Contragent(BaseEnterpriseModel):
    name = models.CharField(max_length=255, db_index=True)
    inn = models.CharField(max_length=12, db_index=True)
    contragent_type = models.CharField(
        max_length=20,
        choices=ContragentType.choices,
        default=ContragentType.LEGAL_ENTITY,
        db_index=True,
    )

    class Meta:
        app_label = "contragents"
        db_table = "contragents_contragents"
