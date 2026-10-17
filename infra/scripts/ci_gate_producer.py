"""Produce an authenticated CI gate evidence bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, cast

from infra.scripts.verification_runner import run_check

IDENTITY = (
    "repository",
    "run_id",
    "run_attempt",
    "job_id",
    "head_sha",
    "base_sha",
    "checkout_sha",
    "tree_sha",
)


def _hex40(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(c in "0123456789abcdef" for c in value)
    )


class ProducerError(ValueError):
    def __init__(self, message: str, *, exit_code: int = 2):
        super().__init__(message)
        self.exit_code = exit_code


def _git(root: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True, check=False
    )
    if p.returncode:
        raise ProducerError("GIT_FAILED")
    return p.stdout.rstrip("\n")


def _tracked(root: Path) -> list[str]:
    raw = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False
    ).stdout
    if not raw or raw.endswith(b"\0") is False:
        raise ProducerError("GIT_INDEX_UNAVAILABLE")
    names = raw[:-1].decode("utf-8").split("\0")
    result: list[str] = []
    for name in names:
        path = root / name
        if (
            not path.is_file()
            or path.is_symlink()
            or not stat.S_ISREG(path.stat().st_mode)
        ):
            raise ProducerError("TRACKED_REGULAR_FILE_REQUIRED")
        result.append(name)
    return sorted(result)


def _clean(root: Path) -> None:
    for args in (("diff-files", "--quiet"), ("diff", "--cached", "--quiet")):
        p = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, check=False
        )
        if p.returncode != 0:
            raise ProducerError("DIRTY_TRACKED_SOURCE")


def _snapshot(root: Path, names: list[str], head: str, tree: str) -> dict[str, Any]:
    files = {}
    for name in names:
        path = root / name
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(root)
        ):
            raise ProducerError("SOURCE_DRIFT")
        files[name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mode": path.stat().st_mode,
        }
    return {"head": head, "tree": tree, "files": files}


def _event_identity(path: Path, checkout_sha: str) -> tuple[str, str]:
    try:
        event = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeError):
        raise ProducerError("CI_EVENT_INVALID") from None
    value = None
    event_head = checkout_sha
    if isinstance(event, dict) and isinstance(event.get("pull_request"), dict):
        value = event["pull_request"].get("base", {}).get("sha")
        event_head = event["pull_request"].get("head", {}).get("sha")
    elif isinstance(event, dict) and isinstance(event.get("before"), str):
        value = event["before"]
    if not _hex40(value) or value == "0" * 40:
        raise ProducerError("CI_BASE_SHA_MISSING")
    if not _hex40(event_head):
        raise ProducerError("CI_HEAD_SHA_MISSING")
    return cast(str, value), cast(str, event_head)


def _jobs(meta: dict[str, Any]) -> int:
    repo, run, attempt = meta["repository"], meta["run_id"], meta["run_attempt"]
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ProducerError("CI_TOKEN_MISSING")
    found: list[dict[str, Any]] = []
    total_count: int | None = None
    for page in range(1, 1001):
        url = f"https://api.github.com/repos/{urllib.parse.quote(repo, safe='/')}/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100&page={page}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read())
        except Exception:
            raise ProducerError("CI_API_UNAVAILABLE") from None
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("jobs"), list)
            or any(not isinstance(j, dict) for j in data["jobs"])
        ):
            raise ProducerError("CI_API_INVALID")
        if total_count is None:
            total_count = data.get("total_count")
        if (
            type(data.get("total_count")) is not int
            or data["total_count"] != total_count
            or data["total_count"] < 1
        ):
            raise ProducerError("CI_API_TOTAL_INVALID")
        if data.get("run_id") is not None and data["run_id"] != run:
            raise ProducerError("CI_API_RUN_MISMATCH")
        for job in data["jobs"]:
            if (
                type(job.get("id")) is not int
                or job["id"] <= 0
                or not isinstance(job.get("name"), str)
            ):
                raise ProducerError("CI_API_INVALID")
            for key, expected in (
                ("run_id", run),
                ("run_attempt", attempt),
                ("head_sha", meta["head_sha"]),
            ):
                if key not in job or job[key] != expected:
                    raise ProducerError("CI_API_JOB_IDENTITY_MISMATCH")
        found.extend(j for j in data["jobs"] if isinstance(j, dict))
        if len(data["jobs"]) < 100:
            break
    if total_count != len(found) or len({j.get("id") for j in found}) != len(found):
        raise ProducerError("CI_API_PAGINATION_INCOMPLETE")
    matches = [j for j in found if j.get("name") == meta["job_name"]]
    if (
        len(matches) != 1
        or type(matches[0].get("id")) is not int
        or matches[0]["id"] <= 0
    ):
        raise ProducerError("CI_JOB_ID_AMBIGUOUS")
    return matches[0]["id"]


def _descriptor(root: Path, path: Path) -> dict[str, str]:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ProducerError("ARTIFACT_UNSAFE")
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not stat.S_ISREG(resolved.stat().st_mode):
        raise ProducerError("ARTIFACT_UNSAFE")
    rel = path.relative_to(root).as_posix()
    return {"path": rel, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def produce(
    command: list[str], *, root: Path, output: Path, artifacts: dict[str, str]
) -> tuple[dict[str, Any], int]:
    root, output = root.resolve(), output.resolve()
    if (
        output.exists()
        or output.is_relative_to(root)
        and not output.relative_to(root).as_posix().startswith(".dev/test-artifacts/")
        or any(
            name in {"receipt", "source_proof", "runner_receipt", "command_log"}
            or re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name) is None
            or not rel
            or Path(rel).is_absolute()
            or ".." in Path(rel).parts
            or str(Path(rel))
            in {
                "ci-receipt.json",
                "source-proof.json",
                "source-before.json",
                "source-after.json",
                "run/receipt.json",
                "run/command.log",
            }
            for name, rel in artifacts.items()
        )
        or len({str(Path(rel)) for rel in artifacts.values()}) != len(artifacts)
    ):
        raise ProducerError("OUTPUT_MUST_BE_NEW_AND_OUTSIDE_SOURCE")
    _clean(root)
    names = _tracked(root)
    head, tree = _git(root, "rev-parse", "HEAD"), _git(root, "rev-parse", "HEAD^{tree}")
    if not _hex40(head) or not _hex40(tree):
        raise ProducerError("GIT_IDENTITY_INVALID")
    env = {
        "repository": os.environ.get("GITHUB_REPOSITORY"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "job_name": os.environ.get("GITHUB_JOB"),
        "head_sha": os.environ.get("GITHUB_SHA"),
        "event": os.environ.get("GITHUB_EVENT_PATH"),
    }
    if not all(isinstance(env[k], str) and env[k] for k in env):
        raise ProducerError("CI_METADATA_MISSING")
    if env["head_sha"] != head or not _hex40(head):
        raise ProducerError("CI_CHECKOUT_MISMATCH")
    repository = cast(str, env["repository"])
    run_id_value = cast(str, env["run_id"])
    run_attempt_value = cast(str, env["run_attempt"])
    event_path = cast(str, env["event"])
    try:
        run_id, run_attempt = int(run_id_value), int(run_attempt_value)
    except ValueError:
        raise ProducerError("CI_IDENTITY_INVALID") from None
    if (
        run_id <= 0
        or run_attempt <= 0
        or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None
    ):
        raise ProducerError("CI_IDENTITY_INVALID")
    base, event_head = _event_identity(Path(event_path), head)
    identity = {
        "repository": repository,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "job_id": None,
        "head_sha": event_head,
        "base_sha": base,
        "checkout_sha": head,
        "tree_sha": tree,
    }
    identity["job_id"] = _jobs({**identity, "job_name": env["job_name"]})
    before = _snapshot(root, names, head, tree)
    output.mkdir(parents=True)
    run_dir = output / "run"
    observed_exit: list[int | None] = []
    try:
        gate = run_check(
            command,
            root=root,
            cwd=root,
            sources=names,
            output=run_dir,
            on_exit=observed_exit.append,
            redact_env=[
                name for name in ("GH_TOKEN", "GITHUB_TOKEN") if os.environ.get(name)
            ],
        )
    except (OSError, ValueError):
        original = observed_exit[0] if observed_exit else None
        code = original if type(original) is int else 2
        code = code if code >= 0 else 128 - code
        return {
            "status": "BLOCKED_EVIDENCE",
            "exit": code if original is not None else None,
            "adapter_exit": code or 2,
            "identity": identity,
            "errors": ["RUNNER_EVIDENCE_FAILED"],
        }, code or 2
    try:
        final_head, final_tree = (
            _git(root, "rev-parse", "HEAD"),
            _git(root, "rev-parse", "HEAD^{tree}"),
        )
        after = _snapshot(root, names, final_head, final_tree)
        final_names = _tracked(root)
    except (OSError, ValueError):
        after = {"head": "", "tree": "", "files": {}}
        final_head, final_tree, final_names = "", "", []
    if (
        before != after
        or final_head != head
        or final_tree != tree
        or final_names != names
        or any(
            p.returncode != 0
            for p in (
                subprocess.run(
                    ["git", "-C", str(root), "diff-files", "--quiet"],
                    capture_output=True,
                ),
                subprocess.run(
                    ["git", "-C", str(root), "diff", "--cached", "--quiet"],
                    capture_output=True,
                ),
            )
        )
    ):
        status = "BLOCKED_EVIDENCE"
    elif gate.get("integrity") != "PASS" or type(gate.get("exit")) is not int:
        status = "BLOCKED_EVIDENCE"
    elif gate.get("exit") == 0:
        status = "PASS"
    else:
        status = "FAIL"
    raw_exit = gate.get("exit")
    original_exit = cast(int, raw_exit) if type(raw_exit) is int else None
    exit_code = (
        2
        if original_exit is None
        else original_exit
        if original_exit >= 0
        else 128 - original_exit
    )
    try:
        return _publish_bundle(
            output,
            before,
            after,
            identity,
            gate,
            status,
            artifacts,
            original_exit,
            exit_code,
        )
    except (OSError, ValueError):
        return {
            "format": "complect-ci-gate-producer-v1",
            "status": "BLOCKED_EVIDENCE",
            "exit": exit_code if original_exit is not None else None,
            "adapter_exit": exit_code or 2,
            "identity": identity,
            "errors": ["EVIDENCE_WRITE_FAILED"],
        }, exit_code or 2


def _publish_bundle(
    output: Path,
    before: dict[str, Any],
    after: dict[str, Any],
    identity: dict[str, Any],
    gate: dict[str, Any],
    status: str,
    artifacts: dict[str, str],
    original_exit: int | None,
    exit_code: int,
) -> tuple[dict[str, Any], int]:
    receipt = {
        "format": "complect-ci-gate-receipt-v1",
        "status": status,
        "exit": exit_code if original_exit is not None else None,
        "original_exit": original_exit,
        "gate": gate,
    }
    try:
        (output / "ci-receipt.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        )
        (output / "source-before.json").write_text(
            json.dumps(before, indent=2, sort_keys=True) + "\n"
        )
        (output / "source-after.json").write_text(
            json.dumps(after, indent=2, sort_keys=True) + "\n"
        )
    except OSError as exc:
        raise ProducerError("EVIDENCE_WRITE_FAILED", exit_code=exit_code) from exc
    descriptors = {
        "receipt": _descriptor(output, output / "ci-receipt.json"),
        "runner_receipt": _descriptor(output, output / "run/receipt.json"),
        "command_log": _descriptor(output, output / "run/command.log"),
    }
    artifact_errors = []
    for name, rel in artifacts.items():
        try:
            descriptors[name] = _descriptor(output, output / rel)
        except (OSError, ValueError):
            artifact_errors.append(f"ARTIFACT_MISSING:{name}")
    if artifact_errors:
        status = "BLOCKED_EVIDENCE"
        receipt["status"] = status
        try:
            (output / "ci-receipt.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n"
            )
        except OSError as exc:
            raise ProducerError("EVIDENCE_WRITE_FAILED", exit_code=exit_code) from exc
        descriptors["receipt"] = _descriptor(output, output / "ci-receipt.json")
    proof = {
        "format": "complect-ci-source-proof-v1",
        "status": "PASS" if status != "BLOCKED_EVIDENCE" else "BLOCKED_EVIDENCE",
        **identity,
        "source_before": _descriptor(output, output / "source-before.json"),
        "source_after": _descriptor(output, output / "source-after.json"),
        "artifacts": descriptors,
    }
    try:
        (output / "source-proof.json").write_text(
            json.dumps(proof, indent=2, sort_keys=True) + "\n"
        )
    except OSError as exc:
        raise ProducerError("EVIDENCE_WRITE_FAILED", exit_code=exit_code) from exc
    result = {
        "format": "complect-ci-gate-producer-v1",
        "status": status,
        "exit": receipt["exit"],
        "identity": identity,
        "proof": _descriptor(output, output / "source-proof.json"),
        "artifacts": descriptors,
        "errors": artifact_errors,
    }
    return result, exit_code if exit_code else (0 if status == "PASS" else 2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        extra = {}
        for item in args.artifact:
            name, sep, rel = item.partition("=")
            if (
                not sep
                or not name
                or not rel
                or Path(rel).is_absolute()
                or ".." in Path(rel).parts
            ):
                raise ProducerError("ARTIFACT_INVALID")
            if name in extra or name in {
                "receipt",
                "source_proof",
                "runner_receipt",
                "command_log",
            }:
                raise ProducerError("ARTIFACT_NAME_RESERVED_OR_DUPLICATE")
            extra[name] = rel
        result, code = produce(
            command, root=args.root, output=args.output, artifacts=extra
        )
        print(json.dumps(result, sort_keys=True))
        return code
    except (ProducerError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED_EVIDENCE", "error": str(exc)}))
        return getattr(exc, "exit_code", 2) or 2


if __name__ == "__main__":
    sys.exit(main())
