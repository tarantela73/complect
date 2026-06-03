import json

import structlog
from django.conf import settings

from apps.common.tenancy.context import get_sre_context
from config.celery import app as celery_app

logger = structlog.get_logger(__name__)


class AdminDlqManagementService:
    STREAM_NAME = "infrastructure:dlq:stream"
    REPLAY_GUARD_PREFIX = "dlq:replay:guard:"

    @classmethod
    def inspect_dead_letters(cls, count: int = 50, start_id: str = "-") -> list[dict]:

        redis_client = settings.REDIS_CLIENT

        raw_records = redis_client.xrange(cls.STREAM_NAME, min=start_id, max="+", count=count)

        return [
            {
                "sequence_id": seq_id,
                "task_id": fields["task_id"],
                "task_name": fields["task_name"],
                "args": json.loads(fields["args"]),
                "kwargs": json.loads(fields["kwargs"]),
                "exception_class": fields["exception_class"],
                "exception_message": fields["exception_message"],
                "failed_at": fields.get("failed_at", ""),
                "correlation_id": fields["correlation_id"],
                # causal_root_id: logical chain root from ADR-0015. Empty string = pre-ADR-0015 record.
                "causal_root_id": fields.get("causal_root_id", ""),
            }
            for seq_id, fields in raw_records
        ]

    @classmethod
    def execute_task_replay(cls, sequence_id: str) -> None:

        sre_ctx = get_sre_context()
        redis_client = settings.REDIS_CLIENT

        # -------------------------
        # 1. REPLAY DEDUP GUARD
        # -------------------------
        guard_key = cls.REPLAY_GUARD_PREFIX + sequence_id

        if redis_client.set(guard_key, "1", nx=True, ex=300) is False:
            raise ValueError("Replay already in progress or executed")

        # -------------------------
        # 2. FETCH EXACT RECORD
        # -------------------------
        raw = redis_client.xrange(cls.STREAM_NAME, min=sequence_id, max=sequence_id, count=1)

        if not raw:
            raise ValueError("Task not found or already processed")

        _, fields = raw[0]
        task_name = fields["task_name"]
        args = json.loads(fields["args"])
        kwargs = json.loads(fields["kwargs"])
        task_id = fields["task_id"]
        corr_id = fields["correlation_id"]
        # Propagate causal_root_id unchanged so the replayed task inherits the original
        # causal chain (ADR-0015 Invariant 2: DLQ Causal Preservation).
        causal_root_id = fields.get("causal_root_id", "")

        logger.info(
            "DLQ_REPLAY_START",
            task_name=task_name,
            sequence_id=sequence_id,
            triggered_by=sre_ctx.get("user_id"),
            causal_root_id=causal_root_id or None,
        )

        # -------------------------
        # 3. DISPATCH TO CELERY
        # -------------------------
        headers: dict = {"correlation_id": corr_id}
        if causal_root_id:
            headers["causal_root_id"] = causal_root_id

        celery_app.send_task(
            name=task_name,
            args=args,
            kwargs=kwargs,
            task_id=task_id,
            headers=headers,
        )

        # -------------------------
        # 4. SAFE DELETE (after dispatch)
        # -------------------------
        redis_client.xdel(cls.STREAM_NAME, sequence_id)
