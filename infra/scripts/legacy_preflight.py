"""Read-only qualification for the fixed legacy regression stand."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from infra.scripts import agent_harness


PASS = "PASS"
BLOCKED_STAND = "BLOCKED_STAND"
ORIGINAL_NODE_IDS_SHA256 = "56626883e2be0b0573b16630042739ac6b5d72375c7385294ee29693c3c5be02"


def require_original_nodes(nodes: object) -> list[str]:
    if not isinstance(nodes, list) or not all(isinstance(node, str) for node in nodes):
        return ["ORIGINAL_MANIFEST_INVALID"]
    digest = hashlib.sha256(json.dumps(nodes, separators=(",", ":")).encode()).hexdigest()
    if len(nodes) != 133 or len(set(nodes)) != 133 or digest != ORIGINAL_NODE_IDS_SHA256:
        return ["ORIGINAL_MANIFEST_MISMATCH"]
    return []


@dataclass(frozen=True)
class PreflightResult:
    status: str
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "errors": list(self.errors)}


def _error(code: str) -> str:
    return code


def _file_kind(path: Path) -> str:
    mode = path.lstat().st_mode
    return "symlink" if stat.S_ISLNK(mode) else "file"


def verify_prepared_checkout(source_root: Path, snapshot: Path) -> list[str]:
    """Compare every included snapshot entry with a prepared checkout."""
    try:
        payload = json.loads(snapshot.read_text())
    except (OSError, ValueError):
        return [_error("SNAPSHOT_UNREADABLE")]
    files = payload.get("files")
    if not isinstance(files, dict):
        return [_error("SNAPSHOT_FILES_INVALID")]
    errors: list[str] = []
    root = source_root.resolve()
    for relative, expected in files.items():
        if not isinstance(relative, str) or not isinstance(expected, dict):
            errors.append("SNAPSHOT_ENTRY_INVALID")
            continue
        candidate = root / relative
        if not candidate.resolve(strict=False).is_relative_to(root) or not candidate.exists():
            errors.append("CHECKOUT_CONTENT_MISMATCH")
            continue
        if _file_kind(candidate) != expected.get("kind"):
            errors.append("CHECKOUT_KIND_MISMATCH")
            continue
        if stat.S_IMODE(candidate.lstat().st_mode) != expected.get("mode"):
            errors.append("CHECKOUT_MODE_MISMATCH")
            continue
        if candidate.is_symlink():
            actual = str(candidate.readlink())
        else:
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual != expected.get("target", expected.get("sha256")):
            errors.append("CHECKOUT_CONTENT_MISMATCH")
    return sorted(set(errors))


def _snapshot_errors(root: Path, snapshot: Path) -> list[str]:
    try:
        payload = json.loads(snapshot.read_text())
        files = payload.get("files")
        required = ("format", "head", "index_entries_sha256", "index_flags_sha256", "files")
        if payload.get("format") != "complect-working-tree-snapshot-v1":
            return ["SNAPSHOT_FORMAT_INVALID"]
        if any(not isinstance(payload.get(field), str) or not payload[field] for field in required[:-1]):
            return ["SNAPSHOT_METADATA_INVALID"]
        if not isinstance(files, dict) or not files:
            return ["SNAPSHOT_FILES_INVALID"]
        for relative, entry in files.items():
            if not isinstance(relative, str) or not relative or not isinstance(entry, dict):
                return ["SNAPSHOT_ENTRY_INVALID"]
            if entry.get("kind") not in {"file", "symlink"} or not isinstance(entry.get("mode"), int):
                return ["SNAPSHOT_ENTRY_INVALID"]
            if entry["kind"] == "file" and not isinstance(entry.get("sha256"), str):
                return ["SNAPSHOT_ENTRY_INVALID"]
            if entry["kind"] == "symlink" and not isinstance(entry.get("target"), str):
                return ["SNAPSHOT_ENTRY_INVALID"]
    except (OSError, ValueError, TypeError):
        return ["SNAPSHOT_INVALID"]
    try:
        errors = agent_harness.verify_working_tree_snapshot(root, snapshot)
    except (OSError, ValueError, subprocess.CalledProcessError):
        return [_error("SNAPSHOT_INVALID")]
    return [_error("SNAPSHOT_MISMATCH")] if errors else []


def _docker_json(run: Callable[..., subprocess.CompletedProcess[str]], *args: str) -> Any:
    try:
        result = run(
            ["docker", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.SubprocessError as exc:
        raise RuntimeError("DOCKER_PROBE_TIMEOUT") from exc
    if result.returncode != 0:
        raise RuntimeError("DOCKER_PROBE_FAILED")
    return json.loads(result.stdout)


def _docker_endpoint_is_local(
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Reject remote Docker before any container inspect or exec probe."""
    endpoint = os.environ.get("DOCKER_HOST", "")
    if endpoint and not endpoint.startswith("unix://"):
        raise RuntimeError("DOCKER_REMOTE_ENDPOINT")
    try:
        selected_context = run(
            ["docker", "context", "show"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if selected_context.returncode != 0:
            raise RuntimeError("DOCKER_CONTEXT_PROBE_FAILED")
        context = selected_context.stdout.strip() or "default"
        if os.environ.get("DOCKER_CONTEXT", context) != context:
            raise RuntimeError("DOCKER_CONTEXT_MISMATCH")
        selected = run(
            [
                "docker",
                "context",
                "inspect",
                context,
                "--format",
                "{{json .Endpoints.docker.Host}}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.SubprocessError as exc:
        raise RuntimeError("DOCKER_ENDPOINT_PROBE_TIMEOUT") from exc
    except OSError as exc:
        raise RuntimeError("DOCKER_ENDPOINT_PROBE_FAILED") from exc
    if selected.returncode != 0:
        raise RuntimeError("DOCKER_ENDPOINT_PROBE_FAILED")
    try:
        selected_endpoint = json.loads(selected.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise RuntimeError("DOCKER_ENDPOINT_PROBE_FAILED") from exc
    if not isinstance(selected_endpoint, str) or not selected_endpoint.startswith(
        "unix://"
    ):
        raise RuntimeError("DOCKER_REMOTE_ENDPOINT")
    if endpoint and endpoint != selected_endpoint:
        raise RuntimeError("DOCKER_ENDPOINT_MISMATCH")


def _check_owned_resources(
    run: Callable[..., subprocess.CompletedProcess[str]],
    ownership: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    containers = ownership.get("containers")
    if not isinstance(containers, dict):
        return ["OWNERSHIP_INVALID"]
    for service, expected in containers.items():
        try:
            actual = _docker_json(run, "inspect", expected["id"])[0]
        except (KeyError, IndexError, OSError, RuntimeError, ValueError, TypeError):
            errors.append("OWNERSHIP_PROBE_FAILED")
            continue
        labels = actual.get("Config", {}).get("Labels", {})
        if (
            actual.get("Id") != expected.get("id")
            or actual.get("Image") != expected.get("image")
            or labels.get("com.docker.compose.project") != ownership.get("project")
            or labels.get("complect.checkout_id") != ownership.get("checkout_id")
            or actual.get("Mounts") != expected.get("mounts")
        ):
            errors.append("RESOURCE_IDENTITY_MISMATCH")
    return errors


def _check_redis(
    run: Callable[..., subprocess.CompletedProcess[str]],
    ownership: dict[str, Any],
    run_id: str,
) -> list[str]:
    redis = ownership.get("redis")
    if not isinstance(redis, dict):
        return ["REDIS_OWNERSHIP_INVALID"]
    try:
        actual = _docker_json(run, "inspect", redis["id"])[0]
    except (KeyError, IndexError, OSError, RuntimeError, ValueError, TypeError):
        return ["REDIS_PROBE_FAILED"]
    labels = actual.get("Config", {}).get("Labels", {})
    networks = set(actual.get("NetworkSettings", {}).get("Networks", {}))
    if (
        actual.get("Id") != redis.get("id")
        or actual.get("Name") != f"/{redis.get('name')}"
        or labels.get("complect.checkout_id") != ownership.get("checkout_id")
        or labels.get("complect.task_run") != run_id
        or networks != {redis.get("network")}
    ):
        return ["REDIS_IDENTITY_MISMATCH"]
    try:
        aof = run(
            [
                "docker",
                "exec",
                redis["id"],
                "redis-cli",
                "--raw",
                "CONFIG",
                "GET",
                "appendonly",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return ["REDIS_AOF_PROBE_TIMEOUT"]
    except OSError:
        return ["REDIS_AOF_PROBE_FAILED"]
    if aof.returncode != 0 or aof.stdout.splitlines() != ["appendonly", "yes"]:
        return ["REDIS_AOF_INVALID"]
    return _check_identity_pair(redis, ownership.get("database"))


def _check_identity_pair(redis: dict[str, Any], database: object) -> list[str]:
    if not isinstance(database, dict):
        return ["DATABASE_IDENTITY_UNAVAILABLE"]
    redis_name = str(redis.get("name", ""))
    db_name = str(database.get("database", ""))
    prefix = "complect-iam-test-"
    if not redis_name.startswith(prefix):
        return ["REDIS_DATABASE_PAIR_MISMATCH"]
    suffix = redis_name.removeprefix(prefix)
    try:
        identity = uuid.UUID(suffix)
    except ValueError:
        return ["REDIS_DATABASE_PAIR_MISMATCH"]
    if suffix not in (identity.hex, str(identity)) or db_name != f"test_iam_5_8_{identity.hex}":
        return ["REDIS_DATABASE_PAIR_MISMATCH"]
    return []


def _check_clamav(
    run: Callable[..., subprocess.CompletedProcess[str]],
    ownership: dict[str, Any],
) -> list[str]:
    expected = ownership.get("containers", {}).get("clamav")
    if not isinstance(expected, dict):
        return ["CLAMAV_OWNERSHIP_INVALID"]
    try:
        result = run(
            ["docker", "exec", expected["id"], "clamdscan", "--ping=1"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ["CLAMAV_PROBE_FAILED"]
    return [] if result.returncode == 0 else ["CLAMAV_UNREADY"]


def _check_database(
    run: Callable[..., subprocess.CompletedProcess[str]],
    ownership: dict[str, Any],
    run_id: str,
) -> list[str]:
    """Check the already-owned PostgreSQL container without creating one."""
    database = ownership.get("database")
    postgres = ownership.get("containers", {}).get("postgres_primary")
    if not isinstance(database, dict) or not isinstance(postgres, dict):
        return ["DATABASE_IDENTITY_UNAVAILABLE"]
    required = ("database", "database_oid", "cluster", "user", "session_user")
    if any(key not in database for key in required):
        return ["DATABASE_IDENTITY_INVALID"]
    query = (
        "SELECT current_database(), d.oid, s.system_identifier::text, "
        "current_user, session_user FROM pg_database d CROSS JOIN pg_control_system() s "
        "WHERE d.datname=current_database()"
    )
    try:
        result = run(
            [
                "docker",
                "exec",
                postgres["id"],
                "psql",
                "--no-password",
                "--tuples-only",
                "--no-align",
                "--dbname",
                str(database["database"]),
                "--username",
                "complect_migrator",
                "--command",
                query,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return ["DATABASE_IDENTITY_PROBE_FAILED"]
    if result.returncode != 0:
        return ["DATABASE_IDENTITY_PROBE_FAILED"]
    actual = result.stdout.strip().split("|")
    expected = [str(database[key]) for key in required]
    if actual != expected:
        return ["DATABASE_IDENTITY_MISMATCH"]
    marker = run(
        [
            "docker",
            "exec",
            postgres["id"],
            "psql",
            "--no-password",
            "--tuples-only",
            "--no-align",
            "--dbname",
            str(database["database"]),
            "--username",
            "complect_migrator",
            "--command",
            "SELECT to_regclass('pytest_lifecycle_control.target')::text",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if marker.returncode != 0:
        return ["DATABASE_MARKER_PROBE_FAILED"]
    if marker.stdout.strip() in {"", "None"}:
        return []
    records = run(
        [
            "docker",
            "exec",
            postgres["id"],
            "psql",
            "--no-password",
            "--tuples-only",
            "--no-align",
            "--dbname",
            str(database["database"]),
            "--username",
            "complect_migrator",
            "--command",
            "SELECT run_id FROM pytest_lifecycle_control.target ORDER BY run_id",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if records.returncode != 0:
        return ["DATABASE_MARKER_PROBE_FAILED"]
    values = [value for value in records.stdout.splitlines() if value]
    if values != [run_id]:
        return ["DATABASE_MARKER_FOREIGN"]
    return []


def _check_runtime_permissions(
    run: Callable[..., subprocess.CompletedProcess[str]],
    ownership: dict[str, Any],
) -> list[str]:
    """Probe the restricted app login from the already-owned backend container."""
    database = ownership.get("database")
    backend = ownership.get("containers", {}).get("backend")
    if not isinstance(database, dict) or not isinstance(backend, dict):
        return ["RUNTIME_PERMISSIONS_UNAVAILABLE"]
    query = (
        "SELECT current_database(), d.oid, r.rolsuper, r.rolbypassrls, "
        "r.rolcreatedb, r.rolcreaterole, "
        "has_database_privilege(current_user, current_database(), 'TEMP'), "
        "EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles mr ON mr.oid=m.roleid "
        "WHERE m.member=r.oid AND mr.rolname='complect_migrator'), "
        "current_user, session_user FROM pg_roles r CROSS JOIN pg_database d "
        "WHERE r.rolname=current_user AND d.datname=current_database()"
    )
    script = (
        "import os; from pathlib import Path; import psycopg; "
        "conn=psycopg.connect(host='postgres_primary', port=5432, "
        "dbname=os.environ['POSTGRES_DB'], user='complect_app_local', "
        "password=Path(os.environ['DATABASE_PASSWORD_FILE']).read_text().strip(), "
        "connect_timeout=10, "
        "options='-c default_transaction_read_only=on -c statement_timeout=10000'); "
        "cur=conn.cursor(); "
        "cur.execute(" + repr(query) + "); "
        "row=cur.fetchone(); "
        "print('|'.join(str(value) for value in row)); "
        "cur.close(); conn.close()"
    )
    try:
        result = run(
            [
                "docker",
                "exec",
                backend["id"],
                "env",
                f"POSTGRES_DB={database['database']}",
                "POSTGRES_USER=complect_app_local",
                "DATABASE_PASSWORD_FILE=/run/complect-secrets/app_postgres_password",
                "python",
                "-c",
                script,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ["RUNTIME_PERMISSIONS_PROBE_FAILED"]
    if result.returncode != 0:
        return ["RUNTIME_PERMISSIONS_PROBE_FAILED"]
    actual = result.stdout.strip().split("|")
    expected = [
        str(database["database"]),
        str(database["database_oid"]),
        "False",
        "False",
        "False",
        "False",
        "False",
        "False",
        "complect_app_local",
        "complect_app_local",
    ]
    return [] if actual == expected else ["RUNTIME_PERMISSIONS_MISMATCH"]


def run_preflight(
    *,
    root: Path,
    source_root: Path,
    snapshot: Path | None,
    ownership: Path,
    output: Path,
    run_id: str,
    manifest: Path | None = None,
    docker_run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> PreflightResult:
    """Run all read-only checks and always write ``preflight.json``."""
    output.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    if snapshot is None:
        errors.append("SNAPSHOT_REQUIRED")
    else:
        errors.extend(_snapshot_errors(root, snapshot))
        errors.extend(verify_prepared_checkout(source_root, snapshot))
    if manifest is None:
        errors.append("ORIGINAL_MANIFEST_REQUIRED")
    else:
        try:
            manifest_payload = json.loads(manifest.read_text())
            errors.extend(require_original_nodes(manifest_payload.get("collected")))
        except (OSError, ValueError):
            errors.append("ORIGINAL_MANIFEST_UNREADABLE")
    if errors:
        result = PreflightResult(BLOCKED_STAND, tuple(sorted(set(errors))))
        (output / "preflight.json").write_text(
            json.dumps(result.as_dict(), indent=2) + "\n"
        )
        return result
    try:
        registered = json.loads(ownership.read_text())
    except (OSError, ValueError):
        registered = {}
        errors.append("OWNERSHIP_UNREADABLE")
    if not isinstance(registered, dict):
        errors.append("OWNERSHIP_INVALID")
        registered = {}
    runner = docker_run or subprocess.run
    try:
        _docker_endpoint_is_local(runner)
    except RuntimeError as exc:
        errors.append(str(exc))
    else:
        errors.extend(_check_owned_resources(runner, registered))
        errors.extend(_check_redis(runner, registered, run_id))
        errors.extend(_check_database(runner, registered, run_id))
        errors.extend(_check_runtime_permissions(runner, registered))
        errors.extend(_check_clamav(runner, registered))
    result = PreflightResult(PASS if not errors else BLOCKED_STAND, tuple(sorted(set(errors))))
    (output / "preflight.json").write_text(json.dumps(result.as_dict(), indent=2) + "\n")
    return result
