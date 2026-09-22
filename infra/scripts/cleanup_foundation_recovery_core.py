"""Explicit recovery of the final cleanup index migration in a disposable bootstrap.

This operator-only command is never called by apply/startup. A reference built
with the same approved bytes and a pre-failure empty-target certificate are
mandatory. It cannot adopt an existing environment or recover runtime cleanup.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import stat
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

TARGET = "users.0003b1_cleanup_admission_indexes"
PREVIOUS = "users.0003b_cleanup_admission_expand"
MUTEX = 0x434F4D50444556
COMMENT_PREFIX = "complect-cleanup-recovery:"
TABLES = {
    "uniq_hold_request_idempotency": "users_identity_hold_requests",
    "cleanup_admission_cleanup_idx": "users_identity_cleanup_admissions",
    "cleanup_admission_op_idx": "users_identity_cleanup_admissions",
    "cleanup_live_identity_gin": "users_identity_cleanup_admissions",
    "cleanup_live_actor_gin": "users_identity_cleanup_admissions",
    "cleanup_live_tenant_gin": "users_identity_cleanup_admissions",
    "hold_request_identity_gin": "users_identity_hold_requests",
    "hold_request_actor_gin": "users_identity_hold_requests",
    "hold_request_tenant_gin": "users_identity_hold_requests",
    "uniq_cleanup_live_owner": "users_identity_cleanup_admissions",
    "uniq_cleanup_admission_subject": "users_identity_cleanup_admission_subjects",
    "uniq_cleanup_lifecycle_version": "users_identity_cleanup_lifecycle",
    "uniq_hold_lifecycle_version": "users_identity_hold_lifecycle",
    "uniq_hold_request_subject": "users_identity_hold_request_subjects",
}
CONSTRAINTS = {
    "uniq_hold_request_idempotency",
    "uniq_cleanup_admission_subject",
    "uniq_cleanup_lifecycle_version",
    "uniq_hold_lifecycle_version",
    "uniq_hold_request_subject",
}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _read(path):
    path = Path(path)
    with path.open("rb") as stream:
        info = os.fstat(stream.fileno())
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
            raise ValueError("evidence must be a regular file not writable by group/others")
        raw = stream.read()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(_json(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _evidence_path(path):
    candidate = Path(path)
    root = Path("/evidence").resolve()
    if not candidate.is_absolute() or not candidate.resolve().is_relative_to(root) or candidate.resolve() == root:
        raise ValueError("recovery artifacts and state must stay inside the task evidence mount")
    return candidate


def _setup():
    # Operator tooling is mounted outside the application package. Its only
    # supported execution context is the explicit disposable /app stage.
    if not Path("/app/manage.py").is_file():
        raise ValueError("operator core requires the mounted disposable backend")
    sys.path.insert(0, "/app")
    from config.dev_database import _setup as setup

    _evidence_path(os.environ.get("DEV_STATE_DIR", "/var/lib/complect-dev"))
    base, settings = setup(read_only=False)
    if os.environ.get("DEV_SCHEMA_MANAGEMENT") != "1" or settings.DATABASES["default"]["HOST"] != "postgres_primary":
        raise ValueError("explicit local Compose migration stage is required")
    if settings.POSTGRES_REPLICA_ENABLED:
        raise ValueError("recovery is restricted to the dedicated disposable single-node rehearsal")
    from django.db import connection

    if not connection.get_autocommit() or connection.in_atomic_block:
        raise ValueError("concurrent DDL requires an autocommit connection")
    return base, connection


def _rows(cursor, sql, params=()):
    cursor.execute(sql, params)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def catalog(connection):
    """Semantic definitions of all non-extension application objects, without OIDs."""
    # Extensions are independently bound by name/version/schema. Excluding their
    # owned objects also excludes PostGIS aggregates from pg_get_functiondef().
    visible = "n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'"
    relation = f"""{visible} AND NOT EXISTS (SELECT 1 FROM extension_owned e
        WHERE e.classid='pg_class'::regclass AND e.objid=c.oid)"""
    queries = {
        "schemas": """SELECT nspname AS name,pg_get_userbyid(nspowner) AS owner,nspacl::text AS acl
            FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname <> 'information_schema' ORDER BY 1""",
        "extensions": """SELECT e.extname,e.extversion,n.nspname AS schema,pg_get_userbyid(e.extowner) AS owner
            FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY 1""",
        "relations": f"""SELECT n.nspname AS schema,c.relname AS name,c.relkind AS kind,
            pg_get_userbyid(c.relowner) AS owner,c.relpersistence AS persistence,c.relacl::text AS acl,
            c.relrowsecurity AS rls,c.relforcerowsecurity AS force_rls,c.relreplident AS replica_identity,
            c.reloptions AS options,COALESCE(ts.spcname,'') AS tablespace,
            CASE WHEN c.relkind IN ('v','m') THEN pg_get_viewdef(c.oid) END AS view_definition
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_tablespace ts ON ts.oid=c.reltablespace
            WHERE {relation} AND c.relkind NOT IN ('i','I') ORDER BY 1,2""",
        "columns": f"""SELECT n.nspname AS schema,c.relname AS table_name,a.attname AS name,
            a.attnum AS ordinal,format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull AS not_null,
            a.attidentity AS identity,a.attgenerated AS generated,a.attacl::text AS acl,
            pg_get_expr(ad.adbin,ad.adrelid) AS default_expr
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_attrdef ad ON ad.adrelid=a.attrelid AND ad.adnum=a.attnum
            WHERE {relation} AND a.attnum>0 AND NOT a.attisdropped AND c.relkind IN ('r','p','v','m')
            ORDER BY 1,2,4""",
        "constraints": f"""SELECT n.nspname AS schema,c.relname AS table_name,con.conname AS name,
            con.contype AS type,con.convalidated AS validated,con.condeferrable AS deferrable,
            con.condeferred AS deferred,pg_get_constraintdef(con.oid) AS definition,
            ci.relname AS index_name
            FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_class ci ON ci.oid=con.conindid
            WHERE {relation} ORDER BY 1,2,3""",
        "indexes": f"""SELECT n.nspname AS schema,t.relname AS table_name,c.relname AS name,
            pg_get_userbyid(c.relowner) AS owner,am.amname AS method,
            ix.indisunique AS unique,ix.indisvalid AS valid,ix.indisready AS ready,ix.indislive AS live,
            ix.indisprimary AS primary,ix.indisexclusion AS exclusion,ix.indimmediate AS immediate,
            ix.indisreplident AS replica_identity,ix.indcheckxmin AS check_xmin,
            ix.indnullsnotdistinct AS nulls_not_distinct,c.reloptions AS options,
            COALESCE(ts.spcname,'') AS tablespace,pg_get_indexdef(c.oid) AS definition,
            con.conname AS constraint_name
            FROM pg_index ix JOIN pg_class c ON c.oid=ix.indexrelid JOIN pg_class t ON t.oid=ix.indrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_am am ON am.oid=c.relam
            LEFT JOIN pg_tablespace ts ON ts.oid=c.reltablespace
            LEFT JOIN pg_constraint con ON con.conindid=c.oid AND con.contype IN ('p','u','x')
            WHERE {relation} ORDER BY 1,2,3""",
        "triggers": f"""SELECT n.nspname AS schema,c.relname AS table_name,t.tgname AS name,
            pg_get_triggerdef(t.oid) AS definition,t.tgenabled AS enabled
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE {relation} AND NOT t.tgisinternal ORDER BY 1,2,3""",
        "functions": f"""SELECT n.nspname AS schema,p.proname AS name,p.prokind AS kind,
            pg_get_function_identity_arguments(p.oid) AS args,pg_get_userbyid(p.proowner) AS owner,
            p.proacl::text AS acl,CASE WHEN p.prokind='a' THEN '<aggregate>'
            ELSE pg_get_functiondef(p.oid) END AS definition
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE {visible}
            AND NOT EXISTS (SELECT 1 FROM extension_owned e WHERE e.classid='pg_proc'::regclass AND e.objid=p.oid)
            ORDER BY 1,2,4""",
        "policies": """SELECT schemaname AS schema,tablename AS table_name,policyname AS name,
            permissive,roles,cmd,qual,with_check FROM pg_policies ORDER BY 1,2,3""",
        "sequences": """SELECT schemaname,sequencename,sequenceowner,data_type::text,start_value,min_value,
            max_value,increment_by,cycle,cache_size FROM pg_sequences WHERE schemaname !~ '^pg_' ORDER BY 1,2""",
        "dependencies": """SELECT d.deptype,obj.type AS object_type,obj.schema AS object_schema,
            obj.identity AS object_identity,ref.type AS reference_type,ref.schema AS reference_schema,
            ref.identity AS reference_identity
            FROM pg_depend d
            CROSS JOIN LATERAL pg_identify_object(d.classid,d.objid,d.objsubid) obj
            CROSS JOIN LATERAL pg_identify_object(d.refclassid,d.refobjid,d.refobjsubid) ref
            WHERE obj.schema IS NOT NULL AND obj.schema !~ '^pg_' AND obj.schema <> 'information_schema'
            AND NOT EXISTS (SELECT 1 FROM extension_owned e WHERE e.classid=d.classid AND e.objid=d.objid)
            ORDER BY 1,2,3,4,5,6,7""",
    }
    extension_owned = """WITH RECURSIVE extension_owned(classid,objid) AS (
        SELECT classid,objid FROM pg_depend WHERE deptype='e'
        UNION
        SELECT d.classid,d.objid FROM pg_depend d JOIN extension_owned e
        ON d.refclassid=e.classid AND d.refobjid=e.objid WHERE d.deptype IN ('a','i')
    ) """
    with connection.cursor() as cursor:
        return {key: _rows(cursor, extension_owned + query) for key, query in queries.items()}


def _server_isolation(connection):
    """Verify storage/network/ownership from the PostgreSQL process, not a JSON claim."""
    marker_path = "/run/complect-foundation-task/owner.json"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_setting('data_directory'),pg_read_file('/proc/self/mountinfo'),"
            "pg_read_file('/proc/self/net/dev'),btrim(pg_read_file('/etc/hostname')),pg_read_file(%s)",
            [marker_path],
        )
        data_directory, mountinfo, network_devices, hostname, marker_raw = cursor.fetchone()
        cursor.execute("SELECT count(*) FROM pg_tablespace WHERE spcname NOT IN ('pg_default','pg_global')")
        if cursor.fetchone()[0]:
            raise ValueError("external tablespaces are not supported")
    hostname = hostname.strip()
    mounts = []
    for line in mountinfo.splitlines():
        left, separator, right = line.partition(" - ")
        fields, filesystem = left.split(), right.split()
        if not separator or len(fields) < 6 or len(filesystem) < 3:
            raise ValueError("cannot establish PostgreSQL mount identity")
        mountpoint = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[4])
        mounts.append((mountpoint, set(fields[5].split(",")), filesystem[0]))

    def enclosing(path):
        matching = [row for row in mounts if path == row[0] or path.startswith(row[0].rstrip("/") + "/")]
        if not matching:
            raise ValueError("PostgreSQL mount cannot be identified")
        return max(matching, key=lambda row: len(row[0]))

    if data_directory != "/var/lib/postgresql/data" or enclosing(data_directory)[2] != "tmpfs":
        raise ValueError("PostgreSQL data directory is not the dedicated disposable tmpfs")
    marker_mount = enclosing(marker_path)
    if marker_mount[0] != marker_path or "ro" not in marker_mount[1]:
        raise ValueError("server task marker must be an exact read-only bind mount")
    interfaces = []
    lines = network_devices.splitlines()
    if len(lines) < 3 or "Inter-" not in lines[0] or "face" not in lines[1]:
        raise ValueError("cannot establish PostgreSQL network isolation")
    for line in lines[2:]:
        if line.strip():
            name, separator, counters = line.partition(":")
            if not separator or len(counters.split()) != 16:
                raise ValueError("cannot establish PostgreSQL network interface state")
            interfaces.append(name.strip())
    if interfaces != ["lo"]:
        raise ValueError("PostgreSQL has an external network interface")
    marker = json.loads(marker_raw)
    if marker.get("scope") != "cleanup-foundation-v2-disposable" or not marker.get("task_owner"):
        raise ValueError("server does not have a foundation task ownership marker")
    if not re.fullmatch(r"[0-9a-f]{32}", marker.get("cluster_nonce", "")):
        raise ValueError("invalid server ownership nonce")
    if not re.fullmatch(r"[0-9a-f]{12,64}", hostname):
        raise ValueError("server hostname is not a Docker container identity")
    return {
        "data_directory": data_directory,
        "network_interfaces": interfaces,
        "server_hostname": hostname,
        "task_owner": marker["task_owner"],
        "cluster_nonce": marker["cluster_nonce"],
        "marker_sha256": hashlib.sha256(marker_raw.encode()).hexdigest(),
    }


def _identity(connection):
    with connection.cursor() as cursor:
        cursor.execute("""SELECT s.system_identifier::text,d.oid,current_database(),current_schema(),current_user,
            current_setting('server_version_num'),COALESCE(shobj_description(d.oid,'pg_database'),'')
            FROM pg_control_system() s CROSS JOIN pg_database d WHERE d.datname=current_database()""")
        values = cursor.fetchone()
    identity = dict(
        zip(
            (
                "system_identifier",
                "database_oid",
                "database_name",
                "schema",
                "current_role",
                "server_version_num",
                "database_comment",
            ),
            values,
            strict=True,
        )
    )

    identity["server_isolation"] = _server_isolation(connection)
    return identity


def _inputs(base, connection):
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    executor.loader.check_consistent_history(connection)
    disk = {
        f"{app}.{name}": hashlib.sha256(Path(inspect.getfile(migration.__class__)).read_bytes()).hexdigest()
        for (app, name), migration in executor.loader.disk_migrations.items()
    }
    inputs = {
        "migration_digests": disk,
        "policy_sha256": hashlib.sha256((base / "config/dev_migration_policy.json").read_bytes()).hexdigest(),
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    pending = [
        [migration.app_label + "." + migration.name, backwards]
        for migration, backwards in executor.migration_plan(executor.loader.graph.leaf_nodes())
    ]
    return inputs, executor, pending


def _ledger(connection):
    from django.db.migrations.recorder import MigrationRecorder

    return {f"{app}.{name}" for app, name in MigrationRecorder(connection).applied_migrations()}


def _assert_empty_foundation(connection):
    with connection.cursor() as cursor:
        for table in sorted(set(TABLES.values())):
            cursor.execute(f"SELECT EXISTS (SELECT 1 FROM public.{connection.ops.quote_name(table)})")
            if cursor.fetchone()[0]:
                raise ValueError("foundation contains protocol state; recovery forbidden")


@contextmanager
def _exclusive(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [MUTEX])
        if not cursor.fetchone()[0]:
            raise ValueError("development migration mutex is busy")
        cursor.execute(
            "SELECT current_setting('lock_timeout'),current_setting('statement_timeout'),current_setting('search_path')"
        )
        previous = cursor.fetchone()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT count(*) FROM pg_stat_activity
                WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend'""")
            if cursor.fetchone()[0]:
                raise ValueError("quiescence missing: database has another client session")
            cursor.execute("SET lock_timeout='2s'; SET statement_timeout='30s'; SET search_path=public,pg_catalog")
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('lock_timeout',%s,false),set_config('statement_timeout',%s,false),"
                "set_config('search_path',%s,false)",
                previous,
            )
            cursor.execute("SELECT pg_advisory_unlock(%s)", [MUTEX])


