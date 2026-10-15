"""Сборщик фактов отчёта Payments gate.

Модуль намеренно не выполняет проверки и не пишет файлы. Runner передаёт ему
наблюдённые факты, а этот объект собирает стабильный JSON-контракт.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any


MANDATORY_PHASES = (
    "preflight",
    "migrations",
    "identity",
    "http_control",
    "payments",
    "http",
    "physical_replica",
    "lifecycle_pytest",
    "lifecycle_unittest",
    "json_validation",
    "cleanup",
)
PHASES = MANDATORY_PHASES


def _error(value: Any) -> dict[str, str]:
    if isinstance(value, BaseException):
        return {"type": type(value).__name__, "reason": str(value)}
    if isinstance(value, dict):
        return copy.deepcopy(value)
    return {"type": type(value).__name__, "reason": str(value)}


def _manifest(value: Any) -> Any:
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    result = copy.deepcopy(value)
    reports = result.get("reports")
    if isinstance(reports, list):
        result.setdefault(
            "skipped",
            sum(
                item.get("outcome") == "skipped"
                for item in reports
                if isinstance(item, dict)
            ),
        )
    return result


def _http_from_manifest(manifest: Any) -> dict[str, Any] | None:
    if not isinstance(manifest, dict):
        return None
    summary: dict[str, Any] = {"conflicts": {}}
    for item in manifest.get("reports", []):
        if not isinstance(item, dict) or item.get("when") != "call":
            continue
        try:
            evidence = copy.deepcopy(
                dict(item.get("user_properties", [])).get("http_evidence")
            )
        except (TypeError, ValueError):
            evidence = None
        if isinstance(evidence, dict):
            node = item["nodeid"]
            operation = (
                "attach" if "attach_stale" in node or "[attach]" in node else "detach"
            )
            if evidence.get("status") == 409:
                value = dict(evidence, code=evidence["error_code"])
                for key in ("before", "after"):
                    raw = evidence[key]
                    value[key] = {
                        "version": raw["version"],
                        "business": {k: v for k, v in raw.items() if k != "version"},
                        "outbox_count": evidence["outbox_" + key],
                    }
                summary["conflicts"][operation] = value
            else:
                summary[operation] = evidence
    return summary if len(summary["conflicts"]) or len(summary) > 1 else None


class EvidenceError(ValueError):
    pass


def check_manifest(manifest: Mapping[str, Any]) -> None:
    nodes = manifest.get("collected", [])
    reports = manifest.get("reports", [])
    if not nodes or len(set(nodes)) != len(nodes) or manifest.get("exit") != 0:
        raise EvidenceError("empty, duplicate or failed pytest collection")
    expected = {
        (node, when) for node in nodes for when in ("setup", "call", "teardown")
    }
    observed = [(item["nodeid"], item["when"]) for item in reports]
    if (
        len(observed) != len(expected)
        or set(observed) != expected
        or any(item["outcome"] != "passed" for item in reports)
    ):
        raise EvidenceError("incomplete or skipped pytest phase evidence")


@dataclass(frozen=True)
class FinalReport:
    report: dict[str, Any]
    exit_code: int


class PaymentsReport:
    def __init__(self, run_id: str, scope: str) -> None:
        if scope not in ("stand", "full"):
            raise EvidenceError(f"unknown report scope: {scope}")
        self._finished = False
        self._mode = scope
        emitted_scope = {"stand": "issue-17-http-control", "full": "issues-18-20"}.get(
            scope, scope
        )
        self._report: dict[str, Any] = {
            "run_id": run_id,
            "status": "BLOCKED_STAND",
            "scope": emitted_scope,
            "owned": [],
            "manifest": {},
            "evidence": {},
            "phases": {
                phase: {"status": "NOT_RUN", "reason": "not executed"}
                for phase in PHASES
            },
            "limits": {
                phase: "NOT_RUN"
                for phase in ("PRODUCTION_RLS", "ERP_ACCEPTANCE", "KONG_JWT_RBAC_E2E")
            },
        }

    def record(self, phase: str | None = None, **facts: Any) -> None:
        if self._finished:
            raise RuntimeError("report is already finished")
        metadata = facts.pop("metadata", None)
        if isinstance(metadata, Mapping):
            facts = {**dict(metadata), **facts}
        if phase is None:
            for key, value in facts.items():
                if key == "primary_error":
                    self._report[key] = _error(value)
                elif key in {
                    "owned",
                    "source_identity",
                    "runtime_identity",
                    "target_identity",
                    "uid_gid",
                    "subnet",
                    "images",
                    "docker_endpoint",
                    "stand_status",
                    "phase_diagnostic",
                }:
                    self._report[key] = copy.deepcopy(value)
                else:
                    raise EvidenceError(f"unknown observation metadata: {key}")
            return
        if phase not in PHASES:
            raise ValueError(f"unknown phase: {phase}")
        normalized = copy.deepcopy(facts)
        failure = "primary_error" in normalized
        if failure:
            normalized["primary_error"] = _error(normalized["primary_error"])
            normalized["status"] = (
                "FAIL"
                if normalized["primary_error"].get("type") == "CheckFailure"
                else "BLOCKED_STAND"
            )
            normalized.setdefault(
                "reason", normalized["primary_error"].get("reason", "phase failed")
            )
            self._report["primary_error"] = copy.deepcopy(normalized["primary_error"])
            normalized.pop("primary_error", None)
            for name, item in self._report["phases"].items():
                if name != phase and item.get("status") == "NOT_RUN":
                    item["reason"] = "dependency " + phase + " failed"
        if "manifest" in normalized:
            normalized["manifest"] = _manifest(normalized["manifest"])
            if phase == "http_control" and normalized.get("status") == "PASS":
                check_manifest(normalized["manifest"])
                normalized.setdefault(
                    "control_evidence",
                    {"status": 200, "endpoints": ["attach", "detach"]},
                )
            if phase == "http":
                try:
                    check_manifest(normalized["manifest"])
                except EvidenceError:
                    pass  # Keep incomplete facts; the runner preserves its primary error.
                else:
                    normalized["http_evidence"] = _http_from_manifest(
                        normalized["manifest"]
                    ) or {"conflicts": {}}
            self._report["manifest"][phase] = copy.deepcopy(normalized["manifest"])
        if "tests" in normalized:
            self._report["manifest"][phase] = copy.deepcopy(normalized["tests"])
        if "manifest_path" in normalized:
            self._report["evidence"][phase] = copy.deepcopy(normalized["manifest_path"])
        if phase == "cleanup" and "errors" in normalized:
            normalized["status"] = "BLOCKED_STAND" if normalized["errors"] else "PASS"
        old = self._report["phases"].get(phase, {})
        if failure or (phase == "physical_replica" and old.get("status") != "NOT_RUN"):
            old.update(normalized)
        else:
            old = normalized
        self._report["phases"][phase] = old

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self._report)

    def finish(self) -> FinalReport:
        if self._finished:
            raise RuntimeError("report is already finished")
        report = self._report
        primary = report.get("primary_error")
        core_complete = all(
            report["phases"][phase].get("status") == "PASS"
            for phase in (
                "preflight",
                "migrations",
                "identity",
                "http_control",
                "cleanup",
            )
        )
        success = not primary and report.get("stand_status") == "PASS" and core_complete
        report["status"] = (
            ("PASS" if self._mode == "full" else "PARTIAL")
            if success
            else (
                "FAIL"
                if primary and primary.get("type") == "CheckFailure"
                else "BLOCKED_STAND"
            )
        )
        if self._mode == "full":
            report["phases"]["json_validation"] = {"status": "PASS"}
            errors = validate_report(report)
            report["phases"]["json_validation"] = {
                "status": "FAIL" if errors else "PASS",
                "errors": errors,
            }
            if errors and success:
                success = False
                report["status"] = "FAIL"
        self._finished = True
        return FinalReport(copy.deepcopy(report), 0 if success else 1)


# Contract validator transferred from the legacy CLI adapter.

TEST_PHASES = ("payments", "http", "lifecycle_pytest", "lifecycle_unittest")
PHASE_STATUSES = frozenset(("PASS", "FAIL", "BLOCKED_STAND", "NOT_RUN"))


def _is_nonempty_mapping(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(value)


def _valid_version(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_replica_command(
    command: Mapping[str, Any], label: str, errors: list[str]
) -> None:
    if command.get("status") != 200 or command.get("primary_alias") != "default":
        errors.append(f"replica {label} command lacks status 200/default primary")
    before, after = command.get("state_before"), command.get("state_after")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        errors.append(f"replica {label} command lacks state snapshots")
    else:
        if (
            not _valid_version(before.get("version"))
            or not _valid_version(after.get("version"))
            or after["version"] != before["version"] + 1
        ):
            errors.append(f"replica {label} command version is not incremented by one")
    if (
        not _valid_version(command.get("outbox_before"))
        or not _valid_version(command.get("outbox_after"))
        or command["outbox_after"] != command["outbox_before"] + 1
    ):
        errors.append(f"replica {label} command outbox is not incremented by one")


def _validate_manifest(
    manifest: Mapping[str, Any], phase: str, errors: list[str]
) -> None:
    collected = manifest.get("collected")
    reports = manifest.get("reports")
    if isinstance(reports, list):
        if not isinstance(collected, list) or any(
            not isinstance(node, str) or not node for node in collected
        ):
            errors.append(f"manifest collected must be a nodeid list for phase {phase}")
            collected = []
        if len(collected) != len(set(collected)):
            errors.append(f"duplicate node in collected manifest for phase {phase}")
        seen: set[tuple[str, str]] = set()
        phases_by_node: dict[str, set[str]] = {node: set() for node in collected}
        for item in reports:
            if not isinstance(item, Mapping):
                errors.append(f"manifest report is not an object for phase {phase}")
                continue
            nodeid, when = item.get("nodeid"), item.get("when")
            if nodeid not in phases_by_node or when not in (
                "setup",
                "call",
                "teardown",
            ):
                errors.append(
                    f"manifest report has unexpected node or phase for phase {phase}"
                )
                continue
            key = (nodeid, when)
            if key in seen:
                errors.append(
                    f"duplicate node phase in manifest for phase {phase}: {nodeid}/{when}"
                )
            seen.add(key)
            phases_by_node[nodeid].add(when)
            if item.get("outcome") != "passed":
                errors.append(
                    f"manifest report is not passed for phase {phase}: {nodeid}/{when}"
                )
        for nodeid, observed in phases_by_node.items():
            if observed != {"setup", "call", "teardown"}:
                errors.append(
                    f"manifest node lacks exact setup/call/teardown for phase {phase}: {nodeid}"
                )
    elif not isinstance(reports, int) or isinstance(reports, bool) or reports <= 0:
        errors.append(f"zero or invalid reports for phase {phase}")
    if manifest.get("exit") != 0:
        errors.append(f"manifest exit is not zero for phase {phase}")


def validate_report(report: Mapping[str, Any]) -> list[str]:
    """Return contract violations; an empty list means the report is valid.

    The function deliberately does not mutate the input and reports all found
    violations, allowing the CLI to expose a useful diagnostic in one run.
    """

    errors: list[str] = []
    if not isinstance(report, Mapping):
        return ["report must be a JSON object"]

    if not report.get("run_id"):
        errors.append("missing required field: run_id")
    preflight = (
        report.get("phases", {}).get("preflight")
        if isinstance(report.get("phases"), Mapping)
        else None
    )
    if not report.get("source_identity") and not (
        isinstance(preflight, Mapping) and preflight.get("status") == "BLOCKED_STAND"
    ):
        errors.append("missing required field: source_identity")
    if report.get("status") == "PASS":
        for field in ("manifest", "evidence"):
            if not _is_nonempty_mapping(report.get(field)):
                errors.append(f"missing or empty evidence {field}")

    phases = report.get("phases")
    if not isinstance(phases, Mapping):
        errors.append("missing or invalid phases")
        phases = {}
    present = set(phases)
    for phase in MANDATORY_PHASES:
        if phase not in present:
            errors.append(f"missing phase: {phase}")
    for phase in sorted(present - set(MANDATORY_PHASES)):
        errors.append(f"unexpected phase: {phase}")

    statuses: dict[str, str] = {}
    for phase in MANDATORY_PHASES:
        value = phases.get(phase)
        if not isinstance(value, Mapping):
            errors.append(f"invalid phase result: {phase}")
            continue
        status = value.get("status")
        statuses[phase] = status if isinstance(status, str) else ""
        if status not in PHASE_STATUSES:
            errors.append(f"invalid status for phase {phase}: {status!r}")
        if status == "NOT_RUN" and (
            not isinstance(value.get("reason"), str) or not value.get("reason").strip()
        ):
            errors.append(f"NOT_RUN phase requires reason: {phase}")
        if phase in TEST_PHASES and status == "PASS":
            manifest_path = value.get("manifest_path")
            if not isinstance(manifest_path, str) or not manifest_path.strip():
                errors.append(f"missing manifest_path for phase {phase}")
            tests = value.get("tests")
            embedded_manifest = value.get("manifest")
            if isinstance(embedded_manifest, Mapping):
                tests = embedded_manifest
                for field in ("collected", "reports", "exit", "skipped"):
                    if field not in embedded_manifest:
                        errors.append(
                            f"missing manifest field {field} for phase {phase}"
                        )
                _validate_manifest(embedded_manifest, phase, errors)
            if isinstance(embedded_manifest, Mapping):
                if embedded_manifest.get("skipped") != 0:
                    errors.append(f"skipped tests for phase {phase}")
            elif not isinstance(tests, Mapping):
                errors.append(f"missing tests evidence for phase {phase}")
            else:
                collected = tests.get("collected")
                skipped = tests.get("skipped")
                if (
                    not isinstance(collected, int)
                    or isinstance(collected, bool)
                    or collected <= 0
                ):
                    errors.append(f"zero or invalid collected tests for phase {phase}")
                if (
                    not isinstance(skipped, int)
                    or isinstance(skipped, bool)
                    or skipped != 0
                ):
                    errors.append(f"skipped tests for phase {phase}")
                if phase == "lifecycle_unittest":
                    for field in ("passed", "failures", "errors"):
                        if (
                            not isinstance(tests.get(field), int)
                            or tests.get(field) < 0
                        ):
                            errors.append(
                                f"missing or invalid {field} count for phase {phase}"
                            )
                    if any(tests.get(field) != 0 for field in ("failures", "errors")):
                        errors.append(f"unittest failures or errors for phase {phase}")
                    if tests.get("passed") != collected:
                        errors.append(
                            f"unittest passed count mismatch for phase {phase}"
                        )

    if statuses.get("http_control") == "PASS":
        control = phases["http_control"].get("control_evidence")
        if not isinstance(control, Mapping) or control.get("status") != 200:
            errors.append("http_control evidence must contain positive status 200")
    if statuses.get("http") == "PASS":
        http = phases["http"].get("http_evidence")
        if not isinstance(http, Mapping):
            errors.append("http evidence is missing")
        else:
            for endpoint in ("attach", "detach"):
                if (
                    not isinstance(http.get(endpoint), Mapping)
                    or http[endpoint].get("status") != 200
                ):
                    errors.append(f"http {endpoint} evidence must contain status 200")
            conflicts = http.get("conflicts")
            if not isinstance(conflicts, Mapping):
                errors.append("http conflicts evidence must contain attach and detach")
                conflicts = {}
            for conflict_name in ("attach", "detach"):
                conflict = conflicts.get(conflict_name)
                if not isinstance(conflict, Mapping) or conflict.get("status") != 409:
                    errors.append(
                        f"http {conflict_name} conflict evidence must contain status 409"
                    )
                    continue
                if conflict.get("code") != "CONCURRENT_MODIFICATION":
                    errors.append(
                        f"http {conflict_name} conflict evidence must contain CONCURRENT_MODIFICATION"
                    )
                if not conflict.get("primary_pid") or not conflict.get("writer_pid"):
                    errors.append(
                        f"http {conflict_name} conflict evidence requires primary_pid and writer_pid"
                    )
                elif conflict["primary_pid"] == conflict["writer_pid"]:
                    errors.append(
                        f"http {conflict_name} conflict evidence requires different writer_pid and primary_pid"
                    )
                before, after = conflict.get("before"), conflict.get("after")
                if not isinstance(before, Mapping) or not isinstance(after, Mapping):
                    errors.append(
                        f"http {conflict_name} conflict evidence requires before and after snapshots"
                    )
                else:
                    if after.get("version") != before.get("version", -1) + 1:
                        errors.append(
                            f"http {conflict_name} conflict evidence requires version increment by one"
                        )
                    if after.get("business") != before.get("business"):
                        errors.append(
                            f"http {conflict_name} conflict evidence changed business state"
                        )
                    if after.get("outbox_count") != before.get("outbox_count"):
                        errors.append(
                            f"http {conflict_name} conflict evidence changed outbox count"
                        )
    if statuses.get("physical_replica") == "PASS":
        replica = phases["physical_replica"].get("replica_evidence")
        runs = replica.get("runs") if isinstance(replica, Mapping) else None
        if not isinstance(runs, list) or len(runs) != 2:
            errors.append("replica evidence requires two independent runs")
        else:
            for index, run in enumerate(runs):
                if not isinstance(run, Mapping) or run.get("before") == run.get(
                    "after"
                ):
                    errors.append(f"replica run {index} lacks distinct lag snapshots")
                if isinstance(run, Mapping):
                    lag = run.get("lag")
                    if isinstance(lag, Mapping):
                        primary_lsn = lag.get("primary_lsn")
                        replica_lsn = lag.get("replica_lsn", lag.get("replay_lsn"))
                        if (
                            not primary_lsn
                            or not replica_lsn
                            or primary_lsn == replica_lsn
                        ):
                            errors.append(
                                f"replica run {index} lacks distinct primary/replica LSNs"
                            )
                        if (
                            not _valid_version(lag.get("lag_bytes"))
                            or lag.get("lag_bytes") <= 0
                            or lag.get("replay_paused") is not True
                            or lag.get("in_recovery") is not True
                        ):
                            errors.append(
                                f"replica run {index} lacks paused positive WAL lag"
                            )
                    commands = run.get("commands")
                    if isinstance(commands, list):
                        names = {
                            command.get("name")
                            for command in commands
                            if isinstance(command, Mapping)
                        }
                        if not {"attach", "detach"}.issubset(names):
                            errors.append(
                                f"replica run {index} lacks attach/detach commands"
                            )
                        for command in commands:
                            if isinstance(command, Mapping) and command.get("name") in {
                                "attach",
                                "detach",
                            }:
                                _validate_replica_command(
                                    command, f"run {index} {command['name']}", errors
                                )
                    else:
                        errors.append(f"replica run {index} lacks commands")
                    if ("state_before" in run) != ("state_after" in run):
                        errors.append(
                            f"replica run {index} requires state_before and state_after together"
                        )
        if (
            not isinstance(replica, Mapping)
            or replica.get("replay_resumed") is not True
        ):
            errors.append("replica evidence requires replay_resumed=true")
        if not isinstance(replica, Mapping) or replica.get("synchronized") is not True:
            errors.append("replica evidence requires synchronized=true")
        if isinstance(replica, Mapping):
            for index, run in enumerate(replica.get("runs", [])):
                if isinstance(run, Mapping) and run.get("replay_resumed") is not True:
                    errors.append(f"replica run {index} requires replay_resumed=true")
                recovered = run.get("recovered") if isinstance(run, Mapping) else None
                recovered_commands = (
                    recovered.get("commands")
                    if isinstance(recovered, Mapping)
                    else None
                )
                if not isinstance(recovered_commands, list):
                    errors.append(f"replica run {index} lacks recovered commands")
                else:
                    for command in recovered_commands:
                        if isinstance(command, Mapping) and command.get("name") in {
                            "attach",
                            "detach",
                        }:
                            _validate_replica_command(
                                command,
                                f"run {index} recovered {command['name']}",
                                errors,
                            )

    cleanup = phases.get("cleanup")
    if (
        report.get("status") == "PASS"
        and isinstance(cleanup, Mapping)
        and cleanup.get("errors")
    ):
        errors.append("cleanup contains errors")

    overall = report.get("status")
    if overall not in PHASE_STATUSES:
        errors.append(f"invalid overall status: {overall!r}")
    if overall == "PASS":
        failed = [phase for phase in MANDATORY_PHASES if statuses.get(phase) != "PASS"]
        if failed:
            errors.append(
                "overall PASS requires all mandatory phases PASS: " + ", ".join(failed)
            )
        if isinstance(cleanup, Mapping) and cleanup.get("errors"):
            errors.append("overall PASS is forbidden when cleanup contains errors")
        owned = report.get("owned")
        run_id = report.get("run_id")
        if not isinstance(owned, list) or not owned:
            errors.append("overall PASS requires non-empty owned resources")
        else:
            for resource in owned:
                labels = (
                    resource.get("labels") if isinstance(resource, Mapping) else None
                )
                if (
                    not isinstance(resource, Mapping)
                    or resource.get("removed") is not True
                ):
                    errors.append("overall PASS requires every owned resource removed")
                if (
                    not isinstance(labels, Mapping)
                    or labels.get("complect.payments_run") != run_id
                ):
                    errors.append("owned resource label does not match run_id")
        if report.get("primary_error"):
            errors.append("overall PASS cannot contain primary_error")
        runtime = report.get("runtime_identity")
        if isinstance(runtime, Mapping):
            if (
                runtime.get("principal") != "complect_app_local"
                or runtime.get("session_user") != "complect_app_local"
            ):
                errors.append(
                    "runtime identity principal/session_user is not complect_app_local"
                )
            privileges = runtime.get("privileges")
            if (
                not isinstance(privileges, list)
                or len(privileges) != 6
                or any(value is not False for value in privileges)
            ):
                errors.append("runtime identity privileges are not six false flags")
            target = report.get("target_identity")
            if not isinstance(target, Mapping) or runtime.get(
                "database_oid"
            ) != target.get("database_oid"):
                errors.append(
                    "runtime identity database does not match target database_oid"
                )
        else:
            errors.append("overall PASS requires runtime_identity")
    if overall == "PASS" and report.get("validation_status") not in (None, "PASS"):
        errors.append("overall PASS conflicts with validation status")
    return errors
