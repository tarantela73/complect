"""Versioned identity and read-only status of a committed legacy attempt."""

from __future__ import annotations

import json
import hashlib
import os
import stat
import uuid
from pathlib import Path
from typing import Any

PROTOCOL = "infra/attempt-identity-protocol.json"


def supports_identity(frozen: Path) -> bool:
    from infra.scripts.run_legacy_qualification import git

    names = git(frozen, "ls-tree", "--name-only", "HEAD", "--", PROTOCOL)
    if not names:
        return False
    protocol = json.loads(git(frozen, "show", f"HEAD:{PROTOCOL}"))
    if (
        not isinstance(protocol, dict)
        or type(protocol.get("version")) is not int
        or protocol != {"version": 1}
    ):
        raise ValueError("unsupported attempt identity protocol")
    return True


def create_identity(directory: Path, state: dict[str, Any]) -> None:
    from infra.scripts.run_legacy_qualification import write_record

    if supports_identity(directory / "frozen"):
        write_record(
            directory / "identity.json",
            {
                "version": 1,
                "attempt_id": str(uuid.uuid4()),
                "directory": str(directory),
                "source_commit": state["source_commit"],
                "source_tree": state["source_tree"],
                "artifacts": {},
            },
        )
        record_artifact(directory / "attempt.json")


def validate_identity(directory: Path, state: dict[str, Any]) -> dict[str, Any] | None:
    from infra.scripts.run_legacy_qualification import read_record

    path = directory / "identity.json"
    if not supports_identity(directory / "frozen"):
        if path.exists() or path.is_symlink():
            raise ValueError("identity supplied for legacy source")
        return None
    value = read_record(path)
    try:
        valid_id = str(uuid.UUID(value["attempt_id"])) == value["attempt_id"]
    except (KeyError, TypeError, ValueError, AttributeError):
        valid_id = False
    if (
        type(value.get("version")) is not int
        or value["version"] != 1
        or not valid_id
        or any(
            value.get(k) != state[k]
            for k in ("directory", "source_commit", "source_tree")
        )
        or not isinstance(value.get("artifacts"), dict)
    ):
        raise ValueError("attempt identity mismatch")
    for binding in value["artifacts"].values():
        if not isinstance(binding, dict) or any(
            binding.get(k) != value[k]
            for k in ("attempt_id", "source_commit", "source_tree")
        ):
            raise ValueError("artifact identity anchor mismatch")
    return value


def _artifact_location(path: Path) -> tuple[Path, str] | None:
    if path.parent.name == "results" and path.name in (
        "manifest.json",
        "result.json",
        "run-cleanup.json",
        "post-run-sources.json",
        "target.json",
        "privilege-probes.json",
        "lifecycle.jsonl",
    ):
        for ancestor in path.parents:
            if path.parent == ancestor / "frozen/.dev/test-artifacts/stand/results":
                return ancestor, "test"
    if path.parent.name == "processes":
        stage = path.name.split(".", 1)[0]
        if stage not in ("prepare", "test") or path.suffix not in (".json", ".log"):
            return None
        return path.parent.parent, stage
    if path.name in (
        "attempt.json",
        "qualification.json",
        "snapshot.json",
        "baseline.json",
    ):
        return path.parent, "qualification"
    if path.name.startswith("recovery-") and path.suffix == ".json":
        return path.parent, "recovery"
    return None


def _digest(path: Path) -> str:
    if (
        path.is_symlink()
        or any(p.is_symlink() for p in path.parents)
        or not path.is_file()
    ):
        raise ValueError("unsafe or missing identity artifact")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record_artifact(path: Path) -> None:
    """Serialize producer bindings across the parent and stage supervisors."""
    import fcntl

    located = _artifact_location(path)
    if located is None or not (located[0] / "identity.json").exists():
        return
    lock = located[0] / "identity.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise ValueError("unsafe identity lock")
        fcntl.flock(fd, fcntl.LOCK_EX)
        _record_artifact(path)
    finally:
        os.close(fd)


