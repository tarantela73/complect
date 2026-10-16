from __future__ import annotations

import json

import pytest
from django.db import connection, transaction

from apps.common.security.audit import GENESIS_HASH, AuditIntegrityEngine, AuditLogChain


@pytest.mark.django_db(transaction=True)
def test_audit_append_uses_plain_select_tail_and_persists_exact_row() -> None:
    payload = {"event": "regression", "unicode": "Иван"}
    previous = AuditLogChain.objects.order_by("-id").first()
    row = AuditIntegrityEngine.append_read_and_commit(payload)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT created_at, payload, previous_hash, current_hash FROM common_audit_chain WHERE id = %s",
            [row.id],
        )
        created_at, stored_payload, previous_hash, current_hash = cursor.fetchone()
    assert json.loads(stored_payload) == payload
    assert previous_hash == (previous.current_hash if previous else GENESIS_HASH)
    assert current_hash == row.current_hash
    assert created_at == row.created_at


@pytest.mark.django_db(transaction=True)
def test_durable_pii_audit_rejects_ambient_atomic_before_append() -> None:
    before = AuditLogChain.objects.count()
    with pytest.raises(PermissionError, match="PII_AUDIT_REQUIRES_DURABLE_BOUNDARY"):
        with transaction.atomic():
            AuditIntegrityEngine.append_read_and_commit({"event": "must-reject"})
    assert AuditLogChain.objects.count() == before
