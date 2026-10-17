"""Read-only, producer-specific acceptance of parent-required check receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, cast


def _json(path: Path) -> dict[str, Any]:
    if (
        path.is_symlink()
        or any(p.is_symlink() for p in path.parents)
        or not path.is_file()
    ):
        raise ValueError(f"unsafe or missing evidence: {path}")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("evidence must be an object")
    return value


def require(condition: Any, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def zero(value: Any) -> bool:
    return type(value) is int and value == 0


def positive(value: Any) -> bool:
    return type(value) is int and value > 0


def sha(value: Any, size: int = 64) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{" + str(size) + "}", value) is not None
    )


def artifact(check: Mapping[str, Any], name: str) -> tuple[Path, dict[str, Any]]:
    reference = check.get("artifacts", {}).get(name)
    require(
        isinstance(reference, dict)
        and ("sha256" not in reference or sha(reference["sha256"]))
        and isinstance(reference.get("path"), str),
        "missing artifact reference: " + name,
    )
    path = Path(reference["path"])
    value = _json(path)
    require(
        "sha256" not in reference
        or hashlib.sha256(path.read_bytes()).hexdigest() == reference["sha256"],
        "artifact hash mismatch: " + name,
    )
    return path, value


def sibling(path: Path, relative: Any) -> tuple[Path, dict[str, Any]]:
    require(
        isinstance(relative, str)
        and relative
        and not Path(relative).is_absolute()
        and ".." not in Path(relative).parts,
        "unsafe evidence relative path",
    )
    target = path.parent / relative
    return target, _json(target)


def _manifest(value: dict[str, Any]) -> None:
    if isinstance(value.get("collected"), list):
        from infra.scripts.payments_gate_evidence import check_manifest

        check_manifest(value)
    else:
        count = value.get("collected")
        require(
            positive(count)
            and value.get("passed") == count
            and zero(value.get("skipped"))
            and zero(value.get("errors"))
            and zero(value.get("failures")),
            "unit manifest incomplete or skipped",
        )


def _source_hash(value: Any, source: Mapping[str, Any]) -> None:
    expected = source.get("producer_sha256")
    require(
        sha(expected) and isinstance(value, dict) and value == {"sha256": expected},
        "producer source identity missing or mismatched",
    )


def _harness(
    payload: dict[str, Any],
    path: Path,
    check: Mapping[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    require(
        payload.get("producer") == "agent_harness.py --record"
        and payload.get("verification_status") == "PASS"
        and zero(payload.get("exit_code"))
        and payload.get("sources_unchanged_during_run") is True,
        "harness verification incomplete",
    )
    require(
        payload.get("run_id") and isinstance(payload.get("cwd"), str),
        "harness execution identity missing",
    )
    verification = payload.get("verification", {})
    require(
        positive(verification.get("python_tests"))
        and positive(verification.get("node_tests"))
        and zero(verification.get("skips")),
        "harness collection empty or skipped",
    )
    expected = source.get("sources")
    require(
        isinstance(expected, dict)
        and expected
        and payload.get("sources") == expected
        and all(sha(v) for v in expected.values()),
        "harness source identity missing or mismatched",
    )
    log = check.get("log")
    if log is None:
        log = Path(payload["cwd"]) / str(payload.get("log", ""))
    else:
        log = Path(log)
    require(
        not log.is_symlink()
        and not any(p.is_symlink() for p in log.parents)
        and log.is_file()
        and hashlib.sha256(log.read_bytes()).hexdigest() == payload.get("sha256"),
        "harness log identity mismatch",
    )
    require(
        payload.get("command") == "bash infra/scripts/check_agent_harness.sh",
        "harness command mismatch",
    )
    return {
        "status": payload["verification_status"],
        "run_id": payload["run_id"],
        "source_identity": {"sources": expected},
        "execution": {"kind": "docker", "command": payload["command"]},
    }


def _infra_leaf(payload: dict[str, Any], path: Path) -> None:
    require(
        payload.get("mode") in ("pytest", "unittest")
        and payload.get("run_id")
        and payload.get("image")
        and payload.get("status") == "PASS"
        and zero(payload.get("exit"))
        and payload.get("cleanup") == "PASS",
        "infrastructure execution/cleanup incomplete",
    )
    _, manifest = sibling(path, payload.get("manifest"))
    _manifest(manifest)


def _recovery_case(
    value: Any,
    name: str,
    *,
    expected_status: str,
    expected_primary_exit: int | None,
    expected_cleanup: str,
    abrupt: bool = False,
    require_tree: bool = True,
    early_success: bool = False,
) -> None:
    """Require fields emitted by the native cleanup-only recovery producer."""
    require(
        isinstance(value, dict) and value.get("status") == "PASS",
        f"recovery {name} details missing or unsuccessful",
    )
    if name.startswith("early."):
        require(
            value.get("mode") == name.split(".", 1)[1]
            and value.get("early_failure") is True,
            f"recovery {name} scenario identity mismatch",
        )
    initial = value.get("initial")
    require(
        isinstance(initial, dict)
        and initial.get("status") == expected_status
        and (abrupt or type(initial.get("exit")) is int)
        and (abrupt or type(initial.get("primary_exit")) is int),
        f"recovery {name} initial verdict missing",
    )
    require(
        (abrupt or (initial.get("primary_exit") == expected_primary_exit
                    and initial.get("exit") == expected_primary_exit))
        and ("exit" not in initial if abrupt else True)
        and ("primary_exit" not in initial if abrupt else True)
        and sha(initial.get("source_commit"), 40),
        f"recovery {name} initial identity/verdict mismatch",
    )
    if require_tree:
        require(sha(initial.get("source_tree"), 40), f"recovery {name} source tree missing")
    require(
        initial.get("cleanup") == expected_cleanup,
        f"recovery {name} cleanup incomplete",
    )
    if early_success:
        return
    initial_source = initial["source_commit"]

    def check_record(record: Any, field: str, status: str, *, errors: bool) -> None:
        require(
            isinstance(record, dict)
            and record.get("operation") == "cleanup-only"
            and record.get("status") == status
            and record.get("original_verdict_unchanged") is True
            and record.get("source_commit") == initial_source
            and isinstance(record.get("errors"), list)
            and bool(record["errors"]) is errors,
            f"recovery {name} {field} details incomplete",
        )

    for field in ("recovery", "repeat"):
        receipt = value.get(field)
        require(
            isinstance(receipt, dict)
            and receipt.get("status") == "PASS"
            and receipt.get("operation") == "cleanup-only"
            and receipt.get("errors") == []
            and receipt.get("original_verdict_unchanged") is True
            and receipt.get("source_commit") == initial_source,
            f"recovery {name} {field} details incomplete",
        )
    if name == "ordinary":
        check_record(value.get("missing_journal"), "missing_journal", "BLOCKED_STAND", errors=True)
        check_record(value.get("blocked"), "blocked", "BLOCKED_STAND", errors=True)
    elif name.startswith("early."):
        check_record(value.get("blocked_recovery"), "blocked_recovery", "BLOCKED_STAND", errors=True)
    elif name == "test_kill":
        check_record(value.get("tampered_recovery"), "tampered_recovery", "BLOCKED_STAND", errors=True)
        check_record(value.get("missing_recovery"), "missing_recovery", "BLOCKED_STAND", errors=True)
        check_record(value.get("restored_recovery"), "restored_recovery", "PASS", errors=False)


def _coverage(directory: Path, check: Mapping[str, Any]) -> str:
    import base64
    from infra.scripts.infra_quality_coverage import (
        validate,
        baseline_locations,
        mapped_diagnostics,
    )

    root: Any = check.get("source_root")
    require(isinstance(root, str), "coverage source root required")
    package = directory / "input/input.json"
    before = _json(package)
    validate(Path(root), before)
    require(
        package.read_bytes() == (directory / "after/input.json").read_bytes(),
        "coverage sources changed during execution",
    )
    report = _json(directory / "coverage.json")
    require(
        report.get("source_identity") == before["source_identity"]
        and report.get("comparison_base") == before["comparison_base"]
        and report.get("status") == "PASS"
        and report.get("unknown_paths") == [],
        "coverage input/report mismatch",
    )
    expected = {
        e["path"]
        for e in before["changes"]
        if e["path"].startswith("infra/") and e["path"].endswith(".py")
    }
    require(
        isinstance(report.get("files"), dict) and set(report["files"]) == expected,
        "coverage changed-file grid incomplete",
    )
    for name, row in report["files"].items():
        change = next(e for e in before["changes"] if e["path"] == name)
        require(
            row.get("source_sha256") == change.get("current_sha256")
            and row.get("baseline_sha256")
            == before["baseline_inventory"].get(name, {}).get("sha256"),
            "coverage per-file source identity mismatch",
        )
        if change["status"] == "D":
            require(
                row.get("status") == "REMOVED" and row.get("checks") == {},
                "deleted coverage invalid",
            )
            continue
        tools = row.get("checks")
        require(
            isinstance(tools, dict) and set(tools) == {"ruff", "mypy"},
            "coverage tools missing",
        )
        require(
            all(
                isinstance(t.get("command"), list)
                and t["command"]
                and type(t.get("exit")) is int
                for t in tools.values()
            ),
            "coverage command/exit missing",
        )
        if row.get("status") == "PASS":
            require(
                all(
                    zero(t["exit"]) and t.get("diagnostics") == []
                    for t in tools.values()
                ),
                "coverage diagnostics not passed",
            )
        else:
            require(
                row.get("status") == "FAIL_BASELINE"
                and row.get("baseline") == "MATCHED",
                "coverage failure is not verified baseline",
            )
            previous = row.get("baseline_checks", {})
            require(
                set(previous) == set(tools) and name in before["baseline_sources"],
                "baseline checks/source missing",
            )
            old = base64.b64decode(before["baseline_sources"][name], validate=True)
            current = (Path(root) / name).read_bytes()
            mapping = baseline_locations(old, current)
            for tool, observation in tools.items():
                baseline = previous[tool]
                mapped = mapped_diagnostics(
                    tool, observation["diagnostics"], name, mapping
                )
                require(
                    isinstance(baseline.get("command"), list)
                    and baseline["command"]
                    and observation["exit"] == baseline.get("exit")
                    and mapped is not None
                    and mapped == baseline.get("diagnostics")
                    and observation.get("stderr") == baseline.get("stderr"),
                    "baseline diagnostics do not match",
                )
    return report["comparison_base"]


def _infra_gate(
    payload: dict[str, Any],
    path: Path,
    check: Mapping[str, Any],
    source: Mapping[str, Any],
) -> None:
    from infra.scripts.infra_test_selection import required_gates
    from infra.scripts.gate_source_proof import verify, SNAPSHOT_FIELDS

    proof = verify(path.parent)
    mandatory = {
        "quality/result.json",
        "unittest/result.json",
        "unittest/manifest.json",
        "pytest/result.json",
        "pytest/manifest.json",
        "coverage/result.json",
        "coverage/coverage.json",
        "coverage/input/input.json",
        "coverage/after/input.json",
        "recovery/result.json",
        "delivery/selection.json",
    }
    _, declared_selection = artifact(check, "selection")
    require(
        Path(check["artifacts"]["selection"]["path"]).absolute()
        == (path.parent / "delivery/selection.json").absolute(),
        "full gate selection path mismatch",
    )
    if "test-payments-gate" in declared_selection.get("required_gates", []):
        mandatory.add("delivery/payments/result.json")
    contexts = list((path.parent / "payments-context").glob("run-*/context.json"))
    require(len(contexts) == 1, "source proof context missing or ambiguous")
    mandatory.add(str(contexts[0].relative_to(path.parent)))
    require(
        set(proof["artifacts"]) == mandatory,
        "source proof artifact grid incomplete or unexpected",
    )
    snapshot_reference = check.get("source_snapshot")
    if snapshot_reference is not None:
        require(
            isinstance(snapshot_reference, dict)
            and isinstance(snapshot_reference.get("path"), str)
            and sha(snapshot_reference.get("sha256")),
            "parent source snapshot reference malformed",
        )
        snapshot_path = Path(snapshot_reference["path"])
        parent_snapshot = _json(snapshot_path)
        require(
            hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
            == snapshot_reference["sha256"]
            and all(
                parent_snapshot.get(k) == proof["source_before"].get(k)
                for k in SNAPSHOT_FIELDS
            ),
            "source proof differs from parent snapshot",
        )

    phases = {
        "quality",
        "unittest",
        "pytest",
        "coverage",
        "payments_context",
        "recovery",
        "delivery",
    }
    require(
        payload.get("status") == "PASS"
        and isinstance(payload.get("phases"), dict)
        and set(payload["phases"]) == phases
        and all(zero(v) for v in payload["phases"].values()),
        "full infrastructure phase grid invalid",
    )
    for name in ("pytest", "unittest"):
        leaf_path = path.parent / name / "result.json"
        _infra_leaf(_json(leaf_path), leaf_path)
    quality = _json(path.parent / "quality/result.json")
    checks: Any = quality.get("checks")
    require(
        quality.get("status") == "PASS"
        and isinstance(checks, list)
        and len(checks) == 3
        and {c.get("name") for c in checks} == {"ruff", "mypy", "actionlint"}
        and all(
            c.get("status") == "PASS" and zero(c.get("exit")) and c.get("command")
            for c in checks
        ),
        "quality execution incomplete",
    )
    coverage_wrapper = _json(path.parent / "coverage/result.json")
    require(
        coverage_wrapper.get("status") == "PASS"
        and zero(coverage_wrapper.get("exit"))
        and coverage_wrapper.get("sources_unchanged") is True
        and coverage_wrapper.get("command"),
        "coverage execution incomplete",
    )
    base = _coverage(path.parent / "coverage", check)
    _, selection = artifact(check, "selection")
    require(
        base == selection.get("comparison_base")
        and selection.get("required_gates")
        == required_gates(selection["changed_paths"]),
        "coverage/selection/base mismatch",
    )
    selected: Any = selection.get("results")
    require(isinstance(selected, list), "delivery results missing")
    expected_payments = "test-payments-gate" in selection["required_gates"]
    require(
        len(selected) == int(expected_payments)
        and all(
            r.get("gate") == "test-payments-gate"
            and zero(r.get("exit"))
            and r.get("status") == "PASS"
            and r.get("command")
            for r in selected
        ),
        "required downstream gate missing",
    )
    if expected_payments:
        payments_path = path.parent / "delivery/payments/result.json"
        _payments(
            _json(payments_path),
            payments_path,
            {},
            {"producer_sha256": selection["source_identity"]["sha256"]},
        )
    recovery = _json(path.parent / "recovery/result.json")
    require(
        recovery.get("status") == "PASS"
        and zero(recovery.get("exit"))
        and recovery.get("command"),
        "recovery scenario execution incomplete",
    )
    context_directory = path.parent / "payments-context"
    contexts = list(context_directory.glob("run-*/context.json"))
    require(len(contexts) == 1, "Payments context receipt missing or ambiguous")
    context = _json(contexts[0])
    require(
        context.get("status") == "PASS" and context.get("cleanup") == "PASS"
        and context.get("source_identity") == selection.get("source_identity")
        and context.get("source_identity") == {"sha256": source.get("producer_sha256")},
        "Payments context/cleanup incomplete",
    )
    _recovery_case(
        recovery.get("ordinary"),
        "ordinary",
        expected_status="BLOCKED_STAND",
        expected_primary_exit=23,
        expected_cleanup="BLOCKED_STAND",
    )
    _recovery_case(
        recovery.get("test_kill"),
        "test_kill",
        expected_status="BLOCKED_STAND",
        expected_primary_exit=None,
        expected_cleanup="NOT_RUN",
        abrupt=True,
        require_tree=False,
    )
    require(recovery["test_kill"].get("test_kill") is True, "test-kill identity missing")
    early = recovery.get("early")
    matrix_value = early.get("matrix") if isinstance(early, dict) else None
    require(
        isinstance(early, dict)
        and early.get("status") == "PASS"
        and isinstance(matrix_value, dict)
        and set(matrix_value) == {"success", "blocked", "tamper"},
        "recovery early matrix missing or unknown",
    )
    matrix = cast(dict[str, Any], matrix_value)
    _recovery_case(
        matrix["success"],
        "early.success",
        expected_status="FAIL",
        expected_primary_exit=137,
        expected_cleanup="PASS",
        early_success=True,
    )
    for case in ("blocked", "tamper"):
        _recovery_case(
            matrix[case],
            f"early.{case}",
            expected_status="BLOCKED_STAND",
            expected_primary_exit=137,
            expected_cleanup="BLOCKED_STAND",
        )


def _infrastructure(
    payload: dict[str, Any],
    path: Path,
    check: Mapping[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    if "mode" in payload:
        _infra_leaf(payload, path)
        execution = {
            "kind": "docker",
            "mode": payload["mode"],
            "image": payload["image"],
        }
    else:
        full_check = dict(check)
        if "snapshot" in source:
            full_check["source_snapshot"] = source["snapshot"]
        _infra_gate(payload, path, full_check, source)
        execution = {"kind": "docker", "command": "make agent-infra-test"}
    _, selection = artifact(check, "selection")
    require(
        selection.get("version") == 1
        and selection.get("status") == "PASS"
        and selection.get("sources_unchanged") is True
        and sha(selection.get("comparison_base"), 40),
        "selection/source proof incomplete",
    )
    _source_hash(selection.get("source_identity"), source)
    return {
        "status": payload["status"],
        "run_id": payload.get("run_id"),
        "comparison_base": selection["comparison_base"],
        "source_identity": selection["source_identity"],
        "execution": execution,
    }


def _payments(
    payload: dict[str, Any],
    path: Path,
    check: Mapping[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    from infra.scripts.payments_gate_evidence import validate_report

    require(
        payload.get("scope") == "issues-18-20"
        and payload.get("run_id")
        and payload.get("status") == "PASS",
        "full Payments receipt required",
    )
    require(not validate_report(payload), "Payments report contract invalid")
    require(
        payload["phases"].get("cleanup", {}).get("status") == "PASS"
        and payload["phases"]["cleanup"].get("errors") == [],
        "Payments cleanup proof missing",
    )
    _source_hash(payload.get("source_identity"), source)
    return {
        "status": payload["status"],
        "run_id": payload["run_id"],
        "source_identity": payload["source_identity"],
        "limits": payload.get("limits", {}),
        "execution": {"kind": "docker", "command": "make test-payments-gate"},
    }


def _legacy(
    payload: dict[str, Any],
    path: Path,
    check: Mapping[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    from infra.scripts.attempt_identity import status
    from infra.scripts.legacy_phase_contract import validate_legacy_phases

    require(isinstance(check.get("attempt"), str), "legacy attempt required")
    directory = Path(check["attempt"]).absolute()
    require(
        path.absolute() == directory / "qualification.json",
        "qualification path differs from attempt",
    )
    view = status(directory)
    require(
        view["binding"] == "PASS",
        "legacy source does not support new identity contract",
    )
    require(
        payload.get("source_commit") == source.get("source_commit")
        and sha(source.get("source_commit"), 40)
        and payload.get("source_tree") == source.get("source_tree")
        and sha(source.get("source_tree"), 40),
        "legacy committed source mismatch",
    )
    expected = check.get("expected_product")
    if expected is None:
        require(
            payload.get("status") == "PASS"
            and payload.get("tests") == "PASS"
            and zero(payload.get("exit"))
            and zero(payload.get("primary_exit"))
            and payload.get("cleanup") == "PASS"
            and payload.get("cleanup_errors") == [],
            "legacy qualification/cleanup incomplete",
        )
        manifest_path, manifest = artifact(check, "manifest")
        expected_path = (
            directory / "frozen/.dev/test-artifacts/stand/results/manifest.json"
        )
        binding = view["artifacts"].get(
            "frozen/.dev/test-artifacts/stand/results/manifest.json", {}
        )
        require(
            manifest_path.absolute() == expected_path
            and binding.get("sha256")
            == hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "legacy manifest is not bound to attempt",
        )
        require(
            not validate_legacy_phases(manifest), "legacy original133/399 proof invalid"
        )
        operation = "legacy-stand-qualify"
    else:
        require(
            isinstance(expected, dict)
            and expected.get("status") in ("FAIL", "BLOCKED_STAND")
            and positive(expected.get("primary_exit"))
            and all(payload.get(k) == v for k, v in expected.items()),
            "expected product failure mismatch",
        )
        _, scenario = artifact(check, "scenario")
        case = check.get("scenario_case")
        if case is not None:
            cases: Any = scenario.get("matrix")
            require(
                case in ("success", "blocked", "tamper")
                and scenario.get("status") == "PASS"
                and isinstance(cases, dict)
                and set(cases) == {"success", "blocked", "tamper"}
                and all(
                    isinstance(value, dict) and value.get("status") == "PASS"
                    for value in cases.values()
                ),
                "early scenario matrix missing or unknown case",
            )
            scenario = cases[case]
            require(
                scenario.get("mode") == case and scenario.get("early_failure") is True,
                "early case identity mismatch",
            )
        require(
            scenario.get("status") == "PASS"
            and scenario.get("initial") == payload
            and scenario.get("source_commit") == payload["source_commit"]
            and scenario.get("source_tree") == payload["source_tree"]
            and scenario.get("attempt_id") == view["attempt_id"],
            "negative scenario evidence missing",
        )
        if case == "success":
            require(
                payload.get("status") == "FAIL"
                and payload.get("cleanup") == "PASS"
                and payload.get("cleanup_errors") == []
                and payload.get("primary_exit") == 137
                and expected.get("cleanup") == "PASS",
                "successful early cleanup proof incomplete",
            )
        references = scenario.get("recovery_artifacts", {})
        seen_records: set[str] = set()
        for name in () if case == "success" else ("recovery", "repeat"):
            ref = references.get(name, {})
            relative = ref.get("path", "")
            require(
                isinstance(relative, str)
                and Path(relative).parent == Path(".")
                and relative.startswith("recovery-")
                and relative.endswith(".json")
                and relative not in seen_records,
                "recovery artifact missing, unsafe or repeated",
            )
            seen_records.add(relative)
            record_path = directory / relative
            bound = view["artifacts"].get(relative, {})
            digest = hashlib.sha256(record_path.read_bytes()).hexdigest()
            require(
                ref.get("sha256") == digest
                and bound.get("sha256") == digest
                and bound.get("attempt_id") == view["attempt_id"]
                and bound.get("source_tree") == payload["source_tree"]
                and bound.get("stage") == "recovery",
                "recovery artifact belongs to another attempt",
            )
            recovered = scenario.get(name, {})
            require(
                recovered == _json(record_path),
                "recovery scenario differs from bound receipt",
            )
            require(
                recovered.get("status") == "PASS"
                and recovered.get("operation") == "cleanup-only"
                and recovered.get("errors") == []
                and recovered.get("original_verdict_unchanged") is True
                and recovered.get("source_commit") == payload["source_commit"],
                "negative scenario recovery incomplete",
            )
        operation = "legacy-recovery-scenario"
    return {
        "status": payload["status"],
        "attempt_id": view["attempt_id"],
        "source_commit": payload["source_commit"],
        "source_tree": payload["source_tree"],
        "source_identity": {
            "source_commit": payload["source_commit"],
            "source_tree": payload["source_tree"],
        },
        "execution": {"kind": "committed", "command": operation},
    }


def accept_receipts(
    requirements: Mapping[str, Any],
    receipt_paths: list[Path],
) -> dict[str, Any]:
    """Validate the pinned parent requirements against each bounded producer seam."""
    reports: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        require(
            type(requirements.get("version")) is int and requirements["version"] == 1,
            "requirements version invalid",
        )
        source: Any = requirements.get("source")
        require(isinstance(source, dict) and source, "source requirement missing")
        checks: Any = requirements.get("checks")
        require(
            isinstance(checks, list)
            and checks
            and all(isinstance(x, dict) for x in checks),
            "required checks missing",
        )
        ids = [c.get("id") for c in checks]
        require(
            all(isinstance(i, str) and i for i in ids) and len(set(ids)) == len(ids),
            "required check IDs missing or duplicate",
        )
        require(
            len([c for c in checks if c.get("applicable") is not False])
            == len(receipt_paths)
            and len({p.resolve() for p in receipt_paths}) == len(receipt_paths),
            "receipt count mismatch or duplicate",
        )
        pending_receipts = iter(receipt_paths)
        for check in checks:
            path = (
                next(pending_receipts)
                if check.get("applicable") is not False
                else Path(".")
            )
            try:
                if check.get("applicable") is False:
                    require(
                        isinstance(check.get("scope_reason"), str)
                        and check["scope_reason"].strip(),
                        "scope exclusion reason missing",
                    )
                    reports.append(
                        {
                            "id": check["id"],
                            "status": "NOT_APPLICABLE",
                            "reason": check["scope_reason"],
                        }
                    )
                    continue
                payload = _json(path)
                if "path" in check:
                    require(
                        Path(check["path"]).absolute() == path.absolute(),
                        "parent check receipt path mismatch",
                    )
                producer = check.get("producer")
                adapters = {
                    "harness": _harness,
                    "infrastructure": _infrastructure,
                    "payments": _payments,
                    "legacy": _legacy,
                }
                require(producer in adapters, "unknown producer")
                normalized = adapters[producer](payload, path, check, source)
                execution = check.get("execution")
                require(
                    isinstance(execution, dict)
                    and execution
                    and execution.get("kind")
                    and all(
                        normalized["execution"].get(k) == v
                        for k, v in execution.items()
                    ),
                    "execution requirement missing or mismatched",
                )
                reports.append(
                    {
                        "id": check["id"],
                        "producer": producer,
                        "path": str(path),
                        **normalized,
                    }
                )
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                errors.append(f"{check['id']}: {exc}")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append(str(exc))
    return {
        "version": 1,
        "status": "PASS" if not errors else "BLOCKED_STAND",
        "errors": errors,
        "receipts": reports,
        "requirements_sha256": hashlib.sha256(
            json.dumps(dict(requirements), sort_keys=True).encode()
        ).hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("accept",))
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    try:
        result = accept_receipts(_json(args.requirements), args.receipt)
    except (OSError, ValueError, TypeError) as exc:
        result = {"status": "BLOCKED_STAND", "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
