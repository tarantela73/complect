from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from infra.scripts.agent_tools.common import CommandResult
from infra.scripts.agent_tools.doctor import (
    Check,
    DoctorContext,
    HttpResponse,
    Status,
    exit_code,
    run_live_checks,
    run_offline_checks,
)


class DoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        base = Path(self.temp_dir.name)
        self.project_root = base / "project"
        self.codex_home = base / "codex"
        self.project_root.mkdir()
        self.codex_home.mkdir()
        self.commands: list[list[str]] = []
        self.http_calls: list[dict[str, object]] = []
        self.docker_stdout = "supermemory running"
        self._write_valid_fixture()

    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _write_json(path: Path, payload: dict[str, object]) -> None:
        DoctorTests._write(path, json.dumps(payload))

    def _write_valid_fixture(self) -> None:
        self._write(
            self.codex_home / "config.toml",
            """
[features]
multi_agent = true
[mcp_servers.openaiDeveloperDocs]
command = "docs"
[mcp_servers.playwright]
command = "playwright"
""",
        )
        self._write(
            self.project_root / ".codex/config.toml",
            """
[agents]
enabled = true
max_concurrent_threads_per_session = 3
""",
        )
        for role in ("complect-explorer", "complect-reviewer", "complect-verifier"):
            self._write(self.project_root / f".codex/agents/{role}.toml", f'name = "{role.replace("-", "_")}"')
        self._write_json(
            self.codex_home / "hooks.json",
            {"hooks": {"SessionStart": [{}], "UserPromptSubmit": [{}], "Stop": [{}]}},
        )
        self._write_json(
            self.codex_home / "supermemory.json",
            {"autoRecallEveryPrompt": False, "captureEveryNTurns": 0},
        )
        self._write_json(
            self.project_root / ".claude/.supermemory-claude/config.json",
            {
                "baseUrl": "http://127.0.0.1:6767",
                "repoContainerTag": "complect:task:issue-142",
                "personalContainerTag": "complect:project",
            },
        )
        self._write_json(
            self.project_root / ".claude/.supermemory-claude/task-state.json",
            {
                "version": 1,
                "taskKey": "issue-142",
                "containerTag": "complect:task:issue-142",
                "status": "active",
            },
        )
        for plugin in ("github", "superpowers", "product-design"):
            self._write_json(
                self.codex_home / f"plugins/cache/remote/{plugin}/1/.codex-plugin/plugin.json",
                {"name": plugin},
            )

    def runner(self, command: list[str], cwd: Path | None) -> CommandResult:
        self.commands.append(list(command))
        if command == ["codex", "mcp", "list"]:
            return CommandResult(0, "openaiDeveloperDocs enabled\nplaywright enabled\n", "")
        if command == ["git", "ls-files", "-z"]:
            return CommandResult(0, ".codex/config.toml\0", "")
        if command == ["gh", "auth", "status"]:
            return CommandResult(127, "", "")
        if command[:2] == ["docker", "compose"]:
            return CommandResult(0, self.docker_stdout, "")
        raise AssertionError(f"unexpected command: {command}")

    def http_probe(self, **kwargs: object) -> HttpResponse:
        self.http_calls.append(dict(kwargs))
        if kwargs["url"].endswith("/v4/search"):
            return HttpResponse(200, {"results": [{"id": "redacted"}]})
        return HttpResponse(200)

    def context(self, *, environ: dict[str, str] | None = None) -> DoctorContext:
        return DoctorContext(
            project_root=self.project_root,
            codex_home=self.codex_home,
            environ=environ or {},
            runner=self.runner,
            http_probe=self.http_probe,
        )

    def test_exit_code_fails_only_on_fail(self) -> None:
        self.assertEqual(exit_code([Check("a", Status.PASS, "ok")]), 0)
        self.assertEqual(exit_code([Check("a", Status.WARN, "optional")]), 0)
        self.assertEqual(exit_code([Check("a", Status.FAIL, "broken")]), 1)

    def test_closed_task_is_fail(self) -> None:
        state_path = self.project_root / ".claude/.supermemory-claude/task-state.json"
        self._write_json(
            state_path,
            {
                "version": 1,
                "taskKey": "issue-142",
                "containerTag": "complect:task:issue-142",
                "status": "closed",
            },
        )

        checks = run_offline_checks(self.context())

        task_check = next(check for check in checks if check.name == "task-state")
        self.assertEqual(task_check.status, Status.FAIL)
        self.assertIn("agent-task-start", task_check.detail)

    def test_reports_actual_cadence_and_stop_hook(self) -> None:
        checks = run_offline_checks(self.context())

        cadence = next(check for check in checks if check.name == "memory-cadence")

        self.assertEqual(cadence.status, Status.PASS)
        self.assertEqual(
            cadence.detail,
            "autoRecallEveryPrompt=false captureEveryNTurns=0 stop_hook=present",
        )

    def test_mcp_plugins_and_missing_github_cli_are_classified(self) -> None:
        checks = run_offline_checks(self.context())
        by_name = {check.name: check for check in checks}

        self.assertEqual(by_name["mcp-openaiDeveloperDocs"].status, Status.PASS)
        self.assertEqual(by_name["mcp-playwright"].status, Status.PASS)
        self.assertEqual(by_name["plugin-github"].status, Status.PASS)
        self.assertEqual(by_name["plugin-superpowers"].status, Status.PASS)
        self.assertEqual(by_name["plugin-product-design"].status, Status.PASS)
        self.assertEqual(
            by_name["github-cli"],
            Check("github-cli", Status.WARN, "gh is not installed; connector availability is session-scoped"),
        )

    def test_missing_required_plugin_fails_and_optional_plugin_warns(self) -> None:
        github = next((self.codex_home / "plugins").glob("cache/**/github/*/.codex-plugin/plugin.json"))
        product_design = next(
            (self.codex_home / "plugins").glob("cache/**/product-design/*/.codex-plugin/plugin.json")
        )
        github.unlink()
        product_design.unlink()

        by_name = {check.name: check for check in run_offline_checks(self.context())}

        self.assertEqual(by_name["plugin-github"].status, Status.FAIL)
        self.assertEqual(by_name["plugin-product-design"].status, Status.WARN)

    def test_live_checks_use_separate_scoped_search_payloads(self) -> None:
        checks = run_live_checks(self.context(environ={"SUPERMEMORY_CODEX_API_KEY": "not-printed"}))

        self.assertTrue(all(check.status is Status.PASS for check in checks))
        self.assertEqual(self.http_calls[0]["url"], "http://127.0.0.1:8090/health")
        self.assertEqual(self.http_calls[1]["url"], "http://127.0.0.1:6767/")
        search_calls = [call for call in self.http_calls if str(call["url"]).endswith("/v4/search")]
        self.assertEqual(
            [call["payload"]["containerTag"] for call in search_calls],  # type: ignore[index]
            ["complect:project", "complect:task:issue-142"],
        )
        self.assertIn(
            ["docker", "compose", "-p", "complect", "-f", "docker-compose.dev.yml", "ps", "supermemory"],
            self.commands,
        )
        self.assertTrue(all("count=1" in check.detail for check in checks if check.name.startswith("search-")))

    def test_live_checks_fail_without_api_key_without_probing_search(self) -> None:
        checks = run_live_checks(self.context())

        key_check = next(check for check in checks if check.name == "supermemory-auth")
        self.assertEqual(key_check.status, Status.FAIL)
        self.assertFalse(any(str(call["url"]).endswith("/v4/search") for call in self.http_calls))

    def test_live_checks_fail_when_compose_service_is_not_listed(self) -> None:
        self.docker_stdout = "NAME IMAGE COMMAND SERVICE CREATED STATUS PORTS"

        checks = run_live_checks(self.context(environ={"SUPERMEMORY_CODEX_API_KEY": "not-printed"}))

        container_check = next(check for check in checks if check.name == "supermemory-container")
        self.assertEqual(container_check.status, Status.FAIL)

    def test_live_checks_reject_non_loopback_supermemory_url_before_http(self) -> None:
        config_path = self.project_root / ".claude/.supermemory-claude/config.json"
        self._write_json(
            config_path,
            {
                "baseUrl": "https://memory.example.test",
                "repoContainerTag": "complect:task:issue-142",
                "personalContainerTag": "complect:project",
            },
        )

        checks = run_live_checks(self.context(environ={"SUPERMEMORY_CODEX_API_KEY": "not-printed"}))

        url_check = next(check for check in checks if check.name == "supermemory-url")
        self.assertEqual(url_check.status, Status.FAIL)
        self.assertFalse(any("memory.example.test" in str(call["url"]) for call in self.http_calls))

    def test_malformed_task_state_fails_offline_and_is_not_searched_live(self) -> None:
        config_path = self.project_root / ".claude/.supermemory-claude/config.json"
        state_path = self.project_root / ".claude/.supermemory-claude/task-state.json"
        self._write_json(
            config_path,
            {
                "baseUrl": "http://127.0.0.1:6767",
                "repoContainerTag": "other:secret",
                "personalContainerTag": "complect:project",
            },
        )
        self._write_json(
            state_path,
            {
                "version": 1,
                "taskKey": "issue-142",
                "containerTag": "other:secret",
                "status": "active",
            },
        )

        offline = {check.name: check for check in run_offline_checks(self.context())}
        live = run_live_checks(
            self.context(environ={"SUPERMEMORY_CODEX_API_KEY": "not-printed"})
        )

        self.assertEqual(offline["task-state"].status, Status.FAIL)
        self.assertEqual(offline["memory-scope"].status, Status.FAIL)
        self.assertFalse(
            any(
                str(call["url"]).endswith("/v4/search")
                and call.get("payload", {}).get("containerTag") == "other:secret"  # type: ignore[union-attr]
                for call in self.http_calls
            )
        )
        self.assertEqual(
            next(check for check in live if check.name == "search-task").status,
            Status.FAIL,
        )


if __name__ == "__main__":
    unittest.main()
