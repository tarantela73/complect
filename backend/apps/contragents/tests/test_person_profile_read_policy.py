"""Exercise the PII fence with issued MFA/session credentials and PostgreSQL."""
import uuid
from concurrent.futures import ThreadPoolExecutor

import pyotp
import pytest
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, connection, connections, transaction
from django.test import RequestFactory

from apps.common.context.core import ContextCore, clear_context
from apps.common.contracts.auth_context import ValidatedAuthContext
from apps.common.contracts.pii_read_context import PiiReadContext
from apps.common.models.rbac import Permission, Role, RoleBinding, RolePermission
from apps.common.repository.sre_engine import EnterpriseSreRepository
from apps.common.security.audit import AuditIntegrityEngine, AuditLogChain
from apps.common.security.pii_vault import PiiEncryptionVault
from apps.common.tenancy.context import tenant_rls_scope
from apps.contragents.models.contragent import Contragent, ContragentType
from apps.contragents.repositories.person_repo import PersonProfileRepository
from apps.contragents.services.person_profile_read_service import PersonProfileReadService
from apps.users.repositories.identity_creation_repository import create_identity
from apps.users.repositories.membership_repository import MembershipRepository

pytestmark = pytest.mark.django_db(transaction=True)
PASSPORT = "4510-777888"
PHONE = "+79991234567"
SNILS = "123-456-789 00"


@pytest.fixture
def profile_access(client):
    tenant = uuid.uuid4()
    password = "Synthetic-profile-password-20261009!"
    email = f"profile-{uuid.uuid4().hex}@example.test"
    with transaction.atomic():
        user = create_identity(
            username=email, email=email, normalized_email=email,
            tenant_id=tenant,
            email_normalization_version=1, password=make_password(password),
            identity_id=uuid.uuid4(), first_name="Fixture", security_version=1,
            security_status="mfa_setup_required", identity_legal_hold=False,
        )
        MembershipRepository.ensure_foundation(
            tenant_id=tenant, identity_id=user.identity_id,
            legacy_actor_user_id=user.pk, display_name="Fixture",
        )
    actor = uuid.UUID(int=user.pk, version=4)
    with tenant_rls_scope(tenant):
        permission, _ = Permission.objects.get_or_create(resource="contragents", action="read")
        role = Role.objects.create(name=f"profile-role-{uuid.uuid4().hex}")
        RolePermission.objects.create(role=role, permission=permission)
        RoleBinding.objects.create(tenant_id=tenant, user_id=actor, role=role, is_active=True)
        contragent = Contragent.objects.create(
            tenant_id=tenant, name="Synthetic organisation", inn="1234567890",
            contragent_type=ContragentType.LEGAL_ENTITY,
        )
        profile = PersonProfileRepository.create_secure_profile(
            contragent, "Fixture", "Profile", "", PASSPORT, SNILS, PHONE,
        )
    gateway = {"HTTP_X_GATEWAY_TOKEN": settings.EXPECTED_GATEWAY_TOKEN}
    login = client.post("/api/v1/auth/login", {"username": email, "password": password},
                        content_type="application/json", **gateway)
    assert login.status_code == 200
    partial = login.json()["access_token"]
    setup = client.post("/api/v1/auth/mfa/setup", content_type="application/json",
                        HTTP_AUTHORIZATION=f"Bearer {partial}", **gateway)
    assert setup.status_code == 200
    confirm = client.post("/api/v1/auth/mfa/confirm", {"totp_code": pyotp.TOTP(setup.json()["secret"]).now()},
                          content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {partial}", **gateway)
    assert confirm.status_code == 200
    login = client.post("/api/v1/auth/login", {"username": email, "password": password},
                        content_type="application/json", **gateway)
    assert login.status_code == 200 and login.json()["mfa_required"] is True
    verified = client.post("/api/v1/auth/mfa/verify", {"totp_code": pyotp.TOTP(setup.json()["secret"]).now()},
                           content_type="application/json",
                           HTTP_AUTHORIZATION=f"Bearer {login.json()['access_token']}", **gateway)
    assert verified.status_code == 200
    token = verified.json()["access_token"]
    yield tenant, actor, contragent, profile, token
    clear_context()


def read_profiles(client, access, request_id):
    _, _, contragent, _, token = access
    return client.get(
        f"/api/v1/contragents/{contragent.id}/persons/",
        HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_GATEWAY_TOKEN=settings.EXPECTED_GATEWAY_TOKEN,
        HTTP_X_REQUEST_ID=str(request_id), HTTP_X_CORRELATION_ID=str(uuid.uuid4()),
    )


def test_policy_denial_stops_before_profile_query_or_audit(monkeypatch):
    """A trusted request with no permission must never reach PII or audit seams."""
    import apps.contragents.services.person_profile_read_service as service_module

    actor_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    contragent_id = uuid.uuid4()
    request = RequestFactory().get("/api/v1/contragents/persons/")
    context = ValidatedAuthContext(actor_id=actor_id, tenant_id=tenant_id)
    ContextCore.user_id.set(actor_id)
    ContextCore.tenant_id.set(tenant_id)
    pii_context = PiiReadContext(
        "user", str(actor_id), tenant_id, "POLICY_REGRESSION", uuid.uuid4(), uuid.uuid4()
    )
    calls: list[str] = []

    class AutocommitConnection:
        in_atomic_block = False

        @staticmethod
        def get_autocommit() -> bool:
            return True

    monkeypatch.setattr(service_module, "verified_request_context", lambda _request: context)
    monkeypatch.setattr(service_module, "connections", {"default": AutocommitConnection()})
    monkeypatch.setattr(service_module, "tenant_rls_scope", lambda *_args, **_kwargs: pii_context.activate())
    monkeypatch.setattr(service_module.PolicyEngine, "check", lambda **_kwargs: False)
    monkeypatch.setattr(service_module, "get_person_profile_ids", lambda *_args: calls.append("query"))
    monkeypatch.setattr(
        service_module.AuditIntegrityEngine,
        "append_read_and_commit",
        lambda *_args: calls.append("audit"),
    )

    with pii_context.activate(), pytest.raises(PermissionDenied, match="PII_POLICY_DENIED"):
        PersonProfileReadService.read(contragent_id, request=request)

    assert calls == []


def test_audit_is_committed_before_ciphertext_and_decrypt(client, profile_access, caplog):
    request_id = uuid.uuid4()
    observer = connections["default"].copy(alias="default")
    seen = []
    def inspect(execute, sql, params, many, context):
        lowered = sql.lower()
        if ("contragents_person" in lowered and "encrypted_passport" in lowered) or "pgp_sym_decrypt" in lowered:
            assert "encrypted_snils" not in lowered
            with observer.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM common_audit_chain WHERE payload->>'request_id'=%s",
                    [str(request_id)],
                )
                assert cursor.fetchone()[0] == 1
            seen.append(lowered)
        return execute(sql, params, many, context)
    try:
        with connection.execute_wrapper(inspect):
            response = read_profiles(client, profile_access, request_id)
    finally:
        observer.close()
    assert response.status_code == 200
    assert len(seen) >= 3
    body = response.json()
    assert body["results"][0]["masked_passport"] == "45**-***888"
    assert body["results"][0]["masked_phone"] == "+7(999)***-45-67"
    for raw in (PASSPORT, PHONE, SNILS):
        assert raw not in response.content.decode()
        assert raw not in caplog.text


