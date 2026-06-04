from __future__ import annotations

import socket
from typing import TYPE_CHECKING

import structlog

from apps.files.services.scan_engine import ScanResult

if TYPE_CHECKING:
    from apps.files.models.file_record import FileRecord

logger = structlog.get_logger(__name__)


class ClamAVScanEngine:
    """
    Production ScanEngine implementation: CLAMD via unix socket.

    Guarantees: scan() never raises. Timeout and socket errors return
    ScanResult.ERROR which the consumer maps to QUARANTINED — files
    never stay stuck in SCANNING state (rule: ERROR → QUARANTINED).

    Circuit breaker: after CLAMAV_CB_FAILURE_THRESHOLD consecutive errors,
    scan() returns ERROR immediately (no socket I/O) until the circuit
    recovers after CLAMAV_CB_RECOVERY_TTL seconds. Prevents 120 s × N
    worker timeout storms when clamd is unreachable.

    Protocol: send object_key over unix socket, read CLAMD response.
    INSTREAM byte-streaming is wired when the S3 read-path is available.
    """

    def __init__(self, socket_path: str | None = None) -> None:
        from django.conf import settings

        self.socket_path: str = socket_path or settings.CLAMAV_SOCKET_PATH

    def scan(self, file_record: FileRecord) -> ScanResult:
        from apps.files.services.clamav_circuit_breaker import get_circuit_breaker

        breaker = get_circuit_breaker()
        if breaker.is_open():
            logger.warning(
                "clamav_circuit_breaker_fast_fail",
                file_id=str(file_record.id),
            )
            return ScanResult.CB_FAST_FAIL

        result = self._scan_via_socket(file_record)

        if result == ScanResult.ERROR:
            breaker.record_failure()
        else:
            breaker.record_success()

        return result

    def _scan_via_socket(self, file_record: FileRecord) -> ScanResult:
        from django.conf import settings

        timeout = getattr(settings, "CLAMAV_SCAN_TIMEOUT_MS", 120_000) / 1000

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(self.socket_path)
                s.sendall(file_record.object_key.encode())
                response = s.recv(1024).decode()

            if "OK" in response:
                return ScanResult.CLEAN
            if "FOUND" in response:
                logger.warning(
                    "clamav_virus_found",
                    file_id=str(file_record.id),
                    response=response,
                )
                return ScanResult.INFECTED
            return ScanResult.ERROR

        except socket.timeout:
            logger.warning(
                "clamav_scan_timeout",
                file_id=str(file_record.id),
                socket_path=self.socket_path,
                timeout_s=timeout,
            )
            return ScanResult.ERROR
        except OSError as exc:
            logger.error(
                "clamav_scan_oserror",
                file_id=str(file_record.id),
                socket_path=self.socket_path,
                error=str(exc),
            )
            return ScanResult.ERROR


def get_scan_engine():
    """
    Returns the configured ScanEngine implementation.

    Reads FILES_SCAN_ENGINE from settings:
      "STUB"   — StubScanEngine (dev/test, always CLEAN)
      "CLAMAV" — ClamAVScanEngine (production, unix socket to clamd)
    """
    from django.conf import settings

    from apps.files.services.scan_engine import StubScanEngine

    if getattr(settings, "FILES_SCAN_ENGINE", "STUB") == "CLAMAV":
        return ClamAVScanEngine()
    return StubScanEngine()
