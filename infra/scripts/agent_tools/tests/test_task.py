from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from infra.scripts.agent_tools.common import (
    NoRedirectHandler,
    atomic_write_json,
    validate_container_tag,
    validate_task_key,
)
from infra.scripts.agent_tools import common
from infra.scripts.agent_tools.task import close_task, read_task_status, start_task


class TaskKeyValidationTests(unittest.TestCase):
    def test_accepts_canonical_key(self) -> None:
        self.assertEqual(validate_task_key("issue-142"), "issue-142")

    def test_rejects_non_canonical_keys(self) -> None:
        for value in ("Issue-142", "issue_142", "-issue", "issue-", ""):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_task_key(value)

    def test_container_validation_returns_scope_and_task(self) -> None:
        self.assertEqual(validate_container_tag("complect:project"), ("project", None))
        self.assertEqual(
            validate_container_tag("complect:task:issue-142"),
            ("task", "issue-142"),
        )

    def test_http_redirects_are_disabled_for_authenticated_local_requests(self) -> None:
        handler = NoRedirectHandler()

        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://example.test"))


class TaskLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.config_path = root / "config.json"
        self.state_path = root / "task-state.json"
        self.now = datetime(2026, 7, 25, tzinfo=UTC)
        self.write_json(
            self.config_path,
            {
                "baseUrl": "http://127.0.0.1:6767",
                "personalContainerTag": "complect:project",
            },
        )

    @staticmethod
    def write_json(path: Path, payload: dict[str, object]) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def read_json(path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def test_start_preserves_unknown_config_fields(self) -> None:
        self.write_json(
            self.config_path,
            {
                "baseUrl": "http://127.0.0.1:6767",
                "personalContainerTag": "complect:project",
                "signalExtraction": True,
            },
        )

        result = start_task(self.config_path, self.state_path, "issue-142", self.now)

        config = self.read_json(self.config_path)
        self.assertEqual(config["repoContainerTag"], "complect:task:issue-142")
        self.assertTrue(config["signalExtraction"])
        self.assertEqual(result["status"], "active")

    def test_start_sets_missing_project_read_container(self) -> None:
        self.write_json(self.config_path, {"baseUrl": "http://127.0.0.1:6767"})

        start_task(self.config_path, self.state_path, "issue-142", self.now)

        self.assertEqual(
            self.read_json(self.config_path)["personalContainerTag"],
            "complect:project",
        )

    def test_start_rejects_remote_url_and_noncanonical_project_container(self) -> None:
        unsafe_configs = (
            {
                "baseUrl": "http://attacker.example:6767",
                "personalContainerTag": "complect:project",
            },
            {
                "baseUrl": "http://127.0.0.1:6767",
                "personalContainerTag": "other:project",
            },
        )
        for config in unsafe_configs:
            with self.subTest(config=config):
                self.write_json(self.config_path, config)
                with self.assertRaises(ValueError):
                    start_task(self.config_path, self.state_path, "issue-142", self.now)
                self.assertFalse(self.state_path.exists())

    def test_closed_task_cannot_be_reused(self) -> None:
        start_task(self.config_path, self.state_path, "issue-142", self.now)
        close_task(self.config_path, self.state_path, self.now)

        with self.assertRaisesRegex(ValueError, "already closed"):
            start_task(self.config_path, self.state_path, "issue-142", self.now)

    def test_active_task_cannot_be_replaced_without_close(self) -> None:
        start_task(self.config_path, self.state_path, "issue-142", self.now)

        with self.assertRaisesRegex(ValueError, "close active task"):
            start_task(self.config_path, self.state_path, "issue-143", self.now)

        self.assertEqual(self.read_json(self.state_path)["taskKey"], "issue-142")
        self.assertEqual(
            self.read_json(self.config_path)["repoContainerTag"],
            "complect:task:issue-142",
        )

    def test_close_does_not_change_repo_container_tag(self) -> None:
        start_task(self.config_path, self.state_path, "issue-142", self.now)

        result = close_task(self.config_path, self.state_path, self.now)

        config = self.read_json(self.config_path)
        state = self.read_json(self.state_path)
        self.assertEqual(config["repoContainerTag"], "complect:task:issue-142")
        self.assertEqual(state["status"], "closed")
        self.assertEqual(state["closedTaskKeys"], ["issue-142"])
        self.assertEqual(result, state)

    def test_read_status_rejects_mismatched_config_and_state(self) -> None:
        start_task(self.config_path, self.state_path, "issue-142", self.now)
        config = self.read_json(self.config_path)
        config["repoContainerTag"] = "complect:task:issue-143"
        self.write_json(self.config_path, config)

        with self.assertRaisesRegex(ValueError, "does not match"):
            read_task_status(self.config_path, self.state_path)

    def test_atomic_write_keeps_original_when_replace_fails(self) -> None:
        self.write_json(self.config_path, {"original": True})

        with patch("infra.scripts.agent_tools.common.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                atomic_write_json(self.config_path, {"original": False})

        self.assertEqual(self.read_json(self.config_path), {"original": True})
        self.assertEqual(list(self.config_path.parent.glob(".config.json.*.tmp")), [])

    def test_start_recovers_when_state_write_fails_after_config_commit(self) -> None:
        self.write_json(
            self.config_path,
            {
                "baseUrl": "http://127.0.0.1:6767",
                "personalContainerTag": "complect:project",
                "repoContainerTag": "complect:task:old-task",
            },
        )

        def fail_state_write(path: Path, payload: dict[str, object]) -> None:
            if path == self.state_path:
                raise OSError("state write failed")
            common.atomic_write_json(path, payload)

        with patch(
            "infra.scripts.agent_tools.task.atomic_write_json",
            side_effect=fail_state_write,
        ):
            with self.assertRaisesRegex(OSError, "state write failed"):
                start_task(self.config_path, self.state_path, "new-task", self.now)

        self.assertEqual(
            self.read_json(self.config_path)["repoContainerTag"],
            "complect:task:new-task",
        )
        self.assertFalse(self.state_path.exists())
        self.assertTrue(self.state_path.with_name("task-transition.json").exists())

        recovered = start_task(self.config_path, self.state_path, "new-task", self.now)

        self.assertEqual(self.read_json(self.state_path), recovered)
        self.assertFalse(self.state_path.with_name("task-transition.json").exists())

    def test_start_recovers_when_config_write_fails_after_intent_commit(self) -> None:
        old_state = {
            "version": 1,
            "taskKey": "old-task",
            "containerTag": "complect:task:old-task",
            "status": "closed",
            "startedAt": self.now.isoformat(),
            "closedAt": self.now.isoformat(),
            "closedTaskKeys": ["old-task"],
        }
        old_config = {
            "baseUrl": "http://127.0.0.1:6767",
            "repoContainerTag": "complect:task:old-task",
            "personalContainerTag": "complect:project",
        }
        self.write_json(self.state_path, old_state)
        self.write_json(self.config_path, old_config)
        write_paths: list[Path] = []

        def fail_config_write(path: Path, payload: dict[str, object]) -> None:
            write_paths.append(path)
            if path == self.config_path:
                raise OSError("config write failed")
            common.atomic_write_json(path, payload)

        with patch(
            "infra.scripts.agent_tools.task.atomic_write_json",
            side_effect=fail_config_write,
        ):
            with self.assertRaisesRegex(OSError, "config write failed"):
                start_task(self.config_path, self.state_path, "new-task", self.now)

        self.assertEqual(self.read_json(self.state_path), old_state)
        self.assertEqual(self.read_json(self.config_path), old_config)
        self.assertEqual(
            write_paths,
            [self.state_path.with_name("task-transition.json"), self.config_path],
        )
        self.assertTrue(self.state_path.with_name("task-transition.json").exists())

        recovered = start_task(self.config_path, self.state_path, "new-task", self.now)

        self.assertEqual(self.read_json(self.state_path), recovered)
        self.assertEqual(
            self.read_json(self.config_path)["repoContainerTag"],
            "complect:task:new-task",
        )
        self.assertFalse(self.state_path.with_name("task-transition.json").exists())

    def test_start_recovers_after_process_death_between_config_and_state(self) -> None:
        class SimulatedProcessDeath(BaseException):
            pass

        original_write = common.atomic_write_json
        write_count = 0

        def die_before_state_commit(path: Path, payload: dict[str, object]) -> None:
            nonlocal write_count
            write_count += 1
            if write_count == 3:
                raise SimulatedProcessDeath
            original_write(path, payload)

        with patch(
            "infra.scripts.agent_tools.task.atomic_write_json",
            side_effect=die_before_state_commit,
        ):
            with self.assertRaises(SimulatedProcessDeath):
                start_task(self.config_path, self.state_path, "issue-142", self.now)

        self.assertEqual(
            self.read_json(self.config_path)["repoContainerTag"],
            "complect:task:issue-142",
        )
        self.assertFalse(self.state_path.exists())

        recovered = start_task(self.config_path, self.state_path, "issue-142", self.now)

        self.assertEqual(recovered["status"], "active")
        self.assertEqual(self.read_json(self.state_path), recovered)
        self.assertFalse(self.state_path.with_name("task-transition.json").exists())


if __name__ == "__main__":
    unittest.main()
