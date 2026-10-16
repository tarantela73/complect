"""Internal compatibility seams; a legacy document UUID is never a File UUID."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from django.http import HttpRequest


class RecoveryPersistenceError(RuntimeError):
    """The durable recovery envelope could not be persisted or verified."""


@dataclass(frozen=True, slots=True)
class LegacySourceProof:
    source_system: str
    legacy_document_id: uuid.UUID
    tenant_id: uuid.UUID
    revision: str
    namespace: str
    object_key: str
    size_bytes: int
    checksum_sha256: str
    snapshot_sha256: str
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class FileProof:
    file_id: uuid.UUID
    tenant_id: uuid.UUID
    object_key: str
    size_bytes: int
    checksum_sha256: str
    storage_provider: str
    status: str
    version: int
    is_legal_hold: bool


@dataclass(frozen=True, slots=True)
class LegacyLink:
    source: LegacySourceProof
    target_file_id: uuid.UUID
    version: int
    target_version: int


class LegacyFileProofProtocol(Protocol):
    def get_proof(self, file_id: uuid.UUID, tenant_id: uuid.UUID) -> FileProof: ...


class LegacySourceVerificationProtocol(Protocol):
    """An installed producer/export verifier, never a caller's assertion."""

    def verify(self, proof: LegacySourceProof, target: FileProof) -> bool: ...


class LegacyLinkProtocol(Protocol):
    def resolve(self, legacy_document_id: uuid.UUID, tenant_id: uuid.UUID) -> LegacyLink | None: ...
    def finalize_resolved(self, link: LegacyLink, operation: Callable[[], None]) -> None: ...


class LegacyCompatibilityProtocol(Protocol):
    def authorize_replay(self, task_id: str, request: HttpRequest) -> str: ...
    def reconcile_processing(self, task_id: str, request: HttpRequest) -> None: ...
    def submit(self, doc_id: uuid.UUID) -> str: ...
    def run(
        self,
        doc_id: str,
        task_id: str,
        args: tuple,
        kwargs: dict,
        metadata: dict,
    ) -> str: ...


_compatibility_service: LegacyCompatibilityProtocol | None = None


def register_legacy_compatibility_service(provider: LegacyCompatibilityProtocol) -> None:
    global _compatibility_service
    _compatibility_service = provider


def get_legacy_compatibility_service() -> LegacyCompatibilityProtocol:
    if _compatibility_service is None:
        raise RuntimeError("LEGACY_COMPATIBILITY_PROVIDER_UNAVAILABLE")
    return _compatibility_service
