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
        constraints = [
            models.UniqueConstraint(
                fields=["tenant_id", "inn"],
                name="unique_tenant_inn",
            )
        ]


class PersonProfile(BaseEnterpriseModel):
    """
    PII-sensitive profile linked to a Contragent.
    All sensitive fields (passport, snils, phone) are AES-256 encrypted via pgcrypto.
    Raw values are NEVER stored in plaintext.
    """

    contragent = models.ForeignKey(
        Contragent,
        on_delete=models.PROTECT,
        related_name="person_profiles",
        db_index=True,
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True, default="")

    # PII fields — stored encrypted via PiiEncryptionVault (AES-256/pgcrypto)
    encrypted_passport = models.TextField()
    encrypted_snils = models.TextField()
    encrypted_phone = models.TextField()

    class Meta:
        app_label = "contragents"
        db_table = "contragents_person_profiles"
        indexes = [
            models.Index(fields=["tenant_id", "contragent_id"], name="idx_person_tenant_contragent"),
        ]
