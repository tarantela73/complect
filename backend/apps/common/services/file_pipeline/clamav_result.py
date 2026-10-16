"""Interpret the installed clamd instream contract without accepting unknown replies."""
from typing import Literal


def parse_clamd_instream_result(result: dict[str, tuple[str, str | None]] | None) -> Literal["CLEAN", "INFECTED"]:
    entry = result.get("stream") if result else None
    if entry is None:
        raise RuntimeError("LEGACY_SCAN_RESULT_UNAVAILABLE")
    status, _signature = entry
    if status == "FOUND":
        return "INFECTED"
    if status == "OK":
        return "CLEAN"
    raise RuntimeError("LEGACY_SCAN_RESULT_UNAVAILABLE")
