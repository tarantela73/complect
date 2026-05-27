import uuid
from pydantic import BaseModel, Field


class ContractCreateRequestSchema(BaseModel):
    """DTO контракт для создания нового договора."""

    contract_number: str = Field(..., max_length=100)
    title: str = Field(..., max_length=255)
    resource_type: str = Field(..., description="ELECTRICITY, GAS, WATER, HEAT, OTHER")
    total_amount: int = Field(..., description="Общая сумма договора в копейках")


class ContractResponseSchema(BaseModel):
    id: uuid.UUID
    contract_number: str
    title: str
    status: str
    resource_type: str  # Возвращаем тип ресурса для UI-слоя
    total_amount: int
    paid_amount: int
    remaining_amount: int
