from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

import django.utils.timezone as tz
import redis
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connections, models, transaction

if TYPE_CHECKING:
    from django.db.models.manager import Manager

GENESIS_HASH = "GENESIS_SALT_VAULT_DOCKER_SECRET"
AUDIT_CHAIN_LOCK_KEY = 0x434F4D504C454354


class AuditLogChain(models.Model):
    objects: Manager["AuditLogChain"]
    id = models.BigAutoField(primary_key=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    payload = models.JSONField()
    previous_hash = models.CharField(max_length=64)
    current_hash = models.CharField(max_length=64, db_index=True)

    class Meta:
        db_table = "common_audit_chain"
        ordering = ["id"]


class AuditIntegrityEngine:
    @staticmethod
    def _append_locked(payload_data: Mapping[str, object], *, using: str = "default") -> AuditLogChain:
        connection = connections[using]
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [AUDIT_CHAIN_LOCK_KEY])
            last_log = AuditLogChain.objects.using(using).order_by("-id").values("current_hash").first()
            previous_hash = last_log["current_hash"] if last_log else GENESIS_HASH
            timestamp = tz.now()
            normalized_payload = json.dumps(
                dict(payload_data), sort_keys=True, ensure_ascii=False, separators=(",", ":")
            )
            digest_source = f"{previous_hash}:{normalized_payload}:{timestamp.isoformat()}"
            current_hash = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
            cursor.execute(
                "INSERT INTO common_audit_chain (created_at, payload, previous_hash, current_hash) "
                "VALUES (%s, %s::jsonb, %s, %s) RETURNING id",
                [timestamp, normalized_payload, previous_hash, current_hash],
            )
            row_id = cursor.fetchone()[0]
        return AuditLogChain.objects.using(using).get(pk=row_id)

    @staticmethod
    def append_log(payload_data: Mapping[str, object], *, using: str = "default") -> AuditLogChain:
        with transaction.atomic(using=using):
            return AuditIntegrityEngine._append_locked(payload_data, using=using)

    @staticmethod
    def append_read_and_commit(payload_data: Mapping[str, object], *, using: str = "default") -> AuditLogChain:
        connection = connections[using]
        if not connection.get_autocommit() or connection.in_atomic_block:
            raise PermissionError("PII_AUDIT_REQUIRES_DURABLE_BOUNDARY")
        try:
            with transaction.atomic(using=using, durable=True):
                return AuditIntegrityEngine._append_locked(payload_data, using=using)
        except DatabaseError:
            raise PermissionError("PII_AUDIT_UNAVAILABLE") from None

    @staticmethod
    def run_nightly_integrity_verifier() -> bool:
        logs = list(AuditLogChain.objects.all().order_by("id"))
        expected_previous_hash = GENESIS_HASH
        for log in logs:
            if log.previous_hash != expected_previous_hash:
                redis.Redis.from_url(settings.REDIS_DEFAULT_URL).set("flag:kill_switch:finance_disabled", "TRUE")
                raise ValidationError(f"CRITICAL_AUDIT_CHAIN_TAMPERING_DETECTED ON_LOG_ID={log.id}")
            recalculated_source = (
                f"{log.previous_hash}:"
                f"{json.dumps(log.payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))}:"
                f"{log.created_at.isoformat()}"
            )
            recalculated_hash = hashlib.sha256(recalculated_source.encode("utf-8")).hexdigest()
            if recalculated_hash != log.current_hash:
                raise ValidationError(f"CRITICAL_AUDIT_HASH_MISMATCH ON_LOG_ID={log.id}")
            expected_previous_hash = log.current_hash
        return True
