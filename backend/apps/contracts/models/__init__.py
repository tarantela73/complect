"""Canonical Django model registration for the migrated Contracts schema."""

from .attachment import AttachmentType, ContractAttachment
from .contract import Contract, ContractStatus, ResourceSupplyType
from .legacy_document_link import LegacyDocumentLink, LegacyDocumentMappingState

__all__ = [
    "AttachmentType",
    "Contract",
    "ContractAttachment",
    "ContractStatus",
    "ResourceSupplyType",
    "LegacyDocumentLink",
    "LegacyDocumentMappingState",
]
