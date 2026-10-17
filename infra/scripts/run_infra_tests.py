"""Run the local infrastructure selection in an isolated backend container."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IMAGE = "complect_backend:dev"
DEFAULT_QUALITY_IMAGE = "complect-infra-quality:dev"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infra.scripts.docker_ownership import OwnershipMismatch, OwnershipProof  # noqa: E402  # root is inserted for direct CLI mode
from infra.scripts import gate_source_proof  # noqa: E402


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


def build_quality_command(*, image: str, output: Path) -> list[str]:
    """Build the read-only command for the static quality image."""
    return [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,exec,nosuid,nodev",
        "-v",
        f"{ROOT}:/workspace:ro",
        "-v",
        f"{output.resolve()}:/evidence:rw",
        "-w",
        "/workspace",
        "-e",
        "PYTHONPATH=/workspace:/workspace/backend",
        image,
    ]


def _tool_mounts() -> list[tuple[str, str]]:
    """Expose only offline test tooling; never expose the Docker socket."""

    mounts: list[tuple[str, str]] = []
    for name in (
        "docker",
        "make",
        "git",
        "git-upload-pack",
        "git-receive-pack",
        "git-upload-archive",
    ):
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


def run_quality(*, image: str, output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    _docker_endpoint_is_local()
    completed = subprocess.run(
        build_quality_command(image=image, output=output),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    result: dict[str, Any] = {
        "mode": "quality",
        "image": image,
        "exit": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
    }
    valid_receipt = False
    try:
        receipt = json.loads(completed.stdout)
        checks = receipt.get("checks") if isinstance(receipt, dict) else None
        required = {"ruff", "mypy", "actionlint"}
        names = [check.get("name") for check in checks] if isinstance(checks, list) else []
        valid_receipt = (
            isinstance(receipt, dict)
            and receipt.get("status") == "PASS"
            and isinstance(checks, list)
            and len(checks) == len(required)
            and set(names) == required
            and len(set(names)) == len(names)
            and all(
                isinstance(check, dict)
                and check.get("exit") == 0
                and check.get("status") == "PASS"
                for check in checks
            )
        )
        if isinstance(checks, list):
            result["checks"] = checks
    except (TypeError, ValueError, AttributeError):
        valid_receipt = False
    if not valid_receipt:
        result["status"] = "FAIL"
        if completed.returncode == 0:
            completed = completed.__class__(
                completed.args,
                2,
                completed.stdout,
                completed.stderr,
            )
    result["exit"] = completed.returncode
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return completed.returncode


def run(
    mode: str,
    *,
    image: str,
    output: Path,
    scope: str = "gate",
    quality_image: str = DEFAULT_QUALITY_IMAGE,
    base: str | None = None,
) -> int:
    if mode == "all":
        statuses: dict[str, int] = {}
        source_before: dict[str, Any] | None = None
        if base is not None:
            source_before = gate_source_proof.capture(ROOT, output, "sources-before.json")
        try:
            statuses["quality"] = run_quality(image=quality_image, output=output / "quality")
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            quality_output = output / "quality"
            quality_output.mkdir(parents=True, exist_ok=True)
            (quality_output / "result.json").write_text(
                json.dumps({"mode": "quality", "status": "BLOCKED_STAND", "error": str(exc)}, indent=2) + "\n"
            )
            statuses["quality"] = 2
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
        if base is not None:
            statuses["coverage"] = run_coverage(base=base, image=quality_image, output=output / "coverage")
            statuses["payments_context"] = run_payments_context(image=image, output=output / "payments-context")
            statuses["recovery"] = run_recovery(image=image, output=output / "recovery")
            statuses["delivery"] = run_delivery(base=base, image=image, output=output / "delivery")
        output.mkdir(parents=True, exist_ok=True)
        primary_exit = next((code for code in statuses.values() if code), 0)
        if source_before is not None:
            candidates = {
                "quality/result.json": output / "quality/result.json",
                "unittest/result.json": output / "unittest/result.json",
                "unittest/manifest.json": output / "unittest/manifest.json",
                "pytest/result.json": output / "pytest/result.json",
                "pytest/manifest.json": output / "pytest/manifest.json",
                "coverage/result.json": output / "coverage/result.json",
                "coverage/coverage.json": output / "coverage/coverage.json",
                "coverage/input/input.json": output / "coverage/input/input.json",
                "coverage/after/input.json": output / "coverage/after/input.json",
                "payments-context/result.json": output / "payments-context/result.json",
                "recovery/result.json": output / "recovery/result.json",
                "delivery/selection.json": output / "delivery/selection.json",
                "delivery/payments/result.json": output / "delivery/payments/result.json",
            }
            required = {name for name, path in candidates.items()
                        if name not in {"delivery/payments/result.json", "payments-context/result.json"}}
            if (output / "delivery/selection.json").is_file():
                selection = json.loads((output / "delivery/selection.json").read_text())
                if "test-payments-gate" in selection.get("required_gates", []):
                    required.add("delivery/payments/result.json")
            context_files = sorted((output / "payments-context").glob("run-*/context.json"))
            if len(context_files) == 1:
                name = str(context_files[0].relative_to(output))
                candidates[name] = context_files[0]
                required.add(name)
            elif primary_exit == 0:
                primary_exit = 2
            artifacts = {name: path for name, path in candidates.items() if path.is_file()}
            missing = sorted(name for name in required if name not in artifacts)
            if missing and primary_exit == 0:
                primary_exit = 2
            _, proof_exit = gate_source_proof.finish(
                root=ROOT, output=output, before=source_before["snapshot"], gate_exit=primary_exit,
                artifacts=artifacts,
            )
            if proof_exit and not primary_exit:
                primary_exit = proof_exit
        (output / "result.json").write_text(
            json.dumps(
                {"status": "PASS" if primary_exit == 0 else "FAIL", "phases": statuses},
                indent=2,
            )
            + "\n"
        )
        return primary_exit
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


def run_coverage(*, base: str, image: str, output: Path) -> int:
    """Prepare complete Git inputs on the host and run checks in the offline image."""
    from infra.scripts.infra_quality_coverage import prepare_input

    _docker_endpoint_is_local()
    output.mkdir(parents=True, exist_ok=True)
    prepare_input(base=base, output=output / "input")
    expected = (output / "input/input.json").read_bytes()
    execution = output / "execution"
    execution.mkdir(exist_ok=True)
    command = build_quality_command(image=image, output=execution)
    command[-1:-1] = ["--entrypoint", "python", "-v", f"{output.resolve() / 'input'}:/coverage-input:ro"]
    command.extend(["-B", "-m", "infra.scripts.infra_quality_coverage",
                    "--coverage-input", "/coverage-input/input.json", "--report", "/evidence/coverage.json"])
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=1800, check=False)
    (output / "command.log").write_text(completed.stdout + completed.stderr)
    prepare_input(base=base, output=output / "after")
    unchanged = expected == (output / "after/input.json").read_bytes()
    receipt_path = execution / "coverage.json"
    receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
    if receipt_path.is_file():
        shutil.copyfile(receipt_path, output / "coverage.json")
    code = completed.returncode
    if not unchanged or receipt.get("status") != "PASS":
        code = code or 2
    (output / "result.json").write_text(json.dumps({
        "version": 1, "command": command, "exit": code, "sources_unchanged": unchanged,
        "status": "PASS" if not code else "FAIL", "report": "coverage.json",
    }, indent=2) + "\n")
    return code


def run_recovery(*, image: str, output: Path) -> int:
    """Run ordinary and early-fault cleanup-only scenarios on the host."""
    destination = output / ("run-" + uuid.uuid4().hex)
    early_destination = output / ("early-" + uuid.uuid4().hex)
    kill_destination = output / ("test-kill-" + uuid.uuid4().hex)
    output.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-B", str(ROOT / "infra/scripts/tests/test_legacy_recovery_cli.py"),
               "--image", image, "--output", str(destination)]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    early_command = [sys.executable, "-B", str(ROOT / "infra/scripts/tests/test_legacy_early_failure_cli.py"),
                     "--image", image, "--output", str(early_destination)]
    early_completed = subprocess.run(early_command, cwd=ROOT, capture_output=True, text=True, check=False)
    kill_command = [sys.executable, "-B", str(ROOT / "infra/scripts/tests/test_legacy_recovery_cli.py"),
                    "--image", image, "--output", str(kill_destination)]
    kill_environment = {**os.environ, "COMPLECT_KILL_IN_TEST": "1"}
    kill_completed = subprocess.run(kill_command, cwd=ROOT, capture_output=True, text=True, check=False, env=kill_environment)
    (output / "command.log").write_text(completed.stdout + completed.stderr + early_completed.stdout + early_completed.stderr + kill_completed.stdout + kill_completed.stderr)
    receipt_path = destination / "result.json"
    early_receipt_path = early_destination / "result.json"
    kill_receipt_path = kill_destination / "result.json"
    receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
    early_receipt = json.loads(early_receipt_path.read_text()) if early_receipt_path.is_file() else {}
    kill_receipt = json.loads(kill_receipt_path.read_text()) if kill_receipt_path.is_file() else {}
    code = completed.returncode or early_completed.returncode or kill_completed.returncode
    if receipt.get("status") != "PASS" or early_receipt.get("status") != "PASS" or kill_receipt.get("status") != "PASS":
        code = code or 2
    (output / "result.json").write_text(json.dumps({"status": "PASS" if not code else "FAIL",
        "exit": code, "command": command, "report": str(receipt_path),
        "early_command": early_command, "early_report": str(early_receipt_path),
        "kill_command": kill_command, "kill_report": str(kill_receipt_path),
        "ordinary": receipt, "early": early_receipt, "test_kill": kill_receipt}, indent=2) + "\n")
    return code


def run_payments_context(*, image: str, output: Path) -> int:
    """Run the existing Payments adapter in its offline context-only mode."""
    output.mkdir(parents=True, exist_ok=True)
    destination = output / ("run-" + uuid.uuid4().hex)
    completed = subprocess.run(
        [sys.executable, "-B", str(ROOT / "infra/scripts/run_payments_gate.py"),
         "--infra-only", "--backend-image", image, "--output", str(destination)],
        cwd=ROOT, capture_output=True, text=True, timeout=1800, check=False,
    )
    (output / "command.log").write_text(completed.stdout + completed.stderr)
    if completed.returncode:
        return completed.returncode
    receipt = json.loads((destination / "context.json").read_text())
    return 0 if receipt.get("status") == "PASS" and receipt.get("cleanup") == "PASS" else 2


def changed_paths(base: str) -> tuple[str, list[str]]:
    """Resolve one explicit comparison and include staged, unstaged and new files."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(ROOT), *args], text=True,
                              capture_output=True, check=True).stdout
    resolved = git("rev-parse", "--verify", "--end-of-options", base + "^{commit}").strip()
    changed = git("diff", "--name-only", "--no-renames", "-z", resolved, "--").split("\0")
    changed += git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
    return resolved, sorted(set(path for path in changed if path))


