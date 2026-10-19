"""Read-only verification of a publishable head and its base."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from infra.scripts.delivery_evidence import accept_receipts, _json


def validate_publication_inputs(
    repository: Path,
    *,
    requirements_path: Path | None,
    receipts: list[Path] | None,
    declared_checkout: Path | None = None,
) -> None:
    """Validate local publication inputs before any remote/Git operation.

    Receipt files are evidence only: this function checks their presence and
    regular-file shape and never interprets or executes their command fields.
    """
    def has_symlink_component(path: Path) -> bool:
        absolute = path.absolute()
        return any(part.is_symlink() for part in (absolute, *absolute.parents))

    if has_symlink_component(repository) or (
        declared_checkout is not None and has_symlink_component(declared_checkout)
    ):
        raise ValueError("SYMLINK_PATH_FORBIDDEN")
    repository = repository.resolve()
    if declared_checkout is not None and repository != declared_checkout.resolve():
        raise ValueError("DECLARED_CHECKOUT_MISMATCH")
    if not repository.is_dir():
        raise ValueError("CHECKOUT_MISSING")
    if (
        requirements_path is None
        or has_symlink_component(requirements_path)
        or not requirements_path.is_file()
    ):
        raise ValueError("REQUIREMENTS_MISSING")
    try:
        requirements = json.loads(requirements_path.read_text())
    except (OSError, ValueError, UnicodeError):
        raise ValueError("REQUIREMENTS_INVALID") from None
    if not isinstance(requirements, dict):
        raise ValueError("REQUIREMENTS_INVALID")
    for receipt in receipts or []:
        if has_symlink_component(receipt) or not receipt.is_file():
            raise ValueError("RECEIPT_MISSING")
        try:
            value = json.loads(receipt.read_text())
        except (OSError, ValueError, UnicodeError):
            raise ValueError("RECEIPT_INVALID") from None
        if not isinstance(value, dict):
            raise ValueError("RECEIPT_INVALID")


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.PIPE).strip()


def stand_source_identity(repository: Path, revision: str) -> str:
    """Recompute the exact namespace hash used by Stand.source_identity."""
    all_names = git(repository, "ls-tree", "-r", "--name-only", revision, "--", "backend", "infra/scripts", "infra/postgres").splitlines()
    names = [name for prefix in ("backend/", "infra/scripts/", "infra/postgres/") for name in all_names if name.startswith(prefix)]
    digest = hashlib.sha256()
    for name in (name for name in names if name.endswith((".py", ".json", ".sql", ".sh")) and "__pycache__" not in name.split("/")):
        data = subprocess.check_output(["git", "-C", str(repository), "show", f"{revision}:{name}"], stderr=subprocess.PIPE)
        digest.update(name.encode())
        digest.update(data)
    return digest.hexdigest()


def snapshot_matches_head(snapshot_path: Path, repository: Path, revision: str) -> bool:
    """Compare a working-tree snapshot's full file/mode map to a commit."""
    from infra.scripts.agent_harness import _snapshot_path_allowed

    try:
        snapshot = json.loads(snapshot_path.read_text())
        if snapshot.get("format") != "complect-working-tree-snapshot-v1":
            return False
        files: dict[str, dict[str, Any]] = {}
        raw = subprocess.check_output(["git", "-C", str(repository), "ls-tree", "-r", "-z", revision], stderr=subprocess.PIPE)
        for record in raw.decode().split("\0"):
            if not record:
                continue
            meta, name = record.split("\t", 1)
            mode, _kind, blob = meta.split()
            if not _snapshot_path_allowed(name):
                continue
            data = subprocess.check_output(["git", "-C", str(repository), "show", f"{revision}:{name}"], stderr=subprocess.PIPE)
            files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "mode": int(mode, 8) & 0o777, "kind": "symlink" if mode == "120000" else "file"}
        return snapshot.get("files") == files and snapshot.get("deleted", []) == []
    except (OSError, ValueError, subprocess.CalledProcessError, UnicodeDecodeError):
        return False


def source_map_matches_head(source_map: Any, repository: Path, revision: str) -> bool:
    if not isinstance(source_map, dict) or not source_map:
        return False
    try:
        for name, expected in source_map.items():
            if not isinstance(name, str) or not isinstance(expected, str) or not name or ".." in Path(name).parts:
                return False
            data = subprocess.check_output(["git", "-C", str(repository), "show", f"{revision}:{name}"], stderr=subprocess.PIPE)
            if hashlib.sha256(data).hexdigest() != expected:
                return False
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def remote_head(repository: Path, remote: str, target: str | None) -> str:
    if not target:
        raise ValueError("target branch required")
    ref = f"refs/heads/{target}" if target else "HEAD"
    completed = subprocess.run(["git", "-C", str(repository), "ls-remote", remote, ref], text=True, capture_output=True, timeout=30, check=False)
    if completed.returncode:
        raise ValueError("selected remote base unavailable")
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 1 and len(fields[0]) == 40:
            return fields[0]
    raise ValueError("selected remote base unavailable")