def _target_index(row):
    return row["schema"] == "public" and TABLES.get(row["name"]) == row["table_name"]


def _target_constraint(row):
    return row["name"] in CONSTRAINTS and _target_index(row)


def _object_is_target(kind, schema, identity, indexes, constraints):
    return schema == "public" and (
        (kind == "index" and identity in indexes) or (kind == "table constraint" and identity in constraints)
    )


def _verify_partial(actual, expected):
    expected_indexes = {row["name"]: row for row in expected["indexes"] if _target_index(row)}
    if set(expected_indexes) != set(TABLES):
        raise ValueError("reference does not have the exact foundation index set")
    expected_constraints = {row["name"]: row for row in expected["constraints"] if _target_constraint(row)}
    if set(expected_constraints) != CONSTRAINTS:
        raise ValueError("reference constraints incomplete")
    actual_indexes = [row for row in actual["indexes"] if _target_index(row)]
    for row in actual_indexes:
        golden = expected_indexes[row["name"]]
        ignored = {"valid", "ready", "constraint_name"}
        if {key: value for key, value in row.items() if key not in ignored} != {
            key: value for key, value in golden.items() if key not in ignored
        }:
            raise ValueError("unexpected foundation index definition/properties")
        if not row["live"] or row["replica_identity"] or (row["valid"] and not row["ready"]):
            raise ValueError("unexpected foundation index lifecycle")
        if row["constraint_name"] not in (None, golden["constraint_name"]):
            raise ValueError("unexpected index constraint")
        if row["constraint_name"] and (not row["valid"] or not row["ready"]):
            raise ValueError("attached constraint index is not valid/ready")
    for row in actual["constraints"]:
        if _target_constraint(row) and row != expected_constraints[row["name"]]:
            raise ValueError("unexpected foundation constraint definition")
    index_ids = {f"public.{name}" for name in TABLES}
    constraint_ids = {f"{name} on public.{TABLES[name]}" for name in CONSTRAINTS}

    def target_dependency(row):
        return _object_is_target(
            row["object_type"], row["object_schema"], row["object_identity"], index_ids, constraint_ids
        )

    expected_deps = {_json(row) for row in expected["dependencies"]}
    for row in actual["dependencies"]:
        references_target = _object_is_target(
            row["reference_type"], row["reference_schema"], row["reference_identity"], index_ids, constraint_ids
        )
        if references_target and _json(row) not in expected_deps:
            raise ValueError("unexpected dependency on foundation index/constraint")
        if target_dependency(row) and _json(row) not in expected_deps:
            # Before UNIQUE USING INDEX, the standalone index has the same
            # column dependencies that the final unique constraint will own.
            name = row["object_identity"].removeprefix("public.")
            if row["object_type"] != "index" or name not in CONSTRAINTS or row["deptype"] != "a":
                raise ValueError("unexpected foundation index dependency")
            counterpart = dict(row, object_type="table constraint", object_identity=f"{name} on public.{TABLES[name]}")
            if _json(counterpart) not in expected_deps:
                raise ValueError("unexpected unattached unique-index dependency")

    def stripped(value):
        result = dict(value)
        result["indexes"] = [row for row in value["indexes"] if not _target_index(row)]
        result["constraints"] = [row for row in value["constraints"] if not _target_constraint(row)]
        result["dependencies"] = [row for row in value["dependencies"] if not target_dependency(row)]
        return result

    if stripped(actual) != stripped(expected):
        raise ValueError("schema differs outside the exact incomplete foundation index batch")
    return actual_indexes