def run_delivery(*, base: str, image: str, output: Path) -> int:
    """Select full host consumers after the fast Infrastructure gate; never recurse."""
    from infra.scripts.infra_test_selection import required_gates
    from infra.scripts.run_payments_gate import Stand

    resolved, paths = changed_paths(base)
    gates = required_gates(paths)
    output.mkdir(parents=True, exist_ok=True)
    source_before = Stand.source_identity()
    results = []
    code = 0
    if "test-payments-gate" in gates:
        destination = output / "payments"
        arguments = ["--backend-image", image, "--output", str(destination),
                     "--postgres-image", os.environ.get("PAYMENTS_POSTGRES_IMAGE", "postgis/postgis:16-3.4"),
                     "--redis-image", os.environ.get("PAYMENTS_REDIS_IMAGE", "redis:7.2-alpine")]
        command = ["make", "test-payments-gate", "PAYMENTS_GATE_ARGS=" + shlex.join(arguments)]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        (output / "payments.log").write_text(completed.stdout + completed.stderr)
        code = completed.returncode
        receipt = json.loads((destination / "result.json").read_text()) if (destination / "result.json").is_file() else {}
        if not code and receipt.get("status") != "PASS":
            code = 2
        results.append({"gate": "test-payments-gate", "command": command, "exit": code,
                        "status": receipt.get("status", "BLOCKED_STAND")})
    unchanged = source_before == Stand.source_identity()
    if not unchanged:
        code = code or 2
    (output / "selection.json").write_text(json.dumps({
        "version": 1, "comparison_base": resolved, "changed_paths": paths,
        "required_gates": gates, "infrastructure": "executed-by-caller",
        "results": results, "source_identity": source_before, "sources_unchanged": unchanged,
        "status": "PASS" if not code else "FAIL",
    }, indent=2) + "\n")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("all", "pytest", "unittest"), default="all", nargs="?")
    parser.add_argument("--backend-image", default=os.environ.get("INFRA_TEST_BACKEND_IMAGE", DEFAULT_IMAGE))
    parser.add_argument("--quality-image", default=os.environ.get("INFRA_TEST_QUALITY_IMAGE", DEFAULT_QUALITY_IMAGE))
    parser.add_argument("--output", type=Path, default=ROOT / ".dev/test-artifacts/infra-tests")
    parser.add_argument("--base", default="HEAD", help="Comparison commit for local acceptance, including working-tree changes")
    args = parser.parse_args(argv)
    try:
        return run(args.mode, image=args.backend_image, quality_image=args.quality_image, output=args.output, base=args.base if args.mode == "all" else None)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "result.json").write_text(
            json.dumps({"mode": args.mode, "status": "BLOCKED_STAND", "error": str(exc)}, indent=2) + "\n"
        )
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
