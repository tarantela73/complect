---
title: DROP INDEX CONCURRENTLY in RunSQL.reverse_sql triggers ActiveSqlTransaction
date: 2026-06-25
category: database-issues
module: apps.tasks.migrations
problem_type: database_issue
component: database
severity: high
symptoms:
  - DROP INDEX CONCURRENTLY fails with psycopg.errors.ActiveSqlTransaction during migration unapply
  - Error occurs even when Migration.atomic = False and connection.in_atomic_block reports False
  - Forward migration with identical SQL in RunSQL.sql succeeds; only the backward path fails
root_cause: wrong_api
resolution_type: code_fix
tags:
  - django-migrations
  - postgresql
  - concurrent-index
  - psycopg3
  - atomic-false
  - reverse-migration
  - drop-index-concurrently
  - runpython
---

# DROP INDEX CONCURRENTLY in RunSQL.reverse_sql triggers ActiveSqlTransaction

## Problem

Django 5.x + psycopg3 (psycopg 3.3.4): `DROP INDEX CONCURRENTLY` in `RunSQL.reverse_sql` raises `psycopg.errors.ActiveSqlTransaction` during backward migration, even when `atomic = False` is set on the migration class and `connection.in_atomic_block` reports `False`.

## Symptoms

- `psycopg.errors.ActiveSqlTransaction: DROP INDEX CONCURRENTLY cannot run inside a transaction block`
- Occurs exclusively in the **backward** (unapply) direction via `migrate <app> <prev_migration>`
- Forward migration with identical SQL in `RunSQL.sql` succeeds without issue
- `connection.in_atomic_block` returns `False` at the point of failure
- `connection.connection.autocommit` returns `True`
- Django's `SchemaEditor` reports `atomic = False`, yet the error still fires

## What Didn't Work

**Splitting into two separate `RunSQL` operations:**

```python
# Still fails — each reverse_sql still dispatches through schema_editor.execute()
migrations.RunSQL(
    sql="DROP INDEX CONCURRENTLY IF EXISTS tasks_attachments_active_unique;",
    reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS tasks_attachments_active_unique;",
),
migrations.RunSQL(
    sql="CREATE UNIQUE INDEX CONCURRENTLY ...",
    reverse_sql="CREATE UNIQUE INDEX CONCURRENTLY ...",
),
```

Splitting does not change the execution path — both `reverse_sql` strings still go through `schema_editor.execute()`.

**Multi-statement SQL in a single `RunSQL`:**

```python
reverse_sql="""
    DROP INDEX CONCURRENTLY IF EXISTS tasks_attachments_active_unique;
    CREATE UNIQUE INDEX CONCURRENTLY ...;
"""
```

Same failure. The problem is the dispatcher (`schema_editor.execute()`), not the SQL content.

**Verifying connection state at runtime:**

Instrumentation confirmed `in_atomic_block=False` and `autocommit=True` at the point of the `execute()` call. The error does not come from an explicit Django transaction wrapper — it originates inside psycopg3's connection state management when `schema_editor.execute()` drives DDL in the backward path.

## Solution

Replace `reverse_sql` strings with a `RunPython` operation whose reverse callable uses **direct `connection.cursor()` calls**, bypassing `schema_editor.execute()` entirely.

**Before (broken):**

```python
class Migration(migrations.Migration):
    atomic = False

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE UNIQUE INDEX CONCURRENTLY tasks_attachments_active_unique
                ON tasks_attachments (task_id, file_id, tenant_id)
                WHERE is_deleted = FALSE;
            """,
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS tasks_attachments_active_unique;",
        ),
    ]
```

**After (working):**

```python
from django.db import connection, migrations


def _reverse_index(apps, schema_editor):
    # schema_editor.execute() triggers ActiveSqlTransaction for CONCURRENTLY DDL
    # in the backward path; bypass via direct connection cursor.
    with connection.cursor() as cursor:
        cursor.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS tasks_attachments_active_unique;"
        )
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE UNIQUE INDEX CONCURRENTLY tasks_attachments_active_unique
            ON tasks_attachments (task_id, file_id)
            WHERE is_deleted = FALSE;
            """
        )


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("tasks", "0003_taskcomment"),
    ]

    operations = [
        migrations.RunSQL(
            sql="DROP INDEX CONCURRENTLY IF EXISTS tasks_attachments_active_unique;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="""
                CREATE UNIQUE INDEX CONCURRENTLY tasks_attachments_active_unique
                ON tasks_attachments (task_id, file_id, tenant_id)
                WHERE is_deleted = FALSE;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunPython(
            migrations.RunPython.noop,
            _reverse_index,
        ),
    ]
```

