# backend/apps/common/tasks/__init__.py
import structlog
from celery import Task

logger = structlog.get_logger(__name__)


class BaseEnterpriseTask(Task):
    abstract = True
    max_retries = 5
    default_retry_delay = 60

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        import json

        from django.conf import settings

        headers = getattr(self.request, "headers", {}) or {}

        correlation_id = getattr(self.request, "correlation_id", None) or headers.get("correlation_id")
        # causal_root_id: propagated from the outbox stream payload or task headers (ADR-0015 Invariant 2).
        # Preserves logical causal lineage across DLQ → replay flows without mutation.
        causal_root_id = getattr(self.request, "causal_root_id", None) or headers.get("causal_root_id")

        try:
            redis_client = settings.REDIS_CLIENT
            redis_client.xadd(
                name=settings.DLQ_STREAM_NAME,
                fields={
                    "task_name": self.name,
                    "task_id": task_id,
                    "args": json.dumps(args),
                    "kwargs": json.dumps(kwargs),
                    "exception_class": exc.__class__.__name__,
                    "exception_message": str(exc),
                    "traceback": einfo.traceback if einfo else "",
                    "correlation_id": str(correlation_id) if correlation_id else "",
                    "causal_root_id": str(causal_root_id) if causal_root_id else "",
                    "retries": str(self.request.retries),
                },
                id="*",
                maxlen=10000,
                approximate=True,
            )
        except Exception as e:
            logger.error("DLQ_WRITE_FAILED", error=str(e))

        super().on_failure(exc, task_id, args, kwargs, einfo)
