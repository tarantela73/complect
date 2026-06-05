from __future__ import annotations

from typing import Literal

from .definitions import SLODefinition

SLORawState = Literal["ok", "warn", "crit"]


def evaluate(value: float | None, slo: SLODefinition) -> SLORawState:
    if value is None or value < 0:
        return "ok"
    if value >= slo.critical:
        return "crit"
    if value >= slo.warn:
        return "warn"
    return "ok"


def extract_metric(snapshot: dict, name: str) -> float | None:
    parts = name.split(".", 1)
    if len(parts) == 2:
        parent, child = parts
        container = snapshot.get(parent)
        if isinstance(container, dict):
            val = container.get(child)
            return float(val) if val is not None else None
    val = snapshot.get(name)
    return float(val) if val is not None else None