Key points:
- Forward `RunSQL` operations remain unchanged — they work correctly in the forward path
- All `reverse_sql` strings are replaced with `migrations.RunSQL.noop`
- The `RunPython` reverse callable issues each `CONCURRENTLY` statement in its own `connection.cursor()` context — `django.db.connection`, not `schema_editor.connection`
- Separate `with connection.cursor()` blocks per statement are required (see Prevention rule 3)

## Why This Works

`schema_editor.execute()` in Django's `DatabaseSchemaEditor` wraps DDL execution with internal state management that, under psycopg3 3.x, places the underlying libpq connection into a deferred-autocommit mode during the backward migration dispatch loop. PostgreSQL's `CONCURRENTLY` DDL interrogates the libpq connection state directly — if the connection has entered any non-autocommit mode at the C level (even transiently), PostgreSQL rejects it with `ActiveSqlTransaction`.

The critical asymmetry: the **forward** path calls `schema_editor.execute()` during `apply_operations()`, which sets up `autocommit=True` cleanly before any SQL runs. The **backward** path goes through `unapply_operations()` → `RunSQL.database_backwards()`, arriving at the cursor through a different code path that leaves the libpq transaction status in a non-`PQTRANS_IDLE` state. Django's `in_atomic_block` is a Python-level boolean and does not reflect actual libpq transaction status.

`django.db.connection.cursor()` bypasses the schema editor entirely. Since `atomic = False` sets `autocommit=True` at migration start, a direct cursor call gets a genuinely idle connection from PostgreSQL's perspective — satisfying the `CONCURRENTLY` precondition.

## Prevention

1. **Never use `reverse_sql` strings for `CONCURRENTLY` DDL.** `RunSQL.reverse_sql` is safe only for transactional DDL (column adds, `NOT VALID` constraints, etc.). Any `CREATE INDEX CONCURRENTLY` or `DROP INDEX CONCURRENTLY` in the backward direction must go through `RunPython`.

2. **Standard template for `CONCURRENTLY` migrations:**

```python
from django.db import connection, migrations


def _reverse_<name>(apps, schema_editor):
    with connection.cursor() as cursor:
        cursor.execute("DROP INDEX CONCURRENTLY IF EXISTS <forward_index_name>;")
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY <prev_index_name> "
            "ON <table> (<prev_columns>) WHERE <prev_predicate>;"
        )


class Migration(migrations.Migration):
    atomic = False

    operations = [
        migrations.RunSQL(sql="<forward DDL>", reverse_sql=migrations.RunSQL.noop),
        migrations.RunPython(migrations.RunPython.noop, _reverse_<name>),
    ]
```

3. **One cursor per `CONCURRENTLY` statement.** Do not reuse a cursor across `DROP ... CONCURRENTLY` and `CREATE ... CONCURRENTLY` in the same `with` block. Open a fresh `connection.cursor()` for each.

4. **`atomic = False` is necessary but not sufficient.** It prevents Django from wrapping the migration in `BEGIN`/`COMMIT`, but does not guarantee `PQTRANS_IDLE` state when `schema_editor.execute()` dispatches `reverse_sql`. Only bypassing `schema_editor.execute()` via `connection.cursor()` gives that guarantee under psycopg3.

5. **Test the backward path in CI:**

```python
def test_migration_backward(migrator):
    migrator.apply("tasks", "0004_taskattachment_fix_unique_idx_tenant")
    migrator.rollback("tasks", "0003_taskcomment")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = %s",
            ["tasks_attachments_active_unique"],
        )
        row = cursor.fetchone()
    assert row is not None
    assert "tenant_id" not in row[0]  # previous index did not include tenant_id
```

6. **Code review rule:** any `RunSQL` with a `reverse_sql` containing `CONCURRENTLY` must be flagged and converted to the `RunPython` pattern above.

## Related Issues

- [backend/apps/tasks/migrations/0004_taskattachment_fix_unique_idx_tenant.py](../../../backend/apps/tasks/migrations/0004_taskattachment_fix_unique_idx_tenant.py) — the migration containing the fix
- [docs/solutions/patterns/cursor-pagination.md](../patterns/cursor-pagination.md) — references `CREATE INDEX CONCURRENTLY` for cursor index migrations (forward path only; backward path not covered)
