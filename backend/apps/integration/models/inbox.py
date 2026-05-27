import uuid
from django.db import models


class IntegrationInboxRecord(models.Model):
    """
    [ИСПРАВЛЕНО] Паттерн Inbox для гарантированной дедупликации входящих внешних событий.
    Исключает повторное проведение финансовых проводок при дублировании пакетов от 1С.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Уникальный ключ транзакции во внешней системе (например, Документ_GUID из 1С)
    external_event_id = models.CharField(max_length=255, unique=True, db_index=True)
    source_system = models.CharField(
        max_length=50, db_index=True
    )  # e.g., 'ONES_1C', 'ESIA_GOSUSLUGI'

    status = models.CharField(max_length=30, default="PENDING", db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "integration_inbox"
