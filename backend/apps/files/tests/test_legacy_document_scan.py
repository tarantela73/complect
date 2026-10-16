"""Synthetic objects, actual PostgreSQL/S3/ClamAV. No historical recovery claim."""

from __future__ import annotations

import hashlib
import uuid

import boto3
import pytest
from botocore.exceptions import ClientError
from django.conf import settings

from apps.common.contracts.legacy_document_contracts import LegacyLink, LegacySourceProof
from apps.common.tenancy.context import tenant_rls_scope
from apps.files.repositories.file_repo import FileRepository
from apps.files.repositories.legacy_file_proof_adapter import LegacyFileProofAdapter
from apps.files.services.legacy_document_scan_adapter import LegacyDocumentScanAdapter


@pytest.fixture
def legacy_object():
    client = boto3.client("s3", region_name=settings.AWS_S3_REGION_NAME)
    for bucket in (settings.S3_QUARANTINE_BUCKET, settings.S3_PRODUCTION_BUCKET):
        try:
            client.create_bucket(Bucket=bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                raise
    objects: list[str] = []

    def create(content: bytes, *, checksum: str | None = None):
        tenant_id, file_id, old_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        key = f"{tenant_id}/{file_id}/synthetic-legacy.txt"
        objects.append(key)
        digest = checksum or hashlib.sha256(content).hexdigest()
        client.put_object(Bucket=settings.S3_QUARANTINE_BUCKET, Key=key, Body=content)
        with tenant_rls_scope(tenant_id):
            FileRepository.create_pending(
                file_id=file_id,
                object_key=key,
                original_name="synthetic-legacy.txt",
                content_type="text/plain",
                size_bytes=len(content),
                uploaded_by=uuid.uuid4(),
                storage_provider="S3",
            )
            assert FileRepository.mark_uploaded(file_id, digest)
        proof = LegacyFileProofAdapter().get_proof(file_id, tenant_id)
        source = LegacySourceProof(
            source_system="COMMON_DOCUMENT",
            legacy_document_id=old_id,
            tenant_id=tenant_id,
            revision="synthetic-export-v1",
            namespace=settings.S3_QUARANTINE_BUCKET,
            object_key=key,
            size_bytes=len(content),
            checksum_sha256=digest,
            snapshot_sha256=hashlib.sha256(b"synthetic-source-export").hexdigest(),
            payload_sha256=hashlib.sha256(b"synthetic-task-envelope").hexdigest(),
        )
        return LegacyLink(source, file_id, 1, proof.version), proof

    yield create
    for key in objects:
        for bucket in (settings.S3_QUARANTINE_BUCKET, settings.S3_PRODUCTION_BUCKET):
            client.delete_object(Bucket=bucket, Key=key)


@pytest.mark.django_db(transaction=True)
def test_real_clean_scan_promotes_confirmed_bytes_and_emits_available(legacy_object):
    link, proof = legacy_object(b"synthetic clean object")
    checkpoints: list[str] = []
    result = LegacyDocumentScanAdapter().process(
        link,
        proof,
        correlation_id=uuid.uuid4(),
        clean_checkpoint=False,
        record_checkpoint=lambda _proof, clean: checkpoints.append("clean") if clean else None,
        revalidate_link=lambda: None,
    )
    assert result == "COMPLETED" and checkpoints == ["clean"]
    assert LegacyFileProofAdapter().get_proof(link.target_file_id, link.source.tenant_id).status == "AVAILABLE"
    LegacyDocumentScanAdapter().verify_completed(link)
    body = boto3.client("s3").get_object(Bucket=link.source.namespace, Key=link.source.object_key)["Body"]
    assert body.read() == b"synthetic clean object"
    body.close()


@pytest.mark.django_db(transaction=True)
def test_real_infected_scan_retains_quarantine_and_never_promotes(legacy_object):
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    link, proof = legacy_object(eicar)
    checkpoints: list[str] = []
    assert (
        LegacyDocumentScanAdapter().process(
            link,
            proof,
            correlation_id=uuid.uuid4(),
            clean_checkpoint=False,
            record_checkpoint=lambda _proof, clean: checkpoints.append("clean") if clean else None,
            revalidate_link=lambda: None,
        )
        == "INFECTED"
    )
    assert not checkpoints
    current = LegacyFileProofAdapter().get_proof(link.target_file_id, link.source.tenant_id)
    assert current.status == "QUARANTINED"
    with pytest.raises(ClientError):
        boto3.client("s3").get_object(Bucket=settings.S3_PRODUCTION_BUCKET, Key=link.source.object_key)


@pytest.mark.django_db(transaction=True)
def test_checksum_mismatch_never_becomes_available(legacy_object):
    link, proof = legacy_object(b"different actual bytes", checksum="0" * 64)
    with pytest.raises(ValueError, match="CHECKSUM"):
        LegacyDocumentScanAdapter().process(
            link,
            proof,
            correlation_id=uuid.uuid4(),
            clean_checkpoint=False,
            record_checkpoint=lambda _proof, clean: None,
            revalidate_link=lambda: None,
        )
    assert LegacyFileProofAdapter().get_proof(link.target_file_id, link.source.tenant_id).status != "AVAILABLE"


@pytest.mark.django_db(transaction=True)
def test_resume_after_confirmed_copy_and_delete_requires_clean_checkpoint(legacy_object):
    link, proof = legacy_object(b"confirmed interrupted promotion")
    # Construct the post-scan crash snapshot with real Files/storage commands.
    proof = LegacyFileProofAdapter.transition(proof, status="SCANNING", correlation_id=uuid.uuid4())
    storage = boto3.client("s3")
    storage.copy_object(
        Bucket=settings.S3_PRODUCTION_BUCKET,
        Key=link.source.object_key,
        CopySource={"Bucket": link.source.namespace, "Key": link.source.object_key},
    )
    storage.delete_object(Bucket=link.source.namespace, Key=link.source.object_key)
    assert (
        LegacyDocumentScanAdapter().process(
            link,
            proof,
            correlation_id=uuid.uuid4(),
            clean_checkpoint=True,
            record_checkpoint=lambda _proof, clean: None,
            revalidate_link=lambda: None,
        )
        == "COMPLETED"
    )
    current = LegacyFileProofAdapter().get_proof(link.target_file_id, link.source.tenant_id)
    with pytest.raises(ValueError, match="WITHOUT_SCAN_EVIDENCE"):
        LegacyDocumentScanAdapter().process(
            link,
            current,
            correlation_id=uuid.uuid4(),
            clean_checkpoint=False,
            record_checkpoint=lambda _proof, clean: None,
            revalidate_link=lambda: None,
        )
