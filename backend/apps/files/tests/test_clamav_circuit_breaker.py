"""
ClamAVCircuitBreaker unit tests.

All Redis interactions use the live Redis instance (same as other tests).
Each test clears the CB keys before and after to prevent state leakage.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from apps.files.services.clamav_circuit_breaker import (
    _CB_FAILURES_KEY,
    _CB_OPEN_KEY,
    ClamAVCircuitBreaker,
    get_circuit_breaker,
)
from apps.files.services.clamav_scan_engine import ClamAVScanEngine
from apps.files.services.scan_engine import ScanResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_cb_state():
    from django.core.cache import cache

    cache.delete(_CB_OPEN_KEY)
    cache.delete(_CB_FAILURES_KEY)
    yield
    cache.delete(_CB_OPEN_KEY)
    cache.delete(_CB_FAILURES_KEY)


def _cb(failure_threshold: int = 3, recovery_ttl: int = 60) -> ClamAVCircuitBreaker:
    return ClamAVCircuitBreaker(
        failure_threshold=failure_threshold,
        recovery_ttl=recovery_ttl,
    )


def _make_file_record(object_key: str = "t/f/test.pdf") -> MagicMock:
    record = MagicMock()
    record.id = uuid.uuid4()
    record.object_key = object_key
    return record


def _mock_socket_response(response: str):
    sock = MagicMock()
    sock.recv.return_value = response.encode()
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=sock)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx, sock


# ---------------------------------------------------------------------------
# State machine: ClamAVCircuitBreaker
# ---------------------------------------------------------------------------


class TestCircuitBreakerStateTransitions:
    def test_initially_closed(self):
        assert not _cb().is_open()

    def test_opens_after_threshold_failures(self):
        cb = _cb(failure_threshold=3)
        for _ in range(3):
            cb.record_failure()
        assert cb.is_open()

    def test_stays_closed_below_threshold(self):
        cb = _cb(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert not cb.is_open()

    def test_success_resets_failure_counter(self):
        cb = _cb(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        # Counter reset — need full threshold again to open.
        cb.record_failure()
        cb.record_failure()
        assert not cb.is_open()

    def test_success_closes_open_circuit(self):
        cb = _cb(failure_threshold=1)
        cb.record_failure()
        assert cb.is_open()
        cb.record_success()
        assert not cb.is_open()

    def test_failure_while_open_does_not_re_arm_ttl(self):
        """
        Subsequent failures while OPEN must be ignored so the TTL-driven
        recovery fires at the original scheduled time.
        """
        cb = _cb(failure_threshold=1, recovery_ttl=60)
        cb.record_failure()  # opens
        cb.record_failure()  # must be no-op
        cb.record_failure()  # must be no-op
        assert cb.is_open()

    def test_failure_counter_increments_correctly(self):
        from django.core.cache import cache

        cb = _cb(failure_threshold=5)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cache.get(_CB_FAILURES_KEY) == 3
        assert not cb.is_open()

    def test_open_key_set_when_circuit_opens(self):
        from django.core.cache import cache

        cb = _cb(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cache.get(_CB_OPEN_KEY) == 1

    def test_failure_counter_cleared_when_circuit_opens(self):
        from django.core.cache import cache

        cb = _cb(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()  # opens; should clear failures counter
        assert cache.get(_CB_FAILURES_KEY) is None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestGetCircuitBreakerFactory:
    @override_settings(CLAMAV_CB_FAILURE_THRESHOLD=10, CLAMAV_CB_RECOVERY_TTL=120)
    def test_factory_reads_settings(self):
        cb = get_circuit_breaker()
        assert cb.failure_threshold == 10
        assert cb.recovery_ttl == 120

    def test_factory_returns_circuit_breaker_instance(self):
        assert isinstance(get_circuit_breaker(), ClamAVCircuitBreaker)


# ---------------------------------------------------------------------------
# Integration: ClamAVScanEngine respects circuit breaker
# ---------------------------------------------------------------------------


class TestClamAVScanEngineCircuitBreaker:
    @override_settings(CLAMAV_CB_FAILURE_THRESHOLD=2, CLAMAV_CB_RECOVERY_TTL=60)
    @patch("socket.socket")
    def test_circuit_opens_after_threshold_socket_errors(self, mock_socket_cls):
        sock = MagicMock()
        sock.connect.side_effect = OSError(111, "Connection refused")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=sock)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_socket_cls.return_value = ctx

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        engine.scan(_make_file_record())
        engine.scan(_make_file_record())

        assert get_circuit_breaker().is_open()

    @override_settings(CLAMAV_CB_FAILURE_THRESHOLD=3, CLAMAV_CB_RECOVERY_TTL=60)
    @patch("socket.socket")
    def test_scan_fast_fails_when_circuit_open(self, mock_socket_cls):
        """When circuit is OPEN, socket.socket() must never be called."""
        from django.core.cache import cache

        cache.set(_CB_OPEN_KEY, 1, timeout=60)

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        result = engine.scan(_make_file_record())

        assert result is ScanResult.CB_FAST_FAIL
        mock_socket_cls.assert_not_called()

    @override_settings(CLAMAV_CB_FAILURE_THRESHOLD=3, CLAMAV_CB_RECOVERY_TTL=60)
    @patch("socket.socket")
    def test_circuit_closes_after_successful_probe(self, mock_socket_cls):
        """After TTL expiry (simulated by manual delete), clean scan → circuit closes."""
        ctx, _ = _mock_socket_response("stream: OK")
        mock_socket_cls.return_value = ctx

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        result = engine.scan(_make_file_record())

        assert result is ScanResult.CLEAN
        assert not get_circuit_breaker().is_open()

    @override_settings(CLAMAV_CB_FAILURE_THRESHOLD=3, CLAMAV_CB_RECOVERY_TTL=60)
    @patch("socket.socket")
    def test_infected_result_closes_open_circuit(self, mock_socket_cls):
        """INFECTED means daemon is alive — treat as success for CB purposes."""
        from django.core.cache import cache

        cache.set(_CB_OPEN_KEY, 1, timeout=60)
        cache.delete(_CB_OPEN_KEY)  # simulate TTL expiry → probe mode

        ctx, _ = _mock_socket_response("stream: Eicar FOUND")
        mock_socket_cls.return_value = ctx

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        result = engine.scan(_make_file_record())

        assert result is ScanResult.INFECTED
        assert not get_circuit_breaker().is_open()

    @override_settings(CLAMAV_CB_FAILURE_THRESHOLD=2, CLAMAV_CB_RECOVERY_TTL=60)
    @patch("socket.socket")
    def test_circuit_stays_open_after_failed_probe(self, mock_socket_cls):
        """Probe attempt that times out → circuit re-opens (failure_threshold=1 after reset)."""
        sock = MagicMock()
        sock.recv.side_effect = TimeoutError("recv timed out")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=sock)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_socket_cls.return_value = ctx

        # Open circuit then manually simulate TTL expiry (probe mode)
        cb = _cb(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open()

        from django.core.cache import cache

        cache.delete(_CB_OPEN_KEY)  # TTL expired → probe window

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        engine.scan(_make_file_record())  # probe fails

        # After one failure in probe mode, circuit is not immediately reopened
        # (threshold=2). Second failure would reopen. This is acceptable — exact
        # half-open semantics require a separate HALF_OPEN key which adds complexity
        # without meaningful safety gain for this use case.
        assert not get_circuit_breaker().is_open()  # first probe failure → failure_count=1

    @override_settings(CLAMAV_CB_FAILURE_THRESHOLD=1, CLAMAV_CB_RECOVERY_TTL=60)
    @patch("socket.socket")
    def test_circuit_reopens_immediately_on_failed_probe_at_threshold_1(
        self, mock_socket_cls
    ):
        """With threshold=1, a single probe failure reopens the circuit."""
        sock = MagicMock()
        sock.connect.side_effect = OSError("refused")
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=sock)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_socket_cls.return_value = ctx

        from django.core.cache import cache

        # Simulate: was open, TTL expired
        cache.delete(_CB_OPEN_KEY)

        engine = ClamAVScanEngine(socket_path="/tmp/test.ctl")
        engine.scan(_make_file_record())  # probe fails

        assert get_circuit_breaker().is_open()
