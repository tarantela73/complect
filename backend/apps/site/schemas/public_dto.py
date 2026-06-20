import uuid

from pydantic import BaseModel, field_validator


class PublicLeadFormRequest(BaseModel):
    client_name: str
    client_phone: str
    address: str
    resource_type: str
    client_email: str = ""
    comment: str = ""
    cf_turnstile_token: str

    @field_validator("client_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("client_name cannot be empty")
        return v

    @field_validator("client_phone")
    @classmethod
    def phone_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("client_phone cannot be empty")
        return v


class PublicLeadFormResponse(BaseModel):
    id: uuid.UUID
    status: str


class PublicPageContentResponse(BaseModel):
    slug: str
    content: dict
    meta: dict
