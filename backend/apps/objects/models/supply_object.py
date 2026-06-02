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

    # Cross-domain refs — UUID only, no model import (ADR-0005)
    contract_id = models.UUIDField(null=True, blank=True, db_index=True)
    contragent_id = models.UUIDField(null=True, blank=True, db_index=True)

    # GIS fields — GIST indexes created automatically via spatial_index=True
    location = gis_models.PointField(null=True, blank=True, srid=4326, spatial_index=True)
    boundary = gis_models.PolygonField(null=True, blank=True, srid=4326, spatial_index=True)

    class Meta:
        db_table = "objects_supply_objects"
