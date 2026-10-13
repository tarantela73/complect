"""Run a fixed manifest against an independently registered disposable Compose DB.

The migrator service alone owns lifecycle credentials. Pytest gets restricted
runtime credentials and a fixed-operation local socket; neither SQL nor database
names can be supplied through that socket.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from infra.scripts.dev_environment import Commands
from infra.scripts.legacy_preflight import BLOCKED_STAND, run_preflight

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL_NODE_IDS_SHA256 = (
    "56626883e2be0b0573b16630042739ac6b5d72375c7385294ee29693c3c5be02"
)


def require_original_nodes(nodes: list[str]) -> None:
    digest = hashlib.sha256(
        json.dumps(nodes, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        len(nodes) != 133
        or len(set(nodes)) != 133
        or digest != ORIGINAL_NODE_IDS_SHA256
    ):
        raise RuntimeError("expected the exact original 133-node manifest")


def verify_owned(actual: dict, registered: dict, checkout: Path) -> None:
    if not checkout.is_relative_to(ROOT / ".dev/test-artifacts"):
        raise RuntimeError("target is outside task disposable checkouts")
    if set(actual) != set(registered["containers"]):
        raise RuntimeError("disposable service inventory changed")
    for service, container in actual.items():
        expected = registered["containers"][service]
        labels = container["Config"]["Labels"]
        if (
            container["Id"] != expected["id"]
            or container["Image"] != expected["image"]
            or labels.get("com.docker.compose.project") != registered["project"]
            or labels.get("complect.checkout_id") != registered["checkout_id"]
            or container["Mounts"] != expected["mounts"]
        ):
            raise RuntimeError("disposable resource identity mismatch")
    if str(checkout) != registered["root"]:
        raise RuntimeError("disposable source root mismatch")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--ownership", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    os.environ["GIT_OPTIONAL_LOCKS"] = "0"
    checkout = args.root.resolve()
    output = args.output.resolve()
    preflight = run_preflight(
        root=(args.source_root or ROOT).resolve(),
        source_root=checkout,
        snapshot=args.snapshot,
        ownership=args.ownership,
        output=output,
        run_id=args.run_id,
        manifest=args.manifest,
    )
    if preflight.status == BLOCKED_STAND:
        raise SystemExit(2)
    if args.preflight_only:
        raise SystemExit(0)
    registered = json.loads(args.ownership.read_text())
    commands = Commands(checkout)
    if not commands.isolated_e2e:
        raise RuntimeError(
            "regressions require explicit portless disposable configuration"
        )
    if not output.is_relative_to(checkout.parent) or output.is_relative_to(checkout):
        raise RuntimeError(
            "evidence must be outside disposable source, inside its task directory"
        )
    verify_owned(commands.containers(), registered, checkout)
    commands.own_resources(commands.configuration())
    redis = registered["redis"]
    found = json.loads(commands.run(["docker", "inspect", redis["id"]]).stdout)[0]
    if (
        found["Id"] != redis["id"]
        or found["Name"] != "/" + redis["name"]
        or found["Config"]["Labels"].get("complect.checkout_id")
        != registered["checkout_id"]
        or found["Config"]["Labels"].get("complect.task_run") != args.run_id
        or set(found["NetworkSettings"]["Networks"]) != {redis["network"]}
    ):
        raise RuntimeError("disposable Redis identity mismatch")
    aof = commands.run(
        [
            "docker",
            "exec",
            redis["id"],
            "redis-cli",
            "--raw",
            "CONFIG",
            "GET",
            "appendonly",
        ]
    ).stdout.splitlines()
    if aof != ["appendonly", "yes"]:
        raise RuntimeError(
            "disposable Redis must preserve canonical AOF/WAITAOF semantics"
        )
    original = json.loads(args.manifest.read_text())["collected"]
    require_original_nodes(original)
    output.mkdir(parents=True, exist_ok=True)  # Preflight creates the directory first.
    commands.run(
        ["docker", "exec", redis["id"], "redis-cli", "FLUSHALL", "SYNC"], write=True
    )
    (output / "redis-clean-before.txt").write_text(
        commands.run(
            ["docker", "exec", redis["id"], "redis-cli", "INFO", "keyspace"]
        ).stdout
    )
    control = Path(tempfile.mkdtemp(prefix="complect-lifecycle-", dir="/tmp"))
    server_name = commands.project + "-lifecycle-" + os.urandom(4).hex()
    owner_secrets: Path | None = None
    phase = "prepare-secrets"
    try:
        (control / "nodes.json").write_text(json.dumps(original))
        runtime_secrets = control / "runtime-secrets"
        runtime_secrets.mkdir(mode=0o700)
        for source, target in (
            ("app_postgres_password", "app_postgres_password"),
            ("django_secret_key", "django_secret_key"),
            ("gateway_token", "app_gateway_token"),
        ):
            shutil.copyfile(
                checkout / ".dev/secrets" / source, runtime_secrets / target
            )
            (runtime_secrets / target).chmod(0o400)
        aws = control / "aws-credentials"
        aws.write_text(
            "[default]\naws_access_key_id=complect_root_user\naws_secret_access_key="
            + (checkout / ".dev/secrets/minio_password").read_text().strip()
            + "\n"
        )
        aws.chmod(0o600)
        owner_secrets = Path(
            tempfile.mkdtemp(prefix="complect-lifecycle-owner-", dir="/tmp")
        )
        shutil.copyfile(
            checkout / ".dev/secrets/postgres_password", owner_secrets / "db_password"
        )
        (owner_secrets / "db_password").chmod(0o400)
        identity = dict(registered["database"], run_id=args.run_id)
        (control / "target.json").write_text(json.dumps(identity))
        redis_name = registered["redis"]["name"]
        redis_environment = [
            part
            for key, index in (
                ("REDIS_DEFAULT_URL", 0),
                ("REDIS_CACHE_URL", 1),
                ("REDIS_CELERY_URL", 2),
                ("REDIS_STREAMS_URL", 3),
                ("REDIS_IDEMPOTENCY_URL", 4),
                ("CHANNEL_LAYER_URL", 5),
                ("CELERY_RESULT_BACKEND", 6),
                ("REDIS_AUDIT_URL", 7),
            )
            for part in ("-e", f"{key}=redis://{redis_name}:6379/{index}")
        ]
        # Provision only the independently recorded cluster/database, with a
        # persistent run marker outside Django's flush table inventory.
        script = """import json,django
