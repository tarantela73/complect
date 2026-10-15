"""Pure contract tests for the physical Payments replica probe."""

from __future__ import annotations

import pytest

import test_support.payments_replica as replica_module
from test_support.payments_replica import PaymentsReplicaProbe


class Cursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = iter(rows)
        self.sql: list[str] = []

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, params: object = None) -> None:
        self.sql.append(sql)

    def fetchone(self) -> tuple[object, ...]:
        return next(self.rows)


class Connection:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.cursor_obj = Cursor(rows)

    def cursor(self) -> Cursor:
        return self.cursor_obj


def test_probe_requires_distinct_physical_connections(monkeypatch: pytest.MonkeyPatch):
    settings = type("Settings", (), {"DATABASES": {"default": {}, "replica": {}}})()
    monkeypatch.setattr(replica_module, "settings", settings)
    settings.DATABASES["default"].update(HOST="primary", PORT="5432")
    settings.DATABASES["replica"].update(HOST="primary", PORT="5432")
    probe = PaymentsReplicaProbe(
        connection_map={
            "default": Connection([("0/10",)]),
            "replica": Connection([(True, "0/9", True)]),
        }
    )

    with pytest.raises(AssertionError, match="aliases the primary"):
        probe.assert_physical()


def test_replay_pause_is_restored_after_probe_body_fails(monkeypatch: pytest.MonkeyPatch):
    settings = type("Settings", (), {"DATABASES": {"default": {}, "replica": {}}})()
    monkeypatch.setattr(replica_module, "settings", settings)
    settings.DATABASES["default"].update(HOST="primary", PORT="5432")
    settings.DATABASES["replica"].update(HOST="replica", PORT="5432")
    replica = Connection([(True, "0/9", True)])
    probe = PaymentsReplicaProbe(
        connection_map={"default": Connection([("0/10",)]), "replica": replica}
    )

    with pytest.raises(RuntimeError):
        with probe.replay_paused():
            raise RuntimeError("HTTP probe failed")

    assert replica.cursor_obj.sql == [
        "SELECT pg_wal_replay_pause()",
        "SELECT pg_wal_replay_resume()",
    ]
