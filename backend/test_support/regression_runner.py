"""Runtime-only pytest entry point with complete phase and SQL failure evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, cast

import django
import pytest
from django.conf import settings
from django.db import connections
from django.db.backends.signals import connection_created


class Evidence:
    def __init__(self, manifest: Path) -> None:
        self.manifest = manifest
        self.reports: list[dict[str, Any]] = []
        self.diagnostics: list[dict[str, Any]] = []
        self.nodes: list[str] = []
        self.sql_failures: list[dict[str, Any]] = []

    def observe_connection(self, sender: Any, connection: Any, **kwargs: Any) -> None:
        with connection.connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_user,session_user,current_database(),oid::integer "
                "FROM pg_database WHERE datname=current_database()"
            )
            user, session, database, oid = cursor.fetchone()
        identity = {
            "alias": connection.alias,
            "current_user": user,
            "session_user": session,
            "database": database,
            "database_oid": oid,
        }

        def execute(execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
            try:
                return execute(sql, params, many, context)
            except Exception as exc:
                operation = sql.split()[0].upper() if sql.split() else "UNKNOWN"
                # Only operation/table identity is retained, never parameters/PII.
                target = re.search(r"(?:FROM|INTO|UPDATE|TABLE|TRUNCATE)\s+([\w.\"]+)", sql, re.IGNORECASE)
                self.sql_failures.append(
                    {
                        **identity,
                        "operation": operation,
                        "target": target.group(1) if target else None,
                        "sqlstate": getattr(exc.__cause__, "sqlstate", None),
                    }
                )
                raise

        previous = getattr(connection, "_regression_evidence_wrapper", None)
        if previous in connection.execute_wrappers:
            connection.execute_wrappers.remove(previous)
        connection.execute_wrappers.append(execute)
        connection._regression_evidence_wrapper = execute

    def pytest_collection_modifyitems(self, session: Any, config: Any, items: list[Any]) -> None:
        self.nodes = [item.nodeid for item in items]

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item: Any, call: Any) -> Any:
        if call.excinfo:
            error = call.excinfo.value
            chain: list[dict[str, Any]] = []
            while error is not None and len(chain) < 8:
                chain.append({"type": type(error).__name__, "sqlstate": getattr(error, "sqlstate", None)})
                error = error.__cause__
            self.diagnostics.append(
                {"nodeid": item.nodeid, "phase": call.when, "chain": chain, "sql_failures": list(self.sql_failures)}
            )
        self.sql_failures.clear()
        yield

    def pytest_runtest_logreport(self, report: Any) -> None:
        self.reports.append({"nodeid": report.nodeid, "when": report.when, "outcome": report.outcome})

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        self.manifest.write_text(
            json.dumps(
                {
                    "collected": self.nodes,
                    "reports": self.reports,
                    "diagnostics": self.diagnostics,
                    "exit": int(exitstatus),
                },
                indent=2,
            )
            + "\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    settings.DATABASES["replica"]["HOST"] = "postgres_primary"
    primary_test = cast(dict[str, Any], settings.DATABASES["default"].setdefault("TEST", {}))
    primary_test["NAME"] = settings.DATABASES["default"]["NAME"]
    mirror_test = cast(dict[str, Any], settings.DATABASES["replica"].setdefault("TEST", {}))
    mirror_test["MIRROR"] = "default"
    django.setup()
    evidence = Evidence(args.manifest)
    connection_created.connect(evidence.observe_connection, weak=False)
    assert not os.access("/run/complect-secrets/postgres_password", os.R_OK), "runtime can read owner credential"
    with connections["default"].cursor() as cursor:
        cursor.execute(
            "SELECT current_user,session_user,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,"
            "has_database_privilege(current_user,current_database(),'TEMP'),"
            "pg_has_role(current_user,'complect_migrator','MEMBER') "
            "FROM pg_roles WHERE rolname=current_user"
        )
        runtime = list(cursor.fetchone())
        assert runtime == ["complect_app_local", "complect_app_local", False, False, False, False, False, False], (
            runtime
        )
    (args.manifest.parent / "runtime.json").write_text(json.dumps(runtime) + "\n")
    privilege_probes = []
    for alias in ("default", "replica"):
        for sql in (
            "CREATE TEMP TABLE forbidden_runtime_fixture(value text)",
            "TRUNCATE TABLE common_audit_chain",
            "SET ROLE complect_migrator",
        ):
            try:
                with connections[alias].cursor() as cursor:
                    cursor.execute(sql)
            except Exception as exc:
                state = getattr(exc.__cause__, "sqlstate", None)
                assert state == "42501", (alias, state)
                privilege_probes.append(evidence.sql_failures[-1])
            else:
                raise AssertionError("runtime unexpectedly authorized lifecycle SQL")
    (args.manifest.parent / "privilege-probes.json").write_text(json.dumps(privilege_probes, indent=2) + "\n")
    evidence.sql_failures.clear()
    nodes = json.loads(args.nodes.read_text())
    raise SystemExit(pytest.main(["-q", "-o", "cache_dir=/tmp/regression-cache", *nodes], plugins=[evidence]))


if __name__ == "__main__":
    main()
