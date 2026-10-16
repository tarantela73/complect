"""Known legacy transport facade, with durable quarantine before acknowledgement."""

from __future__ import annotations

import uuid
from typing import Any, Protocol, cast

import structlog
from celery import Task, shared_task
from celery.exceptions import Ignore, Reject
from celery.worker import state as worker_state
from celery.worker.request import Request

from apps.common.contracts.legacy_document_contracts import (
    RecoveryPersistenceError,
    get_legacy_compatibility_service,
)
from apps.common.services.legacy_recovery_parking import TASK_NAME, park

logger = structlog.get_logger(__name__)


class WorkerShutdownState(Protocol):
    # Celery initializes this public control flag to None and later assigns
    # an exit code. Its inferred vendor type incorrectly fixes it to None.
    should_stop: int | None


class LegacyCompatibilityRequest(Request):
    """A dual durability outage parks the delivery and stops this worker."""

    def on_failure(self, exc_info: Any, send_failed_event: bool = True, return_ok: bool = False) -> None:
        exc = exc_info.exception
        exc = getattr(exc, "exc", exc)
        if isinstance(exc, Reject) and exc.args and exc.args[0] == "LEGACY_RECOVERY_PERSISTENCE_UNAVAILABLE":
            # Signal the parent worker before putting the message back. It must
            # be restarted explicitly after persistence health is restored.
            cast(WorkerShutdownState, worker_state).should_stop = 78
        super().on_failure(exc_info, send_failed_event=send_failed_event, return_ok=return_ok)


class LegacyCompatibilityTask(Task):
    abstract = True
    Request = LegacyCompatibilityRequest


class FilePipelineService:
    @classmethod
    def initialize_upload_pipeline(cls, doc_id: uuid.UUID) -> None:
        get_legacy_compatibility_service().submit(doc_id)


def _park_recovery_envelope(task: Any, cause: Exception) -> None:
    args, kwargs = tuple(task.request.args or ()), dict(task.request.kwargs or {})
    doc_id = str(args[0]) if len(args) == 1 else str(kwargs.get("doc_id", ""))
    park(str(task.request.id), doc_id, args, kwargs, dict(task.request.headers or {}), str(cause))


@shared_task(
    bind=True,
    base=LegacyCompatibilityTask,
    name=TASK_NAME,
    queue="files",
    acks_late=True,
    acks_on_failure_or_timeout=False,
    reject_on_worker_lost=True,
    track_started=True,
)
def run_antivirus_scan_task(self: Any, *args: object, **kwargs: object) -> str:
    original_args, original_kwargs = tuple(self.request.args or args), dict(self.request.kwargs or kwargs)
    valid_positional = len(original_args) == 1 and not original_kwargs
    valid_keyword = not original_args and set(original_kwargs) == {"doc_id"}
    doc_id = original_args[0] if valid_positional else original_kwargs.get("doc_id")
    try:
        if not (valid_positional or valid_keyword) or not isinstance(doc_id, str):
            raise RecoveryPersistenceError("LEGACY_ENVELOPE_SHAPE_UNSUPPORTED")
        uuid.UUID(doc_id)
        headers = self.request.headers or {}
        metadata = {
            key: headers[key]
            for key in (
                "compat_tenant",
                "compat_token",
                "correlation_id",
                "causal_root_id",
            )
            if key in headers
        }
        return get_legacy_compatibility_service().run(
            doc_id,
            str(self.request.id),
            original_args,
            original_kwargs,
            metadata,
        )
    except Ignore as exc:
        try:
            self.update_state(state="LEGACY_RECOVERY_REQUIRED", meta={"code": str(exc)})
        except Exception:
            logger.error("LEGACY_RESULT_BACKEND_UNAVAILABLE")
        raise
    except (RecoveryPersistenceError, ValueError) as exc:
        try:
            _park_recovery_envelope(self, exc)
        except Exception as park_exc:
            logger.error("BLOCKED_DOUBLE_DURABILITY_FAILURE", error=park_exc.__class__.__name__)
            raise Reject("LEGACY_RECOVERY_PERSISTENCE_UNAVAILABLE", requeue=True) from park_exc
        try:
            self.update_state(state="LEGACY_UNRESOLVED", meta={"code": "LEGACY_RECOVERY_PARKED"})
        except Exception:
            logger.error("LEGACY_RESULT_BACKEND_UNAVAILABLE")
        # Ignore does ACK a late-ack task. It is used only after durable transport storage.
        raise Ignore("LEGACY_RECOVERY_PARKED") from exc
