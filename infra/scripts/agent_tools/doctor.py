from __future__ import annotations

import argparse
import json
import os
import subprocess
import tomllib
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from .common import CommandResult, CommandRunner, NoRedirectHandler, validate_loopback_http_url
from .secret_lint import run_secret_lint, tracked_candidate_paths


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


@dataclass(frozen=True)
class HttpResponse:
    status: int
    json_body: Mapping[str, object] | None = None


class HttpProbe(Protocol):
    def __call__(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        payload: Mapping[str, object] | None = None,
        timeout: float = 5.0,
    ) -> HttpResponse: ...


@dataclass(frozen=True)
class DoctorContext:
    project_root: Path
    codex_home: Path
    environ: Mapping[str, str]
    runner: CommandRunner
    http_probe: HttpProbe


def exit_code(checks: Sequence[Check]) -> int:
    return int(any(check.status is Status.FAIL for check in checks))


def _read_toml(path: Path) -> Mapping[str, object]:
    with path.open("rb") as source:
        return tomllib.load(source)


def _read_json(path: Path) -> Mapping[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("expected JSON object")
    return payload


def _safe_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def _parse_configuration(context: DoctorContext) -> tuple[list[Check], Mapping[str, object], Mapping[str, object]]:
    checks: list[Check] = []
    global_path = context.codex_home / "config.toml"
    project_path = context.project_root / ".codex/config.toml"
    try:
        global_config = _read_toml(global_path)
        checks.append(Check("global-config", Status.PASS, f"parsed {global_path}"))
    except (OSError, tomllib.TOMLDecodeError):
        global_config = {}
        checks.append(Check("global-config", Status.FAIL, f"cannot parse {global_path}"))
    try:
        project_config = _read_toml(project_path)
        checks.append(Check("project-config", Status.PASS, f"parsed {project_path}"))
    except (OSError, tomllib.TOMLDecodeError):
        project_config = {}
        checks.append(Check("project-config", Status.FAIL, f"cannot parse {project_path}"))

    features = _safe_mapping(global_config.get("features"))
    agents = _safe_mapping(project_config.get("agents"))
    multi_agent_ok = features.get("multi_agent") is True
    agent_limit_ok = agents.get("enabled") is True and agents.get("max_concurrent_threads_per_session") == 3
    checks.append(
        Check(
            "multi-agent",
            Status.PASS if multi_agent_ok and agent_limit_ok else Status.FAIL,
            "multi_agent=true project_agents=true max_threads=3"
            if multi_agent_ok and agent_limit_ok
            else "expected multi_agent=true and project agent limit 3",
        )
    )

    global_mcp = set(_safe_mapping(global_config.get("mcp_servers")))
    project_mcp = set(_safe_mapping(project_config.get("mcp_servers")))
    duplicate_mcp = sorted(global_mcp & project_mcp)
    checks.append(
        Check(
            "mcp-layering",
            Status.FAIL if duplicate_mcp else Status.PASS,
            "duplicate MCP ids detected" if duplicate_mcp else "no duplicated project MCP ids",
        )
    )
    return checks, global_config, project_config


def _check_roles(context: DoctorContext) -> list[Check]:
    expected = ("complect-explorer.toml", "complect-reviewer.toml", "complect-verifier.toml")
    checks: list[Check] = []
    for filename in expected:
        path = context.project_root / ".codex/agents" / filename
        try:
            _read_toml(path)
            checks.append(Check(f"agent-{path.stem}", Status.PASS, f"parsed {path}"))
        except (OSError, tomllib.TOMLDecodeError):
            checks.append(Check(f"agent-{path.stem}", Status.FAIL, f"cannot parse {path}"))
    return checks


def _check_memory_configuration(context: DoctorContext) -> list[Check]:
    hooks_path = context.codex_home / "hooks.json"
    cadence_path = context.codex_home / "supermemory.json"
    config_path = context.project_root / ".claude/.supermemory-claude/config.json"
    state_path = context.project_root / ".claude/.supermemory-claude/task-state.json"
    try:
        hooks = _safe_mapping(_read_json(hooks_path).get("hooks"))
    except (OSError, ValueError, json.JSONDecodeError):
        hooks = {}
    required_hooks = ("SessionStart", "UserPromptSubmit", "Stop")
    hooks_ok = all(isinstance(hooks.get(name), list) and hooks.get(name) for name in required_hooks)
    hook_check = Check(
        "memory-hooks",
        Status.PASS if hooks_ok else Status.FAIL,
        "SessionStart UserPromptSubmit Stop present" if hooks_ok else "required memory hooks are missing",
    )

    try:
        cadence = _read_json(cadence_path)
    except (OSError, ValueError, json.JSONDecodeError):
        cadence = {}
    cadence_ok = cadence.get("autoRecallEveryPrompt") is False and cadence.get("captureEveryNTurns") == 0
    cadence_detail = (
        f"autoRecallEveryPrompt={str(cadence.get('autoRecallEveryPrompt')).lower()} "
        f"captureEveryNTurns={cadence.get('captureEveryNTurns')} "
        f"stop_hook={'present' if bool(hooks.get('Stop')) else 'missing'}"
    )
    cadence_check = Check("memory-cadence", Status.PASS if cadence_ok and hooks_ok else Status.FAIL, cadence_detail)

    try:
        config = _read_json(config_path)
        state = _read_json(state_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return [
            hook_check,
            cadence_check,
            Check("task-state", Status.FAIL, "missing local task state; run make agent-task-start"),
            Check("memory-scope", Status.FAIL, "local memory scope is not configured"),
        ]

    active = state.get("status") == "active"
    matching = config.get("repoContainerTag") == state.get("containerTag")
    container = state.get("containerTag")
    if active and matching and isinstance(container, str):
        task_check = Check("task-state", Status.PASS, f"active {container}")
    elif not active:
        task_check = Check("task-state", Status.FAIL, "task is closed; run make agent-task-start with a unique key")
    else:
        task_check = Check("task-state", Status.FAIL, "repo container does not match task state")
    personal_ok = config.get("personalContainerTag") == "complect:project"
    scope_check = Check(
        "memory-scope",
        Status.PASS if personal_ok and matching else Status.FAIL,
        "project read scope and task write scope are isolated"
        if personal_ok and matching
        else "project and task memory scopes are inconsistent",
    )
    return [hook_check, cadence_check, task_check, scope_check]


def _check_mcp(context: DoctorContext) -> list[Check]:
    result = context.runner(["codex", "mcp", "list"], context.project_root)
    if result.returncode != 0:
        return [Check("mcp-list", Status.FAIL, "codex mcp list failed")]
    return [
        Check(
            f"mcp-{server}",
            Status.PASS if server in result.stdout else Status.FAIL,
            "enabled" if server in result.stdout else "not present in codex mcp list",
        )
        for server in ("openaiDeveloperDocs", "playwright")
    ]


def _check_plugins(context: DoctorContext) -> list[Check]:
    checks: list[Check] = []
    for plugin, missing_status in (
        ("github", Status.FAIL),
        ("superpowers", Status.FAIL),
        ("product-design", Status.WARN),
    ):
        manifests = list(
            (context.codex_home / "plugins").glob(f"cache/**/{plugin}/*/.codex-plugin/plugin.json")
        )
        valid = False
        for manifest in manifests:
            try:
                _read_json(manifest)
                valid = True
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        checks.append(
            Check(
                f"plugin-{plugin}",
                Status.PASS if valid else missing_status,
                "manifest present" if valid else "manifest missing",
            )
        )
    return checks


def _check_github_cli(context: DoctorContext) -> Check:
    result = context.runner(["gh", "auth", "status"], context.project_root)
    if result.returncode == 0:
        return Check("github-cli", Status.PASS, "gh is installed and authenticated")
    if result.returncode == 127:
        return Check("github-cli", Status.WARN, "gh is not installed; connector availability is session-scoped")
    return Check(
        "github-cli",
        Status.WARN,
        "gh is installed but not authenticated; connector availability is session-scoped",
    )


def _check_secrets(context: DoctorContext) -> Check:
    try:
        paths = tracked_candidate_paths(context.project_root, context.runner)
        findings = run_secret_lint(paths)
    except (OSError, RuntimeError, UnicodeError):
        return Check("secret-lint", Status.FAIL, "tracked-file scan could not complete")
    return Check(
        "secret-lint",
        Status.FAIL if findings else Status.PASS,
        f"findings={len(findings)} files={len(paths)}",
    )


def run_offline_checks(context: DoctorContext) -> list[Check]:
    checks, _, _ = _parse_configuration(context)
    checks.extend(_check_roles(context))
    checks.extend(_check_memory_configuration(context))
    checks.extend(_check_mcp(context))
    checks.extend(_check_plugins(context))
    checks.append(_check_github_cli(context))
    checks.append(_check_secrets(context))
    return checks


def _probe_check(context: DoctorContext, name: str, url: str) -> Check:
    try:
        response = context.http_probe(method="GET", url=url, timeout=5.0)
    except Exception:
        return Check(name, Status.FAIL, "unreachable")
    return Check(name, Status.PASS if response.status == 200 else Status.FAIL, f"status={response.status}")


def _search_check(context: DoctorContext, base_url: str, container_tag: str, api_key: str) -> Check:
    payload = {
        "q": "project scope task kind",
        "containerTag": container_tag,
        "limit": 1,
        "searchMode": "hybrid",
    }
    try:
        response = context.http_probe(
            method="POST",
            url=f"{base_url.rstrip('/')}/v4/search",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload=payload,
            timeout=5.0,
        )
    except Exception:
        return Check(f"search-{container_tag}", Status.FAIL, f"container={container_tag} unreachable")
    body = response.json_body or {}
    results = body.get("results", [])
    count = len(results) if isinstance(results, list) else 0
    status = Status.PASS if response.status == 200 else Status.FAIL
    return Check(f"search-{container_tag}", status, f"container={container_tag} count={count}")


def run_live_checks(context: DoctorContext) -> list[Check]:
    config_path = context.project_root / ".claude/.supermemory-claude/config.json"
    state_path = context.project_root / ".claude/.supermemory-claude/task-state.json"
    try:
        config = _read_json(config_path)
        state = _read_json(state_path)
    except (OSError, ValueError, json.JSONDecodeError):
        config = {}
        state = {}
    checks = [_probe_check(context, "qwen-health", "http://127.0.0.1:8090/health")]
    try:
        base_url = validate_loopback_http_url(
            str(config.get("baseUrl", "http://127.0.0.1:6767"))
        )
    except ValueError:
        checks.append(Check("supermemory-url", Status.FAIL, "base URL is not an allowed loopback HTTP endpoint"))
        return checks
    checks.extend(
        (
            Check("supermemory-url", Status.PASS, "loopback HTTP endpoint"),
            _probe_check(context, "supermemory-health", f"{base_url}/"),
        )
    )
    docker_result = context.runner(
        ["docker", "compose", "-p", "complect", "-f", "docker-compose.dev.yml", "ps", "supermemory"],
        context.project_root,
    )
    docker_ok = docker_result.returncode == 0 and "supermemory" in docker_result.stdout.lower()
    checks.append(
        Check(
            "supermemory-container",
            Status.PASS if docker_ok else Status.FAIL,
            "docker compose reports service" if docker_ok else "docker compose service is not running",
        )
    )
    api_key = context.environ.get("SUPERMEMORY_CODEX_API_KEY")
    if not api_key:
        checks.append(Check("supermemory-auth", Status.FAIL, "SUPERMEMORY_CODEX_API_KEY is not set"))
        return checks
    checks.append(Check("supermemory-auth", Status.PASS, "credential is available through environment"))
    task_container = state.get("containerTag")
    containers = ["complect:project"]
    if isinstance(task_container, str):
        containers.append(task_container)
    else:
        checks.append(Check("search-task", Status.FAIL, "active task container is unavailable"))
        return checks
    checks.extend(_search_check(context, base_url, container, api_key) for container in containers)
    return checks


def _subprocess_runner(command: Sequence[str], cwd: Path | None) -> CommandResult:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return CommandResult(127, "", "")
    return CommandResult(result.returncode, result.stdout, result.stderr)


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
            if url.endswith("/v4/search"):
                body = json.loads(response.read().decode("utf-8"))
                return HttpResponse(response.status, body if isinstance(body, dict) else None)
            return HttpResponse(response.status)
    except urllib.error.HTTPError as error:
        return HttpResponse(error.code)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Complect agent configuration and optional local services.")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root_result = _subprocess_runner(["git", "rev-parse", "--show-toplevel"], Path.cwd())
    if root_result.returncode != 0:
        parser.exit(2, "error: cannot determine Git root\n")
    context = DoctorContext(
        project_root=Path(root_result.stdout.strip()),
        codex_home=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
        environ=os.environ,
        runner=_subprocess_runner,
        http_probe=_urllib_probe,
    )
    checks = run_offline_checks(context)
    if args.live:
        checks.extend(run_live_checks(context))
    if args.json:
        print(json.dumps([{**asdict(check), "status": check.status.value} for check in checks], ensure_ascii=False))
    else:
        for check in checks:
            print(f"{check.status.value} {check.name} {check.detail}")
    return exit_code(checks)


if __name__ == "__main__":
    raise SystemExit(main())
