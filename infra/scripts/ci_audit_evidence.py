"""Read-only verification of downloaded CI evidence, independent of GitHub access."""
from __future__ import annotations

import argparse
import hashlib
import os
import json
import re
import stat
import sys
from pathlib import Path
from typing import Any

from infra.scripts.evidence_bundle import EvidenceBundleError, read_exact_chunk

IDENTITY = ("repository", "run_id", "run_attempt", "job_id", "head_sha", "base_sha", "checkout_sha", "tree_sha")
ACTIVE = {"queued", "in_progress", "waiting", "pending", "requested"}
CONCLUSIONS = {"success", "failure", "cancelled", "timed_out", "neutral", "skipped", "action_required", "stale", "startup_failure"}
MAX_JSON = 1024 * 1024
PRIORITY = ("FAIL", "BLOCKED_EVIDENCE", "PENDING", "PASS")


def _hex(value: object, length: int) -> bool:
    return isinstance(value, str) and re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is not None


def _name(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value) is not None


def _identity_value(key: str, value: Any) -> bool:
    if key in ("run_id", "run_attempt", "job_id"):
        return type(value) is int and value > 0
    if key == "repository":
        return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) is not None
    return _hex(value, 40)


def _selection(requirements: Any) -> list[dict[str, Any]]:
    if not isinstance(requirements, dict) or requirements.get("format") != "complect-ci-audit-requirements-v1":
        raise EvidenceBundleError("REQUIREMENTS_INVALID")
    jobs = requirements.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise EvidenceBundleError("REQUIRED_JOBS_EMPTY")
    names = set()
    identities = set()
    for job in jobs:
        if not isinstance(job, dict) or not _name(job.get("name")):
            raise EvidenceBundleError("REQUIRED_JOB_INVALID")
        if not all(_identity_value(key, job.get(key)) for key in IDENTITY):
            raise EvidenceBundleError("REQUIRED_IDENTITY_INVALID")
        artifacts = job.get("artifacts")
        if (not isinstance(artifacts, list) or not artifacts or not all(_name(n) for n in artifacts)
                or len(set(artifacts)) != len(artifacts) or "receipt" not in artifacts):
            raise EvidenceBundleError("REQUIRED_ARTIFACTS_INVALID")
        identity = tuple(job[k] for k in ("repository", "run_id", "run_attempt", "job_id"))
        if job["name"] in names or identity in identities:
            raise EvidenceBundleError("REQUIRED_JOB_DUPLICATE")
        names.add(job["name"])
        identities.add(identity)
    return jobs


