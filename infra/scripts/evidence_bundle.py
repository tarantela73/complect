"""Fail-closed evidence helpers and CLI for infrastructure acceptance runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping

from infra.scripts import agent_harness
from infra.scripts.legacy_preflight import verify_prepared_checkout

FORMAT = "complect-reviewer-envelope-v1"
_CHUNK_SIZE = 1024 * 1024


class EvidenceBundleError(ValueError):
    """The required reviewer envelope cannot be constructed or verified."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def read_exact_chunk(path: Path, offset: int, count: int, expected_sha256: str) -> bytes:
    """Return bytes from the same stream authenticated by the complete hash."""
    if offset < 0 or count < 0 or count > _CHUNK_SIZE:
        raise EvidenceBundleError("offset/count outside bounded reader limits")
    if not path.is_file():
        raise EvidenceBundleError("exact source artifact unavailable or changed")
    digest = hashlib.sha256()
    selected = bytearray()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_SIZE):
            digest.update(chunk)
            start = max(offset - size, 0)
            stop = min(offset + count - size, len(chunk))
            if start < stop:
                selected.extend(chunk[start:stop])
            size += len(chunk)
    if digest.hexdigest() != expected_sha256:
        raise EvidenceBundleError("exact source artifact unavailable or changed")
    if offset > size or offset + count > size or len(selected) != count:
        raise EvidenceBundleError("requested range exceeds exact source artifact")
    return bytes(selected)


def _safe_path(root: Path, name: object) -> tuple[Path, bool]:
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        return root, True
    candidate = (root / name).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return candidate, True
    return candidate, False


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError as exc:
        raise EvidenceBundleError("evidence artifact is outside evidence root") from exc


def _hash_map(items: Mapping[str, Path], root: Path) -> dict[str, str]:
    if not items:
        raise EvidenceBundleError("source and patch hashes are required")
    result: dict[str, str] = {}
    for name, path in items.items():
        if not name or Path(name).is_absolute() or ".." in Path(name).parts:
            raise EvidenceBundleError("artifact name must be relative")
        if not path.is_file():
            raise EvidenceBundleError("required evidence artifact is unavailable")
        result[_relative_path(path, root)] = _sha256(path)
    return result


def build_reviewer_envelope(
    source_hashes: Mapping[str, Path], patch_hashes: Mapping[str, Path],
    image_id: str, run_id: str, result_paths: Mapping[str, Path],
) -> dict[str, object]:
    if not image_id or not run_id or not source_hashes or not patch_hashes or not result_paths:
        raise EvidenceBundleError("image_id, run_id, source, patch and results are required")
    artifacts = [path.resolve() for path in (*source_hashes.values(), *patch_hashes.values(), *result_paths.values())]
    root = Path(os.path.commonpath([str(path.parent) for path in artifacts]))
    results: dict[str, dict[str, str]] = {}
    for name, path in result_paths.items():
        if not name or not path.is_file():
            raise EvidenceBundleError("required result artifact is unavailable")
        results[name] = {"path": _relative_path(path, root), "sha256": _sha256(path)}
    return {"format": FORMAT, "root": str(root), "source": _hash_map(source_hashes, root), "patch": _hash_map(patch_hashes, root), "image_id": image_id, "run_id": run_id, "results": results}


def verify_reviewer_envelope(envelope: Mapping[str, object], root: Path) -> list[str]:
    errors: list[str] = []
    if envelope.get("format") != FORMAT:
        errors.append("ENVELOPE_FORMAT_INVALID")
    for field in ("image_id", "run_id"):
        if not isinstance(envelope.get(field), str) or not envelope[field]:
            errors.append(f"{field.upper()}_MISSING")
    for section, missing, mismatch in (("source", "SOURCE_ARTIFACT_MISSING", "SOURCE_HASH_MISMATCH"), ("patch", "PATCH_ARTIFACT_MISSING", "PATCH_HASH_MISMATCH")):
        values = envelope.get(section)
        if not isinstance(values, Mapping) or not values:
            errors.append(f"{section.upper()}_HASHES_REQUIRED")
        if not isinstance(values, Mapping):
            errors.append(f"{section.upper()}_INVALID")
            continue
        for name, expected in values.items():
            path, invalid = _safe_path(root, name)
            if invalid:
                errors.append(f"{section.upper()}_PATH_INVALID")
            elif not path.is_file():
                errors.append(missing)
            elif not isinstance(expected, str) or len(expected) != 64 or _sha256(path) != expected:
                errors.append(mismatch)
    results = envelope.get("results")
    if not isinstance(results, Mapping) or not results:
        errors.append("RESULTS_INVALID")
    else:
        for item in results.values():
            if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                errors.append("RESULT_ARTIFACT_INVALID")
                continue
            path, invalid = _safe_path(root, item["path"])
            if invalid:
                errors.append("RESULT_PATH_INVALID")
            elif not path.is_file():
                errors.append("RESULT_ARTIFACT_MISSING")
            elif not isinstance(item.get("sha256"), str) or len(item["sha256"]) != 64 or _sha256(path) != item["sha256"]:
                errors.append("RESULT_HASH_MISMATCH")
    return sorted(set(errors))


