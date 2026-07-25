from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

TASK_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PROJECT_CONTAINER = "complect:project"
TASK_CONTAINER_PREFIX = "complect:task:"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str], Path | None], CommandResult]


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: object,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        return None


def validate_task_key(value: str) -> str:
    if not TASK_KEY_PATTERN.fullmatch(value):
        raise ValueError("task-key must match ^[a-z0-9]+(?:-[a-z0-9]+)*$")
    return value


def validate_container_tag(value: str) -> tuple[str, str | None]:
    if value == PROJECT_CONTAINER:
        return ("project", None)
    if value.startswith(TASK_CONTAINER_PREFIX):
        task_key = validate_task_key(value.removeprefix(TASK_CONTAINER_PREFIX))
        return ("task", task_key)
    raise ValueError("container must be complect:project or complect:task:<task-key>")


def validate_loopback_http_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("base URL must be a valid loopback HTTP URL") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port is None
    ):
        raise ValueError("base URL must be a loopback HTTP URL with an explicit port")
    return value.rstrip("/")


def atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(payload, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
