import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1)
    device_fingerprint: str = Field(default="", max_length=128)


class MFAVerifyRequest(BaseModel):
    totp_code: str = Field(min_length=6, max_length=6)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    mfa_required: bool = False
    permissions: list[str] = []


class RefreshRequest(BaseModel):
    refresh_token: str


class MFASetupResponse(BaseModel):
    secret: str
    otpauth_url: str


class MFAStatusResponse(BaseModel):
    mfa_enabled: bool


class MergeConvergenceStartRequest(BaseModel):
    operation_id: uuid.UUID


class MergeConvergenceStartResponse(BaseModel):
    challenge_token: str
    idempotency_key: uuid.UUID
    expires_at: datetime
    mfa_secret: str | None = None
    otpauth_url: str | None = None


class MergeConvergenceCompleteRequest(BaseModel):
    challenge_token: str = Field(min_length=32, max_length=256)
    new_password: str = Field(min_length=12, max_length=128)
    totp_code: str = Field(min_length=6, max_length=8)
    idempotency_key: uuid.UUID
    requeue_id: uuid.UUID | None = None