def _json_object(data: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    value = json.loads(data, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


class _JobAudit:
    def __init__(self, root: Path, *, available: dict[str, Any] | None = None, unreadable: set[str] | None = None):
        self.root = root.resolve()
        self.available = available
        self.unreadable = unreadable or set()
        self.failed: list[str] = []
        self.missing: list[str] = []
        self.checked: dict[str, dict[str, Any]] = {}

    def identity(self, value: dict[str, Any], expected: dict[str, Any], label: str) -> None:
        for key in IDENTITY:
            if key not in value:
                self.missing.append(f"{label}.{key}:MISSING")
            elif not _identity_value(key, value[key]) or value[key] != expected[key]:
                self.failed.append(f"{label}.{key}:IDENTITY_MISMATCH")

    def read(self, entry: Any, label: str, *, as_json: bool = True) -> Any:
        if entry is None:
            self.missing.append(f"{label}:MISSING")
            return None
        if not isinstance(entry, dict):
            self.failed.append(f"{label}:DESCRIPTOR_INVALID")
            return None
        name, digest = entry.get("path"), entry.get("sha256")
        if name is None or digest is None:
            self.missing.append(f"{label}:DESCRIPTOR_INCOMPLETE")
            return None
        if (not isinstance(name, str) or not name or Path(name).is_absolute()
                or ".." in Path(name).parts or not _hex(digest, 64)):
            self.failed.append(f"{label}:DESCRIPTOR_INVALID")
            return None
        if self.available is not None and label not in self.available:
            self.missing.append(label + (":UNREADABLE" if label in self.unreadable else ":FILE_MISSING"))
            return None
        path = self.root / name
        try:
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise EvidenceBundleError("SYMLINK_FORBIDDEN")
            path = path.resolve()
            if not path.is_relative_to(self.root) or not stat.S_ISREG(path.stat().st_mode):
                raise EvidenceBundleError("REGULAR_FILE_REQUIRED")
            size = path.stat().st_size
            if as_json and size > MAX_JSON:
                raise EvidenceBundleError("JSON_TOO_LARGE")
            # Authenticate the ENTIRE stream, including log bytes beyond the
            # bounded checkout prefix returned by the evidence-bundle reader.
            data = read_exact_chunk(path, 0, min(size, MAX_JSON), digest)
            result = _json_object(data) if as_json else data
            self.checked[label] = {"path": str(path.relative_to(self.root)), "sha256": digest}
            return result
        except FileNotFoundError:
            self.missing.append(f"{label}:FILE_MISSING")
        except PermissionError:
            self.missing.append(f"{label}:UNREADABLE")
        except (OSError, ValueError, UnicodeError):
            self.failed.append(f"{label}:INVALID_OR_HASH_MISMATCH")
        return None

    def log(self, entry: Any, expected: dict[str, Any]) -> None:
        data = self.read(entry, "log", as_json=False)
        if data is None:
            return
        line = entry.get("checkout_line")
        lines = data.decode("utf-8", errors="replace").splitlines()
        if type(line) is not int or line < 2:
            self.failed.append("log:CHECKOUT_LINE_INVALID")
        elif line > len(lines):
            self.missing.append("log:CHECKOUT_OUTSIDE_PREFIX")
        elif (not re.search(r"\[command\](?:\S*/)?git log -1 --format=%H$", lines[line - 2])
              or not lines[line - 1].split()
              or lines[line - 1].split()[-1] != expected["checkout_sha"]):
            self.failed.append("log:CHECKOUT_MISMATCH")

    def proof(self, proof: dict[str, Any], expected: dict[str, Any], entries: dict[str, Any]) -> None:
        self.identity(proof, expected, "proof")
        if proof.get("format") != "complect-ci-source-proof-v1" or proof.get("status") != "PASS":
            self.failed.append("proof:FORMAT_OR_STATUS_INVALID")
        snapshots = []
        for field in ("source_before", "source_after"):
            snapshot = self.read(proof.get(field), field)
            if snapshot is None:
                continue
            snapshots.append(field)
            if snapshot.get("head") != expected["checkout_sha"] or snapshot.get("tree") != expected["tree_sha"]:
                self.failed.append(f"{field}:IDENTITY_MISMATCH")
            files = snapshot.get("files")
            if (not isinstance(files, dict) or not files
                    or any(not isinstance(k, str) or not k or Path(k).is_absolute() or ".." in Path(k).parts
                           or not isinstance(v, dict) or not _hex(v.get("sha256"), 64) for k, v in files.items())):
                self.failed.append(f"{field}:FILES_INVALID")
        if len(snapshots) == 2:
            before, after = (self.checked[field] for field in snapshots)
            if before["path"] == after["path"]:
                self.failed.append("proof:SNAPSHOT_ALIAS")
            if before["sha256"] != after["sha256"]:
                self.failed.append("proof:SOURCE_DRIFT")
        bound = proof.get("artifacts")
        if not isinstance(bound, dict):
            self.missing.append("proof.artifacts:MISSING")
            return
        for name in expected["artifacts"]:
            if name not in bound:
                self.missing.append(f"proof.artifacts.{name}:MISSING")
            elif name in entries and bound[name] != entries[name]:
                self.failed.append(f"proof.artifacts.{name}:BINDING_MISMATCH")

    def job(self, job: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
        self.identity(job, expected, "job")
        self.log(job.get("log"), expected)
        entries = job.get("artifacts")
        if not isinstance(entries, dict):
            self.missing.append("artifacts:MISSING")
            entries = {}
        artifacts = {name: self.read(entries.get(name), f"artifacts.{name}", as_json=name == "receipt")
                     for name in expected["artifacts"]}
        proof = self.read(job.get("source_proof"), "proof")
        if proof is not None:
            self.proof(proof, expected, entries)
        paths = [v["path"] for k, v in self.checked.items() if k != "source_after"]
        if len(paths) != len(set(paths)):
            self.failed.append("job:ARTIFACT_ALIAS")
        receipt = artifacts.get("receipt")
        if receipt is not None:
            for field, original in (("status", "verdict"), ("exit", "exit_code")):
                if field not in receipt or original not in job:
                    self.missing.append(f"receipt.{field}:MISSING")
                elif (receipt[field] != job[original] or (field == "exit" and
                      (type(receipt[field]) is not int or type(job[original]) is not int))):
                    self.failed.append(f"receipt.{field}:MISMATCH")
        state, conclusion = job.get("status"), job.get("conclusion")
        if not isinstance(state, str) or state not in ACTIVE | {"completed"}:
            self.failed.append("job:STATE_INVALID")
        if state == "completed":
            if conclusion is None:
                self.missing.append("job.conclusion:MISSING")
            elif not isinstance(conclusion, str) or conclusion not in CONCLUSIONS:
                self.failed.append("job:CONCLUSION_INVALID")
        elif conclusion is not None:
            self.failed.append("job:ACTIVE_CONCLUSION_INVALID")
        if self.failed:
            status = "FAIL"
        elif isinstance(state, str) and state in ACTIVE:
            status = "PENDING"
        elif self.missing:
            status = "BLOCKED_EVIDENCE"
        elif conclusion != "success" or job.get("verdict") != "PASS" or job.get("exit_code") != 0:
            status = "FAIL"
            self.failed.append("job:CI_NOT_SUCCESSFUL")
        else:
            status = "PASS"
        return {"name": expected["name"], "status": status,
                "identity": {k: job.get(k) for k in IDENTITY},
                "original": {k: job.get(k) for k in ("status", "conclusion", "verdict", "exit_code")},
                "errors": sorted(set(self.failed)), "missing": sorted(set(self.missing)),
                "checked": self.checked}


def verify_ci_audit(requirements: Any, report: Any, root: Path, *, continuation: Any = None) -> dict[str, Any]:
    """Verify one package against an independently declared mandatory selection.

    This is a local integrity check, not remote attestation. No input is changed,
    missing historical metadata is never inferred, and no network is accessed.
    """
    if continuation is not None:
        return _resume_ci_audit(requirements, report, root, continuation)
    result: dict[str, Any] = {"format": "complect-ci-audit-result-v1", "status": "FAIL", "errors": [], "jobs": []}
    try:
        expected = _selection(requirements)
    except EvidenceBundleError as exc:
        result["errors"] = [str(exc)]
        return result
    if not isinstance(report, dict):
        result["errors"] = ["REPORT_INVALID"]
        return result
    if report.get("format") != "complect-ci-audit-report-v1" or "jobs" not in report:
        result.update(status="BLOCKED_EVIDENCE", errors=["REPORT_FORMAT_OR_JOBS_MISSING"])
        return result
    if not isinstance(report["jobs"], list):
        result["errors"] = ["JOBS_INVALID"]
        return result
    observed = {}
    names = {job["name"] for job in expected}
    for job in report["jobs"]:
        if (not isinstance(job, dict) or not _name(job.get("name"))
                or job["name"] not in names or job["name"] in observed):
            result["errors"] = ["JOB_INVALID_DUPLICATE_OR_UNEXPECTED"]
            return result
        observed[job["name"]] = job
    for requirement in expected:
        if requirement["name"] not in observed:
            result["jobs"].append({"name": requirement["name"], "status": "BLOCKED_EVIDENCE",
                                   "errors": [], "missing": ["JOB_MISSING"], "original": {}})
        else:
            result["jobs"].append(_JobAudit(root).job(observed[requirement["name"]], requirement))
    statuses = {j["status"] for j in result["jobs"]}
    result["status"] = next(s for s in PRIORITY if s in statuses)
    return result



def _continuation_requirements(requirements: Any) -> dict[str, Any]:
    return {"format": "complect-ci-audit-requirements-v1", "jobs": [
        {key: list(job[key]) if key == "artifacts" else job[key]
         for key in ("name", *IDENTITY, "artifacts")} for job in _selection(requirements)]}


def _continuation_report(report: dict[str, Any], requirements: Any) -> dict[str, Any]:
    jobs = []
    expected = {j["name"]: j for j in _selection(requirements)}
    for raw in report["jobs"]:
        job = {key: raw[key] for key in ("name", *IDENTITY, "status", "conclusion", "verdict", "exit_code") if key in raw}
        for key, value in job.items():
            if value is not None and type(value) not in (str, int):
                raise EvidenceBundleError("CONTINUATION_METADATA_INVALID")
            if key == "verdict" and value is not None and not _name(value):
                raise EvidenceBundleError("CONTINUATION_METADATA_INVALID")
        for key in ("log", "source_proof"):
            if key in raw:
                job[key] = {k: v for k, v in raw[key].items() if k in ("path", "sha256", "checkout_line")} if isinstance(raw[key], dict) else raw[key]
        if isinstance(raw.get("artifacts"), dict):
            job["artifacts"] = {name: {k: v for k, v in entry.items() if k in ("path", "sha256")} if isinstance(entry, dict) else entry
                                for name, entry in raw["artifacts"].items() if name in expected[raw["name"]]["artifacts"]}
        jobs.append(job)
    return {"format": "complect-ci-audit-report-v1", "jobs": jobs}


def make_ci_audit_continuation(requirements: Any, report: Any, root: Path, *, continuation: Any = None) -> dict[str, Any]:
    """Capture a verified waiting observation; no network or file writes."""
    result = verify_ci_audit(requirements, report, root, continuation=continuation)
    if result["status"] not in {"PENDING", "BLOCKED_EVIDENCE"} or result.get("continuation_missing"):
        raise EvidenceBundleError("CONTINUATION_REQUIRES_WAITING")
    point = {"format": "complect-ci-audit-continuation-v1", "status": result["status"],
            "requirements": _continuation_requirements(requirements),
            "report": _continuation_report(report, requirements),
            "checked": {job["name"]: job.get("checked", {}) for job in result["jobs"]},
            "missing": {job["name"]: job["missing"] for job in result["jobs"]}, "history": []}
    if continuation is not None:
        history = json.loads(json.dumps(continuation["history"]))
        prior = {key: continuation[key] for key in ("status", "report", "checked", "missing")}
        current = {key: point[key] for key in ("status", "report", "checked", "missing")}
        if prior != current and prior not in history:
            history.append(json.loads(json.dumps(prior)))
        point["history"] = history
    return point


def _check_continuation_observation(requirements: Any, observation: Any, root: Path) -> _JobAudit:
    if (not isinstance(observation, dict) or set(observation) != {"status", "report", "checked", "missing"}
            or not isinstance(observation.get("status"), str)
            or observation["status"] not in {"PENDING", "BLOCKED_EVIDENCE"}):
        raise EvidenceBundleError("CONTINUATION_OBSERVATION_INVALID")
    names = {j["name"] for j in _selection(requirements)}
    if (not isinstance(observation.get("checked"), dict) or set(observation["checked"]) != names
            or not isinstance(observation.get("missing"), dict) or set(observation["missing"]) != names):
        raise EvidenceBundleError("CONTINUATION_SUMMARY_INVALID")
    saved_report = observation["report"]
    if (not isinstance(saved_report, dict) or saved_report.get("format") != "complect-ci-audit-report-v1"
            or not isinstance(saved_report.get("jobs"), list)):
        raise EvidenceBundleError("CONTINUATION_REPORT_INVALID")
    prior = verify_ci_audit(requirements, saved_report, root)
    if prior["errors"] or any(job["errors"] for job in prior["jobs"]):
        raise EvidenceBundleError("CONTINUATION_EVIDENCE_INVALID")
    if _continuation_report(saved_report, requirements) != saved_report:
        raise EvidenceBundleError("CONTINUATION_REPORT_INVALID")
    if prior["status"] == "PASS" and observation["status"] == "PENDING":
        raise EvidenceBundleError("CONTINUATION_NOT_PENDING")
    by_name = {job["name"]: job for job in prior["jobs"]}
    if set(by_name) != names:
        raise EvidenceBundleError("CONTINUATION_REPORT_INVALID")
    audit = _JobAudit(root)
    for job in _selection(requirements):
        name = job["name"]
        saved = observation["checked"][name]
        missing = observation["missing"][name]
        if not isinstance(saved, dict) or not isinstance(missing, list) or not all(isinstance(m, str) for m in missing):
            raise EvidenceBundleError("CONTINUATION_SUMMARY_INVALID")
        labels = {"log", "proof", "source_before", "source_after"} | {"artifacts." + a for a in job["artifacts"]}
        if not set(saved).issubset(labels):
            raise EvidenceBundleError("CONTINUATION_SUMMARY_INVALID")
        missing_before = len(audit.missing)
        for label, entry in saved.items():
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
                raise EvidenceBundleError("CONTINUATION_SUMMARY_INVALID")
            audit.read(entry, name + "." + label, as_json=False)
            current = by_name[name].get("checked", {}).get(label)
            if current is not None and current != entry:
                raise EvidenceBundleError("CONTINUATION_CHECKED_MISMATCH")
        if len(audit.missing) == missing_before:
            # Reproduce the saved availability, not today's filesystem: a file
            # declared missing then may have legitimately arrived since.
            unreadable = {m.removesuffix(":UNREADABLE") for m in missing if m.endswith(":UNREADABLE")}
            replay = _JobAudit(root, available=saved, unreadable=unreadable)
            raw = next((r for r in saved_report["jobs"] if r["name"] == name), None)
            reproduced = replay.job(raw, job) if raw is not None else {"missing": ["JOB_MISSING"], "checked": {}}
            if missing != reproduced["missing"] or saved != reproduced["checked"]:
                raise EvidenceBundleError("CONTINUATION_SUMMARY_MISMATCH")
        for item in by_name[name]["missing"]:
            if item.endswith((":FILE_MISSING", ":UNREADABLE")):
                audit.missing.append(name + "." + item)
    if audit.failed:
        raise EvidenceBundleError("CONTINUATION_EVIDENCE_INVALID")
    return audit


def _resume_ci_audit(requirements: Any, report: Any, root: Path, continuation: Any) -> dict[str, Any]:
    result = verify_ci_audit(requirements, report, root)
    try:
        if (not isinstance(continuation, dict) or continuation.get("format") != "complect-ci-audit-continuation-v1"
                or not isinstance(continuation.get("status"), str)
                or continuation["status"] not in {"PENDING", "BLOCKED_EVIDENCE"}):
            raise EvidenceBundleError("CONTINUATION_INVALID")
        if continuation.get("requirements") != _continuation_requirements(requirements):
            raise EvidenceBundleError("CONTINUATION_BINDING_MISMATCH")
        if set(continuation) != {"format", "status", "requirements", "report", "checked", "missing", "history"}:
            raise EvidenceBundleError("CONTINUATION_INVALID")
        history = continuation["history"]
        if not isinstance(history, list):
            raise EvidenceBundleError("CONTINUATION_HISTORY_INVALID")
        current = {key: continuation[key] for key in ("status", "report", "checked", "missing")}
        audit = _JobAudit(root)
        for index, observation in enumerate([*history, current]):
            checked = _check_continuation_observation(requirements, observation, root)
            audit.checked.update({f"{index}.{label}": entry for label, entry in checked.checked.items()})
            audit.missing.extend(f"{index}.{label}" for label in checked.missing)
        if audit.missing:
            result["status"] = next(s for s in PRIORITY if s in {result["status"], "BLOCKED_EVIDENCE"})
            result["continuation_missing"] = sorted(set(audit.missing))
        result["continuation_checked"] = audit.checked
    except EvidenceBundleError as error:
        result.update(status="FAIL", errors=[*result["errors"], str(error)])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--continuation", type=Path)
    parser.add_argument("--continuation-sha256")
    parser.add_argument("--save-continuation", type=Path)
    args = parser.parse_args(argv)
    if ((args.continuation is None) != (args.continuation_sha256 is None)
            or args.continuation is not None and not _hex(args.continuation_sha256, 64)):
        print(json.dumps({"status": "FAIL", "errors": ["CONTINUATION_PIN_REQUIRED"]}))
        return 1
    output = args.output.resolve()
    checkpoint = args.save_continuation.resolve() if args.save_continuation is not None else None
    destinations = [output] + ([checkpoint] if checkpoint is not None else [])
    input_paths = {args.requirements.resolve(), args.report.resolve()}
    if args.continuation is not None:
        input_paths.add(args.continuation.resolve())
    if (len(set(destinations)) != len(destinations) or any(
            path.is_relative_to(args.root.resolve()) or path.exists() or path in input_paths
            for path in destinations)):
        print(json.dumps({"status": "FAIL", "errors": ["SEPARATE_NEW_OUTPUT_REQUIRED"]}))
        return 1
    point = None
    try:
        inputs = []
        for path in (args.requirements, args.report):
            if path.stat().st_size > MAX_JSON:
                raise ValueError("input too large")
            inputs.append(_json_object(path.read_bytes()))
        continuation = None
        if args.continuation is not None:
            path = args.continuation.absolute()
            if any(p.is_symlink() for p in (path, *path.parents)) or not stat.S_ISREG(path.stat().st_mode):
                raise ValueError("continuation must be a regular file")
            size = path.stat().st_size
            if size > MAX_JSON:
                raise ValueError("continuation too large")
            continuation = _json_object(read_exact_chunk(path, 0, size, args.continuation_sha256))
        result = verify_ci_audit(inputs[0], inputs[1], args.root, continuation=continuation)
        if (checkpoint is not None and result["status"] in {"PENDING", "BLOCKED_EVIDENCE"}
                and not result.get("continuation_missing") and not result["errors"]):
            point = make_ci_audit_continuation(inputs[0], inputs[1], args.root, continuation=continuation)
            if len(json.dumps(point).encode()) > MAX_JSON:
                raise ValueError("continuation too large")
    except (FileNotFoundError, PermissionError):
        result = {"format": "complect-ci-audit-result-v1", "status": "BLOCKED_EVIDENCE", "errors": ["INPUT_UNAVAILABLE"], "jobs": []}
    except (OSError, ValueError, UnicodeError):
        result = {"format": "complect-ci-audit-result-v1", "status": "FAIL", "errors": ["INPUT_INVALID"], "jobs": []}
    summary: dict[str, Any] = {"status": result["status"], "jobs": len(result["jobs"]), "output": str(output)}
    try:
        with output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
        if point is not None and checkpoint is not None:
            payload = (json.dumps(point, indent=2) + "\n").encode()
            if len(payload) > MAX_JSON:
                raise ValueError("continuation too large")
            with os.fdopen(os.open(checkpoint, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
                stream.write(payload)
            summary.update(continuation=str(checkpoint), continuation_sha256=hashlib.sha256(payload).hexdigest())
    except (OSError, ValueError):
        print(json.dumps({"status": "FAIL", "errors": ["OUTPUT_UNAVAILABLE"], "output": str(output)}))
        return 1
    print(json.dumps(summary))
    return {"PASS": 0, "FAIL": 1, "BLOCKED_EVIDENCE": 2, "PENDING": 3}[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
