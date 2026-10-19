"""Verify independently pinned criteria and evidence without executing them."""
from __future__ import annotations

import argparse
import json
import re
import stat
from pathlib import Path
from typing import Any, cast

from infra.scripts.evidence_bundle import read_exact_chunk

REQUIREMENTS = "complect-acceptance-matrix-requirements-v1"
MATRIX = "complect-acceptance-matrix-v1"
RESULT = "complect-acceptance-matrix-result-v1"
_LIMIT = 1024 * 1024


class MatrixError(ValueError):
    """A required matrix input cannot be accepted."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise MatrixError(code)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        _require(key not in result, "JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _regular(path: Path) -> None:
    _require(not any(p.is_symlink() for p in (path, *path.parents)), "PATH_SYMLINK")
    _require(stat.S_ISREG(path.stat().st_mode), "PATH_NOT_REGULAR")


def _read(path: Path, digest: object) -> bytes:
    _require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None, "HASH_INVALID")
    _regular(path)
    size = path.stat().st_size
    _require(0 <= size <= _LIMIT, "INPUT_TOO_LARGE")
    return read_exact_chunk(path, 0, size, str(digest))


def _constant(_value: str) -> None:
    raise MatrixError("JSON_NON_FINITE_NUMBER")


def _json(data: bytes) -> dict[str, Any]:
    value = json.loads(data, object_pairs_hook=_pairs, parse_constant=_constant)
    _require(isinstance(value, dict), "JSON_OBJECT_REQUIRED")
    return value


def _path(root: Path, name: object) -> Path:
    _require(isinstance(name, str) and bool(name), "ARTIFACT_PATH_INVALID")
    relative = Path(str(name))
    _require(not relative.is_absolute() and ".." not in relative.parts, "ARTIFACT_PATH_INVALID")
    return root / relative


def _pointer(value: Any, pointer: object) -> Any:
    _require(isinstance(pointer, str) and pointer.startswith("/"), "POINTER_INVALID")
    for raw in str(pointer).split("/")[1:]:
        _require(re.search(r"~(?![01])", raw) is None, "POINTER_INVALID")
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            _require(key in value, "PROOF_FIELD_MISSING")
            value = value[key]
        else:
            _require(isinstance(value, list) and key.isdigit() and int(key) < len(value), "PROOF_FIELD_MISSING")
            value = value[int(key)]
    return value


def _equal(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def verify_acceptance_matrix(requirements: Path, matrix: Path, root: Path, *, requirements_sha256: str, matrix_sha256: str) -> dict[str, Any]:
    """Authenticate parent inputs and report coverage, integrity and behavior separately."""
    result: dict[str, Any] = {"format": RESULT, "status": "FAIL", "coverage": "UNKNOWN", "integrity": "UNKNOWN", "behavior": "UNKNOWN", "errors": []}
    for field, pin in (("requirements_sha256", requirements_sha256), ("matrix_sha256", matrix_sha256)):
        result[field] = pin if isinstance(pin, str) and re.fullmatch(r"[0-9a-f]{64}", pin) else None
    result.update(requirements_authenticated=False, matrix_authenticated=False)
    try:
        _require(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)), "ROOT_INVALID")
        raw = _read(requirements, requirements_sha256)
        result["requirements_authenticated"] = True
        expected = _json(raw)
        raw = _read(matrix, matrix_sha256)
        result["matrix_authenticated"] = True
        actual = _json(raw)
        _require(expected.get("format") == REQUIREMENTS, "REQUIREMENTS_FORMAT_INVALID")
        _require(actual.get("format") == MATRIX, "MATRIX_FORMAT_INVALID")
        _require(actual.get("requirements_sha256") == requirements_sha256, "REQUIREMENTS_BINDING_MISMATCH")
        sources = expected.get("sources")
        _require(isinstance(sources, list) and bool(sources), "SOURCES_REQUIRED")
        documents: dict[str, str] = {}
        for source in cast(list[Any], sources):
            _require(isinstance(source, dict), "SOURCE_INVALID")
            path = source.get("path")
            _require(isinstance(path, str) and path not in documents, "SOURCE_DUPLICATE_OR_INVALID")
            documents[str(path)] = _read(_path(root, path), source.get("sha256")).decode("utf-8")
        criteria = expected.get("criteria")
        _require(isinstance(criteria, list) and bool(criteria), "CRITERIA_REQUIRED")
        selected: dict[str, dict[str, Any]] = {}
        for criterion in cast(list[Any], criteria):
            _require(isinstance(criterion, dict), "CRITERION_INVALID")
            identifier, issue, quote = criterion.get("id"), criterion.get("issue"), criterion.get("quote")
            _require(type(issue) is int and issue > 0 and isinstance(identifier, str) and re.fullmatch(str(issue) + r"-[1-9][0-9]*", identifier) is not None, "CRITERION_ID_INVALID")
            _require(identifier not in selected, "CRITERION_DUPLICATE")
            _require(isinstance(quote, str) and bool(quote.strip()) and quote in documents.get(criterion.get("source"), ""), "CRITERION_QUOTE_INVALID")
            _require(isinstance(criterion.get("source_identity"), dict) and bool(criterion["source_identity"]), "SOURCE_IDENTITY_REQUIRED")
            _require(isinstance(criterion.get("facts"), dict) and bool(criterion["facts"]), "EVIDENCE_FACTS_REQUIRED")
            selected[identifier] = criterion
        result["integrity"] = "PASS"
        result["coverage"] = "FAIL"
        rows = actual.get("rows")
        _require(isinstance(rows, list), "ROWS_REQUIRED")
        observed: dict[str, dict[str, Any]] = {}
        for row in cast(list[Any], rows):
            _require(isinstance(row, dict) and isinstance(row.get("id"), str), "ROW_INVALID")
            identifier = row["id"]
            _require(identifier in selected, "ROW_UNKNOWN")
            _require(identifier not in observed, "ROW_DUPLICATE")
            criterion = selected[identifier]
            _require(type(row.get("issue")) is int and row["issue"] == criterion["issue"] and row.get("quote") == criterion["quote"], "ROW_REQUIREMENT_MISMATCH")
            observed[identifier] = row
        _require(set(selected) == set(observed), "ROW_MISSING")
        result["coverage"] = "PASS"
        result["behavior"] = "PASS"
        for identifier, row in observed.items():
            status = row.get("status")
            _require(isinstance(status, str) and (status in {"PASS", "FAIL", "PARTIAL", "BLOCKED", "NOT_RUN", "FAIL_BASELINE"} or re.fullmatch(r"BLOCKED_[A-Z_]+", status) is not None), "ROW_STATUS_INVALID")
            if status != "PASS":
                result["behavior"] = "FAIL"
            criterion = selected[identifier]
            evidence = row.get("evidence")
            _require(isinstance(evidence, dict), "EVIDENCE_REQUIRED")
            proof = _json(_read(_path(root, cast(dict[str, Any], evidence).get("path")), cast(dict[str, Any], evidence).get("sha256")))
            _require(_equal(_pointer(proof, criterion.get("identity_pointer")), criterion["source_identity"]), "EVIDENCE_SOURCE_MISMATCH")
            for pointer, wanted in criterion["facts"].items():
                _require(_equal(_pointer(proof, pointer), wanted), "EVIDENCE_FACT_MISMATCH")
        result["criteria_count"] = len(selected)
        result["requirements_sha256"] = requirements_sha256
        result["matrix_sha256"] = matrix_sha256
        if result["behavior"] == "PASS":
            result["status"] = "PASS"
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        result["errors"].append(str(error) if isinstance(error, MatrixError) else "INPUT_UNAVAILABLE_OR_INVALID")
        if result["coverage"] != "FAIL":
            result["integrity"] = "FAIL"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--requirements-sha256", required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--matrix-sha256", required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    result = verify_acceptance_matrix(args.requirements, args.matrix, args.root, requirements_sha256=args.requirements_sha256, matrix_sha256=args.matrix_sha256)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1 if result["coverage"] == "FAIL" or result["behavior"] == "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