def verify_post_run_sources(root: Path, source_root: Path, snapshot: Path) -> list[str]:
    errors: list[str] = []
    if agent_harness.verify_working_tree_snapshot(root, snapshot):
        errors.append("SNAPSHOT_MISMATCH")
    errors.extend(verify_prepared_checkout(source_root, snapshot))
    return sorted(set(errors))


def finish_run(
    manifest_path: Path,
    envelope_path: Path,
    envelope: Mapping[str, object],
    root: Path,
    cleanup_failed: bool,
    preexisting_errors: list[str] | None = None,
) -> dict[str, object]:
    """Produce the terminal receipt while preserving the raw pytest exit."""
    manifest = json.loads(manifest_path.read_text())
    final_exit = int(manifest.get("exit", 2))
    if cleanup_failed:
        final_exit = final_exit or 2
    envelope_errors = verify_reviewer_envelope(envelope, root)
    if preexisting_errors:
        envelope_errors.extend(preexisting_errors)
        envelope_errors = sorted(set(envelope_errors))
    if envelope_errors:
        final_exit = final_exit or 2
    receipt: dict[str, object] = {
        "status": "PASS" if final_exit == 0 and not envelope_errors else "BLOCKED_STAND",
        "exit": final_exit,
        "cleanup": "BLOCKED_STAND" if cleanup_failed else "PASS",
        "envelope_errors": envelope_errors,
    }
    envelope_path.write_text(json.dumps(envelope, indent=2) + "\n")
    return receipt


def write_post_run_evidence(path: Path, errors: list[str]) -> None:
    path.write_text(json.dumps({"status": "PASS" if not errors else "BLOCKED_STAND", "errors": errors}, indent=2) + "\n")


def _path_mapping(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path:
            raise EvidenceBundleError("artifacts must use NAME=PATH")
        result[name] = Path(raw_path)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    reader = subparsers.add_parser("read")
    reader.add_argument("--path", type=Path, required=True)
    reader.add_argument("--offset", type=int, default=0)
    reader.add_argument("--count", type=int, required=True)
    reader.add_argument("--sha256", required=True)
    creator = subparsers.add_parser("create")
    creator.add_argument("--source", action="append", default=[])
    creator.add_argument("--patch", action="append", default=[])
    creator.add_argument("--result", action="append", default=[])
    creator.add_argument("--image-id", required=True)
    creator.add_argument("--run-id", required=True)
    creator.add_argument("--output", type=Path, required=True)
    verifier = subparsers.add_parser("verify")
    verifier.add_argument("--envelope", type=Path, required=True)
    verifier.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "read":
            content = read_exact_chunk(args.path, args.offset, args.count, args.sha256)
            print(json.dumps({"path": str(args.path), "offset": args.offset, "count": len(content), "content": content.decode("utf-8")}, ensure_ascii=False))
            return
        if args.command == "create":
            envelope = build_reviewer_envelope(_path_mapping(args.source), _path_mapping(args.patch), args.image_id, args.run_id, _path_mapping(args.result))
            args.output.write_text(json.dumps(envelope, indent=2) + "\n")
            print(json.dumps({"status": "PASS", "envelope": str(args.output)}))
            return
        envelope = json.loads(args.envelope.read_text())
        errors = verify_reviewer_envelope(envelope, args.root)
        print(json.dumps({"status": "PASS" if not errors else "BLOCKED_STAND", "errors": errors}))
        if errors:
            raise SystemExit(2)
    except (EvidenceBundleError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED_STAND", "errors": [str(exc)]}))
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
