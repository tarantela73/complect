"""Host entrypoint for disposable cleanup-foundation migration recovery.

No Docker socket is mounted into PostgreSQL/application containers. This command
performs live host checks before every operator action; the in-container core
independently verifies PostgreSQL storage, network and read-only ownership marker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import uuid
from pathlib import Path

MARKER = "/run/complect-foundation-task/owner.json"


def _command(arguments):
    result = subprocess.run(arguments, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError(f"host verification command failed with status {result.returncode}")
    return result.stdout.strip()


def verify_disposable_server(compose, owner, evidence):
    endpoint = _command(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"])
    if endpoint != "unix:///var/run/docker.sock":
        raise ValueError("only the verified local Docker endpoint is supported")
    container_id = _command([*compose, "ps", "-q", "postgres_primary"])
    if not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise ValueError("one exact PostgreSQL container is required")
    info = json.loads(_command(["docker", "inspect", container_id]))[0]
    host = info["HostConfig"]
    if not info["State"]["Running"] or info["Config"]["Labels"].get("task.owner") != owner:
        raise ValueError("PostgreSQL container is not running for this task owner")
    if host["NetworkMode"] != "none" or host.get("PortBindings"):
        raise ValueError("PostgreSQL must be network-none without published ports")
    if "/var/lib/postgresql/data" not in host.get("Tmpfs", {}):
        raise ValueError("PostgreSQL data must be task-owned tmpfs")
    mounts = [mount for mount in info["Mounts"] if mount["Type"] != "tmpfs"]
    if len(mounts) != 1 or mounts[0]["Type"] != "bind" or mounts[0]["Destination"] != MARKER or mounts[0]["RW"]:
        raise ValueError("only the read-only task ownership marker may be bound into PostgreSQL")
    marker_path = Path(mounts[0]["Source"])
    if marker_path.is_symlink() or not marker_path.is_file():
        raise ValueError("task marker must be a regular host file")
    marker_raw = marker_path.read_bytes()
    marker = json.loads(marker_raw)
    if marker.get("scope") != "cleanup-foundation-v2-disposable" or marker.get("task_owner") != owner:
        raise ValueError("host task marker and Docker owner label disagree")
    if not re.fullmatch(r"[0-9a-f]{32}", marker.get("cluster_nonce", "")):
        raise ValueError("task ownership nonce is invalid")
    hostname = info["Config"]["Hostname"]
    if not re.fullmatch(r"[0-9a-f]{12,64}", hostname) or not container_id.startswith(hostname):
        raise ValueError("PostgreSQL hostname must bind its actual Docker container ID")
    rendered = json.loads(_command([*compose, "config", "--format", "json"]))
    app = rendered["services"]["migrate"]
    if app.get("network_mode") != "service:postgres_primary":
        raise ValueError("migration service must share only the PostgreSQL network-none namespace")
    evidence_mount = [mount for mount in app.get("volumes", []) if mount["target"] == "/evidence"]
    if len(evidence_mount) != 1 or evidence_mount[0]["type"] != "bind":
        raise ValueError("one task-owned evidence bind mount is required")
    if Path(evidence_mount[0]["source"]).resolve() != Path(evidence).resolve():
        raise ValueError("evidence path does not match the actual Compose bind mount")
    if any("docker.sock" in mount.get("source", "") for mount in app.get("volumes", [])):
        raise ValueError("Docker socket must not be available inside the migration service")
    for mount in app.get("volumes", []):
        if mount["type"] != "bind" or mount["target"] not in (
            "/app",
            "/infra",
            "/etc/hosts",
            "/evidence",
            "/state",
        ):
            raise ValueError("migration service has an unexpected mount")
        if mount["target"] not in ("/evidence", "/state") and not mount.get("read_only"):
            raise ValueError("migration source/host configuration must be read-only")
        if mount["target"] == "/state" and not Path(mount["source"]).resolve().is_relative_to(Path(evidence).resolve()):
            raise ValueError("migration state mount is not task-owned")
    system_identifier = _command(
        [
            *compose,
            "exec",
            "-T",
            "postgres_primary",
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "foundation",
            "-d",
            "postgres",
            "-Atc",
            "SELECT system_identifier FROM pg_control_system()",
        ]
    )
    return {
        "container_id": container_id,
        "network_mode": "none",
        "owned_by_harness": True,
        "postgres_tmpfs": True,
        "no_published_ports": True,
        "system_identifier": system_identifier,
        "current_role": "foundation",
        "task_owner": owner,
        "cluster_nonce": marker["cluster_nonce"],
        "marker_sha256": hashlib.sha256(marker_raw).hexdigest(),
        "server_hostname": hostname,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("operation", choices=["reference", "prepare", "recover"])
    parser.add_argument("--reference")
    parser.add_argument("--plan")
    parser.add_argument("--output")
    parser.add_argument("--task-token")
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()
    try:
        state = Path(args.state_dir)
        if not state.is_absolute() or ".." in state.parts or not state.is_relative_to("/evidence"):
            raise ValueError("operator state directory must be inside /evidence")
        if not re.fullmatch(r"[a-z0-9_]+", args.database):
            raise ValueError("invalid disposable database name")
        compose = ["docker", "compose", "-f", str(Path(args.compose).resolve())]
        attestation = verify_disposable_server(compose, args.owner, args.evidence)
        child = ["/infra/scripts/cleanup_foundation_recovery_core.py", args.operation]
        if args.operation in ("reference", "recover"):
            child += ["--state-dir", args.state_dir]
        if args.operation in ("reference", "prepare"):
            if not args.output:
                raise ValueError("reference/prepare requires --output")
            child += ["--output", args.output]
        if args.operation in ("prepare", "recover"):
            if not args.reference or not args.task_token:
                raise ValueError("prepare/recover requires reference and task token")
            child += ["--reference", args.reference, "--task-token", args.task_token]
        if args.operation == "prepare":
            attestation.update(database_name=args.database, task_token=args.task_token)
            path = args.evidence / f"host-ownership-{uuid.uuid4().hex}.json"
            with path.open("x") as stream:
                json.dump(attestation, stream, sort_keys=True)
            child += ["--network-attestation", f"/evidence/{path.name}"]
        if args.operation == "recover":
            if not args.plan:
                raise ValueError("recover requires --plan")
            child += ["--plan", args.plan]
        return subprocess.run(
            [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "-e",
                f"POSTGRES_DB={args.database}",
                "-e",
                f"DEV_STATE_DIR={args.state_dir}",
                "migrate",
                *child,
            ],
            timeout=180,
        ).returncode
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
