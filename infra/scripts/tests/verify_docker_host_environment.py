"""Explicit, bounded host/Docker acceptance; never run by unit discovery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from infra.scripts.docker_host_environment import prepare_host_environment
from infra.scripts.run_legacy_qualification import qualification_result


def docker(args: list[str], environment: dict[str, str]) -> str:
    completed = subprocess.run(
        ["docker", *args], env=environment, capture_output=True, text=True, timeout=180
    )
    if completed.returncode:
        raise RuntimeError("Docker operation failed: " + args[0])
    return completed.stdout.strip()


def run(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    environment = dict(os.environ)
    daemon = docker(["info", "--format", "{{.ID}}"], environment)
    if not daemon:
        raise RuntimeError("Docker daemon identity unavailable")
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="complect-host-environment-") as temporary:
        base = Path(temporary)
        context = base / "build"
        context.mkdir()
        (context / "sentinel").write_text("isolated acceptance\n")
        for case in ("default", "explicit-buildx", "explicit-docker-context-auth"):
            values = dict(environment)
            values.pop("BUILDX_CONFIG", None)
            case_root = base / case
            case_root.mkdir(mode=0o700)
            auth_hash = None
            config_file = None
            if case == "explicit-buildx":
                path = case_root / "explicit-buildx"
                path.mkdir(mode=0o700)
                values["BUILDX_CONFIG"] = str(path)
            if case == "explicit-docker-context-auth":
                config = case_root / "docker-config"
                config.mkdir(mode=0o700)
                values["DOCKER_CONFIG"] = str(config)
                values.pop("DOCKER_HOST", None)
                values.pop("DOCKER_CONTEXT", None)
                name = "proof-" + uuid.uuid4().hex
                endpoint = json.loads(
                    docker(
                        [
                            "context",
                            "inspect",
                            "--format",
                            "{{json .Endpoints.docker.Host}}",
                        ],
                        environment,
                    )
                )
                if not isinstance(endpoint, str) or not endpoint.startswith("unix://"):
                    raise RuntimeError(
                        "proof requires existing local unix Docker endpoint"
                    )
                docker(
                    ["context", "create", name, "--docker", "host=" + endpoint], values
                )
                docker(["context", "use", name], values)
                config_file = config / "config.json"
                config_data = json.loads(config_file.read_text())
                # Synthetic credential only; original Docker auth is never copied.
                config_data["auths"] = {
                    "example.invalid": {"auth": "c3ludGhldGljOnByb29m"}
                }
                config_file.write_text(json.dumps(config_data))
                auth_hash = hashlib.sha256(config_file.read_bytes()).hexdigest()
            lease = prepare_host_environment(case_root / "service", values)
            tag = "complect-host-proof:" + uuid.uuid4().hex
            token = uuid.uuid4().hex
            (context / "Dockerfile").write_text(
                "FROM scratch\nLABEL complect.host_environment="
                + token
                + "\nCOPY sentinel /sentinel\n"
            )
            image = None
            try:
                self_id = docker(["info", "--format", "{{.ID}}"], lease.environment)
                if self_id != daemon:
                    raise RuntimeError("Docker daemon changed")
                docker(
                    [
                        "build",
                        "--network=none",
                        "--pull=false",
                        "-q",
                        "-t",
                        tag,
                        str(context),
                    ],
                    lease.environment,
                )
                image = json.loads(
                    docker(["image", "inspect", tag], lease.environment)
                )[0]
                if image["Config"]["Labels"].get("complect.host_environment") != token:
                    raise RuntimeError("image ownership mismatch")
                if (
                    config_file is not None
                    and hashlib.sha256(config_file.read_bytes()).hexdigest()
                    != auth_hash
                ):
                    raise RuntimeError("context/auth configuration changed")
                results.append(
                    {
                        "case": case,
                        "status": "PASS",
                        "daemon_id": daemon,
                        "image_id": image["Id"],
                        "auth_bytes_unchanged": config_file is not None,
                        "build_exit": 0,
                    }
                )
            finally:
                try:
                    probe = subprocess.run(
                        ["docker", "image", "inspect", tag],
                        env=lease.environment,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if probe.returncode == 0:
                        current = json.loads(probe.stdout)[0]
                        if (
                            image is not None and current["Id"] != image["Id"]
                        ) or current["Config"]["Labels"].get(
                            "complect.host_environment"
                        ) != token:
                            raise RuntimeError("image replaced before cleanup")
                        docker(["image", "rm", tag], lease.environment)
                    elif image is not None:
                        raise RuntimeError("image cleanup identity unavailable")
                finally:
                    errors = lease.cleanup()
                    if errors:
                        raise RuntimeError("host environment cleanup blocked")
            if case == "explicit-buildx" and not Path(values["BUILDX_CONFIG"]).is_dir():
                raise RuntimeError("explicit directory was deleted")
        for setting in ("DOCKER_CONFIG", "BUILDX_CONFIG"):
            values = {**environment, setting: "/sys"}
            try:
                prepare_host_environment(base / ("invalid-" + setting), values)
            except ValueError:
                results.append(
                    {
                        "case": "unwritable-" + setting,
                        "status": "PASS",
                        "build": "NOT_RUN",
                        "original_result": "BLOCKED",
                    }
                )
            else:
                raise RuntimeError("unwritable explicit directory accepted")
        lease = prepare_host_environment(base / "cleanup-failure", environment)
        lease.base.chmod(0o500)
        errors = lease.cleanup()
        if not errors:
            raise RuntimeError("proof requires non-root permission enforcement")
        failed = qualification_result(
            primary_exit=23, tests_passed=False, cleanup_errors=errors
        )
        if (
            failed["status"] != "BLOCKED_STAND"
            or failed["exit"] != 23
            or failed["primary_exit"] != 23
        ):
            raise RuntimeError("cleanup failure hid primary result")
        lease.base.chmod(0o700)
        if lease.cleanup():
            raise RuntimeError("cleanup retry failed")
        results.append(
            {
                "case": "cleanup-failure",
                "status": "PASS",
                "original_status": failed["status"],
                "original_exit": failed["primary_exit"],
                "retry_cleanup": "PASS",
            }
        )
    result = {
        "status": "PASS",
        "cases": results,
        "cleanup": "PASS",
        "limits": [
            "Synthetic auth byte preservation; no external registry login",
            "scratch image builds only; no production services",
        ],
    }
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.output)
    except Exception as error:
        print(
            json.dumps(
                {"status": "FAIL", "error": type(error).__name__, "reason": str(error)}
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                "cases": len(result["cases"]),
                "cleanup": result["cleanup"],
                "artifact": str(args.output / "result.json"),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
