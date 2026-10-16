"""RED/GREEN checks through existing public audit and crypto seams."""
import hashlib
import json

import pytest
from django.db import connection, models

from apps.common.security.audit import AuditIntegrityEngine
from apps.common.security.pii_vault import PiiEncryptionVault

pytestmark = pytest.mark.django_db(transaction=True)


def test_existing_append_contract_produces_a_verifiable_persisted_hash():
    row = AuditIntegrityEngine.append_log({"event": "existing_contract_probe"})
    payload = json.dumps(row.payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(f"{row.previous_hash}:{payload}:{row.created_at.isoformat()}".encode()).hexdigest()
    assert row.current_hash == digest
    assert AuditIntegrityEngine.run_nightly_integrity_verifier()


def test_existing_bytes_writer_textfield_reader_postgresql_roundtrip(ciphertext_table):
    raw = "Синтетический документ 🙂"
    cipher = PiiEncryptionVault.encrypt_field(raw)
    with ciphertext_table("old_person_ciphertext"), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO old_person_ciphertext(value) VALUES (%s)",
            [models.TextField().get_prep_value(cipher)],
        )
        cursor.execute("SELECT value FROM old_person_ciphertext")
        stored = cursor.fetchone()[0]
    assert PiiEncryptionVault.decrypt_field(stored) == raw
