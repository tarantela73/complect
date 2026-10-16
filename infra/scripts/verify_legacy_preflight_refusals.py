"""Verify the four destructive-operation refusals on isolated legacy stands.

Each case owns a complete stand.  The receipt used by the runner is copied and
then made invalid; the canonical receipt is retained for cleanup.  The runner
is intentionally invoked normally so this proves the refusal happens before
Redis cleanup, lifecycle startup, or pytest.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from infra.scripts.prepare_legacy_stand import MANIFEST, cleanup, prepare, stand_commands

ROOT = Path(__file__).resolve().parents[2]
REVIEW_PATCH = ROOT / ".dev/test-artifacts/legacy-preflight-review.patch"
SENTINEL_PREFIX = "legacy-preflight-refusal-"


def _noop() -> None:
    return None


@dataclass(frozen=True)
class Scenario:
    name: str
    reason: str


SCENARIOS = (
    Scenario("redis_identity", "REDIS_IDENTITY_MISMATCH"),
    Scenario("database_identity", "DATABASE_IDENTITY_MISMATCH"),
    Scenario("runtime_permissions", "RUNTIME_PERMISSIONS_MISMATCH"),
    Scenario("clamav_unavailable", "CLAMAV_UNREADY"),
)


def _safe_identifier(value: object, label: str) -> str:
    text = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", text):
        raise RuntimeError(f"unsafe {label} identity")
    return text


def _docker(commands, arguments: list[str], *, write: bool = False):
    return commands.run(["docker", *arguments], write=write, check=False)


def _copy_receipt(canonical: Path, scenario_dir: Path) -> Path:
    copied = scenario_dir / "ownership.json"
    shutil.copy2(canonical, copied)
    return copied


def _break_redis_identity(receipt_path: Path) -> Callable[[], None]:
    receipt = json.loads(receipt_path.read_text())
    redis = receipt["redis"]
    redis["name"] = str(redis["name"]) + "-foreign"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    return lambda: None


def _break_database_identity(receipt_path: Path) -> Callable[[], None]:
    receipt = json.loads(receipt_path.read_text())
    database = receipt["database"]
    database["cluster"] = str(int(database["cluster"]) + 1)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    return lambda: None


def _break_runtime_permissions(receipt_path: Path):
    receipt = json.loads(receipt_path.read_text())
    checkout = Path(receipt["root"])
    commands = stand_commands(checkout, receipt)
    database = receipt["database"]
    postgres = receipt["containers"]["postgres_primary"]["id"]
    database_name = _safe_identifier(database["database"], "database")
    owner = receipt["database"].get("user", "complect_migrator")
    _safe_identifier(owner, "database owner")
    _owned_container(commands, receipt, "postgres_primary")
    # The ownership identity is read back before granting TEMP.  This keeps a
    # stale or foreign receipt from becoming an authorization primitive.
    probe = _docker(
        commands,
        [
            "exec", postgres, "psql", "-X", "-At", "-U", owner,
            "-d", database_name, "-c",
            "SELECT current_database(), current_user",
        ],
    )
    if probe.returncode != 0 or probe.stdout.strip() != f"{database_name}|{owner}":
        raise RuntimeError("runtime permission owner proof failed")
    user = "complect_app_local"
    _docker(
        commands,
        ["exec", postgres, "psql", "-X", "-At", "-U", owner, "-d", database_name,
         "-c", f"GRANT TEMP ON DATABASE {database_name} TO {user}"],
        write=True,
    ).check_returncode()

    def restore() -> None:
        _owned_container(commands, receipt, "postgres_primary")
        _docker(
            commands,
            ["exec", postgres, "psql", "-X", "-At", "-U", owner, "-d", database_name,
             "-c", f"REVOKE TEMP ON DATABASE {database_name} FROM {user}"],
            write=True,
        ).check_returncode()

    return restore


def _break_clamav(receipt_path: Path):
    receipt = json.loads(receipt_path.read_text())
    checkout = Path(receipt["root"])
    commands = stand_commands(checkout, receipt)
    container = receipt["containers"]["clamav"]
    container_id = _safe_identifier(container["id"], "ClamAV container")
    _owned_container(commands, receipt, "clamav")
    observed = json.loads(_docker(commands, ["inspect", container_id]).stdout)[0]
    labels = observed.get("Config", {}).get("Labels", {})
    if observed.get("Id") != container_id or labels.get("complect.checkout_id") != receipt["checkout_id"]:
        raise RuntimeError("ClamAV ownership proof failed")
    _owned_container(commands, receipt, "clamav")
    _docker(commands, ["stop", "--time", "5", container_id], write=True).check_returncode()

    def restore() -> None:
        _owned_container(commands, receipt, "clamav")
        _docker(commands, ["start", container_id], write=True).check_returncode()

    return restore


def _set_sentinel(receipt_path: Path, value: str) -> Callable[[], None]:
    receipt = json.loads(receipt_path.read_text())
    checkout = Path(receipt["root"])
    commands = stand_commands(checkout, receipt)
    redis = receipt["redis"]
    redis_id = _safe_identifier(redis["id"], "Redis container")
    _owned_container(commands, receipt, "redis")
    result = _docker(commands, ["exec", redis_id, "redis-cli", "SET", value, value], write=True)
    result.check_returncode()

    def check() -> None:
        _owned_container(commands, receipt, "redis")
        result = _docker(commands, ["exec", redis_id, "redis-cli", "GET", value])
        if result.returncode != 0 or result.stdout.strip() != value:
            raise RuntimeError("REDIS_SENTINEL_CHANGED")

    return check


def _owned_container(commands, receipt: dict, service: str) -> dict:
    expected = receipt["containers"][service]
    resources = commands.ownership.owned(
        "container",
        [expected["id"]],
        {
            "com.docker.compose.project": receipt["project"],
            "complect.checkout_id": receipt["checkout_id"],
        },
    )
    if len(resources) != 1:
        raise RuntimeError(f"{service} ownership proof failed")
    actual = resources[0]
    if any(actual.get(observed) != expected.get(recorded) for observed, recorded in (("Id", "id"), ("Image", "image"), ("Mounts", "mounts"))):
        raise RuntimeError(f"{service} ownership fingerprint changed")
    return actual


def _capture_effects(receipt_path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    receipt = json.loads(receipt_path.read_text())
    commands = stand_commands(Path(receipt["root"]), receipt)
    database = _safe_identifier(receipt["database"]["database"], "database")
    owner = _safe_identifier(receipt["database"].get("user", "complect_migrator"), "database owner")
    postgres = receipt["containers"]["postgres_primary"]["id"]
    _owned_container(commands, receipt, "postgres_primary")
    marker_table = _docker(commands, [
        "exec", postgres, "psql", "-X", "-At", "-U", owner, "-d", database,
        "-c", "SELECT to_regclass('pytest_lifecycle_control.target')::text",
    ])
    if marker_table.returncode != 0:
        raise RuntimeError("database marker probe failed")
    if marker_table.stdout.strip() in {"", "None"}:
        marker = ()
    else:
        marker_result = _docker(commands, [
            "exec", postgres, "psql", "-X", "-At", "-U", owner, "-d", database,
            "-c", "SELECT run_id FROM pytest_lifecycle_control.target ORDER BY run_id",
        ])
        if marker_result.returncode != 0:
            raise RuntimeError("database marker probe failed")
        marker = tuple(line for line in marker_result.stdout.splitlines() if line)
    lifecycle = _docker(commands, [
        "ps", "-aq", "--filter", f"label=com.docker.compose.project={receipt['project']}",
        "--filter", "name=-lifecycle-",
    ])
    if lifecycle.returncode != 0:
        raise RuntimeError("lifecycle inventory probe failed")
    return tuple(marker), tuple(
        line for line in lifecycle.stdout.splitlines() if line
    )


def _assert_effects_unchanged(receipt_path: Path, before: tuple[tuple[str, ...], tuple[str, ...]]) -> None:
    if _capture_effects(receipt_path) != before:
        raise RuntimeError("STAND_EFFECTS_CHANGED")


def _run_runner(*, receipt: Path, stand_dir: Path, snapshot: Path, source_root: Path, preflight_only: bool = False) -> subprocess.CompletedProcess[str]:
    snapshot = snapshot.resolve()
    source_root = source_root.resolve()
    manifest = stand_dir / "manifest.json"
    output = stand_dir / ("baseline-evidence" if preflight_only else "refusal-evidence")
    command = [
        sys.executable, "-B", "-m", "infra.scripts.run_legacy_regressions",
        "--root", str(stand_dir / "checkout"),
        "--ownership", str(receipt),
        "--manifest", str(manifest),
        "--output", str(output),
        "--run-id", json.loads(receipt.read_text())["run_id"],
        "--snapshot", str(snapshot),
        "--review-patch", str(REVIEW_PATCH.resolve()),
        "--source-root", str(source_root),
    ]
    if preflight_only:
        command.append("--preflight-only")
    result = subprocess.run(command, cwd=source_root, capture_output=True, text=True, check=False, timeout=1800)
    output.mkdir(parents=True, exist_ok=True)
    error_types = re.findall(r"^([A-Za-z_][A-Za-z_0-9]*Error):", result.stderr, re.MULTILINE)
    (output / "runner-process.json").write_text(json.dumps({
        "exit": result.returncode, "error_types": error_types,
        "stdout_bytes": len(result.stdout.encode()), "stderr_bytes": len(result.stderr.encode()),
    }, indent=2) + "\n")
    return result


def _assert_refusal(result: subprocess.CompletedProcess[str], stand_dir: Path, scenario: str, reason: str) -> None:
    if result.returncode != 2:
        raise RuntimeError(f"{scenario}: expected exit 2, got {result.returncode}")
    evidence = stand_dir / "refusal-evidence" / "preflight.json"
    payload = json.loads(evidence.read_text())
    if payload.get("status") != "BLOCKED_STAND" or payload.get("errors") != [reason]:
        raise RuntimeError(f"{scenario}: unexpected preflight refusal")
    for name in ("lifecycle.jsonl", "lifecycle.sock", "pytest.log", "manifest.json"):
        if (stand_dir / "refusal-evidence" / name).exists():
            raise RuntimeError(f"{scenario}: effect artifact exists: {name}")


def verify(*, snapshot: Path, directory: Path, source_root: Path = ROOT) -> dict[str, object]:
    directory = directory.resolve()
    if not directory.is_relative_to(ROOT / ".dev/test-artifacts") or directory.exists():
        raise ValueError("refusal directory must be fresh under .dev/test-artifacts")
    directory.mkdir(parents=True, mode=0o700)
    results: list[dict[str, str]] = []
    for scenario in SCENARIOS:
        stand_dir = directory / scenario.name
        canonical = stand_dir / "ownership.json"
        restore: Callable[[], None] = _noop
        sentinel_check: Callable[[], None] = _noop
        effects_before: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        phase = "prepare"
        primary_error: BaseException | None = None
        cleanup_error: BaseException | None = None
        try:
            prepare(source_root, snapshot, stand_dir, manifest=MANIFEST)
            phase = "baseline"
            baseline = _run_runner(receipt=canonical, stand_dir=stand_dir,
                                   snapshot=snapshot, source_root=source_root,
                                   preflight_only=True)
            baseline_receipt = stand_dir / "baseline-evidence/preflight.json"
            if baseline.returncode != 0 or not baseline_receipt.is_file():
                raise RuntimeError("baseline runner did not confirm readiness")
            baseline_payload = json.loads(baseline_receipt.read_text())
            if baseline.returncode != 0 or baseline_payload != {"status": "PASS", "errors": []}:
                raise RuntimeError("refusal requires a ready baseline")
            scenario_dir = stand_dir / "scenario"
            scenario_dir.mkdir(mode=0o700)
            receipt = _copy_receipt(canonical, scenario_dir)
            phase = "sentinel"
            sentinel = f"{SENTINEL_PREFIX}{uuid.uuid4().hex}"
            sentinel_check = _set_sentinel(canonical, sentinel)
            phase = "effects-before"
            effects_before = _capture_effects(canonical)
            phase = "inject"
            if scenario.name == "redis_identity":
                restore = _break_redis_identity(receipt)
            elif scenario.name == "database_identity":
                restore = _break_database_identity(receipt)
            elif scenario.name == "runtime_permissions":
                restore = _break_runtime_permissions(canonical)
            else:
                restore = _break_clamav(canonical)
            phase = "runner"
            result = _run_runner(receipt=receipt, stand_dir=stand_dir, snapshot=snapshot, source_root=source_root)
            _assert_refusal(result, stand_dir, scenario.name, scenario.reason)
            sentinel_check()
            if effects_before is None:
                raise RuntimeError("missing effects baseline")
            _assert_effects_unchanged(canonical, effects_before)
        except BaseException as exc:
            primary_error = exc
        finally:
            try:
                restore()
            except BaseException as exc:
                cleanup_error = exc
            if canonical.is_file():
                try:
                    cleaned = cleanup(canonical)
                    if cleaned.get("cleanup") != "PASS":
                        raise RuntimeError(f"{scenario.name}: canonical cleanup blocked")
                except BaseException as exc:
                    cleanup_error = exc
            record: dict[str, str] = {
                "scenario": scenario.name,
                "status": "PASS" if primary_error is None and cleanup_error is None else "BLOCKED_STAND",
                "reason": scenario.reason,
                "phase": phase,
            }
            if primary_error is not None:
                record["error"] = type(primary_error).__name__
            if cleanup_error is not None:
                record["cleanup_error"] = type(cleanup_error).__name__
            stand_dir.mkdir(parents=True, exist_ok=True)
            (stand_dir / "result.json").write_text(json.dumps(record, indent=2) + "\n")
            results.append(record)
    if any(record["status"] != "PASS" for record in results):
        raise RuntimeError("legacy preflight refusal verification blocked")
    return {"status": "PASS", "scenarios": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(verify(snapshot=args.snapshot, directory=args.directory, source_root=args.source_root), indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED_STAND", "error": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
