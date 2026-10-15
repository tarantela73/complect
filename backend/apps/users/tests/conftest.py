import base64
import json as jsonlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings as django_settings
from django.db import models, transaction
from django.test import Client
from django.test.client import MULTIPART_CONTENT
from django.utils import timezone

from apps.users.models import IdentityMergeOperation, RecoveryOperation, RecoveryToken, User
from apps.users.security.credential_entrypoints import credential_entrypoint_manifest_digest
from apps.users.security.reconciliation_approval import (
    ReconciliationApproval,
    canonical_action_bytes,
)
from apps.users.services.identity_collision_resolution import IdentityCollisionResolutionService

_UNSET = object()

for _alias in ("iam_concurrency_a", "iam_concurrency_b"):
    django_settings.DATABASES.setdefault(
        _alias,
        {
            **django_settings.DATABASES["default"],
            "TEST": {"MIRROR": "default"},
        },
    )


class JsonTestClient(Client):
    def post(
        self,
        path,
        data=None,
        content_type=MULTIPART_CONTENT,
        secure=False,
        *,
        headers=None,
        query_params=None,
        json=_UNSET,
        cookies=None,
        **extra,
    ):
        if json is not _UNSET:
            if data is not None:
                raise TypeError("pass either data or json")
            data = jsonlib.dumps(json)
            content_type = "application/json"
        for name, value in (cookies or {}).items():
            self.cookies[name] = value
        return super().post(
            path,
            data=data,
            content_type=content_type,
            secure=secure,
            headers=headers,
            query_params=query_params,
            **extra,
        )


@pytest.fixture
def client(settings):
    settings.EXPECTED_GATEWAY_TOKEN = "test-gateway-token"
    return JsonTestClient(headers={"X-Gateway-Token": settings.EXPECTED_GATEWAY_TOKEN})


@pytest.fixture
def client_factory(settings):
    settings.EXPECTED_GATEWAY_TOKEN = "test-gateway-token"

    def create_client():
        return JsonTestClient(headers={"X-Gateway-Token": settings.EXPECTED_GATEWAY_TOKEN})

    return create_client


@pytest.fixture
def identity(db):
    identity_id = uuid.uuid4()
    return User.objects.create_user(
        username=f"identity-{identity_id.hex[:12]}",
        email=f"identity-{identity_id.hex[:12]}@example.com",
        password="TemporaryPassword123!",
        identity_id=identity_id,
        normalized_email=f"identity-{identity_id.hex[:12]}@example.com",
        email_normalization_version=1,
        security_version=1,
        security_status=User.SecurityStatus.ACTIVE,
        identity_legal_hold=False,
    )


@pytest.fixture
def recovery_token(identity):
    with transaction.atomic():
        operation = RecoveryOperation.objects.create(
            delivery_ciphertext=b"ciphertext",
            delivery_nonce=b"nonce-12-byte",
            delivery_key_id="test-recovery-key",
            request_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            causal_root_id=uuid.uuid4(),
        )
        return RecoveryToken.objects.create(
            identity_id=identity.identity_id,
            operation=operation,
            verifier_hash=uuid.uuid4().hex * 2,
            key_id="test-verifier-key",
            purpose=RecoveryToken.Purpose.PASSWORD_RESET,
            expires_at=timezone.now() + timedelta(minutes=30),
        )