from pathlib import Path
from django.db import connection
django.setup()
expected=json.loads(Path('/control/target.json').read_text())
with connection.cursor() as c:
 c.execute("SELECT current_database(),d.oid,s.system_identifier::text,current_user,session_user FROM pg_database d CROSS JOIN pg_control_system() s WHERE d.datname=current_database()")
 row=c.fetchone()
 assert list(row)==[expected[k] for k in ('database','database_oid','cluster','user','session_user')], 'target identity mismatch'
 c.execute('SELECT to_regnamespace(%s)', ['pytest_lifecycle_control'])
 if c.fetchone()[0] is None:
  c.execute('CREATE SCHEMA pytest_lifecycle_control AUTHORIZATION complect_migrator')
  c.execute('REVOKE ALL ON SCHEMA pytest_lifecycle_control FROM PUBLIC')
  c.execute('CREATE TABLE pytest_lifecycle_control.target(run_id text NOT NULL)')
 else:
  c.execute('SELECT run_id FROM pytest_lifecycle_control.target')
  assert c.fetchone()[0]==expected['run_id'], 'foreign lifecycle marker'
 c.execute('DELETE FROM pytest_lifecycle_control.target')
 c.execute('INSERT INTO pytest_lifecycle_control.target VALUES (%s)',[expected['run_id']])
 for name in ('old_person_ciphertext','pii_legacy_text'):
  c.execute('CREATE TABLE IF NOT EXISTS public.'+name+'(value text)')
  c.execute('GRANT SELECT, INSERT ON public.'+name+' TO complect_app_runtime')
