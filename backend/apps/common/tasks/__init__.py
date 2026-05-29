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

        correlation_id = getattr(self.request, "correlation_id", None)
        if not correlation_id:
            headers = getattr(self.request, "headers", {}) or {}
            correlation_id = headers.get("correlation_id")

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
                    "retries": str(self.request.retries),
                },
                id="*",
                maxlen=10000,
                approximate=True,
            )
        except Exception as e:
            logger.error("DLQ_WRITE_FAILED", error=str(e))

        super().on_failure(exc, task_id, args, kwargs, einfo)
