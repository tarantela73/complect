from __future__ import annotations

import uuid

from django.core.exceptions import PermissionDenied
from django.db import connections
from django.http import HttpRequest

from apps.common.api.auth import verified_request_context
from apps.common.contracts.pii_read_context import get_pii_read_context
from apps.common.security.audit import AuditIntegrityEngine
from apps.common.security.pii_vault import PiiEncryptionVault
from apps.common.security.policy_engine import PolicyEngine
from apps.common.tenancy.context import tenant_rls_scope
from apps.contragents.queries.contragent_queries import get_person_profile_ids, get_person_profile_rows


def _mask_passport(value: str) -> str:
    compact = "".join(ch for ch in value if ch.isalnum())
    if len(compact) < 10:
        return "****-******"
    return f"{compact[:2]}**-***{compact[-3:]}"


def _mask_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) < 11:
        return "+7(***)***-**-**"
    if len(digits) >= 11:
        return f"+{digits[:1]}({digits[1:4]})***-{digits[-4:-2]}-{digits[-2:]}"
    return f"***-{digits[-4:-2]}-{digits[-2:]}"


class PersonProfileReadService:
    @staticmethod
    def read(
        contragent_id: uuid.UUID,
        *,
        request: HttpRequest,
    ) -> list[dict]:
        trusted_context = verified_request_context(request)
        if trusted_context is None:
            raise PermissionDenied("PII_TRUSTED_AUTH_CONTEXT_REQUIRED")
        context = get_pii_read_context()
        if (
            context.actor_type != "user"
            or context.tenant_id != trusted_context.tenant_id
            or context.actor_ref != str(trusted_context.actor_id)
        ):
            raise PermissionDenied("PII_READ_CONTEXT_UNTRUSTED")
        connection = connections["default"]
        if connection.in_atomic_block or not connection.get_autocommit():
            raise PermissionError("PII_AUDIT_REQUIRES_DURABLE_BOUNDARY")
        with tenant_rls_scope(trusted_context.tenant_id, using="default"):
            allowed = PolicyEngine.check(
                actor_id=trusted_context.actor_id,
                tenant_id=trusted_context.tenant_id,
                action="read",
                resource="contragents",
            )
            if not allowed:
                raise PermissionDenied("PII_POLICY_DENIED")
            profile_ids = get_person_profile_ids(contragent_id, trusted_context.tenant_id)
        AuditIntegrityEngine.append_read_and_commit(
            {
                "event": "contragents.person_profile_read",
                "actor_ref": context.actor_ref,
                "tenant_id": str(context.tenant_id),
                "subject_ref": str(contragent_id),
                "actor_type": "user",
                "reason_code": "CONTRAGENT_PERSONS_READ",
                "request_id": str(context.request_id),
                "correlation_id": str(context.correlation_id),
                "profile_ids": [str(profile_id) for profile_id in profile_ids],
            }
        )
        with tenant_rls_scope(trusted_context.tenant_id, using="default"):
            rows = get_person_profile_rows(contragent_id, trusted_context.tenant_id, profile_ids)
        result: list[dict] = []
        for row in rows:
            try:
                passport = PiiEncryptionVault.decrypt_field(row.encrypted_passport)
                phone = PiiEncryptionVault.decrypt_field(row.encrypted_phone)
            except Exception as exc:
                raise PermissionDenied("PII_DECRYPTION_FAILED") from exc
            result.append(
                {
                    "id": row.id,
                    "first_name": row.first_name,
                    "last_name": row.last_name,
                    "middle_name": row.middle_name,
                    "masked_passport": _mask_passport(passport),
                    "masked_phone": _mask_phone(phone),
                }
            )
        return result