def test_audit_failure_never_selects_or_decrypts_pii(client, profile_access):
    seen = []
    def fail_audit(execute, sql, params, many, context):
        lowered = sql.lower()
        if "contragents_person" in lowered and "encrypted_passport" in lowered or "pgp_sym_decrypt" in lowered:
            seen.append(lowered)
        if "insert into common_audit_chain" in lowered:
            raise DatabaseError("synthetic audit storage outage")
        return execute(sql, params, many, context)
    with connection.execute_wrapper(fail_audit):
        response = read_profiles(client, profile_access, uuid.uuid4())
    assert response.status_code >= 400
    assert not seen


def test_decrypt_failure_cannot_rollback_committed_audit(client, profile_access):
    tenant, _, _, profile, _ = profile_access
    with tenant_rls_scope(tenant):
        EnterpriseSreRepository.atomic_cas_update(profile, encrypted_passport="\\x00")
    request_id = uuid.uuid4()
    response = read_profiles(client, profile_access, request_id)
    assert response.status_code >= 400
    assert AuditLogChain.objects.using("default").filter(payload__request_id=str(request_id)).count() == 1
    assert PASSPORT not in response.content.decode()


def test_forged_pii_metadata_is_not_authority(profile_access):
    tenant, actor, contragent, _, _ = profile_access
    request = RequestFactory().get("/api/v1/contragents/persons/")
    request.jwt_user_id = actor
    request.jwt_tenant_id = tenant
    before = AuditLogChain.objects.using("default").count()
    context = PiiReadContext("user", str(actor), tenant, "forged", uuid.uuid4(), uuid.uuid4())
    with context.activate(), pytest.raises(PermissionDenied, match="PII_TRUSTED_AUTH_CONTEXT_REQUIRED"):
        PersonProfileReadService.read(contragent.id, request=request)
    assert AuditLogChain.objects.using("default").count() == before


