"""Full working-tree proof for the multi-phase Infrastructure gate."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from infra.scripts import agent_harness

SNAPSHOT_FIELDS = ("head", "index_entries_sha256", "index_flags_sha256", "files", "deleted", "excluded")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture(root: Path, output: Path, name: str) -> dict[str, Any]:
    """Capture and persist one complete source snapshot before/after the gate."""
    output.mkdir(parents=True, exist_ok=True)
    snapshot = agent_harness.working_tree_snapshot(root)
    path = output / name
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": str(path), "sha256": sha256_file(path), "snapshot": snapshot}


def _same_source(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    return all(before.get(field) == after.get(field) for field in SNAPSHOT_FIELDS)


def finish(
    *,
    root: Path,
    output: Path,
    before: Mapping[str, Any],
    gate_exit: int,
    artifacts: Mapping[str, Path],
) -> tuple[str, int]:
    """Write source-proof.json and preserve the primary phase exit code."""
    status = "BLOCKED_STAND"
    exit_code = gate_exit
    proof: dict[str, Any] = {"version": 1, "status": status}
    try:
        after = capture(root, output, "sources-after.json")
        before_path = output / "sources-before.json"
        if not before_path.is_file():
            raise ValueError("source-before snapshot missing")
        source_before = json.loads(before_path.read_text(encoding="utf-8"))
        expected_before = before.get("snapshot", before) if isinstance(before, Mapping) else before
        if source_before != expected_before:
            raise ValueError("source-before snapshot differs from captured source")
        source_after = after["snapshot"]
        artifact_hashes: dict[str, str] = {}
        for name, path in artifacts.items():
            path = path.resolve()
            if not path.is_file() or not path.is_relative_to(output.resolve()):
                raise ValueError(f"invalid source-proof artifact: {name}")
            artifact_hashes[str(path.relative_to(output.resolve()))] = sha256_file(path)
        unchanged = _same_source(source_before, source_after)
        status = "PASS" if unchanged and gate_exit == 0 else "FAIL"
        if not unchanged and exit_code == 0:
            exit_code = 2
        proof = {
            "version": 1,
            "status": status,
            "source_before": {"path": "sources-before.json", "sha256": sha256_file(before_path)},
            "source_after": {"path": "sources-after.json", "sha256": after["sha256"]},
            "artifacts": artifact_hashes,
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        proof["error"] = str(exc)
        if exit_code == 0:
            exit_code = 2
    (output / "source-proof.json").write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return status, exit_code


def verify(output: Path) -> dict[str, Any]:
    """Verify a producer-written source-proof sidecar without executing commands."""
    proof_path = output / "source-proof.json"
    if proof_path.is_symlink() or any(p.is_symlink() for p in proof_path.parents):
        raise ValueError("unsafe source proof path")
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    if not isinstance(proof,dict) or type(proof.get("version")) is not int or proof["version"] != 1 or proof.get("status") != "PASS":
        raise ValueError("source proof is not PASS")
    loaded: dict[str, Any] = {}
    for key in ("source_before", "source_after"):
        reference = proof.get(key)
        if not isinstance(reference, Mapping) or not isinstance(reference.get("path"), str):
            raise ValueError(f"invalid source proof reference: {key}")
        expected_name = "sources-before.json" if key == "source_before" else "sources-after.json"
        if reference["path"] != expected_name or not isinstance(reference.get("sha256"),str) or not re.fullmatch(r"[0-9a-f]{64}",reference["sha256"]):
            raise ValueError("invalid source snapshot identity")
        path = output / reference["path"]
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError("source snapshot symlink refused")
        path = path.resolve()
        if not path.is_relative_to(output.resolve()) or not path.is_file():
            raise ValueError(f"unsafe source proof path: {key}")
        if reference.get("sha256") != sha256_file(path):
            raise ValueError(f"source proof hash mismatch: {key}")
        loaded[key] = json.loads(path.read_text(encoding="utf-8"))
        snapshot = loaded[key]
        if (not isinstance(snapshot,dict) or snapshot.get("format") != "complect-working-tree-snapshot-v1"
                or not isinstance(snapshot.get("files"),dict) or not isinstance(snapshot.get("deleted"),list)
                or not isinstance(snapshot.get("excluded"),list) or any(field not in snapshot for field in SNAPSHOT_FIELDS)):
            raise ValueError("source snapshot contract incomplete")
    if not _same_source(loaded["source_before"], loaded["source_after"]):
        raise ValueError("source proof snapshots differ")
    artifacts = proof.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("source proof artifacts missing")
    for relative, digest in artifacts.items():
        if not isinstance(relative,str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("unsafe artifact reference")
        path = output / relative
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise ValueError("source proof artifact symlink refused")
        path = path.resolve()
        if not path.is_relative_to(output.resolve()) or not path.is_file() or digest != sha256_file(path):
            raise ValueError(f"source proof artifact mismatch: {relative}")
    loaded["artifacts"] = artifacts
    return loaded
