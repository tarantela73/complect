"""Ensure one GitHub issue relation through gh, with checked preflight/readback."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from typing import Any


class _LinkFailure(ValueError):
    def __init__(
        self, code: str, http_status: int | None = None, *, definitive: bool = False
    ):
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.definitive = definitive


def _request(
    method: str, path: str, fields: dict[str, int | bool] | None = None
) -> tuple[int | None, Any]:
    args = [
        "gh",
        "api",
        "--hostname",
        "github.com",
        "--include",
        "--method",
        method,
        path,
    ]
    for key, value in (fields or {}).items():
        args += [
            "-F",
            f"{key}={str(value).lower() if isinstance(value, bool) else value}",
        ]
    try:
        completed = subprocess.run(
            args, capture_output=True, text=True, timeout=30, check=False
        )
        header, body = completed.stdout.replace("\r\n", "\n").split("\n\n", 1)
        match = re.match(r"HTTP/\S+ (\d{3})\b", header)
        if match is None:
            return None, None
        status = int(match[1])
        if completed.returncode and status < 400:
            return None, None
        try:
            data = json.loads(body) if body.strip() else None
        except ValueError:
            data = None
        return status, data
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, None


def _metadata(issue: dict[str, Any]) -> str:
    if (
        not isinstance(issue.get("title"), str)
        or "body" not in issue
        or issue["body"] is not None
        and not isinstance(issue["body"], str)
        or issue.get("state") not in ("open", "closed")
        or not isinstance(issue.get("labels"), list)
    ):
        raise _LinkFailure("ISSUE_METADATA_INVALID")
    labels: list[str] = []
    for label in issue["labels"]:
        name = (
            label
            if isinstance(label, str)
            else label.get("name")
            if isinstance(label, dict)
            else None
        )
        if not isinstance(name, str) or not name:
            raise _LinkFailure("ISSUE_LABEL_INVALID")
        labels.append(name)
    values = {key: issue[key] for key in ("title", "body", "state")}
    values["labels"] = sorted(labels)
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _identity(value: Any, number: int, repository: str) -> bool:
    return (
        isinstance(value, dict)
        and type(value.get("id")) is int
        and value["id"] > 0
        and type(value.get("number")) is int
        and value["number"] == number
        and isinstance(value.get("repository_url"), str)
        and value["repository_url"].rstrip("/").casefold()
        == ("https://api.github.com/repos/" + repository).casefold()
        and "pull_request" not in value
    )


def _read_issue(prefix: str, number: int, repository: str) -> dict[str, Any]:
    code, value = _request("GET", prefix + str(number))
    if code != 200:
        raise _LinkFailure("ISSUE_READ_FAILED", code)
    if not _identity(value, number, repository):
        raise _LinkFailure("ISSUE_IDENTITY_INVALID", code, definitive=True)
    return value


def _rows(path: str, repository: str) -> list[dict[str, Any]]:
    result = []
    identifiers = set()
    for page in range(1, 21):
        code, rows = _request("GET", f"{path}?per_page=100&page={page}")
        if code != 200 or not isinstance(rows, list) or len(rows) > 100:
            raise _LinkFailure("RELATION_READ_FAILED", code)
        for row in rows:
            if (
                not isinstance(row, dict)
                or type(row.get("id")) is not int
                or row["id"] <= 0
                or type(row.get("number")) is not int
                or row["number"] <= 0
                or not _identity(row, row["number"], repository)
                or row["id"] in identifiers
            ):
                raise _LinkFailure("RELATION_ROW_INVALID", code)
            identifiers.add(row["id"])
            result.append(row)
        if len(rows) < 100:
            return result
    raise _LinkFailure("PAGINATION_LIMIT")


def _relation(
    path: str,
    prefix: str,
    kind: str,
    source: dict[str, Any],
    target: dict[str, Any],
    repository: str,
) -> bool:
    found = False
    for row in _rows(path, repository):
        if row["id"] == target["id"]:
            if not _identity(row, target["number"], repository):
                raise _LinkFailure("RELATION_IDENTITY_INVALID")
            found = True
    if kind == "sub-issue":
        code, parent = _request("GET", prefix + str(target["number"]) + "/parent")
        if code == 404:
            if found:
                raise _LinkFailure("INCONSISTENT_PARENT_READ", code)
            return False
        if code != 200 or not isinstance(parent, dict):
            raise _LinkFailure("PARENT_READ_FAILED", code)
        if parent.get("id") != source["id"]:
            raise _LinkFailure("PARENT_CONFLICT", code, definitive=True)
        if not _identity(parent, source["number"], repository) or not found:
            raise _LinkFailure("INCONSISTENT_PARENT_READ", code)
    return found


def ensure_link(kind: str, repository: str, issue: int, related: int) -> dict[str, Any]:
    """Ensure an explicitly requested relation; at most one POST, no issue creation."""
    if (
        kind not in ("sub-issue", "blocked-by")
        or not isinstance(repository, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repository) is None
        or repository.split("/")[-1] in (".", "..")
        or type(issue) is not int
        or type(related) is not int
        or issue <= 0
        or related <= 0
        or issue == related
    ):
        return {"status": "FAIL", "error": "INVALID_REQUEST", "write_attempted": False}
    result: dict[str, Any] = {
        "status": "BLOCKED",
        "repository": repository,
        "kind": kind,
        "issue": issue,
        "related": related,
        "write_attempted": False,
        "relation_verified": False,
        "metadata_unchanged": None,
        "changed": None,
    }
    try:
        auth = subprocess.run(
            ["gh", "auth", "status", "-h", "github.com"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {**result, "error": "AUTH_UNAVAILABLE"}
    if auth.returncode:
        return {**result, "error": "AUTH_REQUIRED"}
    prefix = f"repos/{repository}/issues/"
    path = (
        prefix
        + str(issue)
        + ("/sub_issues" if kind == "sub-issue" else "/dependencies/blocked_by")
    )
    try:
        source = _read_issue(prefix, issue, repository)
        target = _read_issue(prefix, related, repository)
        if source["id"] == target["id"]:
            raise _LinkFailure("ISSUE_ID_COLLISION", definitive=True)
        result.update(issue_id=source["id"], related_id=target["id"])
        before = _metadata(source)
        present = _relation(path, prefix, kind, source, target, repository)
    except _LinkFailure as error:
        return {
            **result,
            "status": "FAIL" if error.definitive else "BLOCKED",
            "error": error.code,
            "http_status": error.http_status,
        }
    write_problem: tuple[str, str] | None = None
    write_code = None
    if not present:
        result["write_attempted"] = True
        fields = (
            {"sub_issue_id": target["id"], "replace_parent": False}
            if kind == "sub-issue"
            else {"issue_id": target["id"]}
        )
        write_code, _ = _request("POST", path, fields)
        result["write_http_status"] = write_code
        if write_code is None:
            write_problem = ("UNKNOWN", "WRITE_OUTCOME_UNKNOWN")
        elif write_code not in (200, 201, 204):
            write_problem = ("FAIL", "WRITE_HTTP_ERROR")
    definitive = False
    try:
        result["relation_verified"] = _relation(
            path, prefix, kind, source, target, repository
        )
        after = _read_issue(prefix, issue, repository)
        if after["id"] != source["id"]:
            raise _LinkFailure("ISSUE_ID_CHANGED", definitive=True)
        result["metadata_unchanged"] = _metadata(after) == before
        if not result["metadata_unchanged"]:
            raise _LinkFailure("ISSUE_METADATA_CHANGED", definitive=True)
        result["metadata_sha256"] = before
        if not result["relation_verified"]:
            raise _LinkFailure("RELATION_NOT_CONFIRMED")
        result.update(status="PASS", changed=result["write_attempted"])
    except _LinkFailure as error:
        definitive = error.definitive
        result.update(
            status="FAIL"
            if definitive
            else "UNKNOWN"
            if result["write_attempted"]
            else "BLOCKED",
            error=error.code,
            http_status=error.http_status,
        )
    if write_problem and not definitive:
        result.update(
            status=write_problem[0],
            error=write_problem[1],
            http_status=write_code,
            changed=None,
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_subparsers(dest="kind", required=True)
    sub = operations.add_parser("sub-issue")
    sub.add_argument("--repo", required=True)
    sub.add_argument("--parent", required=True, type=int)
    sub.add_argument("--child", required=True, type=int)
    blocked = operations.add_parser("blocked-by")
    blocked.add_argument("--repo", required=True)
    blocked.add_argument("--issue", required=True, type=int)
    blocked.add_argument("--blocker", required=True, type=int)
    args = parser.parse_args(argv)
    source, target = (
        (args.parent, args.child)
        if args.kind == "sub-issue"
        else (args.issue, args.blocker)
    )
    result = ensure_link(args.kind, args.repo, source, target)
    print(json.dumps(result))
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2, "UNKNOWN": 3}.get(result["status"], 2)


if __name__ == "__main__":
    sys.exit(main())
