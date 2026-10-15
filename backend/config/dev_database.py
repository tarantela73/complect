"""Explicit local development migration preflight; never imported by production settings."""

from __future__ import annotations


def assess_migrations(disk: dict, applied: set[str], policy: dict, receipt: dict | None) -> dict:
    unknown = sorted(applied - set(disk))
    if unknown:
        raise ValueError("Unknown applied migration: " + ", ".join(unknown))
    if applied and receipt is None:
        raise ValueError("Unmanaged existing schema: no development migration receipt; manual preflight required")
    if receipt:
        if receipt.get("state") != "ready":
            raise ValueError("Incomplete migration batch: inspect failure before any retry")
        if set(receipt.get("applied", {})) != applied:
            raise ValueError("Migration receipt and ledger disagree; refusing automatic repair")
        for name, digest in receipt.get("applied", {}).items():
            if name in disk and disk[name]["sha256"] != digest:
                raise ValueError("Applied migration content changed: " + name)
    pending = set(disk) - applied
    gated = pending & set(policy.get("bootstrap_only", []))
    if applied and gated:
        raise ValueError("Gated migration requires explicit release preflight: " + ", ".join(sorted(gated)))
    unreviewed = [
        name
        for name in sorted(pending)
        if disk[name]["project"] and policy.get("approved", {}).get(name) != disk[name]["sha256"]
    ]
    if unreviewed:
        raise ValueError("Unreviewed migration or changed policy input: " + ", ".join(unreviewed))
    return {"pending": sorted(pending)}


def _setup(read_only: bool):
    import os
    import sys
    from pathlib import Path

    base = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(base))
    if os.environ.get("DJANGO_SETTINGS_MODULE") != "config.settings.local":
        raise ValueError("Development lifecycle requires config.settings.local")
    from django.conf import settings

    for configuration in settings.DATABASES.values():
        options = configuration.setdefault("OPTIONS", {})
        options["connect_timeout"] = 5
        if read_only:
            options["options"] = options.get("options", "") + " -c default_transaction_read_only=on"
    import django

    django.setup()
    if settings.IAM_DEFERRED_MERGE_EXECUTION_ENABLED:
        raise ValueError("IAM merge execution must remain disabled in the development lifecycle")
    return base, settings


def _snapshot(base, state_directory):
    import hashlib
    import inspect
    import json
    from pathlib import Path

    from django.db import connection, connections, transaction
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.recorder import MigrationRecorder

    with transaction.atomic(using="default"):
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT extname FROM pg_extension")
            extensions = sorted(row[0] for row in cursor.fetchall())
            cursor.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            role_super, role_bypass = cursor.fetchone()
        executor = MigrationExecutor(connection)
        executor.loader.check_consistent_history(connection)
        disk = {}
        for (app, name), migration in executor.loader.disk_migrations.items():
            path = Path(inspect.getfile(migration.__class__)).resolve()
            disk[f"{app}.{name}"] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "project": path.is_relative_to(base / "apps"),
            }
        applied = {f"{app}.{name}" for app, name in executor.loader.applied_migrations}
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if any(backwards for _, backwards in plan):
            raise ValueError("Reverse migration plan is not allowed")
        receipt_path = state_directory / "schema.json"
        receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else None
        policy = json.loads((base / "config/dev_migration_policy.json").read_text())
        result = assess_migrations(disk, applied, policy, receipt)
        result["pending"] = [f"{migration.app_label}.{migration.name}" for migration, _ in plan]
        result.update(extensions=extensions, superuser=role_super, bypassrls=role_bypass)
    from django.conf import settings

    if settings.POSTGRES_REPLICA_ENABLED:
        replica = connections["replica"]
        with replica.cursor() as cursor:
            cursor.execute("SELECT pg_is_in_recovery()")
            result["replica_recovery"] = bool(cursor.fetchone()[0])
        replica_applied = {f"{app}.{name}" for app, name in MigrationRecorder(replica).applied_migrations()}
        result["replica_current"] = applied <= replica_applied
    else:
        result["replica_recovery"] = None
        result["replica_current"] = None
    result["applied_count"] = len(applied)
    return result, disk, applied, executor


def _write_receipt(directory, content):
    import json
    import os
    import tempfile
    from pathlib import Path

    directory.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(content, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(directory / "schema.json")


def main() -> int:
    import argparse
    import json
    import os
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "plan", "ready", "apply", "schema"])
    arguments = parser.parse_args()
    state_directory = Path(os.getenv("DEV_STATE_DIR", "/var/lib/complect-dev"))
    try:
        base, settings = _setup(read_only=arguments.action != "apply")
        if arguments.action == "schema":
            from config.urls import api

            print(json.dumps(api.get_openapi_schema(), sort_keys=True, separators=(",", ":")))
            return 0
        from django.db import connection

        mutex = 0x434F4D50444556
        locked = False
        if arguments.action == "apply":
            if os.getenv("DEV_SCHEMA_MANAGEMENT") != "1" or settings.DATABASES["default"]["HOST"] != "postgres_primary":
                raise ValueError("Explicit local Compose migration stage is required")
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [mutex])
                locked = bool(cursor.fetchone()[0])
            if not locked:
                raise ValueError("Another development migration stage is running")
        try:
            result, disk, applied, executor = _snapshot(base, state_directory)
            if arguments.action == "apply" and result["pending"]:
                print("FORWARD MIGRATION PLAN (before application): " + ", ".join(result["pending"]), flush=True)
                _write_receipt(
                    state_directory,
                    {"state": "in_progress", "applied": {name: disk[name]["sha256"] for name in applied}},
                )
                # No --fake, backwards target, reconciliation, seed command or database reset.
                executor.migrate(executor.loader.graph.leaf_nodes())
                from config.runtime_role_grants import refresh_runtime_grants

                refresh_runtime_grants()
                from django.db.migrations.recorder import MigrationRecorder

                applied = {f"{app}.{name}" for app, name in MigrationRecorder(connection).applied_migrations()}
                _write_receipt(
                    state_directory, {"state": "ready", "applied": {name: disk[name]["sha256"] for name in applied}}
                )
                result["pending"] = []
                result["applied_count"] = len(applied)
            if arguments.action == "ready" and (result["pending"] or result["replica_current"] is False):
                raise ValueError("Schema is not ready; run make bootstrap or make sync explicitly")
            print(json.dumps(result, sort_keys=True))
            return 0
        finally:
            if locked:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [mutex])
    except ValueError as error:
        print(json.dumps({"error": str(error)}, sort_keys=True))
        return 1
    except Exception as error:
        # SQL exceptions can contain data values. Report the category, never raw SQL/parameters.
        print(json.dumps({"error": "Development database check failed", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
