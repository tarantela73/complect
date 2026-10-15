import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.utils import timezone as tz

import apps.common.tasks.outbox_dispatcher as dispatcher_module
from apps.common.events.contracts import ContractClosedEvent
from apps.common.models.outbox import OutboxRecord
from apps.common.services.outbox_service import TransactionalOutboxService
from apps.common.tasks.outbox_dispatcher import (
    MAX_PUBLISH_RETRIES,
    run_outbox_polling_dispatcher_task,
)


def _make_event() -> ContractClosedEvent:
    return ContractClosedEvent.create(
        correlation_id=uuid.uuid4(),
        contract_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        remaining_amount=0,
    )


def _push() -> OutboxRecord:
    TransactionalOutboxService.push_to_outbox(_make_event())
    return OutboxRecord.objects.filter(is_processed=False).order_by("-created_at").first()


@pytest.mark.django_db(transaction=True)
class TestOutboxReliabilityMesh:
    # ── at-least-once delivery ────────────────────────────────────────────────

    def test_record_retained_on_redis_failure(self):
        """On Redis failure the DB record stays is_processed=False for retry."""
        _push()
        assert OutboxRecord.objects.filter(is_processed=False).count() == 1

        with patch.object(settings.REDIS_CLIENT, "xadd", side_effect=ConnectionError("Redis down")):
            run_outbox_polling_dispatcher_task()

        assert OutboxRecord.objects.filter(is_processed=False, failed_permanently_at__isnull=True).count() == 1
        assert OutboxRecord.objects.filter(is_processed=True).count() == 0

    def test_retry_count_incremented_on_failure(self):
        """Each failed dispatch attempt increments retry_count and saves last_error."""
        record = _push()

        with patch.object(settings.REDIS_CLIENT, "xadd", side_effect=ConnectionError("net err")):
            run_outbox_polling_dispatcher_task()

        record.refresh_from_db()
        assert record.retry_count == 1
        assert "net err" in record.last_error
        assert record.is_processed is False
        assert record.failed_permanently_at is None

    # ── permanent failure after MAX_PUBLISH_RETRIES ───────────────────────────

    def test_permanently_failed_after_max_retries(self):
        """After MAX_PUBLISH_RETRIES failures the record is marked permanently failed."""
        record = _push()
        t = tz.now()

        with patch.object(settings.REDIS_CLIENT, "xadd", side_effect=ConnectionError("Redis down")):
            for i in range(MAX_PUBLISH_RETRIES):
                # Advance clock by 1h per step — collapses any backoff window cleanly.
                with patch.object(dispatcher_module, "_clock", new=lambda _i=i: t + timedelta(hours=_i + 1)):
                    run_outbox_polling_dispatcher_task()

        record.refresh_from_db()
        assert record.retry_count == MAX_PUBLISH_RETRIES
        assert record.failed_permanently_at is not None
        assert record.is_processed is False

    def test_permanently_failed_record_excluded_from_polling(self):
        """Records with failed_permanently_at set are never picked up again."""
        record = _push()
        t = tz.now()

        with patch.object(settings.REDIS_CLIENT, "xadd", side_effect=ConnectionError("Redis down")):
            for i in range(MAX_PUBLISH_RETRIES):
                with patch.object(dispatcher_module, "_clock", new=lambda _i=i: t + timedelta(hours=_i + 1)):
                    run_outbox_polling_dispatcher_task()

        record.refresh_from_db()
        assert record.failed_permanently_at is not None

        # Subsequent runs must not touch the record
        xadd_calls = []
        with patch.object(settings.REDIS_CLIENT, "xadd", side_effect=lambda *a, **kw: xadd_calls.append(1)):
            run_outbox_polling_dispatcher_task()

        assert xadd_calls == [], "Permanently failed record was retried — should be excluded"

    # ── batch isolation ───────────────────────────────────────────────────────

    def test_one_failed_record_does_not_block_batch(self):
        """A failing record must not prevent healthy records in the same batch from publishing."""
        bad_record = _push()
        good_record = _push()

        publish_log: list[str] = []
        original_xadd = settings.REDIS_CLIENT.xadd

        def selective_xadd(name, fields, **kwargs):
            record_id = fields.get("outbox_record_id", "")
            if record_id == str(bad_record.id):
                raise ConnectionError("Redis intermittent failure")
            publish_log.append(record_id)
            return original_xadd(name, fields, **kwargs)

        with patch.object(settings.REDIS_CLIENT, "xadd", side_effect=selective_xadd):
            run_outbox_polling_dispatcher_task()

        good_record.refresh_from_db()
        bad_record.refresh_from_db()

        assert good_record.is_processed is True
        assert bad_record.is_processed is False
        assert bad_record.retry_count == 1
        assert str(good_record.id) in publish_log

    # ── deduplication key ─────────────────────────────────────────────────────

    def test_outbox_record_id_included_in_stream_message(self):
        """outbox_record_id must be in every Redis Stream message for consumer-side dedup."""
        record = _push()

        published_fields: dict = {}

        def capture_xadd(name, fields, **kwargs):
            published_fields.update(fields)
            return b"0-0"

        with patch.object(settings.REDIS_CLIENT, "xadd", side_effect=capture_xadd):
            run_outbox_polling_dispatcher_task()

        assert "outbox_record_id" in published_fields
        assert published_fields["outbox_record_id"] == str(record.id)

    def test_stream_message_contains_all_required_fields(self):
        """Stream message must carry: outbox_record_id, event_type, correlation_id, payload."""
        _push()

        published_fields: dict = {}

        def capture_xadd(name, fields, **kwargs):
            published_fields.update(fields)
            return b"0-0"

        with patch.object(settings.REDIS_CLIENT, "xadd", side_effect=capture_xadd):
            run_outbox_polling_dispatcher_task()

        for key in ("outbox_record_id", "event_type", "correlation_id", "payload"):
            assert key in published_fields, f"Missing field in stream message: {key}"

    # ── happy path ────────────────────────────────────────────────────────────

    def test_successful_dispatch_marks_processed(self):
        """On successful xadd the record is marked is_processed=True."""
        record = _push()

        run_outbox_polling_dispatcher_task()

        record.refresh_from_db()
        assert record.is_processed is True
        assert record.processed_at is not None
        assert record.retry_count == 0

    def test_already_processed_records_skipped(self):
        """Dispatcher must not re-publish already-processed records."""
        _push()
        run_outbox_polling_dispatcher_task()  # marks processed

        xadd_calls: list = []
        with patch.object(settings.REDIS_CLIENT, "xadd", side_effect=lambda *a, **kw: xadd_calls.append(1)):
            run_outbox_polling_dispatcher_task()

        assert xadd_calls == []

    # ── CONCURRENCY ───────────────────────────────────────────────────────────

    def test_dispatcher_uses_skip_locked(self):
        """
        Dispatcher must call select_for_update(skip_locked=True).
        SKIP LOCKED is the DBMS-level guarantee against deadlocks under parallel workers;
        we verify our code passes the correct kwarg.
        Full cross-connection lock semantics are guaranteed by PostgreSQL itself.
        """
        import threading

        record = _push()

        lock_held = threading.Event()
        release_lock = threading.Event()
        thread_error: list = []

        def hold_lock_in_separate_connection():
            from django.db import connections

            conn = connections.create_connection("default")
            try:
                conn.ensure_connection()
                conn.set_autocommit(False)
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM common_transactional_outbox WHERE id = %s FOR UPDATE",
                        [str(record.id)],
                    )
                    lock_held.set()
                    release_lock.wait(timeout=5)
                conn.rollback()
            except Exception as exc:
                thread_error.append(exc)
            finally:
                conn.close()

        locker = threading.Thread(target=hold_lock_in_separate_connection)
        locker.start()
        lock_held.wait(timeout=5)

        if thread_error:
            raise thread_error[0]

        xadd_calls: list = []
        with patch.object(settings.REDIS_CLIENT, "xadd", side_effect=lambda *a, **kw: xadd_calls.append(1)):
            run_outbox_polling_dispatcher_task()

        release_lock.set()
        locker.join(timeout=5)

        assert xadd_calls == [], "Locked record dispatched by concurrent worker — SKIP LOCKED not working"

        record.refresh_from_db()
        assert record.is_processed is False

    # ── BACKOFF ───────────────────────────────────────────────────────────────

    def test_failure_sets_next_retry_at(self):
        """After a failed dispatch next_retry_at must be set to a future timestamp."""
        record = _push()

        with patch.object(settings.REDIS_CLIENT, "xadd", side_effect=ConnectionError("down")):
            run_outbox_polling_dispatcher_task()

        record.refresh_from_db()
        assert record.next_retry_at is not None
        assert record.next_retry_at > tz.now()

    def test_backoff_window_respected(self):
        """Record with future next_retry_at must not be dispatched until the window expires."""
        record = _push()
        OutboxRecord.objects.filter(id=record.id).update(
            retry_count=1,
            next_retry_at=tz.now() + timedelta(hours=1),
        )

        xadd_calls: list = []
        with patch.object(settings.REDIS_CLIENT, "xadd", side_effect=lambda *a, **kw: xadd_calls.append(1)):
            run_outbox_polling_dispatcher_task()

        assert xadd_calls == [], "Record in backoff window was dispatched prematurely"
        record.refresh_from_db()
        assert record.is_processed is False

    def test_backoff_expired_record_is_dispatched(self):
        """Record with an elapsed next_retry_at must be picked up and processed normally."""
        record = _push()
        OutboxRecord.objects.filter(id=record.id).update(
            retry_count=1,
            next_retry_at=tz.now() - timedelta(seconds=1),
        )

        run_outbox_polling_dispatcher_task()

        record.refresh_from_db()
        assert record.is_processed is True
        assert record.next_retry_at is None

    # ── WAKE-UP ON INSERT ─────────────────────────────────────────────────────

    def test_push_fires_dispatcher_on_commit(self):
        """push_to_outbox must enqueue the dispatcher task after the transaction commits."""
        from django.core.cache import cache

        cache.delete("outbox:dispatch:wakeup")
        cache.delete("outbox:dispatch:inflight")

        dispatch_calls: list = []

        with patch.object(
            run_outbox_polling_dispatcher_task,
            "apply_async",
            side_effect=lambda *a, **kw: dispatch_calls.append(1),
        ):
            _push()

        assert dispatch_calls == [1], "Dispatcher was not triggered after outbox write"

    def test_inflight_guard_prevents_second_wake_up(self):
        """
        If the inflight guard key is set, a second on_commit write must not
        trigger a duplicate dispatcher — the first task is still in-flight.
        """
        from django.core.cache import cache

        cache.delete("outbox:dispatch:wakeup")
        cache.delete("outbox:dispatch:inflight")

        dispatch_calls: list = []

        with patch.object(
            run_outbox_polling_dispatcher_task,
            "apply_async",
            side_effect=lambda *a, **kw: dispatch_calls.append(1),
        ):
            _push()  # first write: wakeup + inflight set, task enqueued
            cache.delete("outbox:dispatch:wakeup")  # simulate TTL expiry of coalesce window
            _push()  # second write: wakeup acquired BUT inflight still set → no second task

        assert len(dispatch_calls) == 1, f"Expected 1 dispatch (inflight guard), got {len(dispatch_calls)}"

    def test_task_clears_inflight_guard_on_completion(self):
        """Dispatcher task must delete the inflight guard key after processing."""
        from django.core.cache import cache

        cache.set("outbox:dispatch:inflight", 1, timeout=30)
        _push()

        run_outbox_polling_dispatcher_task()

        assert cache.get("outbox:dispatch:inflight") is None, "Inflight guard was not cleared after task completed"
