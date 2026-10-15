"""Disposable rehearsal subprocess; never imported by application code.

Runs unchanged migration SQL. INVALID is produced by cancelling a real
concurrent build while it waits for a transaction holding ROW EXCLUSIVE.
No PostgreSQL system catalog is modified.
"""

import importlib.util
import json
import os
import sys
import threading
import time

import django
import psycopg
from django.db import connection

from config import dev_database

django.setup()

mode = sys.argv[1]
recovery_args = sys.argv[2:]
fired = False
error = []
blocker = None
thread = None
first = "CREATE UNIQUE INDEX CONCURRENTLY uniq_hold_request_idempotency "


def interrupt_build(pid):
    try:
        with psycopg.connect(
            dbname=os.environ["POSTGRES_DB"], user="foundation", host="127.0.0.1", autocommit=True
        ) as observer:
            for _ in range(200):
                row = observer.execute(
                    "SELECT i.indisvalid,i.indisready FROM pg_index i "
                    "JOIN pg_class c ON c.oid=i.indexrelid "
                    "WHERE c.relname='uniq_hold_request_idempotency'"
                ).fetchone()
                if row is not None:
                    print("REAL_CONCURRENT_BUILD_CATALOG=" + json.dumps(row), flush=True)
                    observer.execute("SELECT pg_cancel_backend(%s)", (pid,))
                    return
                time.sleep(0.025)
            error.append("concurrent index catalog did not appear")
            observer.execute("SELECT pg_cancel_backend(%s)", (pid,))
    except Exception as exc:
        error.append(type(exc).__name__)


def inject(execute, sql, params, many, context):
    global fired, blocker, thread
    if not fired and sql.startswith(first):
        fired = True
        if mode == "missing":
            raise RuntimeError("CONTROLLED_FAILURE_BEFORE_FIRST_INDEX")
        if mode in ("invalid", "timeout"):
            blocker = psycopg.connect(dbname=os.environ["POSTGRES_DB"], user="foundation", host="127.0.0.1")
            blocker.execute("LOCK TABLE public.users_identity_hold_requests IN ROW EXCLUSIVE MODE")
            if mode == "invalid":
                pid = connection.connection.info.backend_pid
                thread = threading.Thread(target=interrupt_build, args=(pid,))
                thread.start()
            return execute(sql, params, many, context)
        result = execute(sql, params, many, context)
        if mode == "valid":
            raise RuntimeError("CONTROLLED_VALID_INDEX_BEFORE_CONSTRAINT_AND_LEDGER")
        return result
    if mode == "multiple" and sql.startswith("CREATE INDEX CONCURRENTLY cleanup_live_identity_gin "):
        raise RuntimeError("CONTROLLED_PARTIAL_INDEX_SET")
    if mode == "recorded" and sql.startswith('INSERT INTO "django_migrations"') and params:
        if "0003b1_cleanup_admission_indexes" in params:
            execute(sql, params, many, context)
            raise RuntimeError("CONTROLLED_FAILURE_AFTER_MIGRATION_LEDGER_BEFORE_RECEIPT")
    return execute(sql, params, many, context)


with connection.cursor() as cursor:
    cursor.execute("SELECT current_setting('lock_timeout'),current_setting('statement_timeout')")
    initial_timeouts = cursor.fetchone()

sys.argv = ["dev_database", "apply"]
try:
    with connection.execute_wrapper(inject):
        if recovery_args:
            spec = importlib.util.spec_from_file_location(
                "cleanup_foundation_operator", "/infra/scripts/cleanup_foundation_recovery_core.py"
            )
            operator = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(operator)
            sys.argv = ["cleanup_foundation_recovery", *recovery_args]
            status = operator.main()
        else:
            status = dev_database.main()
finally:
    if blocker:
        blocker.rollback()
        blocker.close()
    if thread:
        thread.join(timeout=10)
with connection.cursor() as cursor:
    cursor.execute("SELECT current_setting('lock_timeout'),current_setting('statement_timeout')")
    assert cursor.fetchone() == initial_timeouts, "session timeouts were not restored"
connection.close()
print(json.dumps({"mode": mode, "fired": fired, "injection_errors": error, "exit": status}))
raise SystemExit(0 if fired and status == 1 and not error else 2)