def _record_artifact(path: Path) -> None:
    """Producer-side binding at a durable write; status never invents bindings."""
    from infra.scripts.run_legacy_qualification import read_record, write_record

    located = _artifact_location(path)
    if located is None:
        return
    directory, stage = located
    if not (directory / "identity.json").exists():
        return  # Old sources retain their historical producer/recovery behavior.
    state = read_record(directory / "attempt.json")
    identity = validate_identity(directory, state)
    if identity is None:
        return

    def bind(target: Path, digest: str | None) -> None:
        identity["artifacts"][str(target.relative_to(directory))] = {
            "attempt_id": identity["attempt_id"],
            "source_commit": identity["source_commit"],
            "source_tree": identity["source_tree"],
            "stage": stage,
            "sha256": digest,
        }

    bind(path, _digest(path))
    if path.parent.name == "processes" and path.name == stage + ".json":
        log = path.with_suffix(".log")
        if log.exists():
            terminal = read_record(path).get("state") in (
                "finished",
                "stopped",
                "leader_gone",
                "blocked_parent_lost",
                "blocked_timeout",
            )
            bind(log, _digest(log) if terminal else None)
    write_record(directory / "identity.json", identity)


def bind_runtime_artifacts(directory: Path) -> None:
    """Seal the bounded original133 outputs after all stage work has stopped."""
    results = directory / "frozen/.dev/test-artifacts/stand/results"
    if results.is_symlink():
        raise ValueError("results directory symlink refused")
    if results.is_dir():
        for path in results.iterdir():
            if _artifact_location(path) is not None:
                record_artifact(path)


def _verified_artifacts(directory: Path, identity: dict[str, Any]) -> dict[str, Any]:
    recorded = identity["artifacts"]
    observed = {
        p.relative_to(directory).as_posix()
        for p in directory.iterdir()
        if _artifact_location(p) is not None
    }
    results = directory / "frozen/.dev/test-artifacts/stand/results"
    if results.is_symlink():
        raise ValueError("results directory symlink refused")
    if results.is_dir():
        observed.update(
            p.relative_to(directory).as_posix()
            for p in results.iterdir()
            if _artifact_location(p) is not None
        )
    processes = directory / "processes"
    if processes.is_symlink():
        raise ValueError("process directory symlink refused")
    if processes.exists():
        observed.update(
            p.relative_to(directory).as_posix()
            for p in processes.iterdir()
            if _artifact_location(p) is not None
        )
    if set(recorded) != observed:
        raise ValueError("unbound or missing attempt artifacts")
    for relative, binding in recorded.items():
        path = directory / relative
        located = _artifact_location(path)
        if (
            not isinstance(binding, dict)
            or located is None
            or located[0] != directory
            or any(
                binding.get(k) != identity[k]
                for k in ("attempt_id", "source_commit", "source_tree")
            )
            or binding.get("stage") != located[1]
            or binding.get("sha256") != _digest(path)
        ):
            raise ValueError(
                "artifact binding changed or stage log not sealed: " + relative
            )
    return recorded


def status(directory: Path) -> dict[str, Any]:
    from infra.scripts.run_legacy_qualification import read_record, validate_attempt

    directory = directory.absolute()
    state = validate_attempt(directory, verify_identity=False)
    errors: list[str] = []
    identity = None
    artifacts = {}
    try:
        identity = validate_identity(directory, state)
        artifacts = _verified_artifacts(directory, identity) if identity else {}
    except (ValueError, OSError, TypeError, KeyError) as error:
        errors.append(str(error))
    qualification = directory / "qualification.json"
    verdict = (
        read_record(qualification).get("status")
        if qualification.exists()
        else "NOT_RUN"
    )
    return {
        "status": "BLOCKED_STAND" if errors else "PASS",
        "binding": "BLOCKED" if errors else ("PASS" if identity else "LEGACY"),
        "errors": errors,
        "attempt_id": identity["attempt_id"] if identity else None,
        "source_commit": state["source_commit"],
        "source_tree": state["source_tree"],
        "phase": state["phase"],
        "original_verdict": verdict,
        "artifacts": artifacts,
    }
