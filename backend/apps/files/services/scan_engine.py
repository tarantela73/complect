from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from apps.files.models.file_record import FileRecord


class ScanResult(StrEnum):
    CLEAN = "clean"
    INFECTED = "infected"
    ERROR = "error"


class ScanEngine(Protocol):
    """
    Scan engine contract. Decouples FilesConsumer from ClamAV at the architectural level.

    Implementations:
      StubScanEngine  — always CLEAN; used in development and tests.
      ClamAVScanEngine — real daemon via unix socket (next iteration).

    Returning ERROR must not raise — the consumer maps ERROR to QUARANTINED
    with reason "scan_error" so the file doesn't stay stuck in SCANNING forever.
    """

    def scan(self, file_record: "FileRecord") -> ScanResult: ...


class StubScanEngine:
    """Stub for local dev and tests. Always returns CLEAN."""

    def scan(self, file_record: "FileRecord") -> ScanResult:
        return ScanResult.CLEAN
