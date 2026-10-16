"""Contract tests for the offline infrastructure quality runner."""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from infra.scripts import run_infra_quality


class InfraQualityTests(unittest.TestCase):
    def test_commands_have_explicit_infra_scope_and_workflow(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "", "")
        commands: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return completed

        with patch.object(run_infra_quality, "_tool", side_effect=lambda name: f"/tools/{name}"), patch.object(
            run_infra_quality, "_run", side_effect=fake_run
        ):
            self.assertEqual(run_infra_quality.run(), 0)

        self.assertEqual(commands[0][1:], ["check", "--no-cache", *run_infra_quality.RUFF_SCOPE])
        self.assertEqual(
            commands[1][1:],
            [
                "--config-file",
                "infra/mypy.ini",
                "--ignore-missing-imports",
                "--explicit-package-bases",
                "--cache-dir",
                "/tmp/infra-mypy-cache",
                *run_infra_quality.DEFAULT_SCOPE,
            ],
        )
        self.assertEqual(commands[2][1:], [".github/workflows/infra-tests.yml"])

    def test_missing_tool_blocks_before_running_checks(self) -> None:
        with patch.object(run_infra_quality, "_tool", side_effect=RuntimeError("required offline tool is missing: mypy")):
            self.assertEqual(run_infra_quality.main([]), 2)

    def test_receipt_marks_static_failure(self) -> None:
        failed = subprocess.CompletedProcess([], 17, "", "ruff failure")
        with patch.object(run_infra_quality, "_tool", side_effect=lambda name: f"/tools/{name}"), patch.object(
            run_infra_quality, "_run", return_value=failed
        ), patch("builtins.print") as output:
            self.assertEqual(run_infra_quality.run(), 17)
        receipt = json.loads(output.call_args.args[0])
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(receipt["checks"][0]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