def repository_identity(repository: Path, remote: str) -> str:
    value = git(repository, "remote", "get-url", remote)
    if value.startswith("file://"):
        return value
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", value)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return value.rstrip("/")


def pr_metadata(repository: Path, pr: str, *, remote: str = "origin", gh_runner=subprocess.run) -> dict[str, Any]:
    repo = repository_identity(repository, remote)
    completed = gh_runner(
        ["gh", "pr", "view", pr, "--repo", repo, "--json", "baseRefName,baseRefOid,headRefOid,url"],
        cwd=repository, capture_output=True, text=True, check=False, timeout=30,
    )
    if completed.returncode:
        raise ValueError("existing PR metadata unavailable")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict) or not all(isinstance(value.get(key), str) and value[key] for key in ("baseRefName", "baseRefOid", "headRefOid", "url")):
        raise ValueError("existing PR metadata incomplete")
    # A PR's historical baseRefOid can lag the current target branch.
    # Bind the selected remote to the live API ref, preserving the PR metadata.
    target_ref = gh_runner(
        ["gh", "api", f"repos/{repo}/git/ref/heads/{quote(value['baseRefName'], safe='')}"],
        cwd=repository, capture_output=True, text=True, check=False, timeout=30,
    )
    if target_ref.returncode:
        raise ValueError("current PR target branch unavailable")
    ref = json.loads(target_ref.stdout)
    object_ref = ref.get("object") if isinstance(ref, dict) else None
    if (not isinstance(object_ref, dict) or object_ref.get("type") != "commit"
            or not isinstance(object_ref.get("sha"), str)
            or re.fullmatch(r"[0-9a-f]{40}", object_ref["sha"]) is None):
        raise ValueError("current PR target branch identity incomplete")
    value["baseBranchHeadOid"] = object_ref["sha"]
    return value


