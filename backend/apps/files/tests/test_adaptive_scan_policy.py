"""
AdaptiveScanPolicy unit tests.

Verifies Redis LIST-backed rescan queue: push, pop, size, batch limit, FIFO order.
All tests clean up _RESCAN_QUEUE_KEY before and after to prevent state leakage.
"""

from __future__ import annotations

import uuid

import pytest

from apps.files.services.adaptive_scan_policy import (
    _RESCAN_QUEUE_KEY,
    get_adaptive_scan_policy,
)


@pytest.fixture(autouse=True)
def clear_rescan_queue():
    import redis
    from django.conf import settings

    r = redis.Redis.from_url(settings.REDIS_CACHE_URL, decode_responses=True)
    r.delete(_RESCAN_QUEUE_KEY)
    yield
    r.delete(_RESCAN_QUEUE_KEY)


class TestRescanQueueOperations:
    def test_push_enqueues_item(self):
        policy = get_adaptive_scan_policy()

        policy.push_rescan(file_id=uuid.uuid4(), tenant_id=uuid.uuid4())

        assert policy.rescan_queue_size() == 1

    def test_pop_returns_pushed_item(self):
        policy = get_adaptive_scan_policy()
        file_id = uuid.uuid4()
        tenant_id = uuid.uuid4()

        policy.push_rescan(file_id=file_id, tenant_id=tenant_id)
        items = policy.pop_rescan_batch(1)

        assert len(items) == 1
        assert items[0]["file_id"] == str(file_id)
        assert items[0]["tenant_id"] == str(tenant_id)

    def test_pop_removes_items_from_queue(self):
        policy = get_adaptive_scan_policy()
        policy.push_rescan(file_id=uuid.uuid4(), tenant_id=uuid.uuid4())

        policy.pop_rescan_batch(1)

        assert policy.rescan_queue_size() == 0

    def test_pop_batch_respects_size_limit(self):
        policy = get_adaptive_scan_policy()
        for _ in range(5):
            policy.push_rescan(file_id=uuid.uuid4(), tenant_id=uuid.uuid4())

        items = policy.pop_rescan_batch(3)

        assert len(items) == 3
        assert policy.rescan_queue_size() == 2

    def test_pop_empty_queue_returns_empty_list(self):
        policy = get_adaptive_scan_policy()

        assert policy.pop_rescan_batch(10) == []

    def test_rescan_queue_size_zero_on_empty(self):
        assert get_adaptive_scan_policy().rescan_queue_size() == 0

    def test_push_multiple_preserves_fifo_order(self):
        policy = get_adaptive_scan_policy()
        ids = [uuid.uuid4() for _ in range(3)]
        tenant_id = uuid.uuid4()

        for fid in ids:
            policy.push_rescan(file_id=fid, tenant_id=tenant_id)

        items = policy.pop_rescan_batch(3)

        assert [uuid.UUID(i["file_id"]) for i in items] == ids
