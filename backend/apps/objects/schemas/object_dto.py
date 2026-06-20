import uuid
from datetime import date, datetime
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
    status: str
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


# --- Enriched detail schema ---

class UserDisplaySchema(BaseModel):
    id: uuid.UUID
    full_name: str


class ObjectStageResponse(BaseModel):
    stage_key: str
    status: str
    order: int
    is_optional: bool
    completed_at: Optional[datetime] = None
    completed_by_id: Optional[uuid.UUID] = None
    note: str = ""


class SupplyObjectDetailResponse(BaseModel):
    id: uuid.UUID
    name: str
    address: str
    object_type: str
    resource_type: str
    status: str
    description: str
    manager: Optional[UserDisplaySchema] = None
    foreman: Optional[UserDisplaySchema] = None
    team_name: str
    contract_id: Optional[uuid.UUID] = None
    contragent_id: Optional[uuid.UUID] = None
    planned_start_date: Optional[date] = None
    planned_end_date: Optional[date] = None
    actual_start_date: Optional[date] = None
    actual_end_date: Optional[date] = None
    stages: list[ObjectStageResponse] = []
    longitude: Optional[float] = None
    latitude: Optional[float] = None
    is_public: bool = False
    slug: Optional[str] = None
    cover_image_key: Optional[str] = None

    model_config = {"from_attributes": True}


# --- Update request ---

class SupplyObjectUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    address: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    status: Optional[str] = None
    manager_id: Optional[uuid.UUID] = None
    foreman_id: Optional[uuid.UUID] = None
    team_name: Optional[str] = Field(None, max_length=255)
    planned_start_date: Optional[date] = None
    planned_end_date: Optional[date] = None
    actual_start_date: Optional[date] = None
    actual_end_date: Optional[date] = None
    is_public: Optional[bool] = None
    slug: Optional[str] = Field(None, max_length=255)
    cover_image_key: Optional[str] = Field(None, max_length=500)

    @field_validator("slug")
    @classmethod
    def validate_slug_format(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        import re
        if not v:
            raise ValueError("Slug не может быть пустым")
        if re.search(r'\s', v):
            raise ValueError("Slug не может содержать пробелы")
        if v != v.lower():
            raise ValueError("Slug должен быть в нижнем регистре")
        if not re.match(r'^[\w-]+$', v, re.UNICODE) or '_' in v:
            raise ValueError("Slug: только строчные буквы, цифры и дефисы")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        from apps.objects.models.supply_object import ObjectStatus
        valid = {choice[0] for choice in ObjectStatus.choices}
        if v not in valid:
            msg = f"Недопустимый статус: {v}. Допустимые: {valid}"
            raise ValueError(msg)
        return v


class StageUpdateRequest(BaseModel):
    status: str
    note: Optional[str] = None
    completed_by_id: Optional[uuid.UUID] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        from apps.objects.models.object_stage import ObjectStageStatus
        valid = {choice[0] for choice in ObjectStageStatus.choices}
        if v not in valid:
            msg = f"Недопустимый статус этапа: {v}. Допустимые: {valid}"
            raise ValueError(msg)
        return v


# --- Create request ---

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
    results: list[SupplyObjectDetailResponse]
    next_cursor: Optional[str] = None
    count: int


class ObjectContractLinkRequest(BaseModel):
    contract_id: uuid.UUID


class ObjectContractResponse(BaseModel):
    contract_id: uuid.UUID


# --- Public site schemas (no internal fields) ---

class PublicObjectLocationSchema(BaseModel):
    lat: float
    lon: float


class PublicObjectSchema(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    address: str
    resource_type: str
    object_type: str
    description: str
    cover_image_url: Optional[str] = None
    location: Optional[PublicObjectLocationSchema] = None

    model_config = {"from_attributes": True}


class PublicObjectListResponse(BaseModel):
    results: list[PublicObjectSchema]
    next_cursor: Optional[str] = None
    count: int
