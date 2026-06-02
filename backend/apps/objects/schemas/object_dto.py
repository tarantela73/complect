import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class PointGeometry(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: list[float]  # [longitude, latitude]


class SupplyObjectProperties(BaseModel):
    id: uuid.UUID
    name: str
    address: str
    object_type: str
    resource_type: str
    contract_id: Optional[uuid.UUID] = None
    contragent_id: Optional[uuid.UUID] = None

    model_config = {"from_attributes": True}


class FeatureSchema(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: Optional[PointGeometry] = None
    properties: SupplyObjectProperties


class FeatureCollectionSchema(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[FeatureSchema]


class SupplyObjectCreateRequest(BaseModel):
    name: str = Field(..., max_length=255)
    address: str = Field("", max_length=500)
    object_type: str = Field(..., description="BUILDING, GRS, PIPELINE, SUBSTATION, RESERVOIR, HEATING_POINT, OTHER")
    resource_type: str = Field(..., description="ELECTRICITY, GAS, WATER, HEAT, OTHER")
    contract_id: Optional[uuid.UUID] = None
    contragent_id: Optional[uuid.UUID] = None
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    latitude: Optional[float] = Field(None, ge=-90, le=90)

    @field_validator("object_type")
    @classmethod
    def validate_object_type(cls, v: str) -> str:
        from apps.objects.models.supply_object import SupplyObjectType

        valid = {choice[0] for choice in SupplyObjectType.choices}
        if v not in valid:
            msg = f"Недопустимый тип объекта: {v}. Допустимые: {valid}"
            raise ValueError(msg)
        return v

    @field_validator("resource_type")
    @classmethod
    def validate_resource_type(cls, v: str) -> str:
        from apps.objects.models.supply_object import ResourceType

        valid = {choice[0] for choice in ResourceType.choices}
        if v not in valid:
            msg = f"Недопустимый тип ресурса: {v}. Допустимые: {valid}"
            raise ValueError(msg)
        return v


class UpdateLocationRequest(BaseModel):
    longitude: float = Field(..., ge=-180, le=180)
    latitude: float = Field(..., ge=-90, le=90)


class SupplyObjectListResponse(BaseModel):
    results: list[FeatureSchema]
    next_cursor: Optional[str] = None
    count: int
