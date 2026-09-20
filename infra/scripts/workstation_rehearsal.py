"""Explicit disposable-checkout acceptance test; never run against developer data.

Requires a fresh checkout and a local Docker daemon. Creates only the checkout's
new resources through bootstrap, adds a verification-only sentinel, and retains
all volumes for inspection. It never cleans up or imports existing volumes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-candidate-patch", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        raise SystemExit("Evidence output must be outside the checkout")
    output.mkdir(parents=True, exist_ok=True)
    results = []
    fixture = "acceptance_only_" + uuid.uuid4().hex
    (output / "fixture.json").write_text(
        json.dumps(
            {
                "schema": fixture,
                "migration_app": fixture,
                "migration_name": "synthetic_unknown_branch",
            }
        )
        + "\n"
    )
    environment = dict(os.environ, GIT_OPTIONAL_LOCKS="0", PYTHONUNBUFFERED="1")
    cli = [sys.executable, "-B", "infra/scripts/dev_environment.py", "compose"]

    def run(command, *, label=None, expected=0, env=None):
        result = subprocess.run(
            command, cwd=ROOT, env=env or environment, capture_output=True, text=True
        )
        if label:
            (output / (label + ".log")).write_text(result.stdout + result.stderr)
            results.append(
                {
                    "name": label,
                    "command": command,
                    "exit_code": result.returncode,
                    "expected": expected,
                }
            )
            (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
            print(
                label, "PASS" if result.returncode == expected else "FAIL", flush=True
            )
        if result.returncode != expected:
            raise RuntimeError(
                f"{label or command[0]} returned {result.returncode}; see saved evidence"
            )
        return result.stdout

    for name in (
        ".env",
        ".env.dev",
        ".dev",
        ".codegraph",
        "frontend/node_modules",
        "frontend/e2e/node_modules",
        "frontend/src/shared/api/generated",
    ):
        if (ROOT / name).exists():
            raise SystemExit(f"Rehearsal requires absent initial state: {name}")
    status = run(["git", "status", "--porcelain"])
    if status and not args.allow_candidate_patch:
        raise SystemExit("CI rehearsal requires a clean committed checkout")
    (output / "initial.json").write_text(
        json.dumps(
            {
                "head": run(["git", "rev-parse", "HEAD"]).strip(),
                "status": status,
                "candidate_patch_applied": args.allow_candidate_patch,
                "root": str(ROOT),
            },
            indent=2,
        )
        + "\n"
    )

    def sql(query):
        return run(
            [
                *cli,
                "exec",
                "-T",
                "postgres_primary",
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "complect_admin",
                "-d",
                "complect_db",
                "-Atc",
                query,
            ]
        ).strip()

    def files():
        names = set(
            filter(
                None,
                run(
                    [
                        "git",
                        "ls-files",
                        "--cached",
                        "--others",
                        "--exclude-standard",
                        "-z",
                    ]
                ).split("\0"),
            )
        )
        for base in (".dev", "frontend/src/shared/api/generated"):
            names.update(
                str(p.relative_to(ROOT))
                for p in (ROOT / base).rglob("*")
                if p.is_file()
            )
        names.add(".env.dev")
        result = {}
        for name in sorted(names):
            p = ROOT / name
            if p.is_file():
                info = p.stat()
                result[name] = [
                    hashlib.sha256(p.read_bytes()).hexdigest(),
                    info.st_mode & 0o777,
                    info.st_uid,
                    info.st_gid,
                ]
        index = Path(run(["git", "rev-parse", "--git-path", "index"]).strip())
        if not index.is_absolute():
            index = ROOT / index
        result["GIT_INDEX"] = hashlib.sha256(index.read_bytes()).hexdigest()
        return result

    def durable():
        project = next(
            line.split("=", 1)[1]
            for line in (ROOT / ".env.dev").read_text().splitlines()
            if line.startswith("COMPOSE_PROJECT_NAME=")
        )
        volumes = run(
            [
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ]
        ).split()
        details = json.loads(run(["docker", "volume", "inspect", *volumes]))
        return {
            "volumes": {v["Name"]: v.get("CreatedAt") for v in details},
            "cluster": sql("SELECT system_identifier FROM pg_control_system()"),
            "database_oid": sql(
                "SELECT oid FROM pg_database WHERE datname=current_database()"
            ),
            "ledger": sql(
                "SELECT app || ':' || name FROM django_migrations ORDER BY app,name"
            ),
            "sentinel": sql(f"SELECT value FROM {fixture}.sentinel"),
            "secrets": {
                k: v
                for k, v in files().items()
                if k.startswith(".dev/secrets/") or k == ".env.dev"
            },
        }

    run(["make", "bootstrap"], label="bootstrap-first")
    sql(
        f"CREATE SCHEMA {fixture}; CREATE TABLE {fixture}.sentinel(value text); "
        f"INSERT INTO {fixture}.sentinel VALUES ('isolated-rehearsal-data-must-survive')"
    )
    initial = durable()
    (output / "durable-before.json").write_text(json.dumps(initial, indent=2) + "\n")
    run(["make", "doctor"], label="doctor-first")
    run(["make", "bootstrap"], label="bootstrap-second")
    assert durable() == initial, (
        "Repeated bootstrap changed durable identity/data/secrets"
    )
    run(["make", "doctor"], label="doctor-second")
    run(["make", "sync"], label="sync")
    assert durable() == initial, "Sync changed durable identity/data/secrets"
    (output / "durable-after.json").write_text(json.dumps(durable(), indent=2) + "\n")

    before = files()
    before_durable = durable()
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        trace = output / "doctor-docker-trace.jsonl"
        real_docker = shutil.which("docker")
        wrapper = directory / "docker"
        wrapper.write_text(
            "#!"
            + sys.executable
            + "\nimport json,os,sys\n"
            + "with open("
            + repr(str(trace))
            + ',"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
            + "os.execv("
            + repr(real_docker)
            + ",["
            + repr(real_docker)
            + ",*sys.argv[1:]])\n"
        )
        wrapper.chmod(0o700)
        run(
            ["make", "doctor"],
            label="doctor-read-only",
            env=dict(
                environment, PATH=str(directory) + os.pathsep + environment["PATH"]
            ),
        )
    assert files() == before, (
        "Doctor changed checkout/config/secret/SDK/index bytes or ownership"
    )
    assert durable() == before_durable, (
        "Doctor changed durable database/volume/secret state"
    )
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    forbidden = {
        "up",
        "restart",
        "build",
        "run",
        "create",
        "rm",
        "down",
        "pull",
        "push",
        "chmod",
        "chown",
        "migrate",
        "seed",
        "apply",
    }
    assert not any(forbidden.intersection(call) for call in calls), (
        "Doctor ran corrective Docker command"
    )
    (output / "doctor-read-only.json").write_text(
        json.dumps(
            {
                "files_unchanged": True,
                "durable_unchanged": True,
                "docker_calls": len(calls),
                "no_corrective_calls": True,
            },
            indent=2,
        )
        + "\n"
    )

    sdk = ROOT / "frontend/src/shared/api/generated/schema.d.ts"
    content = sdk.read_bytes()
    try:
        sdk.write_bytes(content + b"\n// intentional verification drift\n")
        run(["make", "doctor"], label="doctor-stale-sdk", expected=2)
        assert sdk.read_bytes() != content, "Doctor repaired SDK"
    finally:
        sdk.write_bytes(content)
    secret = ROOT / ".dev/secrets/gateway_token"
    hidden = secret.with_name("gateway_token.verification-backup")
    secret.rename(hidden)
    try:
        run(["make", "doctor"], label="doctor-missing-secret", expected=2)
        assert not secret.exists(), "Doctor generated a secret"
    finally:
        hidden.rename(secret)
    sql(
        f"INSERT INTO django_migrations(app,name,applied) VALUES ('{fixture}','synthetic_unknown_branch',now())"
    )
    try:
        run(["make", "doctor"], label="doctor-unknown-ledger", expected=2)
        run(["make", "sync"], label="sync-unknown-ledger", expected=2)
        assert (
            sql(f"SELECT count(*) FROM django_migrations WHERE app='{fixture}'") == "1"
        )
    finally:
        sql(
            f"DELETE FROM django_migrations WHERE app='{fixture}' AND name='synthetic_unknown_branch'"
        )
    run(["make", "doctor"], label="doctor-restored")
    assert durable() == initial, "Failure-mode tests changed existing durable data"
    sql(f"DROP TABLE {fixture}.sentinel; DROP SCHEMA {fixture}")
    (output / "cleanup.json").write_text(
        json.dumps(
            {
                "synthetic_migration_removed": True,
                "fixture_table_and_schema_removed": True,
                "volumes_retained": True,
            }
        )
        + "\n"
    )
    run(["git", "diff", "--check"], label="diff-check")
    print(
        "PASS: clean initialization, idempotence, sync, doctor immutability and negative gates",
        flush=True,
    )


if __name__ == "__main__":
    main()
