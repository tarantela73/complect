"""Canonical Django model registration for the migrated Contracts schema."""

from .attachment import AttachmentType, ContractAttachment
from .contract import Contract, ContractStatus, ResourceSupplyType

__all__ = [
    "AttachmentType",
    "Contract",
    "ContractAttachment",
    "ContractStatus",
    "ResourceSupplyType",
]
