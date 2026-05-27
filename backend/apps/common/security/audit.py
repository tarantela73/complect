import hashlib
import json

import django.utils.timezone as tz
import redis

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction


GENESIS_HASH = "GENESIS_SALT_VAULT_DOCKER_SECRET"


class AuditLogChain(models.Model):
    id = models.BigAutoField(primary_key=True)

    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    payload = models.JSONField()

    previous_hash = models.CharField(
        max_length=64,
    )

    current_hash = models.CharField(
        max_length=64,
        db_index=True,
    )

    class Meta:
        db_table = "common_audit_chain"

        ordering = ["id"]


class AuditIntegrityEngine:
    @staticmethod
    def append_log(payload_data: dict) -> AuditLogChain:
        """
        Append-only audit chain.
        """

        with transaction.atomic():
            last_log = AuditLogChain.objects.select_for_update().order_by("-id").first()

            previous_hash = last_log.current_hash if last_log else GENESIS_HASH

            timestamp = tz.now().isoformat()

            normalized_payload = json.dumps(
                payload_data,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )

            digest_source = f"{previous_hash}:{normalized_payload}:{timestamp}"

            current_hash = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()

            return AuditLogChain.objects.create(
                payload=payload_data,
                previous_hash=previous_hash,
                current_hash=current_hash,
            )

    @staticmethod
    def run_nightly_integrity_verifier() -> bool:
        """
        Full audit-chain verification.
        """

        logs = list(AuditLogChain.objects.all().order_by("id"))

        expected_previous_hash = GENESIS_HASH

        for log in logs:
            if log.previous_hash != expected_previous_hash:
                redis_client = redis.Redis.from_url(settings.REDIS_URL)

                redis_client.set(
                    "flag:kill_switch:finance_disabled",
                    "TRUE",
                )

                raise ValidationError((f"CRITICAL_AUDIT_CHAIN_TAMPERING_DETECTED ON_LOG_ID={log.id}"))

            recalculated_source = (
                f"{log.previous_hash}:"
                f"{json.dumps(log.payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))}:"
                f"{log.created_at.isoformat()}"
            )

            recalculated_hash = hashlib.sha256(recalculated_source.encode("utf-8")).hexdigest()

            if recalculated_hash != log.current_hash:
                raise ValidationError((f"CRITICAL_AUDIT_HASH_MISMATCH ON_LOG_ID={log.id}"))

            expected_previous_hash = log.current_hash

        return True
