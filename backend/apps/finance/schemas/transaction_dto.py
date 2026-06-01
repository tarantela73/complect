import uuid
from typing import Optional

from pydantic import BaseModel, Field


class TransactionPostRequest(BaseModel):
    contract_id: uuid.UUID
    amount: int = Field(gt=0, description="Сумма в копейках")
    transaction_type: str = Field(default="PAYMENT", description="PAYMENT, REFUND, CORRECTION")
    external_ref: str = Field(default="", max_length=255)


class TransactionResponse(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    transaction_type: str
    status: str
    amount: int
    amount_rub: float = 0.0
    external_ref: str

    model_config = {"from_attributes": True}

    def model_post_init(self, __context) -> None:
        object.__setattr__(self, "amount_rub", self.amount / 100)


class TransactionListResponse(BaseModel):
    results: list[TransactionResponse]
    next_cursor: Optional[str] = None
    count: int
