"""Host-driven cleanup-only recovery contract for a disposable legacy stand."""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from infra.scripts.run_infra_tests import _docker_endpoint_is_local


def _run(command: list[str], *, check: bool = True, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, check=check, **kwargs)


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _inspect(kind: str, identifier: str) -> dict[str, Any]:
    prefix = [] if kind == "container" else [kind]
    return json.loads(subprocess.check_output(["docker", *prefix, "inspect", identifier], text=True))[0]


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _wait_for(path: Path, process: subprocess.Popen[str], timeout: float = 300) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if process.poll() is not None:
            raise RuntimeError(f"qualification exited before {path}: {process.returncode}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out waiting for {path}")
        time.sleep(0.25)


def _wait_for_inventory(path: Path, process: subprocess.Popen[str], timeout: float = 900) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        if path.is_file():
            value = json.loads(path.read_text())
            if value.get("inventory_complete") is True and value.get("resources"):
                return value
        if process.poll() is not None:
            raise RuntimeError(f"qualification exited before owned inventory: {process.returncode}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out waiting for owned inventory: {path}")
        time.sleep(0.5)


def _recovery(attempt: Path, output: Path, name: str) -> tuple[int, dict[str, Any]]:
    completed = _run(
        [sys.executable, "-B", "-m", "infra.scripts.run_legacy_qualification", "recover", "--directory", str(attempt)],
        check=False,
        capture_output=True,
        cwd=attempt / "frozen",
        env={**os.environ, "PYTHONPATH": str(attempt / "frozen")},
    )
    (output / f"{name}.stdout").write_text(completed.stdout, encoding="utf-8")
    receipts = sorted(attempt.glob("recovery-*.json"), key=lambda item: item.stat().st_mtime_ns)
    if not receipts:
        raise RuntimeError(f"recovery produced no receipt: {completed.stdout}")
    return completed.returncode, json.loads(receipts[-1].read_text())


def _owned_resources(receipt: dict[str, Any]) -> list[tuple[str, str]]:
    return [(item["kind"], item["id"]) for item in receipt.get("resources", [])]


def _assert_owned_absent(receipt: dict[str, Any]) -> None:
    for kind, identifier in _owned_resources(receipt):
        command = {
            "container": ["docker", "ps", "-aq", "--filter", f"id={identifier}"],
            "network": ["docker", "network", "ls", "-q", "--filter", f"id={identifier}"],
            "volume": ["docker", "volume", "ls", "-q", "--filter", f"name={identifier}"],
        }[kind]
        if subprocess.check_output(command, text=True).strip():
            raise AssertionError(f"owned resource remains: {kind}:{identifier}")


