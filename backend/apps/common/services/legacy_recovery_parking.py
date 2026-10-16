"""ADR-0013 transport quarantine; unknown tenants never enter domain SQL scopes."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol, cast

from django.conf import settings

from apps.common.infra.redis import get_streams_redis

TASK_NAME = "apps.common.services.file_pipeline.orchestrator.run_antivirus_scan_task"
INDEX = "legacy_compatibility:parking_index"

_PARK = """
local previous = redis.call('HGET', KEYS[2], ARGV[1])
if previous and previous ~= 'CONFLICT' then
  local rows = redis.call('XRANGE', KEYS[1], previous, previous)
  if #rows > 0 then
    local fields = rows[1][2]
    for i=1,#fields,2 do
      if fields[i] == 'compat_envelope_hash' and fields[i+1] == ARGV[2] then
        return previous
      end
    end
    previous = 'CONFLICT'
  end
end
local entry = redis.call('XADD', KEYS[1], '*', unpack(ARGV, 3))
redis.call('HSET', KEYS[2], ARGV[1], previous == 'CONFLICT' and 'CONFLICT' or entry)
return entry
"""


class ParkingRedis(Protocol):
    def eval(self, script: str, numkeys: int, *args: str) -> str: ...
    def execute_command(self, command: str, *args: int) -> list[int]: ...
    def hget(self, name: str, key: str) -> str | None: ...
    def xrange(self, name: str, *, min: str, max: str, count: int) -> list[tuple[str, dict[str, str]]]: ...


def park(task_id: str, doc_id: str, args: tuple, kwargs: dict, metadata: dict, reason: str) -> str:
    args_json = json.dumps(list(args), separators=(",", ":"), ensure_ascii=False)
    kwargs_json = json.dumps(kwargs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    fingerprint = hashlib.sha256((doc_id + args_json + kwargs_json).encode()).hexdigest()
    payload: dict[str, str] = {
        "task_name": TASK_NAME,
        "task_id": task_id,
        "args": args_json,
        "kwargs": kwargs_json,
        "exception_class": "LegacyRecoveryRequired",
        "exception_message": reason,
        "traceback": "",
        "retries": "0",
        "compat_legacy_doc_id": doc_id,
        "compat_envelope_hash": fingerprint,
        "correlation_id": str(metadata.get("correlation_id", "")),
        "causal_root_id": str(metadata.get("causal_root_id", "")),
    }
    flattened: list[str] = []
    for key, value in payload.items():
        flattened.extend((key, value))
    client = cast(ParkingRedis, get_streams_redis())
    entry = client.eval(_PARK, 2, settings.DLQ_STREAM_NAME, INDEX, task_id, fingerprint, *flattened)
    persisted = client.execute_command("WAITAOF", 1, 0, 2000)
    if not isinstance(persisted, (list, tuple)) or not persisted or int(persisted[0]) < 1:
        raise RuntimeError("LEGACY_RECOVERY_PARK_NOT_PERSISTED")
    return str(entry)


def load_parked(task_id: str) -> dict[str, str]:
    client = cast(ParkingRedis, get_streams_redis())
    entry = client.hget(INDEX, task_id)
    if not entry or entry == "CONFLICT":
        raise ValueError("LEGACY_PARKED_ENVELOPE_MISSING_OR_CONFLICTED")
    rows = client.xrange(settings.DLQ_STREAM_NAME, min=entry, max=entry, count=1)
    if len(rows) != 1:
        raise ValueError("LEGACY_PARKED_ENVELOPE_REMOVED_BY_OPERATOR")
    fields = rows[0][1]
    if fields.get("task_name") != TASK_NAME or fields.get("task_id") != task_id:
        raise ValueError("LEGACY_PARKED_ENVELOPE_INVALID")
    return fields
