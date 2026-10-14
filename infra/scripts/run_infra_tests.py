"""Run the local infrastructure selection in an isolated backend container."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IMAGE = "complect_backend:dev"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infra.scripts.docker_ownership import OwnershipMismatch, OwnershipProof  # noqa: E402  # root is inserted for direct CLI mode


def _docker_endpoint_is_local() -> None:
    endpoint = os.environ.get("DOCKER_HOST", "")
    if endpoint and not endpoint.startswith("unix://"):
        raise RuntimeError("agent infra tests require a local Docker unix endpoint")
    context = subprocess.run(
        ["docker", "context", "show"], capture_output=True, text=True, timeout=30, check=True,
    ).stdout.strip()
    selected = subprocess.run(
        ["docker", "context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}"],
        capture_output=True, text=True, timeout=30, check=True,
    )
    selected_endpoint = json.loads(selected.stdout.strip())
    if endpoint and endpoint != selected_endpoint:
        raise RuntimeError("Docker endpoint override differs from selected context")
    if not isinstance(selected_endpoint, str) or not selected_endpoint.startswith("unix://"):
        raise RuntimeError("agent infra tests require local Docker unix endpoint")


def build_command(
    *, image: str, mode: str, run_id: str, scope: str = "gate", action: str = "create", evidence_dir: Path | None = None
) -> list[str]:
    """Build the constrained Docker command; no socket or network is mounted."""

    command = [
        "docker",
        action,
        "--pull=never",
        "--network=none",
        "--name",
        run_id,
        "--label",
        f"complect.infra_test_run={run_id}",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,exec,nosuid,nodev",
        "-v",
        f"{ROOT}:/workspace:ro",
        "-w",
        "/workspace",
        "-e",
        "PYTHONPATH=/workspace:/workspace/backend",
    ]
    for host_path, container_path in _tool_mounts():
        command.extend(["-v", f"{host_path}:{container_path}:ro"])
    manifest_path = f"/tmp/{run_id}.json"
    if evidence_dir is not None:
        command.extend(["-v", f"{evidence_dir.resolve()}:/evidence:rw"])
        manifest_path = "/evidence/manifest.json"
    command.extend([
        image,
        "python",
        "-m",
        "test_support.payments_infra",
        mode,
        "--scope",
        scope,
        "--manifest",
        manifest_path,
    ])
    return command


def _tool_mounts() -> list[tuple[str, str]]:
    """Expose only offline test tooling; never expose the Docker socket."""

    mounts: list[tuple[str, str]] = []
    for name in ("docker", "make"):
        path = shutil.which(name)
        if path:
            mounts.append((path, f"/usr/local/bin/{name}"))
    compose_candidates = (
        os.environ.get("DOCKER_COMPOSE_PLUGIN"),
        "/usr/libexec/docker/cli-plugins/docker-compose",
        "/usr/lib/docker/cli-plugins/docker-compose",
        "/usr/local/lib/docker/cli-plugins/docker-compose",
    )
    for candidate in compose_candidates:
        if candidate and Path(candidate).is_file():
            mounts.append((candidate, "/usr/local/lib/docker/cli-plugins/docker-compose"))
            break
    return mounts


def extract_manifest(output: Path) -> Path:
    """Validate the evidence written to the dedicated host bind mount."""

    destination = output / "manifest.json"
    payload = json.loads(destination.read_bytes())
    if not isinstance(payload, dict):
        raise ValueError("infra test manifest must be a JSON object")
    return destination


def run(mode: str, *, image: str, output: Path, scope: str = "gate") -> int:
    if mode == "all":
        statuses: dict[str, int] = {}
        for selected in ("unittest", "pytest"):
            try:
                statuses[selected] = run(
                    selected,
                    image=image,
                    output=output / selected,
                    scope="local" if selected == "unittest" else "gate",
                )
            except (OSError, RuntimeError) as exc:
                phase_output = output / selected
                phase_output.mkdir(parents=True, exist_ok=True)
                (phase_output / "result.json").write_text(
                    json.dumps({"mode": selected, "status": "BLOCKED_STAND", "error": str(exc)}, indent=2) + "\n"
                )
                statuses[selected] = 2
        output.mkdir(parents=True, exist_ok=True)
        (output / "result.json").write_text(
            json.dumps(
                {"status": "PASS" if all(code == 0 for code in statuses.values()) else "FAIL", "phases": statuses},
                indent=2,
            )
            + "\n"
        )
        return max(statuses.values())
    _docker_endpoint_is_local()
    run_id = f"infra-test-{uuid.uuid4().hex}"
    output.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"run_id": run_id, "mode": mode, "image": image, "status": "BLOCKED_STAND"}
    container_id: str | None = None
    exit_code = 2
    completed_ok = False
    try:
        created = subprocess.run(
            build_command(image=image, mode=mode, run_id=run_id, scope=scope, evidence_dir=output),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        if created.returncode:
            result.update({"exit": created.returncode, "stdout": created.stdout, "stderr": created.stderr})
            result["status"] = "FAIL"
            exit_code = created.returncode
        else:
            container_id = created.stdout.strip().splitlines()[-1]
            if not container_id:
                raise RuntimeError("Docker returned no infrastructure test container id")
            result["container_id"] = container_id
            completed = subprocess.run(
                ["docker", "start", "--attach", container_id],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
            result.update({"exit": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr})
            result["status"] = "PASS" if completed.returncode == 0 else "FAIL"
            exit_code = completed.returncode
            completed_ok = completed.returncode == 0
    finally:
        if container_id:
            try:
                inspected = subprocess.run(
                    ["docker", "inspect", container_id],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                )
                resources = json.loads(inspected.stdout)
                proof = OwnershipProof(lambda _kind, identifiers, optional=False: resources)
                proof.owned("container", [container_id], {"complect.infra_test_run": run_id})
                if completed_ok:
                    try:
                        extract_manifest(output)
                        result["manifest"] = "manifest.json"
                    except (OSError, ValueError, subprocess.SubprocessError) as exc:
                        result["status"] = "FAIL"
                        result["manifest_error"] = str(exc)
                        exit_code = 2
                removed = subprocess.run(["docker", "rm", "-f", container_id], cwd=ROOT, timeout=60, check=False)
                if removed.returncode:
                    raise subprocess.CalledProcessError(removed.returncode, removed.args)
                result["cleanup"] = "PASS"
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, OwnershipMismatch) as exc:
                result["cleanup"] = "BLOCKED_STAND"
                result["cleanup_error"] = str(exc)
                if result["status"] == "PASS":
                    result["status"] = "BLOCKED_STAND"
                exit_code = exit_code or 2
                result["exit"] = exit_code
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("all", "pytest", "unittest"), default="all", nargs="?")
    parser.add_argument("--backend-image", default=os.environ.get("INFRA_TEST_BACKEND_IMAGE", DEFAULT_IMAGE))
    parser.add_argument("--output", type=Path, default=ROOT / ".dev/test-artifacts/infra-tests")
    args = parser.parse_args(argv)
    try:
        return run(args.mode, image=args.backend_image, output=args.output)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "result.json").write_text(
            json.dumps({"mode": args.mode, "status": "BLOCKED_STAND", "error": str(exc)}, indent=2) + "\n"
        )
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
