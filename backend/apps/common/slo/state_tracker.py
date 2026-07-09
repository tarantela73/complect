"""
Redis-backed SLO state machine.

Keys (all on DB=0 default Redis):
  slo:state:{name}      → raw state string: ok | warn | crit
  slo:recovering:{name} → exists (TTL=300s) when recently transitioned back to ok
  slo:budget:{name}     → Redis list, last 30 evaluation outcomes (1=ok, 0=breach)
"""

from __future__ import annotations

from typing import Literal

from apps.common.infra.redis import get_default_redis

SLODisplayState = Literal["ok", "warn", "crit", "recovering"]

_STATE_KEY = "slo:state:{name}"
_RECOVERING_KEY = "slo:recovering:{name}"
_BUDGET_KEY = "slo:budget:{name}"

_RECOVERING_TTL = 300  # 5 minutes
_BUDGET_SLOTS = 30  # 30 evaluations × 60s = 30 minutes


def get_slo_display_state(name: str) -> SLODisplayState:
    r = get_default_redis()
    raw = r.get(_STATE_KEY.format(name=name)) or "ok"
    if raw == "ok" and r.exists(_RECOVERING_KEY.format(name=name)):
        return "recovering"
    return raw  # type: ignore[return-value]


def get_error_budget(name: str) -> float:
    """Fraction of last 30 evaluations that were compliant (0.0–1.0)."""
    r = get_default_redis()
    values = r.lrange(_BUDGET_KEY.format(name=name), 0, -1)
    if not values:
        return 1.0
    ok_count = sum(1 for v in values if v == "1")
    return round(ok_count / len(values), 3)


def record_evaluation(name: str, raw_state: str) -> tuple[SLODisplayState, SLODisplayState]:
    """
    Persist the evaluation result and manage state transitions.
    Returns (old_display_state, new_display_state).
    """
    r = get_default_redis()
    state_key = _STATE_KEY.format(name=name)
    recovering_key = _RECOVERING_KEY.format(name=name)
    budget_key = _BUDGET_KEY.format(name=name)

    old_raw = r.get(state_key) or "ok"
    old_recovering = r.exists(recovering_key)
    old_display: SLODisplayState = "recovering" if (old_raw == "ok" and old_recovering) else old_raw  # type: ignore

    # Budget: push 1 (ok/recovering counts as ok) or 0 (breach)
    r.lpush(budget_key, 1 if raw_state == "ok" else 0)
    r.ltrim(budget_key, 0, _BUDGET_SLOTS - 1)
    r.expire(budget_key, 3600)

    # Recovery window: set on breach→ok transition, clear on any new breach
    if raw_state == "ok" and old_raw in ("warn", "crit"):
        r.setex(recovering_key, _RECOVERING_TTL, "1")
    elif raw_state != "ok":
        r.delete(recovering_key)

    r.set(state_key, raw_state)

    new_recovering = r.exists(recovering_key)
    new_display: SLODisplayState = "recovering" if (raw_state == "ok" and new_recovering) else raw_state  # type: ignore
    return old_display, new_display