def receipt_comparison_base(payload: Any) -> str | None:
    if isinstance(payload, dict):
        if isinstance(payload.get("comparison_base"), str):
            return payload["comparison_base"]
        for value in payload.values():
            found = receipt_comparison_base(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = receipt_comparison_base(value)
            if found:
                return found
    return None


def preflight(
    repository: Path, *, remote: str, head: str,
    requirements: dict[str, Any] | None = None, receipts: list[Path] | None = None,
    resolver: Callable[[Path, str, str], str] | None = None,
    target: str | None = None,
    pr: str | None = None,
    gh_resolver: Callable[[Path, str], dict[str, Any]] | None = None,
    requirements_path: Path | None = None,
    declared_checkout: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    repository = repository.resolve()
    actual_head: str | None = None
    head_tree: str | None = None
    actual_base: str | None = None
    base_tree: str | None = None
    pr_head: str | None = None
    if requirements_path is not None or declared_checkout is not None:
        try:
            validate_publication_inputs(
                repository,
                requirements_path=requirements_path,
                receipts=receipts,
                declared_checkout=declared_checkout,
            )
        except ValueError as exc:
            return {
                "version": 1,
                "status": "BLOCKED_STAND",
                "errors": [str(exc)],
                "repository": str(repository.resolve()),
                "remote": remote,
                "head": None,
                "head_tree": None,
                "base": None,
                "base_tree": None,
            }
    try:
        if bool(pr) == bool(target):
            raise ValueError("exactly one of target or pr is required")
        top = Path(git(repository, "rev-parse", "--show-toplevel")).resolve()
        if top != repository:
            errors.append("REPOSITORY_MISMATCH")
        actual_head = git(repository, "rev-parse", f"{head}^{{commit}}")
        head_tree = git(repository, "rev-parse", f"{actual_head}^{{tree}}")
        if pr:
            metadata = gh_resolver(repository, pr) if gh_resolver else pr_metadata(repository, pr, remote=remote)
            target = metadata["baseRefName"]
            actual_base = remote_head(repository, remote, target)
            if actual_base != metadata.get("baseBranchHeadOid", metadata["baseRefOid"]):
                errors.append("PR_BASE_MISMATCH")
            pr_head = metadata["headRefOid"]
            try:
                if not subprocess.run(["git", "-C", str(repository), "merge-base", "--is-ancestor", pr_head, actual_head], capture_output=True, timeout=30, check=False).returncode == 0:
                    errors.append("PR_HEAD_NOT_ANCESTOR")
            except (OSError, subprocess.SubprocessError):
                errors.append("PR_HEAD_NOT_ANCESTOR")
            pr_url = metadata.get("url")
            if pr_url and repository_identity(repository, remote) not in str(pr_url):
                errors.append("PR_REPOSITORY_MISMATCH")
        else:
            if not target:
                raise ValueError("target branch required for new PR")
            actual_base = remote_head(repository, remote, target)
        base_tree = git(repository, "rev-parse", f"{actual_base}^{{tree}}")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        errors.append(f"GIT_INPUT_INVALID:{exc}")
        actual_head = head_tree = actual_base = base_tree = None
    acceptance = None
    if requirements is None:
        errors.append("REQUIREMENTS_REQUIRED")
    if receipts is None or not receipts:
        errors.append("RECEIPTS_REQUIRED")
    if requirements is not None and receipts is not None:
        acceptance = accept_receipts(requirements, receipts)
        errors.extend(f"RECEIPT:{error}" for error in acceptance["errors"])
        normalized = acceptance.get("receipts", []) if isinstance(acceptance, dict) else []
        expected_source = requirements.get("source", requirements)
        expected = expected_source.get("source_commit") if isinstance(expected_source, dict) else None
        if isinstance(expected, str) and expected != actual_head:
            if any(isinstance(item, dict) and item.get("producer") == "legacy" for item in normalized) or not (isinstance(expected_source, dict) and isinstance(expected_source.get("snapshot"), dict)):
                errors.append("PUBLISH_HEAD_SOURCE_MISMATCH")
        expected_tree = expected_source.get("source_tree") if isinstance(expected_source, dict) else None
        if isinstance(expected_tree, str) and expected_tree != head_tree:
            errors.append("PUBLISH_HEAD_TREE_MISMATCH")
        source = requirements.get("source", requirements)
        if not isinstance(source, dict) or not any(source.get(key) for key in ("source_commit", "source_tree", "working_tree_sha256", "producer_sha256", "sources", "snapshot")):
            errors.append("SOURCE_REQUIREMENT_MISSING")
        elif isinstance(source, dict) and actual_head:
            needs_snapshot = bool(source.get("producer_sha256") or any(isinstance(item, dict) and item.get("producer") == "harness" for item in normalized))
            if needs_snapshot:
                if not isinstance(source.get("snapshot"), dict) or not isinstance(source["snapshot"].get("path"), str) or not isinstance(source["snapshot"].get("sha256"), str):
                    errors.append("SOURCE_SNAPSHOT_REQUIRED")
                else:
                    snapshot = Path(source["snapshot"]["path"])
                    if not snapshot.is_file() or hashlib.sha256(snapshot.read_bytes()).hexdigest() != source["snapshot"]["sha256"] or not snapshot_matches_head(snapshot, repository, actual_head):
                        errors.append("SOURCE_SNAPSHOT_MISMATCH")
                expected_hash = stand_source_identity(repository, actual_head)
                for item in normalized:
                    identity = item.get("source_identity", {}) if isinstance(item, dict) else {}
                    if isinstance(item, dict) and item.get("producer") == "infrastructure" and identity.get("sha256") != expected_hash:
                        errors.append("SOURCE_PRODUCER_HASH_MISMATCH")
            if source.get("sources"):
                for item in normalized:
                    identity = item.get("source_identity", {}) if isinstance(item, dict) else {}
                    if isinstance(item, dict) and item.get("producer") == "harness" and (identity.get("sources") != source["sources"] or not source_map_matches_head(source["sources"], repository, actual_head)):
                        errors.append("SOURCE_FILE_HASH_MAP_MISMATCH")
        expected_repo = requirements.get("repository")
        if not isinstance(expected_repo, str) or not expected_repo:
            errors.append("REPOSITORY_REQUIREMENT_MISSING")
        else:
            try:
                if repository_identity(repository, remote) != expected_repo:
                    errors.append("REPOSITORY_IDENTITY_MISMATCH")
            except (OSError, subprocess.CalledProcessError):
                errors.append("REMOTE_IDENTITY_UNAVAILABLE")
        infra_receipts = [item for item in normalized if isinstance(item, dict) and item.get("producer") == "infrastructure"]
        if not infra_receipts or any(item.get("comparison_base") != actual_base for item in infra_receipts):
            errors.append("COMPARISON_BASE_MISSING_OR_MISMATCH")
    result: dict[str, Any] = {"version": 1, "status": "PASS" if not errors else "BLOCKED_STAND", "errors": sorted(set(errors)),
              "repository": str(repository), "remote": remote, "head": actual_head, "head_tree": head_tree,
              "base": actual_base, "base_tree": base_tree}
    if pr_head is not None:
        result["pr_head"] = pr_head
    if acceptance is not None:
        result["acceptance"] = acceptance
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--head", required=True)
    boundary = parser.add_mutually_exclusive_group(required=True)
    boundary.add_argument("--target", help="remote branch used for a new PR")
    boundary.add_argument("--pr", help="existing PR number or URL")
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--receipt", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    try:
        receipts = args.receipt if args.requirements else None
        validate_publication_inputs(
            args.repository,
            requirements_path=args.requirements,
            receipts=receipts,
            declared_checkout=args.repository,
        )
        requirements = _json(args.requirements) if args.requirements else None
        result = preflight(args.repository, remote=args.remote, head=args.head, target=args.target, pr=args.pr, requirements=requirements, receipts=receipts, requirements_path=args.requirements, declared_checkout=args.repository if args.requirements else None)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"version": 1, "status": "BLOCKED_STAND", "errors": [f"INPUT_INVALID:{exc}"]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