@dataclass
class IdentityMergeOperationFactory:
    source: User
    canonical: User

    def __call__(
        self,
        *,
        state=IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE,
        approval_expires_at=None,
    ):
        now = timezone.now()
        return IdentityMergeOperation.objects.create(
            source_user=self.source,
            source_identity_id=self.source.identity_id,
            canonical_identity_id=self.canonical.identity_id,
            state=state,
            approval_reference=f"TEST-{uuid.uuid4()}",
            approval_digest=uuid.uuid4().hex * 2,
            approval_key_id="test-key",
            approval_signature=b"test-signature",
            approved_at=now,
            approval_expires_at=approval_expires_at or now + timedelta(hours=1),
            synthetic_normalized_email=f"merged+{self.source.identity_id}@identity.invalid",
            retention_until=now + timedelta(days=30),
        )

    def advance_to(self, state):
        operation = self()
        now = timezone.now()
        path = {
            IdentityMergeOperation.State.APPROVED_PENDING_CONVERGENCE: [],
            IdentityMergeOperation.State.APPROVED_DEFERRED: [IdentityMergeOperation.State.APPROVED_DEFERRED],
            IdentityMergeOperation.State.READY_TO_FINALIZE: [
                IdentityMergeOperation.State.APPROVED_DEFERRED,
                IdentityMergeOperation.State.READY_TO_FINALIZE,
            ],
            IdentityMergeOperation.State.FINALIZED: [
                IdentityMergeOperation.State.APPROVED_DEFERRED,
                IdentityMergeOperation.State.READY_TO_FINALIZE,
                IdentityMergeOperation.State.FINALIZED,
            ],
            IdentityMergeOperation.State.CANCELLED: [IdentityMergeOperation.State.CANCELLED],
            IdentityMergeOperation.State.EXPIRED: [IdentityMergeOperation.State.EXPIRED],
        }[state]
        for next_state in path:
            updates = {"state": next_state, "version": models.F("version") + 1, "updated_at": now}
            if next_state == IdentityMergeOperation.State.APPROVED_DEFERRED:
                updates.update(
                    convergence_mode=IdentityMergeOperation.ConvergenceMode.OWNER_CONFIRMED,
                    credential_epoch=1,
                    credentials_changed_at=now,
                    credentials_converged_at=now,
                )
            if next_state == IdentityMergeOperation.State.FINALIZED:
                updates["finalized_at"] = now
            IdentityMergeOperation.objects.filter(pk=operation.pk).update(**updates)
        operation.refresh_from_db()
        return operation


@pytest.fixture
def identity_merge_operation_factory(identity):
    canonical_id = uuid.uuid4()
    canonical = User.objects.create_user(
        username=f"canonical-{canonical_id.hex[:12]}",
        email=f"canonical-{canonical_id.hex[:12]}@example.com",
        password="TemporaryPassword123!",
        identity_id=canonical_id,
        normalized_email=f"canonical-{canonical_id.hex[:12]}@example.com",
        email_normalization_version=1,
        security_version=1,
        security_status=User.SecurityStatus.ACTIVE,
        identity_legal_hold=False,
    )
    return IdentityMergeOperationFactory(identity, canonical)


@dataclass(frozen=True, slots=True)
class CollisionPair:
    first: User
    second: User


@pytest.fixture
def collision_pair(db):
    first_tenant, second_tenant = uuid.uuid4(), uuid.uuid4()
    first_id, second_id = uuid.uuid4(), uuid.uuid4()
    first = User.objects.create_user(
        username=f"first-{first_id.hex[:8]}",
        email="collision@example.com",
        password="FirstPassword123!",
        tenant_id=first_tenant,
        identity_id=first_id,
        normalized_email="collision@example.com",
        email_normalization_version=1,
        security_version=1,
        security_status=User.SecurityStatus.MFA_SETUP_REQUIRED,
        identity_legal_hold=False,
    )
    second = User.objects.create_user(
        username=f"second-{second_id.hex[:8]}",
        email="COLLISION@example.com",
        password="SecondPassword123!",
        tenant_id=second_tenant,
        identity_id=second_id,
        normalized_email="collision@example.com",
        email_normalization_version=1,
        security_version=1,
        security_status=User.SecurityStatus.MFA_SETUP_REQUIRED,
        identity_legal_hold=False,
    )
    return CollisionPair(first=first, second=second)


@pytest.fixture
def identity_collision_resolution_service():
    return IdentityCollisionResolutionService()


