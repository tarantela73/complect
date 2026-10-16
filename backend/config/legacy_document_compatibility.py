"""Composition root for the dormant historical document compatibility seam."""

from apps.common.contracts.file_service_protocol import register_file_service
from apps.common.contracts.finance_contracts import register_finance_reader
from apps.common.contracts.legacy_document_contracts import register_legacy_compatibility_service
from apps.contracts.repositories.legacy_document_link_repository import LegacyDocumentLinkRepository
from apps.files.services.file_common_service import default_file_service
from apps.files.services.legacy_document_compatibility_service import LegacyDocumentCompatibilityService
from apps.finance.services.finance_reader import default_finance_reader


def register_contract_read_providers() -> None:
    """Wire the existing owner implementations through their typed contracts."""
    register_file_service(default_file_service)
    register_finance_reader(default_finance_reader)


def register_legacy_document_compatibility() -> None:
    register_legacy_compatibility_service(
        LegacyDocumentCompatibilityService(LegacyDocumentLinkRepository()),
    )