def reference(args):
    base, connection = _setup()
    from config.dev_database import _snapshot

    with _exclusive(connection):
        result, _, applied, _ = _snapshot(base, Path(args.state_dir))
        inputs, _, pending = _inputs(base, connection)
        if result["pending"] or pending or set(inputs["migration_digests"]) != applied or TARGET not in applied:
            raise ValueError("reference requires the complete approved ready bootstrap")
        _assert_empty_foundation(connection)
        snapshot = catalog(connection)
        _verify_partial(snapshot, snapshot)
        for row in snapshot["indexes"]:
            if _target_index(row) and (not row["valid"] or not row["ready"] or row["replica_identity"]):
                raise ValueError("reference foundation indexes are not valid/ready")
        _write_new(
            args.output,
            {
                "kind": "cleanup-foundation-reference-v2",
                "inputs": inputs,
                "identity": _identity(connection),
                "catalog": snapshot,
                "applied": sorted(applied),
            },
        )
    print(_json({"state": "reference_written"}))


def prepare(args):
    base, connection = _setup()
    ref, reference_digest = _read(args.reference)
    attestation, _ = _read(args.network_attestation)
    from config.dev_database import _snapshot

    with _exclusive(connection):
        inputs, _, pending = _inputs(base, connection)
        identity = _identity(connection)
        if ref.get("kind") != "cleanup-foundation-reference-v2" or ref["inputs"] != inputs:
            raise ValueError("reference build/policy mismatch")
        for key in ("system_identifier", "schema", "current_role", "server_version_num"):
            if ref["identity"][key] != identity[key]:
                raise ValueError("reference server/role/schema mismatch")
        for key, value in {
            "network_mode": "none",
            "owned_by_harness": True,
            "postgres_tmpfs": True,
            "no_published_ports": True,
            "task_token": args.task_token,
            "system_identifier": identity["system_identifier"],
            "database_name": identity["database_name"],
            "current_role": identity["current_role"],
        }.items():
            if attestation.get(key) != value:
                raise ValueError(f"ownership/isolation attestation mismatch: {key}")
        container_id = attestation.get("container_id", "")
        isolation = identity["server_isolation"]
        if not re.fullmatch(r"[0-9a-f]{64}", container_id) or not container_id.startswith(isolation["server_hostname"]):
            raise ValueError("attestation Docker identity differs from actual PostgreSQL container")
        for key in ("task_owner", "cluster_nonce", "marker_sha256", "server_hostname"):
            if attestation.get(key) != isolation[key]:
                raise ValueError(f"live server ownership mismatch: {key}")
        if not args.task_token:
            raise ValueError("missing task token")
        state = Path(os.environ.get("DEV_STATE_DIR", "/var/lib/complect-dev"))
        if (state / "schema.json").exists() or _ledger(connection) or identity["database_comment"]:
            raise ValueError("prepare requires an unused empty target and no receipt")
        _snapshot(base, state)  # Existing approved/hash/bootstrap policy, unchanged.
        if not pending or pending[-1] != [TARGET, False] or any(back for _, back in pending):
            raise ValueError("target index migration must be the final forward migration")
        if {name for name, _ in pending} != set(ref["applied"]):
            raise ValueError("prepared bootstrap plan differs from complete reference")
        snapshot = catalog(connection)
        if any(value for key, value in snapshot.items() if key not in ("schemas", "extensions")):
            raise ValueError("target contains non-extension schema objects")
        if snapshot["extensions"] != ref["catalog"]["extensions"]:
            raise ValueError("target extensions differ from reference")
        with connection.cursor() as cursor:
            name = connection.ops.quote_name(identity["database_name"])
            cursor.execute(f"COMMENT ON DATABASE {name} IS %s", [COMMENT_PREFIX + args.task_token])
        _write_new(
            args.output,
            {
                "kind": "cleanup-foundation-prepare-v2",
                "identity": _identity(connection),
                "task_token": args.task_token,
                "attestation": attestation,
                "inputs": inputs,
                "reference_sha256": reference_digest,
                "migration_plan": pending,
                "initial_catalog": snapshot,
                "initial_receipt": None,
                "initial_ledger": [],
            },
        )
    print(_json({"state": "prepared"}))