"""
        commands.compose(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--volume",
            f"{control}:/control:z",
            "--entrypoint",
            "python",
            "-e",
            f"POSTGRES_DB={identity['database']}",
            "-e",
            "POSTGRES_USER=complect_migrator",
            "-e",
            "DATABASE_PASSWORD_FILE=/run/complect-lifecycle-owner/db_password",
            "--volume",
            f"{owner_secrets}:/run/complect-lifecycle-owner:ro,z",
            "migrate",
            "-B",
            "-c",
            script,
            write=True,
            timeout=120,
        )
        verify_owned(commands.containers(), registered, checkout)
        phase = "start-lifecycle"
        commands.compose(
            "run",
            "-d",
            "--no-deps",
            "--name",
            server_name,
            "--volume",
            f"{control}:/control:z",
            "--entrypoint",
            "python",
            "-e",
            f"POSTGRES_DB={identity['database']}",
            "-e",
            "POSTGRES_USER=complect_migrator",
            "-e",
            "DATABASE_PASSWORD_FILE=/run/complect-lifecycle-owner/db_password",
            "--volume",
            f"{owner_secrets}:/run/complect-lifecycle-owner:ro,z",
            "migrate",
            "-B",
            "-m",
            "test_support.lifecycle_database",
            "--identity",
            "/control/target.json",
            "--socket",
            "/control/lifecycle.sock",
            "--evidence",
            "/control/lifecycle.jsonl",
            write=True,
            timeout=120,
        )
        for _ in range(100):
            if (control / "lifecycle.sock").exists():
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("lifecycle server did not start")
        # Strictly runtime Compose service. The owner-readable secret is not
        # copied into its environment; application user cannot read it.
        phase = "pytest"
        result = commands.compose(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--volume",
            f"{control}:/control:z",
            "-e",
            "PYTEST_LIFECYCLE_SOCKET=/control/lifecycle.sock",
            "-e",
            f"PYTEST_LIFECYCLE_RUN_ID={args.run_id}",
            "--entrypoint",
            "python",
            "--volume",
            f"{runtime_secrets}:/run/complect-secrets:ro,z",
            "-e",
            f"POSTGRES_DB={identity['database']}",
            "-e",
            f"IAM_TEST_DATABASE={identity['database']}",
            "-e",
            "AWS_SHARED_CREDENTIALS_FILE=/control/aws-credentials",
            "-e",
            "AWS_ENDPOINT_URL_S3=http://minio:9000",
            *redis_environment,
            "-e",
            "POSTGRES_USER=complect_app_local",
            "backend",
            "-B",
            "-m",
            "test_support.regression_runner",
            "--nodes",
            "/control/nodes.json",
            "--manifest",
            "/control/manifest.json",
            write=True,
            timeout=1200,
            check=False,
        )
        (output / "pytest.log").write_text(
            commands.redact(result.stdout + "\n" + result.stderr)
        )
        (output / "pytest.exit").write_text(str(result.returncode) + "\n")
        print("regression exit", result.returncode, flush=True)
    except BaseException as exc:
        (output / "failure.json").write_text(
            json.dumps({"phase": phase, "error": type(exc).__name__}, indent=2) + "\n"
        )
        raise
    finally:
        try:
            found = commands.run(["docker", "inspect", server_name], check=False)
            if found.returncode == 0:
                container = json.loads(found.stdout)[0]
                labels = container["Config"]["Labels"]
                if (
                    labels.get("com.docker.compose.project") != registered["project"]
                    or labels.get("complect.checkout_id") != registered["checkout_id"]
                ):
                    raise RuntimeError("lifecycle container owner mismatch")
                commands.run(
                    ["docker", "stop", "--time", "5", container["Id"]], write=True
                )
                commands.run(["docker", "rm", container["Id"]], write=True)
        except Exception as exc:
            (output / "cleanup-failure.json").write_text(
                json.dumps({"error": type(exc).__name__}) + "\n"
            )
            raise
        finally:
            for name in (
                "manifest.json",
                "runtime.json",
                "lifecycle.jsonl",
                "target.json",
                "privilege-probes.json",
            ):
                if (control / name).exists():
                    shutil.copy2(control / name, output / name)
            try:
                shutil.rmtree(control)
            finally:
                if owner_secrets is not None:
                    shutil.rmtree(owner_secrets)
    if not (output / "manifest.json").is_file():
        (output / "failure.json").write_text(
            json.dumps({"phase": phase, "error": "MissingPytestManifest"}) + "\n"
        )
        raise RuntimeError("pytest failed before producing a phase manifest")
    manifest = json.loads((output / "manifest.json").read_text())
    if manifest["collected"] != original:
        raise RuntimeError("original test manifest changed")
    counts = {}
    for phase in ("setup", "call", "teardown"):
        counts[phase] = {
            kind: sum(
                r["when"] == phase and r["outcome"] == kind for r in manifest["reports"]
            )
            for kind in ("passed", "failed", "skipped")
        }
    (output / "summary.json").write_text(json.dumps(counts, indent=2) + "\n")
    print(json.dumps(counts), flush=True)
    raise SystemExit(int(manifest["exit"]))


if __name__ == "__main__":
    main()