@dataclass
class SignedReconciliationApprovalFactory:
    private_key: Ed25519PrivateKey
    service: IdentityCollisionResolutionService
    key_id: str = "test-reconciliation-key"

    def _sign(self, action, reference):
        signature = self.private_key.sign(canonical_action_bytes(action)).hex()
        return ReconciliationApproval(
            reference=reference,
            key_id=self.key_id,
            signature=signature,
            action=action,
        )

    def for_correct_email(self, *, user_pk, email, reference):
        user = User.objects.using("default").get(pk=user_pk)
        action = self.service.build_correct_email_action(user=user, new_email=email, reference=reference)
        return self._sign(action, reference)

    def for_merge(self, *, source_pk, canonical_pk, reference):
        source = User.objects.using("default").get(pk=source_pk)
        canonical = User.objects.using("default").get(pk=canonical_pk)
        action = self.service.build_merge_action(source=source, canonical=canonical, reference=reference)
        return self._sign(action, reference)

    def for_cancel_merge(self, *, operation_id, expected_version, reference):
        operation = IdentityMergeOperation.objects.using("default").get(pk=operation_id)
        assert operation.version == expected_version
        action = self.service.build_cancel_action(operation=operation, reference=reference)
        return self._sign(action, reference)


@pytest.fixture
def signed_reconciliation_approval(settings, identity_collision_resolution_service):
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    settings.IAM_RECONCILIATION_APPROVAL_PUBLIC_KEYS = {
        "test-reconciliation-key": base64.b64encode(public_key).decode(),
    }
    return SignedReconciliationApprovalFactory(private_key, identity_collision_resolution_service)


@pytest.fixture
def valid_runtime_merge_coverage_attestation(settings):
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    now = datetime.now(UTC)
    # WSL/Docker clocks can slew backwards by a few seconds while a suite is
    # running. Keep the signed approval clearly in the past so that behavior
    # tests do not confuse clock synchronization with a future attestation.
    approved_at = (now - timedelta(minutes=1)).isoformat()
    expires_at = (now + timedelta(days=1)).isoformat()
    settings.ENVIRONMENT = "test"
    settings.BUILD_COMMIT = "test-build-commit"
    settings.IAM_DEFERRED_MERGE_EXECUTION_ENABLED = True
    settings.IAM_DEFERRED_MERGE_COVERAGE_DIGEST = credential_entrypoint_manifest_digest()
    settings.IAM_DEFERRED_MERGE_COVERAGE_KEY_ID = "test-coverage-key"
    settings.IAM_DEFERRED_MERGE_COVERAGE_COMMIT = settings.BUILD_COMMIT
    settings.IAM_DEFERRED_MERGE_COVERAGE_APPROVED_AT = approved_at
    settings.IAM_DEFERRED_MERGE_COVERAGE_EXPIRES_AT = expires_at
    settings.IAM_DEFERRED_MERGE_COVERAGE_NONCE = "test-coverage-nonce"
    settings.IAM_DEFERRED_MERGE_COVERAGE_PUBLIC_KEYS = {
        "test-coverage-key": base64.b64encode(public_key).decode(),
    }
    envelope = {
        "purpose": "complect-iam-deferred-merge-coverage-v1",
        "environment": settings.ENVIRONMENT,
        "schema_version": 1,
        "scanner_version": 1,
        "manifest_sha256": settings.IAM_DEFERRED_MERGE_COVERAGE_DIGEST,
        "build_commit": settings.BUILD_COMMIT,
        "approved_at": approved_at,
        "expires_at": expires_at,
        "nonce": settings.IAM_DEFERRED_MERGE_COVERAGE_NONCE,
    }
    settings.IAM_DEFERRED_MERGE_COVERAGE_SIGNATURE = private_key.sign(
        jsonlib.dumps(envelope, sort_keys=True, separators=(",", ":")).encode(),
    ).hex()
    return envelope