def run(output: Path, image: str) -> dict[str, Any]:
    """Run the bounded real-Docker scenario and return JSON evidence."""
    if shutil.which("docker") is None or shutil.which("git") is None:
        raise RuntimeError("BLOCKED_STAND: host-driven recovery needs docker and git")
    _docker_endpoint_is_local()
    root = Path(__file__).resolve().parents[3]
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    docker_config = output / "docker-config"
    buildx_config = output / "buildx-config"
    docker_config.mkdir(mode=0o700)
    buildx_config.mkdir(mode=0o700)
    os.environ.setdefault("DOCKER_CONFIG", str(docker_config))
    os.environ.setdefault("BUILDX_CONFIG", str(buildx_config))
    source = output / "source"
    attempt = output / "attempt"
    _run(["git", "clone", "--quiet", "--no-hardlinks", str(root), str(source)])
    release = output / "release"
    runner = source / "infra/scripts/run_legacy_regressions.py"
    runner.write_text(
        "import argparse\nimport time\nfrom pathlib import Path\n"
        "parser = argparse.ArgumentParser()\nparser.add_argument('--runtime-directory', type=Path, required=True)\n"
        "args, _ = parser.parse_known_args()\n"
        "args.runtime_directory.mkdir(mode=0o700, parents=True, exist_ok=True)\n"
        "(args.runtime_directory / 'control').mkdir(mode=0o700)\n"
        "(args.runtime_directory / 'owner-secrets').mkdir(mode=0o700)\n"
        "(args.runtime_directory / 'owner-secrets' / 'credential').write_text('synthetic-secret')\n"
        "(args.runtime_directory / 'control' / 'test-started').write_text('ready')\n"
        f"release = Path({str(release)!r})\nwhile not release.exists(): time.sleep(0.1)\nraise SystemExit(23)\n",
        encoding="utf-8",
    )
    preparer = source / "infra/scripts/prepare_legacy_stand.py"
    preparer.write_text(
        preparer.read_text(encoding="utf-8").replace(
            "    commands = stand_commands(checkout, receipt)\n",
            "    commands = stand_commands(checkout, receipt)\n"
            "    internal_network = f'recovery-{identifier.hex}'\n"
            "    subnet = f'10.251.{identifier.int % 240}.0/28'\n"
            "    commands.run(['docker', 'network', 'create', '--internal', '--label', f'com.docker.compose.project={commands.project}', '--label', f'complect.checkout_id={receipt[\"checkout_id\"]}', '--subnet', subnet, internal_network], write=True)\n"
            "    commands.run(['docker', 'volume', 'create', '--label', f'com.docker.compose.project={commands.project}', '--label', f'complect.checkout_id={receipt[\"checkout_id\"]}', f'{commands.project}_recovery_data'], write=True)\n"
            "    _inventory(commands, receipt)\n"
            "    receipt['status'] = 'PASS'\n"
            "    _write(path, receipt)\n"
            "    return receipt\n",
        ),
        encoding="utf-8",
    )
    _run(["git", "-C", str(source), "add", "infra/scripts/run_legacy_regressions.py", "infra/scripts/prepare_legacy_stand.py"])
    _run(["git", "-C", str(source), "-c", "user.name=Recovery Test", "-c", "user.email=recovery@test.invalid", "commit", "-qm", "controlled recovery failure"])
    source_commit = _git(source, "rev-parse", "HEAD")
    process = subprocess.Popen(
        [sys.executable, "-B", "-m", "infra.scripts.run_legacy_qualification", "run", "--source-commit", source_commit, "--directory", str(attempt)],
        text=True,
        stdout=(output / "qualification.stdout").open("w"),
        stderr=subprocess.STDOUT,
        cwd=source,
        env={**os.environ, "PYTHONPATH": str(source)},
    )
    foreign_name = f"complect-recovery-foreign-{uuid.uuid4().hex[:12]}"
    foreign_container = ""
    foreign_volume = f"complect-recovery-foreign-volume-{uuid.uuid4().hex[:12]}"
    receipt: dict[str, Any] | None = None
    try:
        ownership = attempt / "frozen/.dev/test-artifacts/stand/ownership.json"
        _wait_for(ownership, process)
        receipt = _wait_for_inventory(ownership, process)
        networks = [item["id"] for item in receipt["resources"] if item["kind"] == "network"]
        if not networks:
            raise RuntimeError("qualification produced no owned network")
        _wait_for(attempt / "private/control/test-started", process)
        _run(["docker", "volume", "create", "--label", "complect.recovery_probe=foreign", foreign_volume])
        foreign_container = subprocess.check_output(
            ["docker", "run", "-d", "--pull=never", "--name", foreign_name, "--label", "complect.recovery_probe=foreign", "--network", networks[0], "--mount", f"type=volume,source={foreign_volume},target=/foreign", image, "python", "-c", "import time; time.sleep(600)"],
            text=True,
        ).strip()
        foreign_before = _inspect("container", foreign_container)
        foreign_volume_before = _inspect("volume", foreign_volume)
        release.touch()
        process_exit = process.wait(timeout=900)
        qualification_result = json.loads((attempt / "qualification.json").read_text())
        if process_exit != 23 or qualification_result.get("primary_exit") != 23 or qualification_result["exit"] != 23 or qualification_result["status"] != "BLOCKED_STAND":
            raise AssertionError(f"initial fault contract failed: {process_exit}, {qualification_result}")
        original = (attempt / "qualification.json").read_bytes()
        credentials = attempt / "frozen/.dev/test-artifacts/stand/checkout/.dev/secrets"
        if credentials.exists() or (attempt / "private").exists():
            raise AssertionError("credentials survived primary cleanup")
        process_journal = attempt / "processes/test.json"
        process_journal_before = process_journal.read_bytes()
        process_journal.unlink()
        missing_exit, missing = _recovery(attempt, output, "recovery-missing")
        if missing_exit != 2 or missing["status"] != "BLOCKED_STAND":
            raise AssertionError("incomplete process journal was not blocked")
        process_journal.write_bytes(process_journal_before)
        process_journal.write_text("{malformed", encoding="utf-8")
        blocked_exit, blocked = _recovery(attempt, output, "recovery-blocked")
        if blocked_exit != 2 or blocked["status"] != "BLOCKED_STAND":
            raise AssertionError(f"malformed journal was not blocked: {blocked}")
        process_journal.write_bytes(process_journal_before)
        _run(["docker", "network", "disconnect", networks[0], foreign_container])
        foreign_after = _inspect("container", foreign_container)
        foreign_volume_after = _inspect("volume", foreign_volume)
        for key in ("Id", "Name", "Config", "Mounts"):
            if foreign_after.get(key) != foreign_before.get(key):
                raise AssertionError(f"foreign container identity changed: {key}")
        for key in ("Name", "Driver", "Options", "Labels"):
            if foreign_volume_after.get(key) != foreign_volume_before.get(key):
                raise AssertionError(f"foreign volume identity changed: {key}")
        recovered_exit, recovered = _recovery(attempt, output, "recovery-pass")
        if recovered_exit != 0 or recovered["status"] != "PASS":
            raise AssertionError(f"recovery did not pass after unblock: {recovered}")
        repeat_exit, repeated = _recovery(attempt, output, "recovery-repeat")
        if repeat_exit != 0 or repeated["status"] != "PASS":
            raise AssertionError(f"repeat recovery did not pass: {repeated}")
        if (attempt / "qualification.json").read_bytes() != original:
            raise AssertionError("recovery changed original qualification verdict")
        _assert_owned_absent(receipt)
        foreign_after = _inspect("container", foreign_container)
        volume_after = _inspect("volume", foreign_volume)
        if any(foreign_after.get(key) != foreign_before.get(key) for key in ("Id", "Name", "Config", "Mounts")):
            raise AssertionError("recovery changed foreign container")
        if any(volume_after.get(key) != foreign_volume_before.get(key) for key in ("Name", "CreatedAt", "Driver", "Options", "Labels")):
            raise AssertionError("recovery changed foreign volume")
        return {"status": "PASS", "source_commit": source_commit, "initial": qualification_result, "missing_journal": missing, "blocked": blocked, "recovery": recovered, "repeat": repeated, "foreign_container": foreign_container, "foreign_volume": foreign_volume}
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=120)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=30)
                from infra.scripts.qualification_processes import stop_work
                stop_work(attempt)
        if foreign_container:
            current_foreign = _inspect("container", foreign_container)
            if current_foreign["Id"] != foreign_container or current_foreign["Config"].get("Labels", {}).get("complect.recovery_probe") != "foreign":
                raise AssertionError("foreign probe ownership changed before cleanup")
            subprocess.run(["docker", "rm", "-f", foreign_container], check=True, capture_output=True)
        volumes = subprocess.run(["docker", "volume", "ls", "-q", "--filter", f"name={foreign_volume}"], check=True, capture_output=True, text=True).stdout.split()
        if foreign_volume in volumes:
            current_volume = _inspect("volume", foreign_volume)
            if current_volume.get("Labels", {}).get("complect.recovery_probe") != "foreign":
                raise AssertionError("foreign volume ownership changed before cleanup")
            subprocess.run(["docker", "volume", "rm", foreign_volume], check=True, capture_output=True)
        ownership_path = attempt / "frozen/.dev/test-artifacts/stand/ownership.json"
        if ownership_path.is_file():
            # A failed prepare can leave an intentionally partial inventory.
            # Refresh through the existing ownership guards before deleting anything.
            from infra.scripts.prepare_legacy_stand import recover_inventory, cleanup
            try:
                cleanup_exit, _ = _recovery(attempt, output, "recovery-finally")
            except Exception:
                cleanup_exit = 2
            if cleanup_exit:
                receipt = recover_inventory(ownership_path)
                cleaned = cleanup(ownership_path)
                if cleaned.get("cleanup") != "PASS":
                    raise AssertionError("diagnostic fixture cleanup remains blocked")
            else:
                receipt = json.loads(ownership_path.read_text())
            _assert_owned_absent(receipt)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default=os.environ.get("RECOVERY_TEST_IMAGE", "complect-infra-deps:20261016"))
    args = parser.parse_args()
    try:
        result = run(args.output.resolve(), args.image)
    except Exception as exc:
        diagnostic = {"status": "BLOCKED_STAND", "error": type(exc).__name__, "reason": str(exc)}
        args.output.mkdir(parents=True, exist_ok=True)
        _write_json(args.output.resolve() / "result.json", diagnostic)
        print(json.dumps(diagnostic), flush=True)
        return 2
    _write_json(args.output.resolve() / "result.json", result)
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
