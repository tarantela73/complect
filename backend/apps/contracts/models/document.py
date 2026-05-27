from django.db import models
from apps.common.models.base import BaseEnterpriseModel


class DocumentStage(models.TextChoices):
    UPLOADED = "UPLOADED", "Загружен"
    QUARANTINE = "QUARANTINE", "В карантине"
    VIRUS_SCANNING = "VIRUS_SCANNING", "Сканирование антивирусом"
    PROCESSING = "PROCESSING", "В обработке"
    READY = "READY", "Готов к использованию"
    FAILED = "FAILED", "Ошибка обработки"


class CommonDocument(BaseEnterpriseModel):
    file_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    file_size = models.BigIntegerField()
    s3_key = models.CharField(max_length=512)

    # stage — критически важно для Celery воркеров антивируса и OCR, индекс обязателен [Пункт 17]
    stage = models.CharField(
        max_length=30,
        choices=DocumentStage.choices,
        default=DocumentStage.UPLOADED,
        db_index=True,
    )
    meta_json = models.JSONBField(default=dict)

    class Meta:
        db_table = "contracts_documents"
