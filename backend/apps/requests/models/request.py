from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class RequestStatus(models.TextChoices):
    NEW = "NEW", "Новая"
    ASSIGNED = "ASSIGNED", "Назначена"
    PROCESSING = "PROCESSING", "В обработке"
    CONVERTED = "CONVERTED", "Конвертирована"
    CLOSED = "CLOSED", "Закрыта"


class RequestSource(models.TextChoices):
    WEBSITE = "WEBSITE", "Сайт"
    MANUAL = "MANUAL", "Ручной ввод"
    PHONE = "PHONE", "Телефон"


class ResourceType(models.TextChoices):
    ELECTRICITY = "ELECTRICITY", "Электроснабжение"
    GAS = "GAS", "Газоснабжение"
    WATER = "WATER", "Водоснабжение"
    HEAT = "HEAT", "Теплоснабжение"
    OTHER = "OTHER", "Прочее"


class Request(BaseEnterpriseModel):
    client_name = models.CharField(max_length=255)
    client_phone = models.CharField(max_length=20)
    client_email = models.CharField(max_length=255, blank=True, default="")
    address = models.CharField(max_length=500)
    resource_type = models.CharField(
        max_length=30,
        choices=ResourceType.choices,
        default=ResourceType.OTHER,
        db_index=True,
    )
    comment = models.TextField(blank=True, default="")
    source = models.CharField(
        max_length=20,
        choices=RequestSource.choices,
        default=RequestSource.MANUAL,
    )
    status = models.CharField(
        max_length=20,
        choices=RequestStatus.choices,
        default=RequestStatus.NEW,
        db_index=True,
    )
    manager_id = models.UUIDField(null=True, blank=True, db_index=True)
    converted_object_id = models.UUIDField(null=True, blank=True)
    converted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "requests_requests"
        ordering = ["-created_at"]
