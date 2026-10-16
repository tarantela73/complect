"""Operational collectors exercise the configured synchronous Redis client."""

import json
import os
import uuid
from urllib.parse import urlsplit

import pytest
from django.conf import settings
from redis import Redis

from apps.common.context.core import ContextCore, is_primary_db_enforced
from apps.common.infra.redis import get_default_redis
from apps.common.services.dlq_service import AdminDlqManagementService
from apps.common.services.sre_manager import SreInfrastructureGovernanceManager
from apps.common.slo.metrics import collect_celery_queue_depths, collect_dlq_count, collect_ws_connections
from apps.common.slo.state_tracker import get_error_budget


@pytest.fixture(autouse=True)
def require_owned_disposable_redis():
    host = urlsplit(settings.REDIS_DEFAULT_URL).hostname or ""
    run_id = uuid.UUID(host.removeprefix("complect-iam-test-"))
    assert host == f"complect-iam-test-{run_id}"
    assert os.environ.get("IAM_TEST_DATABASE") == f"test_iam_5_8_{run_id.hex}"
    assert urlsplit(settings.REDIS_CELERY_URL).hostname == host


def test_budget_reads_real_redis_list():
    client = get_default_redis()
    name = str(uuid.uuid4())
    key = f"slo:budget:{name}"
    try:
        assert get_error_budget(name) == 1.0
        client.rpush(key, "1", "0", "1")
        assert get_error_budget(name) == 0.667
    finally:
        client.delete(key)


def test_queue_depth_reads_real_redis_integer():
    client = Redis.from_url(settings.REDIS_CELERY_URL, decode_responses=True)
    queue = "retention"
    try:
        client.delete(queue)
        client.rpush(queue, "test-message", "test-message")
        assert collect_celery_queue_depths()[queue] == 2
    finally:
        client.delete(queue)
        client.close()


def test_websocket_count_reads_real_redis_string():
    client = get_default_redis()
    key = "ws:active_connections"
    try:
        client.delete(key)
        assert collect_ws_connections() == 0
        client.set(key, "7")
        assert collect_ws_connections() == 7
    finally:
        client.delete(key)


def test_dlq_inspection_reads_real_redis_stream():
    client = get_default_redis()
    stream = AdminDlqManagementService.STREAM_NAME
    marker = str(uuid.uuid4())
    fields = {
        "task_id": marker,
        "task_name": "test.operational_collector",
        "args": json.dumps([]),
        "kwargs": json.dumps({}),
        "exception_class": "TestError",
        "exception_message": "test failure",
        "correlation_id": marker,
    }
    entry = client.xadd(stream, fields)
    try:
        assert collect_dlq_count() >= 1
        records = AdminDlqManagementService.inspect_dead_letters()
        assert any(record["task_id"] == marker and record["args"] == [] for record in records)
    finally:
        client.xdel(stream, entry)


def test_replica_health_routes_only_current_context_to_primary():
    client = settings.REDIS_CLIENT
    key = SreInfrastructureGovernanceManager.REPLICA_HEALTH_KEY
    token = ContextCore.force_primary.set(False)
    try:
        SreInfrastructureGovernanceManager.enforce_replica_lag_protection(2.0)
        assert client.get(key) == "DEGRADED"
        assert is_primary_db_enforced()
    finally:
        client.delete(key)
        ContextCore.force_primary.reset(token)
