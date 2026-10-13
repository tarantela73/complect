"""Controls and evidence probes for the disposable physical Payments replica.

This module deliberately uses the two configured Django connections directly.  It
is not a test-database mirror and it never changes database roles or replication
configuration.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import connections


@dataclass(frozen=True, slots=True)
class ReplicaLagSnapshot:
    primary_lsn: str
    replay_lsn: str | None
    replay_paused: bool
    in_recovery: bool


@dataclass(frozen=True, slots=True)
class ReplicaCommandRun:
    """Evidence returned by one attach/detach run."""

    name: str
    response_status: int
    state_before: dict[str, Any]
    state_after: dict[str, Any]
    outbox_before: int
    outbox_after: int
    replica_line_before_pause: dict[str, Any] | None
    replica_line_while_paused: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ReplicaScenarioEvidence:
    synchronized: ReplicaLagSnapshot
    lagged: ReplicaLagSnapshot
    recovered: ReplicaLagSnapshot
    runs: tuple[ReplicaCommandRun, ...]

    def as_dict(self) -> dict[str, Any]:
        def snapshot(value: ReplicaLagSnapshot) -> dict[str, Any]:
            return {
                "primary_lsn": value.primary_lsn,
                "replay_lsn": value.replay_lsn,
                "replay_paused": value.replay_paused,
                "in_recovery": value.in_recovery,
            }

        return {
            "synchronized": snapshot(self.synchronized),
            "lagged": snapshot(self.lagged),
            "recovered": snapshot(self.recovered),
            "runs": [asdict(run) for run in self.runs],
        }


class PaymentsReplicaProbe:
    """Small public seam used by the Payments physical-replica phase."""

    def __init__(
        self,
        *,
        connection_map: Any = connections,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._connections = connection_map
        self._sleep = sleeper
        self._monotonic = monotonic

    def assert_physical(self) -> None:
        primary = settings.DATABASES["default"]
        replica = settings.DATABASES["replica"]
        if primary.get("HOST") == replica.get("HOST") and primary.get("PORT") == replica.get("PORT"):
            raise AssertionError("replica connection aliases the primary host")
        snapshot = self.snapshot()
        if not snapshot.in_recovery:
            raise AssertionError("replica is not in PostgreSQL recovery mode")

    def snapshot(self) -> ReplicaLagSnapshot:
        with self._connections["default"].cursor() as cursor:
            cursor.execute("SELECT pg_current_wal_lsn()")
            primary_lsn = str(cursor.fetchone()[0])
        with self._connections["replica"].cursor() as cursor:
            cursor.execute(
                "SELECT pg_is_in_recovery(), pg_last_wal_replay_lsn(), "
                "pg_is_wal_replay_paused()"
            )
            in_recovery, replay_lsn, replay_paused = cursor.fetchone()
        return ReplicaLagSnapshot(
            primary_lsn,
            str(replay_lsn) if replay_lsn else None,
            bool(replay_paused),
            bool(in_recovery),
        )

    def pause_replay(self) -> None:
        with self._connections["replica"].cursor() as cursor:
            cursor.execute("SELECT pg_wal_replay_pause()")

    def resume_replay(self) -> None:
        with self._connections["replica"].cursor() as cursor:
            cursor.execute("SELECT pg_wal_replay_resume()")

    @contextmanager
    def replay_paused(self) -> Iterator[None]:
        self.pause_replay()
        try:
            yield
        finally:
            # Recovery must be restored even when the HTTP assertion fails.
            self.resume_replay()

    def wait_until_lagged(self, *, timeout: float = 30.0, interval: float = 0.1) -> ReplicaLagSnapshot:
        deadline = self._monotonic() + timeout
        last = self.snapshot()
        while self._monotonic() < deadline:
            last = self.snapshot()
            if last.in_recovery and last.replay_paused and last.replay_lsn is not None:
                with self._connections["default"].cursor() as cursor:
                    cursor.execute("SELECT pg_wal_lsn_diff(%s, %s) > 0", [last.primary_lsn, last.replay_lsn])
                    if cursor.fetchone()[0]:
                        return last
            self._sleep(interval)
        raise TimeoutError("physical replica did not show WAL lag before timeout")

    def wait_until_replaying(self, *, timeout: float = 30.0, interval: float = 0.1) -> ReplicaLagSnapshot:
        """Confirm the recovery process accepted the resume control."""
        deadline = self._monotonic() + timeout
        while self._monotonic() < deadline:
            snapshot = self.snapshot()
            if snapshot.in_recovery and not snapshot.replay_paused:
                return snapshot
            self._sleep(interval)
        raise TimeoutError("physical replica did not resume WAL replay before timeout")

    def wait_until_synchronized(self, *, timeout: float = 30.0, interval: float = 0.1) -> ReplicaLagSnapshot:
        """Wait until streaming replay has caught the primary before the run."""
        deadline = self._monotonic() + timeout
        while self._monotonic() < deadline:
            snapshot = self.snapshot()
            if snapshot.in_recovery and not snapshot.replay_paused and snapshot.replay_lsn:
                with self._connections["default"].cursor() as cursor:
                    cursor.execute("SELECT pg_wal_lsn_diff(%s, %s) = 0", [snapshot.primary_lsn, snapshot.replay_lsn])
                    if cursor.fetchone()[0]:
                        return snapshot
            self._sleep(interval)
        raise TimeoutError("physical replica did not synchronize before timeout")

    def run_http_commands(
        self,
        *,
        primary_write: Callable[[], dict[str, Any]],
        lag_write: Callable[[dict[str, Any]], None],
        http_runs: tuple[Callable[[dict[str, Any]], dict[str, Any]], ...],
        replica_read: Callable[[dict[str, Any]], dict[str, Any] | None],
        timeout: float = 30.0,
    ) -> ReplicaScenarioEvidence:
        """Run attach/detach callbacks during a real paused-replay window.

        The callbacks own the Ninja Client and auth/RBAC fixture.  They must
        return ``status``, ``state_before``, ``state_after``, ``outbox_before``
        and ``outbox_after``.  This keeps HTTP evidence separate from database
        control and makes the auth seam explicit to the gate.
        """
        synchronized = self.wait_until_synchronized(timeout=timeout)
        payload = primary_write()
        synchronized = self.wait_until_synchronized(timeout=timeout)
        before_pause = replica_read(payload)
        runs: list[ReplicaCommandRun] = []
        try:
            self.pause_replay()
            lag_write(payload)
            lagged = self.wait_until_lagged(timeout=timeout)
            while_paused = replica_read(payload)
            for callback in http_runs:
                result = callback(payload)
                runs.append(
                    ReplicaCommandRun(
                        name=str(result["name"]),
                        response_status=int(result["status"]),
                        state_before=dict(result["state_before"]),
                        state_after=dict(result["state_after"]),
                        outbox_before=int(result["outbox_before"]),
                        outbox_after=int(result["outbox_after"]),
                        replica_line_before_pause=before_pause,
                        replica_line_while_paused=while_paused,
                    )
                )
        finally:
            self.resume_replay()
        recovered = self.wait_until_synchronized(timeout=timeout)
        return ReplicaScenarioEvidence(synchronized, lagged, recovered, tuple(runs))

    def run_while_lagged(
        self,
        primary_write: Callable[[], Any],
        command_probe: Callable[[], Any],
        *,
        timeout: float = 30.0,
    ) -> Any:
        """Pause replay, make a primary write, run a command probe, then recover."""
        try:
            with self.replay_paused():
                primary_write()
                self.wait_until_lagged(timeout=timeout)
                result = command_probe()
        finally:
            # Wait after the context exits so a failed HTTP assertion cannot
            # leave recovery merely requested but unverified.
            self.wait_until_replaying(timeout=timeout)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a physical Payments streaming replica")
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    probe = PaymentsReplicaProbe()
    probe.assert_physical()
    synchronized = probe.wait_until_synchronized(timeout=args.timeout)
    probe.pause_replay()
    try:
        lagged = probe.wait_until_lagged(timeout=args.timeout)
    finally:
        probe.resume_replay()
    recovered = probe.wait_until_synchronized(timeout=args.timeout)
    Path(args.output).write_text(json.dumps({
        "synchronized": asdict(synchronized),
        "lagged": asdict(lagged),
        "recovered": asdict(recovered),
        "http_runs": [],
    }, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
