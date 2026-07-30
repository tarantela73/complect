from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from .common import (
    DEFAULT_SUPERMEMORY_URL,
    PROJECT_CONTAINER,
    TASK_CONTAINER_PREFIX,
    atomic_write_json,
    durable_unlink,
    validate_loopback_http_url,
    validate_task_key,
    validate_task_state,
)

DEFAULT_CONFIG_PATH = Path(".claude/.supermemory-claude/config.json")
DEFAULT_STATE_PATH = Path(".claude/.supermemory-claude/task-state.json")
TRANSITION_FILENAME = "task-transition.json"


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _closed_task_keys(state: Mapping[str, object]) -> list[str]:
    value = state.get("closedTaskKeys", [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("closedTaskKeys must be a list of task keys")
    return list(value)


def _validate_memory_config(config: dict[str, object]) -> None:
    base_url = config.get("baseUrl", DEFAULT_SUPERMEMORY_URL)
    if not isinstance(base_url, str):
        raise ValueError("Supermemory baseUrl must be configured")
    validate_loopback_http_url(base_url)
    personal_container = config.get("personalContainerTag")
    if personal_container not in {None, PROJECT_CONTAINER}:
        raise ValueError("personalContainerTag must be complect:project")


def _transition_path(state_path: Path) -> Path:
    return state_path.with_name(TRANSITION_FILENAME)


def _transition_state(payload: Mapping[str, object]) -> dict[str, object]:
    if payload.get("version") != 1 or payload.get("operation") != "start":
        raise ValueError("task transition is invalid")
    state = payload.get("state")
    if not isinstance(state, dict):
        raise ValueError("task transition state is invalid")
    validate_task_state(state, require_active=True)
    return dict(state)


def _complete_start_transition(
    config_path: Path,
    state_path: Path,
    config: dict[str, object],
    state: dict[str, object],
) -> dict[str, object]:
    _, container_tag, _ = validate_task_state(state, require_active=True)
    config["repoContainerTag"] = container_tag
    config.setdefault("personalContainerTag", PROJECT_CONTAINER)
    atomic_write_json(config_path, config)
    atomic_write_json(state_path, state)
    durable_unlink(_transition_path(state_path))
    return state


def start_task(
    config_path: Path,
    state_path: Path,
    task_key: str,
    now: datetime,
) -> dict[str, object]:
    task_key = validate_task_key(task_key)
    config = _read_json(config_path)
    _validate_memory_config(config)
    transition_path = _transition_path(state_path)
    pending_transition = _read_json(transition_path)
    if pending_transition:
        pending_state = _transition_state(pending_transition)
        pending_task_key, _, _ = validate_task_state(
            pending_state,
            require_active=True,
        )
        if pending_task_key != task_key:
            raise ValueError(f"recover pending task {pending_task_key} before starting another task-key")
        return _complete_start_transition(
            config_path,
            state_path,
            config,
            pending_state,
        )

    previous_state = _read_json(state_path)
    closed_task_keys = _closed_task_keys(previous_state)
    if previous_state.get("status") == "active":
        active_task_key, active_container, _ = validate_task_state(
            previous_state,
            require_active=True,
        )
        if active_task_key != task_key:
            raise ValueError("close active task before starting a different task-key")
        if config.get("repoContainerTag") != active_container:
            raise ValueError("Supermemory repoContainerTag does not match active task state")
        return previous_state
    if task_key in closed_task_keys:
        raise ValueError(f"task {task_key} is already closed and cannot be reused")

    container_tag = f"{TASK_CONTAINER_PREFIX}{task_key}"
    state: dict[str, object] = {
        "version": 1,
        "taskKey": task_key,
        "containerTag": container_tag,
        "status": "active",
        "startedAt": now.isoformat(),
        "closedAt": None,
        "closedTaskKeys": closed_task_keys,
    }
    transition = {
        "version": 1,
        "operation": "start",
        "state": state,
    }
    atomic_write_json(transition_path, transition)
    return _complete_start_transition(config_path, state_path, config, state)


def read_task_status(config_path: Path, state_path: Path) -> dict[str, object]:
    state = _read_json(state_path)
    if not state:
        raise ValueError("task state does not exist; start a task first")
    _, container_tag, _ = validate_task_state(state, require_active=False)
    config = _read_json(config_path)
    if config.get("repoContainerTag") != container_tag:
        raise ValueError("Supermemory repoContainerTag does not match task state")
    return state


def close_task(config_path: Path, state_path: Path, now: datetime) -> dict[str, object]:
    state = read_task_status(config_path, state_path)
    if state.get("status") != "active":
        raise ValueError("no active task to close")
    task_key = state.get("taskKey")
    if not isinstance(task_key, str):
        raise ValueError("task state has no valid taskKey")
    closed_task_keys = _closed_task_keys(state)
    if task_key not in closed_task_keys:
        closed_task_keys.append(task_key)
    state["status"] = "closed"
    state["closedAt"] = now.isoformat()
    state["closedTaskKeys"] = closed_task_keys
    atomic_write_json(state_path, state)
    return state


def _git_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip())


def _print_status(state: Mapping[str, object]) -> None:
    print(f"task={state.get('taskKey')} container={state.get('containerTag')} status={state.get('status')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the local Complect agent task lifecycle.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("task_key")
    subparsers.add_parser("status")
    subparsers.add_parser("close")
    args = parser.parse_args()

    root = _git_root()
    config_path = root / DEFAULT_CONFIG_PATH
    state_path = root / DEFAULT_STATE_PATH
    now = datetime.now(UTC)
    try:
        if args.command == "start":
            state = start_task(config_path, state_path, args.task_key, now)
        elif args.command == "close":
            state = close_task(config_path, state_path, now)
        else:
            state = read_task_status(config_path, state_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, f"error: {error}\n")
    _print_status(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
