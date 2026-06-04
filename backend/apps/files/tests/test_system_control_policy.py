"""
SystemControlPolicy unit tests.

Verifies:
  - evaluate() selects the correct batch_size rule based on signals.
  - Rule priority: backlog > CB rate > scan latency > nominal.
  - record_scan_latency() maintains EWMA correctly.
  - get_batch_size() falls back to default when key absent.
  - CB open events counter incremented when circuit opens.
"""
from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from apps.files.services.clamav_circuit_breaker import (
    _CB_FAILURES_KEY,
    _CB_OPEN_EVENTS_KEY,
    _CB_OPEN_KEY,
    get_circuit_breaker,
)
from apps.files.services.system_control_policy import (
    _BATCH_SIZE_KEY,
    _SCAN_MS_EWMA_KEY,
    SystemControlPolicy,
    get_system_control_policy,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_control_state():
    from django.core.cache import cache

    keys = [_CB_OPEN_KEY, _CB_FAILURES_KEY, _CB_OPEN_EVENTS_KEY, _SCAN_MS_EWMA_KEY, _BATCH_SIZE_KEY]
    for k in keys:
        cache.delete(k)
    yield
    for k in keys:
        cache.delete(k)


def _policy(
    *,
    batch_size_default: int = 100,
    batch_size_reduced: int = 25,
    batch_size_throttled: int = 10,
    cb_rate_threshold: int = 3,
    scan_latency_threshold_ms: float = 5000.0,
    backlog_throttle_threshold: int = 100,
    scan_ewma_alpha: float = 0.3,
    scan_ewma_ttl: int = 60,
    param_ttl: int = 60,
) -> SystemControlPolicy:
    return SystemControlPolicy(
        batch_size_default=batch_size_default,
        batch_size_reduced=batch_size_reduced,
        batch_size_throttled=batch_size_throttled,
        cb_rate_threshold=cb_rate_threshold,
        scan_latency_threshold_ms=scan_latency_threshold_ms,
        backlog_throttle_threshold=backlog_throttle_threshold,
        scan_ewma_alpha=scan_ewma_alpha,
        scan_ewma_ttl=scan_ewma_ttl,
        param_ttl=param_ttl,
    )


# ---------------------------------------------------------------------------
# Policy rules
# ---------------------------------------------------------------------------


class TestSystemControlPolicyRules:
    def test_nominal_sets_default_batch_size(self):
        p = _policy()
        p.evaluate()
        assert p.get_batch_size() == 100

    def test_high_rescan_backlog_throttles(self):
        import redis
        from django.conf import settings

        from apps.files.services.adaptive_scan_policy import _RESCAN_QUEUE_KEY

        r = redis.Redis.from_url(settings.REDIS_CACHE_URL, decode_responses=True)
        # Push 101 items to backlog
        for _ in range(101):
            r.lpush(_RESCAN_QUEUE_KEY, '{"file_id":"x","tenant_id":"y"}')
        try:
            p = _policy(backlog_throttle_threshold=100)
            p.evaluate()
            assert p.get_batch_size() == 10
        finally:
            r.delete(_RESCAN_QUEUE_KEY)

    def test_high_cb_rate_throttles(self):
        from django.core.cache import cache

        cache.set(_CB_OPEN_EVENTS_KEY, 3, timeout=60)
        p = _policy(cb_rate_threshold=3)
        p.evaluate()
        assert p.get_batch_size() == 10

    def test_high_scan_latency_reduces(self):
        from django.core.cache import cache

        cache.set(_SCAN_MS_EWMA_KEY, 6000.0, timeout=60)
        p = _policy(scan_latency_threshold_ms=5000.0)
        p.evaluate()
        assert p.get_batch_size() == 25

    def test_backlog_rule_beats_scan_latency(self):
        import redis
        from django.conf import settings
        from django.core.cache import cache

        from apps.files.services.adaptive_scan_policy import _RESCAN_QUEUE_KEY

        cache.set(_SCAN_MS_EWMA_KEY, 9000.0, timeout=60)  # would trigger REDUCED
        r = redis.Redis.from_url(settings.REDIS_CACHE_URL, decode_responses=True)
        for _ in range(101):
            r.lpush(_RESCAN_QUEUE_KEY, '{"file_id":"x","tenant_id":"y"}')
        try:
            p = _policy(backlog_throttle_threshold=100, scan_latency_threshold_ms=5000.0)
            p.evaluate()
            assert p.get_batch_size() == 10  # THROTTLED, not REDUCED
        finally:
            r.delete(_RESCAN_QUEUE_KEY)

    def test_cb_rate_beats_scan_latency(self):
        from django.core.cache import cache

        cache.set(_SCAN_MS_EWMA_KEY, 9000.0, timeout=60)  # would trigger REDUCED
        cache.set(_CB_OPEN_EVENTS_KEY, 3, timeout=60)      # triggers THROTTLED
        p = _policy(cb_rate_threshold=3, scan_latency_threshold_ms=5000.0)
        p.evaluate()
        assert p.get_batch_size() == 10  # THROTTLED wins

    def test_signals_clear_restores_default(self):
        from django.core.cache import cache

        cache.set(_CB_OPEN_EVENTS_KEY, 3, timeout=60)
        p = _policy(cb_rate_threshold=3)
        p.evaluate()
        assert p.get_batch_size() == 10

        cache.delete(_CB_OPEN_EVENTS_KEY)
        p.evaluate()
        assert p.get_batch_size() == 100

    def test_log_emitted_when_batch_size_changes(self):
        from django.core.cache import cache

        cache.set(_CB_OPEN_EVENTS_KEY, 3, timeout=60)
        p = _policy(cb_rate_threshold=3)
        p.evaluate()  # sets to 10

        cache.delete(_CB_OPEN_EVENTS_KEY)
        with capture_logs() as log:
            p.evaluate()  # changes from 10 → 100

        events = [e for e in log if e.get("event") == "files_system_control_adjusted"]
        assert len(events) == 1
        assert events[0]["new_batch_size"] == 100
        assert events[0]["old_batch_size"] == 10

    def test_no_log_when_batch_size_unchanged(self):
        p = _policy()
        p.evaluate()  # nominal → 100
        with capture_logs() as log:
            p.evaluate()  # still nominal → 100

        events = [e for e in log if e.get("event") == "files_system_control_adjusted"]
        assert len(events) == 0


# ---------------------------------------------------------------------------
# Scan latency EWMA
# ---------------------------------------------------------------------------


class TestScanLatencyEWMA:
    def test_first_sample_initialises_ewma(self):
        from django.core.cache import cache

        p = _policy(scan_ewma_alpha=0.3)
        p.record_scan_latency(2000.0)

        assert cache.get(_SCAN_MS_EWMA_KEY) == pytest.approx(2000.0)

    def test_ewma_smooths_subsequent_samples(self):
        from django.core.cache import cache

        p = _policy(scan_ewma_alpha=0.3)
        p.record_scan_latency(1000.0)  # EWMA = 1000
        p.record_scan_latency(2000.0)  # EWMA = 0.3*2000 + 0.7*1000 = 1300

        assert cache.get(_SCAN_MS_EWMA_KEY) == pytest.approx(1300.0)

    def test_ewma_drives_reduce_rule(self):
        p = _policy(scan_latency_threshold_ms=1000.0, scan_ewma_alpha=1.0)
        p.record_scan_latency(2000.0)  # EWMA = 2000 (α=1)
        p.evaluate()
        assert p.get_batch_size() == 25


# ---------------------------------------------------------------------------
# Batch size control parameter
# ---------------------------------------------------------------------------


class TestBatchSizeControl:
    def test_get_batch_size_returns_default_when_key_absent(self):
        assert _policy(batch_size_default=100).get_batch_size() == 100

    def test_get_batch_size_returns_stored_value(self):
        from django.core.cache import cache

        cache.set(_BATCH_SIZE_KEY, 42, timeout=60)
        assert _policy().get_batch_size() == 42

    def test_factory_reads_settings(self):
        from django.test import override_settings

        with override_settings(FILES_CONTROL_BATCH_SIZE_DEFAULT=77):
            p = get_system_control_policy()
        assert p.batch_size_default == 77


# ---------------------------------------------------------------------------
# CB open events counter
# ---------------------------------------------------------------------------


class TestCBOpenEventsTracking:
    def test_open_events_incremented_when_circuit_opens(self):
        from django.core.cache import cache
        from django.test import override_settings

        with override_settings(CLAMAV_CB_FAILURE_THRESHOLD=1, CLAMAV_CB_RECOVERY_TTL=60):
            cb = get_circuit_breaker()
            cb.record_failure()  # opens

        assert cache.get(_CB_OPEN_EVENTS_KEY) == 1

    def test_open_events_accumulate_across_opens(self):
        from django.core.cache import cache
        from django.test import override_settings

        with override_settings(CLAMAV_CB_FAILURE_THRESHOLD=1, CLAMAV_CB_RECOVERY_TTL=60):
            cb = get_circuit_breaker()
            cb.record_failure()  # opens; count=1
            cb.record_success()  # closes
            cb.record_failure()  # opens again; count=2

        assert cache.get(_CB_OPEN_EVENTS_KEY) == 2

    def test_open_events_not_incremented_when_already_open(self):
        from django.core.cache import cache
        from django.test import override_settings

        with override_settings(CLAMAV_CB_FAILURE_THRESHOLD=1, CLAMAV_CB_RECOVERY_TTL=60):
            cb = get_circuit_breaker()
            cb.record_failure()  # opens; count=1
            cb.record_failure()  # OPEN, ignored; count still 1

        assert cache.get(_CB_OPEN_EVENTS_KEY) == 1