def test_foreign_tenant_profile_cannot_be_returned(client, profile_access):
    other = uuid.uuid4()
    with tenant_rls_scope(other):
        foreign = Contragent.objects.create(tenant_id=other, name="Foreign fixture", inn="1234567890",
                                             contragent_type=ContragentType.LEGAL_ENTITY)
        PersonProfileRepository.create_secure_profile(foreign, "Other", "Fixture", "", PASSPORT, SNILS, PHONE)
    _, _, _, _, token = profile_access
    response = client.get(f"/api/v1/contragents/{foreign.id}/persons/",
                          HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_GATEWAY_TOKEN=settings.EXPECTED_GATEWAY_TOKEN)
    assert response.status_code in {403, 404}
    assert PASSPORT not in response.content.decode()


def test_ciphertext_hex_and_legacy_writer_remain_compatible(ciphertext_table):
    from django.db import models
    raw = "Синтетический Unicode 🙂"
    cipher = PiiEncryptionVault.encrypt_field(raw)
    text = PiiEncryptionVault.encode_for_text_field(cipher)
    with ciphertext_table("pii_legacy_text"), connection.cursor() as cursor:
        cursor.execute("SELECT pgp_sym_decrypt(%s, %s)", [text, PiiEncryptionVault._get_master_key_runtime()])
        assert cursor.fetchone()[0] == raw
        cursor.execute("INSERT INTO pii_legacy_text(value) VALUES (%s)", [models.TextField().get_prep_value(cipher)])
        cursor.execute("SELECT value FROM pii_legacy_text")
        legacy = cursor.fetchone()[0]
    assert PiiEncryptionVault.decrypt_field(legacy) == raw
    assert PiiEncryptionVault.decrypt_field(text) == raw
    assert PiiEncryptionVault.decrypt_field(memoryview(cipher)) == raw


def test_corrupt_or_wrong_key_never_returns_plaintext(monkeypatch):
    cipher = PiiEncryptionVault.encrypt_field(PASSPORT)
    with pytest.raises(PermissionDenied):
        PiiEncryptionVault.decrypt_field("\\x00")
    monkeypatch.setattr(PiiEncryptionVault, "_cached_master_key", "synthetic-wrong-key")
    with pytest.raises(PermissionDenied):
        PiiEncryptionVault.decrypt_field(cipher)


def test_textual_ciphertext_is_not_encoded_twice():
    cipher = PiiEncryptionVault.encrypt_field(PASSPORT)
    text = PiiEncryptionVault.encode_for_text_field(cipher)
    assert PiiEncryptionVault.encode_for_text_field(text) == text
    assert PiiEncryptionVault.encode_for_text_field(memoryview(cipher)) == text
    with pytest.raises(PermissionDenied):
        PiiEncryptionVault.encode_for_text_field(PASSPORT)


def test_request_ambient_transaction_is_rejected_before_pii(client, profile_access):
    request_id = uuid.uuid4()
    with transaction.atomic():
        response = read_profiles(client, profile_access, request_id)
    assert response.status_code == 403
    assert not AuditLogChain.objects.using("default").filter(payload__request_id=str(request_id)).exists()


@pytest.mark.parametrize("empty", [None, "", b"", "\\x", "b''"])
def test_null_and_empty_keep_the_existing_empty_rule(empty):
    assert PiiEncryptionVault.decrypt_field(empty) == ""


def test_concurrent_genesis_appends_have_one_verifiable_chain():
    assert not AuditLogChain.objects.using("default").exists()
    def append(number):
        try:
            AuditIntegrityEngine.append_read_and_commit({"event": "concurrent_fixture", "number": number})
        finally:
            connections["default"].close()
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(append, range(4)))
    assert AuditLogChain.objects.using("default").count() == 4
    assert AuditIntegrityEngine.run_nightly_integrity_verifier()


def test_runtime_group_cannot_mutate_or_truncate_chain():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_table_privilege('complect_app_runtime','common_audit_chain',x) "
            "FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) x"
        )
        assert [row[0] for row in cursor.fetchall()] == [True, True, False, False, False]
