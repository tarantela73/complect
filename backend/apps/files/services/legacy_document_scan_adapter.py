"""Stream, verify and promote actual S3 objects before Files can become AVAILABLE."""

from __future__ import annotations

import hashlib
import tempfile
import time
import uuid
from collections.abc import Callable
from typing import BinaryIO, Protocol, TypedDict

import boto3
import clamd
from botocore.config import Config
from django.conf import settings

from apps.common.contracts.legacy_document_contracts import FileProof, LegacyLink
from apps.common.services.file_pipeline.clamav_result import parse_clamd_instream_result
from apps.files.models.file_record import FileStatus
from apps.files.repositories.legacy_file_proof_adapter import LegacyFileProofAdapter


class ObjectResponse(TypedDict):
    Body: BinaryIO


class ObjectClient(Protocol):
    def get_object(self, *, bucket: str, key: str) -> ObjectResponse: ...
    def copy_object(self, *, bucket: str, key: str, source: dict[str, str]) -> object: ...


class S3ObjectClient:
    def __init__(self) -> None:
        self.client = boto3.client(
            "s3",
            region_name=settings.AWS_S3_REGION_NAME,
            endpoint_url=getattr(settings, "LEGACY_COMPAT_S3_ENDPOINT_URL", None),
            config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2}),
        )

    def get_object(self, *, bucket: str, key: str) -> ObjectResponse:
        return {"Body": self.client.get_object(Bucket=bucket, Key=key)["Body"]}

    def copy_object(self, *, bucket: str, key: str, source: dict[str, str]) -> object:
        return self.client.copy_object(Bucket=bucket, Key=key, CopySource=source)


class LegacyDocumentScanAdapter:
    def __init__(self, storage: ObjectClient | None = None) -> None:
        self.storage = storage or S3ObjectClient()
        self.files = LegacyFileProofAdapter()

    def _verified_stream(self, bucket: str, link: LegacyLink) -> tempfile.SpooledTemporaryFile[bytes]:
        if not 0 <= link.source.size_bytes <= 500 * 1024 * 1024:
            raise ValueError("LEGACY_OBJECT_EXCEEDS_FILES_SIZE_LIMIT")
        body = self.storage.get_object(bucket=bucket, key=link.source.object_key)["Body"]
        stream = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
        digest = hashlib.sha256()
        size = 0
        deadline = time.monotonic() + 120
        try:
            while block := body.read(1024 * 1024):
                if time.monotonic() > deadline:
                    raise TimeoutError("LEGACY_OBJECT_READ_DEADLINE")
                size += len(block)
                if size > link.source.size_bytes:
                    raise ValueError("LEGACY_OBJECT_SIZE_MISMATCH")
                digest.update(block)
                stream.write(block)
            if size != link.source.size_bytes or digest.hexdigest() != link.source.checksum_sha256:
                raise ValueError("LEGACY_OBJECT_CHECKSUM_MISMATCH")
            stream.seek(0)
            return stream
        except BaseException:
            stream.close()
            raise
        finally:
            body.close()

    def verify_completed(self, link: LegacyLink) -> None:
        with self._verified_stream(settings.S3_PRODUCTION_BUCKET, link):
            pass

    def process(
        self,
        link: LegacyLink,
        proof: FileProof,
        *,
        correlation_id: uuid.UUID,
        clean_checkpoint: bool,
        record_checkpoint: Callable[[FileProof, bool], None],
        revalidate_link: Callable[[], None],
        finalize: Callable[[FileProof], None] | None = None,
    ) -> str:
        if proof.storage_provider != "S3" or link.source.namespace != settings.S3_QUARANTINE_BUCKET:
            raise ValueError("LEGACY_STORAGE_NAMESPACE_UNSUPPORTED")
        if proof.status == FileStatus.AVAILABLE:
            if not clean_checkpoint:
                raise ValueError("LEGACY_AVAILABLE_WITHOUT_SCAN_EVIDENCE")
            self.verify_completed(link)
            if finalize is not None:
                finalize(proof)
            return "COMPLETED"
        if proof.is_legal_hold:
            raise ValueError("LEGACY_FILE_ON_HOLD")
        if proof.status != FileStatus.SCANNING:
            proof = self.files.transition(proof, status=FileStatus.SCANNING, correlation_id=correlation_id)
        if not clean_checkpoint:
            record_checkpoint(proof, False)
            with self._verified_stream(link.source.namespace, link) as stream:
                scanner = clamd.ClamdNetworkSocket(
                    host=settings.CLAMAV_HOST,
                    port=settings.CLAMAV_PORT,
                    timeout=getattr(settings, "CLAMAV_SCAN_TIMEOUT_MS", 120_000) / 1000,
                )
                verdict = parse_clamd_instream_result(scanner.instream(stream))
            if verdict == "INFECTED":
                self.files.transition(
                    proof, status=FileStatus.QUARANTINED, correlation_id=correlation_id, reason="virus_detected"
                )
                return "INFECTED"
            # Persist the verified scan before the first irreversible storage operation.
            record_checkpoint(proof, True)
        revalidate_link()
        # A persisted clean checkpoint allows recovery after copy/delete crashed.
        # Always verify the resulting object bytes; S3 metadata is insufficient.
        try:
            self.verify_completed(link)
        except Exception:
            self.storage.copy_object(
                bucket=settings.S3_PRODUCTION_BUCKET,
                key=link.source.object_key,
                source={"Bucket": link.source.namespace, "Key": link.source.object_key},
            )
            self.verify_completed(link)
        # Preserve source bytes. Quarantine retirement is a separate protected
        # retention command; a pre-check cannot guard concurrent Legal Hold.
        # Availability requires verified scan + verified destination bytes.
        revalidate_link()
        if finalize is None:
            self.files.transition(proof, status=FileStatus.AVAILABLE, correlation_id=correlation_id)
        else:
            finalize(proof)
        return "COMPLETED"
