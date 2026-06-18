from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class MetaUpdateRequest(BaseModel):
    """Meta fields to set via PATCH. Any explicitly set field marks its override as True."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None
    canonical: str | None = None
    og_image: str | None = None
    schema_org: dict | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 60:
            raise ValueError("title must be ≤ 60 characters")
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 160:
            raise ValueError("description must be ≤ 160 characters")
        return v
