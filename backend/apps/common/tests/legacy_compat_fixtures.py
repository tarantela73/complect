"""Synthetic producer export and real identity/MFA/public auth fixtures."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

import boto3
import pyotp
import pytest
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.test import Client, RequestFactory

from apps.common.api.auth import jwt_auth, verified_request_context
from apps.common.contracts.legacy_document_contracts import FileProof, LegacySourceProof
from apps.common.models.rbac import Permission, Role, RoleBinding, RolePermission
from apps.common.security.policy_engine import PolicyEngine
from apps.common.tenancy.context import tenant_rls_scope
from apps.files.repositories.file_repo import FileRepository
from apps.files.services.legacy_document_compatibility_service import envelope_sha256
from apps.users.models import IamTenantRegistryEntry, User
from apps.users.repositories.identity_creation_repository import create_identity
from apps.users.repositories.membership_repository import MembershipRepository


@dataclass
class SyntheticExportVerifier:
    path: Path

    def verify(self, proof: LegacySourceProof, target: FileProof) -> bool:
        raw = self.path.read_bytes()
        export = json.loads(raw)
        return (
            hashlib.sha256(raw).hexdigest() == proof.snapshot_sha256
            and export["legacy_document_id"] == str(proof.legacy_document_id)
            and export["target_file_id"] == str(target.file_id)
            and export["tenant_id"] == str(proof.tenant_id)
            and export["object_key"] == proof.object_key
            and export["checksum_sha256"] == proof.checksum_sha256
            and export["revision"] == proof.revision
        )


@pytest.fixture
def legacy_authenticated_request(settings):
    settings.RBAC_ENFORCEMENT_MODE = "ENFORCE"
    settings.AUTH_ACTIVE_SIGNING_KEY_ID = "compat-fixture-auth"
    settings.AUTH_SIGNING_KEYRING = {"compat-fixture-auth": "isolated-test-signing-key-with-32-bytes"}
    settings.MFA_ACTIVE_KEY_ID = "compat-fixture-mfa"
    settings.MFA_KEYRING = {"compat-fixture-mfa": base64.b64encode(b"m" * 32).decode()}
    settings.SWITCH_RESPONSE_ACTIVE_KEY_ID = "compat-fixture-response"
    settings.SWITCH_RESPONSE_KEYRING = {"compat-fixture-response": base64.b64encode(b"r" * 32).decode()}
    tenant_id, identity_id = uuid.uuid4(), uuid.uuid4()
    email = f"compat-{identity_id.hex}@example.invalid"
    password = "synthetic-fixture-only-password"
    with transaction.atomic():
        identity = create_identity(
            username=f"compat-{identity_id.hex}",
            email=email,
            tenant_id=tenant_id,
            normalized_email=email,
            identity_id=identity_id,
            password=make_password(password),
            email_normalization_version=1,
            security_version=1,
            security_status=User.SecurityStatus.MFA_SETUP_REQUIRED,
        )
    IamTenantRegistryEntry.objects.get_or_create(tenant_id=tenant_id)
    with transaction.atomic(), tenant_rls_scope(tenant_id):
        MembershipRepository.ensure_foundation(
            tenant_id=tenant_id,
            identity_id=identity.identity_id,
            legacy_actor_user_id=identity.pk,
            display_name="Synthetic compatibility operator",
        )
    client = Client(headers={"X-Gateway-Token": settings.EXPECTED_GATEWAY_TOKEN})
    login = client.post(
        "/api/v1/auth/login",
        data={"username": identity.username, "password": password},
        content_type="application/json",
    )
    assert login.status_code == 200
    setup_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    setup = client.post("/api/v1/auth/mfa/setup", headers=setup_headers)
    if setup.status_code != 200:
        import re

        raise AssertionError(re.findall(r"\b[A-Z][A-Z_]{3,80}\b", json.dumps(setup.json())))
    secret = setup.json()["secret"]
    confirmed = client.post(
        "/api/v1/auth/mfa/confirm",
        data={"totp_code": pyotp.TOTP(secret).now()},
        content_type="application/json",
        headers=setup_headers,
    )
    assert confirmed.status_code == 200
    login = client.post(
        "/api/v1/auth/login",
        data={"username": identity.username, "password": password},
        content_type="application/json",
    )
    assert login.status_code == 200
    verified = client.post(
        "/api/v1/auth/mfa/verify",
        data={"totp_code": pyotp.TOTP(secret).now()},
        content_type="application/json",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert verified.status_code == 200
    request = RequestFactory().post(
        "/api/v1/contracts/",
        headers={
            "Authorization": f"Bearer {verified.json()['access_token']}",
        },
    )
    assert jwt_auth(request) is not None
    auth = verified_request_context(request)
    assert auth is not None and auth.tenant_id == tenant_id
    permission, _ = Permission.objects.get_or_create(action="write", resource="legacy_document_mapping")
    role = Role.objects.create(name=f"compat-{uuid.uuid4().hex[:20]}")
    RolePermission.objects.create(role=role, permission=permission)
    with tenant_rls_scope(tenant_id):
        RoleBinding.objects.create(tenant_id=tenant_id, user_id=auth.actor_id, role=role)
    PolicyEngine.invalidate(tenant_id=tenant_id, actor_id=auth.actor_id)
    return request, auth


@pytest.fixture
def legacy_export(tmp_path, settings):
    objects: list[str] = []
    storage = boto3.client("s3")

    def create(tenant_id, content=b"synthetic source bytes", *, legacy_id=None):
        file_id, old_id = uuid.uuid4(), legacy_id or uuid.uuid4()
        key = f"{tenant_id}/{file_id}/legacy-fixture.txt"
        digest = hashlib.sha256(content).hexdigest()
        objects.append(key)
        storage.put_object(Bucket=settings.S3_QUARANTINE_BUCKET, Key=key, Body=content)
        with tenant_rls_scope(tenant_id):
            FileRepository.create_pending(
                file_id=file_id,
                object_key=key,
                original_name="legacy-fixture.txt",
                content_type="text/plain",
                size_bytes=len(content),
                uploaded_by=uuid.uuid4(),
                storage_provider="S3",
            )
            assert FileRepository.mark_uploaded(file_id, digest)
        export = dict(
            legacy_document_id=str(old_id),
            target_file_id=str(file_id),
            tenant_id=str(tenant_id),
            revision="synthetic-producer-v1",
            object_key=key,
            checksum_sha256=digest,
        )
        raw = json.dumps(export, sort_keys=True).encode()
        path = tmp_path / f"{old_id}.json"
        path.write_bytes(raw)
        source = LegacySourceProof(
            source_system="COMMON_DOCUMENT",
            legacy_document_id=old_id,
            tenant_id=tenant_id,
            revision=export["revision"],
            namespace=settings.S3_QUARANTINE_BUCKET,
            object_key=key,
            size_bytes=len(content),
            checksum_sha256=digest,
            snapshot_sha256=hashlib.sha256(raw).hexdigest(),
            payload_sha256=envelope_sha256((str(old_id),), {}),
        )
        return source, file_id, SyntheticExportVerifier(path)

    yield create
    for key in objects:
        for bucket in (settings.S3_QUARANTINE_BUCKET, settings.S3_PRODUCTION_BUCKET):
            storage.delete_object(Bucket=bucket, Key=key)
