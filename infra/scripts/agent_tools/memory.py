from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from .common import NoRedirectHandler, validate_container_tag, validate_loopback_http_url
from .doctor import HttpProbe, HttpResponse

ALLOWED_KINDS = {"decision", "finding", "feedback", "handoff", "reference"}
CUSTOM_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class MemoryHttpError(RuntimeError):
    pass


def build_search_payload(container_tag: str, query: str, limit: int) -> dict[str, object]:
    validate_container_tag(container_tag)
    if not query.strip():
        raise ValueError("query must not be empty")
    if limit < 1:
        raise ValueError("limit must be positive")
    return {
        "q": query,
        "containerTag": container_tag,
        "limit": limit,
        "searchMode": "hybrid",
    }


def validate_save_scope(container_tag: str, task_state: Mapping[str, object] | None) -> tuple[str, str | None]:
    scope, task_key = validate_container_tag(container_tag)
    if scope == "task":
        active_match = (
            task_state is not None
            and task_state.get("status") == "active"
            and task_state.get("taskKey") == task_key
            and task_state.get("containerTag") == container_tag
        )
        if not active_match:
            raise ValueError("task save requires the matching active task container")
    return scope, task_key


def build_save_payload(
    container_tag: str,
    custom_id: str,
    kind: str,
    content: str,
) -> dict[str, object]:
    scope, task_key = validate_container_tag(container_tag)
    if not CUSTOM_ID_PATTERN.fullmatch(custom_id):
        raise ValueError("custom-id must contain only letters, digits, dot, underscore or hyphen")
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(sorted(ALLOWED_KINDS))}")
    metadata: dict[str, object] = {
        "project": "complect",
        "scope": scope,
        "kind": kind,
    }
    if task_key is not None:
        metadata["task"] = task_key
    memory = {
        "content": content,
        "customId": custom_id,
        "isStatic": scope == "project",
        "metadata": metadata,
    }
    return {"containerTag": container_tag, "memories": [memory]}


def _contains_confirmed_custom_id(value: object, custom_id: str) -> bool:
    if isinstance(value, dict):
        if value.get("customId") == custom_id:
            return True
        return any(_contains_confirmed_custom_id(item, custom_id) for item in value.values())
    if isinstance(value, list):
        return any(_contains_confirmed_custom_id(item, custom_id) for item in value)
    return False


class SupermemoryClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        http_probe: HttpProbe,
        output: TextIO,
        custom_id_contract_confirmed: bool = False,
    ) -> None:
        self.base_url = validate_loopback_http_url(base_url)
        self.api_key = api_key
        self.http_probe = http_probe
        self.output = output
        self.custom_id_contract_confirmed = custom_id_contract_confirmed

    def _post(self, endpoint: str, container_tag: str, payload: Mapping[str, object]) -> HttpResponse:
        response = self.http_probe(
            method="POST",
            url=f"{self.base_url}{endpoint}",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout=5.0,
        )
        if not 200 <= response.status < 300:
            raise MemoryHttpError(
                f"endpoint={endpoint} status={response.status} container={container_tag}"
            )
        return response

    def search(self, container_tag: str, query: str, limit: int) -> int:
        payload = build_search_payload(container_tag, query, limit)
        response = self._post("/v4/search", container_tag, payload)
        body = response.json_body or {}
        results = body.get("results", [])
        count = len(results) if isinstance(results, list) else 0
        print(f"search container={container_tag} count={count}", file=self.output)
        return count

    def save(
        self,
        container_tag: str,
        custom_id: str,
        kind: str,
        content: str,
        *,
        task_state: Mapping[str, object] | None,
    ) -> str:
        validate_save_scope(container_tag, task_state)
        if not self.custom_id_contract_confirmed:
            raise ValueError("customId contract is not confirmed; save is disabled")
        payload = build_save_payload(container_tag, custom_id, kind, content)
        response = self._post("/v4/memories", container_tag, payload)
        if not _contains_confirmed_custom_id(response.json_body, custom_id):
            raise ValueError("customId contract is not confirmed; save is disabled")
        print(f"saved custom_id={custom_id} container={container_tag}", file=self.output)
        return custom_id


def _urllib_probe(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str] | None = None,
    payload: Mapping[str, object] | None = None,
    timeout: float = 5.0,
) -> HttpResponse:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
            return HttpResponse(response.status, body if isinstance(body, dict) else None)
    except urllib.error.HTTPError as error:
        return HttpResponse(error.code)


def _read_task_state(root: Path) -> Mapping[str, object]:
    path = root / ".claude/.supermemory-claude/task-state.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("task state must be a JSON object")
    return payload


def _git_root() -> Path:
    import subprocess

    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Use local Supermemory with one explicit Complect container.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("--container", required=True)
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--limit", type=int, default=5)

    save_parser = subparsers.add_parser("save")
    save_parser.add_argument("--container", required=True)
    save_parser.add_argument("--custom-id", required=True)
    save_parser.add_argument("--kind", required=True, choices=sorted(ALLOWED_KINDS))
    save_parser.add_argument("--content-file", required=True)
    args = parser.parse_args()

    api_key = os.environ.get("SUPERMEMORY_CODEX_API_KEY")
    if not api_key:
        parser.exit(2, "error: SUPERMEMORY_CODEX_API_KEY is not set\n")
    client = SupermemoryClient(
        base_url=os.environ.get("SUPERMEMORY_API_URL", "http://127.0.0.1:6767"),
        api_key=api_key,
        http_probe=_urllib_probe,
        output=sys.stdout,
    )
    try:
        if args.command == "search":
            client.search(args.container, args.query, args.limit)
        else:
            content = (
                sys.stdin.read()
                if args.content_file == "-"
                else Path(args.content_file).read_text(encoding="utf-8")
            )
            scope, _ = validate_container_tag(args.container)
            task_state = _read_task_state(_git_root()) if scope == "task" else None
            client.save(
                args.container,
                args.custom_id,
                args.kind,
                content,
                task_state=task_state,
            )
    except (MemoryHttpError, OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
        parser.exit(1, f"error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
