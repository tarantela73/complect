"""Identity checks and external lifecycle target for the disposable Payments stand."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import django
from django.db import connection


def prepare(run_id: str) -> None:
    from config.runtime_role_grants import _assert_principal_contract

    with connection.cursor() as cursor:
        _assert_principal_contract(cursor)
        cursor.execute(
            "SELECT current_database(), d.oid, s.system_identifier::text, current_user, session_user "
            "FROM pg_database d CROSS JOIN pg_control_system() s WHERE d.datname=current_database()"
        )
        row = cursor.fetchone()
        target = dict(
            zip(("database", "database_oid", "cluster", "user", "session_user"), row, strict=True), run_id=run_id
        )
        assert target["database"] == "payments_gate"
        tables = (
            "django_migrations",
            "payments_bank_statements",
            "payments_bank_statement_lines",
            "common_transactional_outbox",
        )
        for table in tables:
            cursor.execute("SELECT to_regclass(%s)", [table])
            assert cursor.fetchone()[0], f"required table missing: {table}"
        cursor.execute("CREATE SCHEMA pytest_lifecycle_control AUTHORIZATION complect_migrator")
        cursor.execute("REVOKE ALL ON SCHEMA pytest_lifecycle_control FROM PUBLIC")
        cursor.execute("CREATE TABLE pytest_lifecycle_control.target(run_id text NOT NULL)")
        cursor.execute("INSERT INTO pytest_lifecycle_control.target VALUES (%s)", [run_id])
        # Existing fixed-operation lifecycle inventories these two compatibility fixtures.
        for table in ("old_person_ciphertext", "pii_legacy_text"):
            cursor.execute(f"CREATE TABLE public.{table}(value text)")
            cursor.execute(f"GRANT SELECT, INSERT ON public.{table} TO complect_app_runtime")
    Path("/control/target.json").write_text(json.dumps(target) + "\n")


def runtime() -> None:
    target = json.loads(Path("/control/target.json").read_text())
    assert os.environ["POSTGRES_USER"] == "complect_app_local"
    assert os.environ["DATABASE_HOST"] == "postgres_primary"
    assert not Path("/run/complect-secrets/postgres_password").exists()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_user, session_user, current_database(), d.oid, r.rolsuper, r.rolbypassrls, "
            "r.rolcreatedb, r.rolcreaterole, has_database_privilege(current_user,current_database(),'TEMP'), "
            "pg_has_role(current_user,'complect_migrator','MEMBER') "
            "FROM pg_database d CROSS JOIN pg_roles r WHERE d.datname=current_database() AND r.rolname=current_user"
        )
        row = cursor.fetchone()
        assert list(row) == [
            "complect_app_local",
            "complect_app_local",
            target["database"],
            target["database_oid"],
            False,
            False,
            False,
            False,
            False,
            False,
        ], "unsafe runtime or wrong target"
    Path("/control/runtime.json").write_text(
        json.dumps(
            {
                "principal": row[0],
                "session_user": row[1],
                "database": row[2],
                "database_oid": row[3],
                "superuser": row[4],
                "bypassrls": row[5],
                "privileges": list(row[4:10]),
            }
        )
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "runtime"))
    parser.add_argument("--run-id")
    args = parser.parse_args()
    django.setup()
    if args.action == "prepare":
        if not args.run_id:
            parser.error("prepare requires --run-id")
        prepare(args.run_id)
    else:
        runtime()


if __name__ == "__main__":
    main()
