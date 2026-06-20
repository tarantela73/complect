from django.contrib.gis.db import models as gis_models
from django.db import models

from apps.common.models.base import BaseEnterpriseModel


class ResourceType(models.TextChoices):
    ELECTRICITY = "ELECTRICITY", "Электроснабжение"
    GAS = "GAS", "Газоснабжение"
    WATER = "WATER", "Водоснабжение"
    HEAT = "HEAT", "Теплоснабжение"
    OTHER = "OTHER", "Прочее"


class SupplyObjectType(models.TextChoices):
    BUILDING = "BUILDING", "Здание"
    GRS = "GRS", "Газораспределительная станция"
    PIPELINE = "PIPELINE", "Трубопровод"
    SUBSTATION = "SUBSTATION", "Подстанция"
    RESERVOIR = "RESERVOIR", "Водохранилище"
    HEATING_POINT = "HEATING_POINT", "Тепловой пункт"
    OTHER = "OTHER", "Прочее"


class ObjectStatus(models.TextChoices):
    NEW = "NEW", "Новый"
    IN_PROGRESS = "IN_PROGRESS", "В работе"
    PAUSED = "PAUSED", "Приостановлен"
    COMPLETED = "COMPLETED", "Завершён"
    CANCELLED = "CANCELLED", "Отменён"


class SupplyObject(BaseEnterpriseModel):
    name = models.CharField(max_length=255, db_index=True)
    address = models.CharField(max_length=500, blank=True, default="")
    object_type = models.CharField(
        max_length=30,
        choices=SupplyObjectType.choices,
        db_index=True,
    )
    resource_type = models.CharField(
        max_length=30,
        choices=ResourceType.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=ObjectStatus.choices,
        default=ObjectStatus.NEW,
        db_index=True,
    )
    description = models.TextField(blank=True, default="")

    # Cross-domain refs — UUID only, no model import (ADR-0005)
    contract_id = models.UUIDField(null=True, blank=True, db_index=True)  # legacy single ref
    contragent_id = models.UUIDField(null=True, blank=True, db_index=True)

    # Responsible persons — UUID only, no ORM FK (ADR-0005)
    manager_id = models.UUIDField(null=True, blank=True, db_index=True)
    foreman_id = models.UUIDField(null=True, blank=True, db_index=True)
    team_name = models.CharField(max_length=255, blank=True, default="")

    # Planning and execution dates
    planned_start_date = models.DateField(null=True, blank=True)
    planned_end_date = models.DateField(null=True, blank=True)
    actual_start_date = models.DateField(null=True, blank=True)
    actual_end_date = models.DateField(null=True, blank=True)

    # GIS fields — GIST indexes created automatically via spatial_index=True
    location = gis_models.PointField(null=True, blank=True, srid=4326, spatial_index=True)
    boundary = gis_models.PolygonField(null=True, blank=True, srid=4326, spatial_index=True)

    # Public site fields
    is_public = models.BooleanField(default=False, db_index=True)
    slug = models.SlugField(max_length=255, null=True, blank=True, db_index=True)
    cover_image_key = models.CharField(max_length=500, null=True, blank=True)

    class Meta:
        db_table = "objects_supply_objects"
