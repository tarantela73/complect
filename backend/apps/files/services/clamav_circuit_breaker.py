from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)

_CB_OPEN_KEY = "clamav:cb:open"
_CB_FAILURES_KEY = "clamav:cb:failures"
# Sliding-window open-event counter consumed by SystemControlPolicy.
_CB_OPEN_EVENTS_KEY = "clamav:cb:open_events"


class ClamAVCircuitBreaker:
    """
    Redis-backed circuit breaker for ClamAV unix socket connections.

    States (implicit via Redis keys):
      CLOSED    — normal; failure counter below threshold
      OPEN      — _CB_OPEN_KEY present; all scan calls fail fast → ScanResult.ERROR
      (probe)   — _CB_OPEN_KEY expired (TTL elapsed); first scan attempt goes through

    Transitions:
      CLOSED → OPEN  : failure_threshold consecutive scan errors
      OPEN   → probe : recovery_ttl seconds elapse (_CB_OPEN_KEY expires)
      probe  → CLOSED: non-ERROR result (daemon responded)
      probe  → OPEN  : ERROR result (re-count from 1, threshold still applies)

    Fast-fail path prevents socket-timeout storms: 120 s × N workers × backlog
    collapses to an instant ERROR when OPEN.
    """

    def __init__(self, *, failure_threshold: int, recovery_ttl: int) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_ttl = recovery_ttl

    def is_open(self) -> bool:
        from django.core.cache import cache

        return bool(cache.get(_CB_OPEN_KEY))

    def record_success(self) -> None:
        """
        Call when ClamAV daemon responded (CLEAN or INFECTED).
        Clears failure counter and closes any open circuit.
        """
        from django.core.cache import cache

        was_open = cache.get(_CB_OPEN_KEY)
        cache.delete(_CB_OPEN_KEY)
        cache.delete(_CB_FAILURES_KEY)
        if was_open:
            logger.info("clamav_circuit_breaker_closed", reason="probe_succeeded")

    def record_failure(self) -> None:
        """
        Call when scan returned ERROR (socket timeout, OSError, unparseable response).
        Increments failure counter; opens circuit if threshold reached.
        """
        from django.core.cache import cache

        if cache.get(_CB_OPEN_KEY):
            # Already open — let the TTL drive recovery; don't re-arm.
            return

        failures = (cache.get(_CB_FAILURES_KEY) or 0) + 1
        cache.set(_CB_FAILURES_KEY, failures, timeout=self.recovery_ttl * 2)

        if failures >= self.failure_threshold:
            cache.set(_CB_OPEN_KEY, 1, timeout=self.recovery_ttl)
            cache.delete(_CB_FAILURES_KEY)
            # Track open-event rate for SystemControlPolicy.
            from django.conf import settings
            _window = getattr(settings, "FILES_CONTROL_CB_RATE_WINDOW_SECONDS", 300)
            open_events = (cache.get(_CB_OPEN_EVENTS_KEY) or 0) + 1
            cache.set(_CB_OPEN_EVENTS_KEY, open_events, timeout=_window)
            logger.error(
                "clamav_circuit_breaker_opened",
                failure_threshold=self.failure_threshold,
                recovery_ttl_s=self.recovery_ttl,
                open_events_in_window=open_events,
            )
        else:
            logger.warning(
                "clamav_circuit_breaker_failure_recorded",
                failures=failures,
                failure_threshold=self.failure_threshold,
            )


def get_circuit_breaker() -> ClamAVCircuitBreaker:
    from django.conf import settings

    return ClamAVCircuitBreaker(
        failure_threshold=getattr(settings, "CLAMAV_CB_FAILURE_THRESHOLD", 5),
        recovery_ttl=getattr(settings, "CLAMAV_CB_RECOVERY_TTL", 60),
    )