def recover(args):
    base, connection = _setup()
    ref, reference_digest = _read(args.reference)
    plan, _ = _read(args.plan)
    from config.dev_database import _snapshot, _write_receipt

    with _exclusive(connection):
        inputs, executor, pending = _inputs(base, connection)
        if plan.get("kind") != "cleanup-foundation-prepare-v2" or plan.get("task_token") != args.task_token:
            raise ValueError("prepared ownership evidence mismatch")
        if plan["reference_sha256"] != reference_digest or plan["inputs"] != inputs or ref["inputs"] != inputs:
            raise ValueError("prepared reference/build/policy hashes changed")
        if (
            _identity(connection) != plan["identity"]
            or plan["identity"]["database_comment"] != COMMENT_PREFIX + args.task_token
        ):
            raise ValueError("prepared database/server/schema/role identity changed")
        original = plan["migration_plan"]
        if not original or original[-1] != [TARGET, False] or any(back for _, back in original):
            raise ValueError("invalid original bootstrap plan")
        if plan["initial_ledger"] or plan["initial_receipt"] is not None:
            raise ValueError("existing-environment adoption is forbidden")
        complete = {name for name, _ in original}
        if complete != set(ref["applied"]) or complete != set(inputs["migration_digests"]):
            raise ValueError("original bootstrap plan no longer matches reference")
        ledger = _ledger(connection)
        state = Path(args.state_dir)
        receipt, _ = _read(state / "schema.json")
        before = catalog(connection)
        _assert_empty_foundation(connection)
        if ledger == complete and not pending:
            if before != ref["catalog"]:
                raise ValueError("completed schema differs from reference")
            if receipt.get("state") == "ready":
                _snapshot(base, state)
                print(_json({"state": "already_ready"}))
                return
            if receipt != {"state": "in_progress", "applied": {}}:
                raise ValueError("original interrupted bootstrap receipt required")
        else:
            if ledger != complete - {TARGET} or pending != [[TARGET, False]] or PREVIOUS not in ledger:
                raise ValueError("only the exact final foundation index migration can be resumed")
            if receipt != {"state": "in_progress", "applied": {}}:
                raise ValueError("original interrupted bootstrap receipt required")
            owned_indexes = _verify_partial(before, ref["catalog"])
            _write_new(
                state / f"cleanup-recovery-before-{uuid.uuid4().hex}.json",
                {
                    "identity": _identity(connection),
                    "ledger": sorted(ledger),
                    "receipt": receipt,
                    "catalog": before,
                    "inputs": inputs,
                    "pending": pending,
                    "reference_sha256": reference_digest,
                },
            )
            # The entire catalog has passed before the first DROP. A constraint
            # removes its owned index; the other exact indexes are dropped
            # concurrently. No CASCADE/IF EXISTS and no broad name-prefix DDL.
            attached = {row["name"] for row in owned_indexes if row["constraint_name"]}
            with connection.cursor() as cursor:
                for name in sorted(attached):
                    cursor.execute(
                        f"ALTER TABLE public.{connection.ops.quote_name(TABLES[name])} "
                        f"DROP CONSTRAINT {connection.ops.quote_name(name)}"
                    )
                for row in sorted(owned_indexes, key=lambda row: row["name"]):
                    if row["name"] not in attached:
                        cursor.execute(f"DROP INDEX CONCURRENTLY public.{connection.ops.quote_name(row['name'])}")
            # Explicit continuation of the certified original fresh bootstrap,
            # not assess_migrations bypass for an existing-environment upgrade.
            executor.migrate(executor.loader.graph.leaf_nodes())
            if _ledger(connection) != complete or catalog(connection) != ref["catalog"]:
                raise ValueError("post-recovery schema/ledger verification failed")
        actual = _ledger(connection)
        _write_receipt(
            state, {"state": "ready", "applied": {name: inputs["migration_digests"][name] for name in actual}}
        )
        _snapshot(base, state)
    print(_json({"state": "recovered", "migration": TARGET}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    ref = commands.add_parser("reference")
    ref.add_argument("--output", required=True)
    ref.add_argument("--state-dir", default=os.environ.get("DEV_STATE_DIR", "/var/lib/complect-dev"))
    ref.set_defaults(run=reference)
    prep = commands.add_parser("prepare")
    prep.add_argument("--reference", required=True)
    prep.add_argument("--network-attestation", required=True)
    prep.add_argument("--task-token", required=True)
    prep.add_argument("--output", required=True)
    prep.set_defaults(run=prepare)
    recovery = commands.add_parser("recover")
    recovery.add_argument("--reference", required=True)
    recovery.add_argument("--plan", required=True)
    recovery.add_argument("--task-token", required=True)
    recovery.add_argument("--state-dir", required=True)
    recovery.set_defaults(run=recover)
    args = parser.parse_args()
    try:
        for name in ("reference", "plan", "output", "network_attestation", "state_dir"):
            value = getattr(args, name, None)
            if value:
                _evidence_path(value)
        args.run(args)
    except (ValueError, OSError) as error:
        print(_json({"error": str(error)}))
        return 1
    except Exception as error:
        # Database exceptions may contain values; preserve only their category.
        print(_json({"error": "cleanup migration recovery failed", "error_type": type(error).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
