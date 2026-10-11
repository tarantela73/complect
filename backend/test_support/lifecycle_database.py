"""Fixed-operation migrator service for an explicitly owned disposable test DB."""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path
from typing import Any
from unittest.mock import patch

import django
from django.core.management import call_command
from django.db import connection

FIXTURES = frozenset({"old_person_ciphertext", "pii_legacy_text"})


class TargetMismatchError(RuntimeError):
    """The independently provisioned database identity did not match."""


def database_identity() -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_database(), d.oid, s.system_identifier::text, current_user, session_user "
            "FROM pg_database d CROSS JOIN pg_control_system() s WHERE d.datname=current_database()"
        )
        name, oid, cluster, user, session = cursor.fetchone()
        cursor.execute("SELECT run_id FROM pytest_lifecycle_control.target")
        marker = cursor.fetchone()[0]
    return {
        "database": name,
        "database_oid": oid,
        "cluster": cluster,
        "user": user,
        "session_user": session,
        "run_id": marker,
    }


def require_target(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    keys = ("database", "database_oid", "cluster", "user", "session_user", "run_id")
    if not expected.get("run_id") or any(actual.get(k) != expected.get(k) for k in keys):
        raise TargetMismatchError("disposable database identity mismatch")
    if actual["user"] != "complect_migrator" or actual["session_user"] != actual["user"]:
        raise TargetMismatchError("lifecycle principal mismatch")


def perform(request: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    op = request.get("op")
    allowed = {"op", "run_id"} | ({"fixture"} if op in {"fixture_prepare", "fixture_cleanup"} else set())
    if set(request) != allowed or request.get("run_id") != expected["run_id"]:
        raise TargetMismatchError("invalid request or run identity")
    if op not in {"flush", "fixture_prepare", "fixture_cleanup"}:
        raise TargetMismatchError("unsupported lifecycle operation")
    if op != "flush" and request.get("fixture") not in FIXTURES:
        raise TargetMismatchError("unknown lifecycle fixture")
    actual = database_identity()
    require_target(actual, expected)
    if op == "flush":
        # The supported Django flush implementation preserves migrations, schema,
        # constraints, triggers and RLS. Runtime emits its own post_migrate signal.
        from config.runtime_role_grants import EXCLUDED, _tables

        tables = [name for name in _tables() if name != "django_migrations"]
        # PostGIS images also expose tiger/topology tables on the search path.
        # This lifecycle owns application tables in public only.
        with connection.cursor() as cursor:
            cursor.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public'")
            existing = {row[0] for row in cursor.fetchall()}
        if existing - EXCLUDED != set(tables) | FIXTURES | {"django_migrations"}:
            raise RuntimeError("unregistered application table in disposable database")
        before_rows = {}
        with connection.cursor() as cursor:
            for table in tables:
                cursor.execute(f"SELECT count(*) FROM {connection.ops.quote_name(table)}")
                before_rows[table] = cursor.fetchone()[0]
        with patch.object(connection.introspection, "django_table_names", return_value=tables):
            call_command("flush", interactive=False, verbosity=0, reset_sequences=False, inhibit_post_migrate=True)
        for table in sorted(FIXTURES):
            with connection.cursor() as cursor:
                cursor.execute(f"TRUNCATE TABLE public.{table}")
        with connection.cursor() as cursor:
            counts = {}
            for table in sorted(tables):
                cursor.execute(f"SELECT count(*) FROM {connection.ops.quote_name(table)}")
                counts[table] = cursor.fetchone()[0]
        if any(counts.values()):
            raise RuntimeError("Django flush left application rows")
        return {"ok": True, "identity": actual, "empty_tables": counts, "before_rows": before_rows}
    table = request["fixture"]
    with connection.cursor() as cursor:
        cursor.execute(f"TRUNCATE TABLE public.{table}")
    return {"ok": True, "identity": actual, "fixture": table}


class LifecycleService:
    """A failed lifecycle operation permanently poisons this service instance."""

    def __init__(self, expected: dict[str, Any]) -> None:
        self.expected = expected
        self.failed = False

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            if self.failed:
                raise RuntimeError("lifecycle state poisoned by previous failure")
            return perform(request, self.expected)
        except Exception as exc:
            self.failed = True
            return {"ok": False, "error": type(exc).__name__, "sqlstate": getattr(exc.__cause__, "sqlstate", None)}


def serve(socket_path: Path, expected: dict[str, Any], evidence: Path) -> None:
    require_target(database_identity(), expected)
    if socket_path.exists():
        raise RuntimeError("refusing existing lifecycle socket")
    service = LifecycleService(expected)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        server.listen(4)
        try:
            while True:
                channel, _ = server.accept()
                with channel:
                    channel.settimeout(30)
                    raw = bytearray()
                    while b"\n" not in raw and len(raw) <= 4096:
                        chunk = channel.recv(4096)
                        if not chunk:
                            break
                        raw.extend(chunk)
                    try:
                        if len(raw) > 4096 or b"\n" not in raw:
                            raise ValueError("invalid request framing")
                        request = json.loads(raw.split(b"\n", 1)[0])
                        if not isinstance(request, dict):
                            raise ValueError("invalid request shape")
                        response = service.handle(request)
                    except Exception as exc:
                        service.failed = True
                        # No raw SQL, credentials or request data in error response.
                        response = {
                            "ok": False,
                            "error": type(exc).__name__,
                            "sqlstate": getattr(exc.__cause__, "sqlstate", None),
                        }
                    with evidence.open("a") as stream:
                        stream.write(
                            json.dumps({"op": request.get("op") if "request" in locals() else None, **response}) + "\n"
                        )
                    channel.sendall((json.dumps(response) + "\n").encode())
        finally:
            socket_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    django.setup()
    serve(args.socket, json.loads(args.identity.read_text()), args.evidence)


if __name__ == "__main__":
    main()
