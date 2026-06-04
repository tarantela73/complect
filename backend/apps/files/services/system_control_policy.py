from __future__ import annotations

import structlog

from apps.files.services.clamav_circuit_breaker import _CB_OPEN_EVENTS_KEY

logger = structlog.get_logger(__name__)

_SCAN_MS_EWMA_KEY = "files:control:scan_ms_ewma"
_BATCH_SIZE_KEY = "files:control:batch_size"


class SystemControlPolicy:
    """
    Closed-loop control plane for the Files consumer.

    Signals → Rules → Control parameters.

    Signals (Redis-backed):
      CB open events count  (_CB_OPEN_EVENTS_KEY, sliding window)
      Scan latency EWMA     (_SCAN_MS_EWMA_KEY, per-file ms, α-smoothed)
      Rescan backlog        (files:rescan:queue LLEN)

    Control parameters (Redis-backed, expire to default when evaluator stops):
      Batch size            (_BATCH_SIZE_KEY)

    Rules (priority order — highest rule wins):
      1. rescan_backlog > backlog_throttle_threshold  → THROTTLED
      2. cb_opens >= cb_rate_threshold                → THROTTLED
      3. scan_ms_ewma > scan_latency_threshold_ms     → REDUCED
      4. nominal                                      → DEFAULT
    """

    def __init__(
        self,
        *,
        batch_size_default: int,
        batch_size_reduced: int,
        batch_size_throttled: int,
        cb_rate_threshold: int,
        scan_latency_threshold_ms: float,
        backlog_throttle_threshold: int,
        scan_ewma_alpha: float,
        scan_ewma_ttl: int,
        param_ttl: int,
    ) -> None:
        self.batch_size_default = batch_size_default
        self.batch_size_reduced = batch_size_reduced
        self.batch_size_throttled = batch_size_throttled
        self.cb_rate_threshold = cb_rate_threshold
        self.scan_latency_threshold_ms = scan_latency_threshold_ms
        self.backlog_throttle_threshold = backlog_throttle_threshold
        self.scan_ewma_alpha = scan_ewma_alpha
        self.scan_ewma_ttl = scan_ewma_ttl
        self.param_ttl = param_ttl

    def get_batch_size(self) -> int:
        from django.core.cache import cache

        return cache.get(_BATCH_SIZE_KEY) or self.batch_size_default

    def record_scan_latency(self, per_file_scan_ms: float) -> None:
        from django.core.cache import cache

        old = cache.get(_SCAN_MS_EWMA_KEY)
        if old is None:
            new_ewma = per_file_scan_ms
        else:
            new_ewma = self.scan_ewma_alpha * per_file_scan_ms + (1 - self.scan_ewma_alpha) * old
        cache.set(_SCAN_MS_EWMA_KEY, new_ewma, timeout=self.scan_ewma_ttl)

    def evaluate(self) -> None:
        from django.core.cache import cache

        from apps.files.services.adaptive_scan_policy import get_adaptive_scan_policy

        cb_opens = cache.get(_CB_OPEN_EVENTS_KEY) or 0
        scan_ms_ewma = cache.get(_SCAN_MS_EWMA_KEY) or 0.0
        rescan_backlog = get_adaptive_scan_policy().rescan_queue_size()

        if rescan_backlog > self.backlog_throttle_threshold:
            new_batch_size = self.batch_size_throttled
            reason = "rescan_backlog_high"
        elif cb_opens >= self.cb_rate_threshold:
            new_batch_size = self.batch_size_throttled
            reason = "cb_open_rate_high"
        elif scan_ms_ewma > self.scan_latency_threshold_ms:
            new_batch_size = self.batch_size_reduced
            reason = "scan_latency_high"
        else:
            new_batch_size = self.batch_size_default
            reason = "nominal"

        old_batch_size = cache.get(_BATCH_SIZE_KEY) or self.batch_size_default
        cache.set(_BATCH_SIZE_KEY, new_batch_size, timeout=self.param_ttl)

        if old_batch_size != new_batch_size:
            logger.info(
                "files_system_control_adjusted",
                old_batch_size=old_batch_size,
                new_batch_size=new_batch_size,
                reason=reason,
                cb_opens=cb_opens,
                scan_ms_ewma=round(scan_ms_ewma, 1),
                rescan_backlog=rescan_backlog,
            )


def get_system_control_policy() -> SystemControlPolicy:
    from django.conf import settings

    return SystemControlPolicy(
        batch_size_default=getattr(settings, "FILES_CONTROL_BATCH_SIZE_DEFAULT", 100),
        batch_size_reduced=getattr(settings, "FILES_CONTROL_BATCH_SIZE_REDUCED", 25),
        batch_size_throttled=getattr(settings, "FILES_CONTROL_BATCH_SIZE_THROTTLED", 10),
        cb_rate_threshold=getattr(settings, "FILES_CONTROL_CB_RATE_THRESHOLD", 3),
        scan_latency_threshold_ms=getattr(settings, "FILES_CONTROL_SCAN_LATENCY_THRESHOLD_MS", 5000.0),
        backlog_throttle_threshold=getattr(settings, "FILES_CONTROL_BACKLOG_THROTTLE_THRESHOLD", 100),
        scan_ewma_alpha=getattr(settings, "FILES_CONTROL_SCAN_EWMA_ALPHA", 0.3),
        scan_ewma_ttl=getattr(settings, "FILES_CONTROL_SCAN_EWMA_TTL", 600),
        param_ttl=getattr(settings, "FILES_CONTROL_PARAM_TTL", 120),
    )
