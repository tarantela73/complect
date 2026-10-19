"""Contract tests for the offline infrastructure quality runner."""

from __future__ import annotations

import json
import contextlib
import io
import subprocess
import unittest
from unittest.mock import patch

from infra.scripts import run_infra_quality


class InfraQualityTests(unittest.TestCase):
    def test_required_evidence_files_are_selected_by_quality_commands(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "", "")
        output = io.StringIO()
        with patch.object(run_infra_quality, "_tool", side_effect=lambda name: f"/tools/{name}"), patch.object(
            run_infra_quality, "_run", return_value=completed
        ), contextlib.redirect_stdout(output):
            self.assertEqual(run_infra_quality.run(), 0)

        receipt = json.loads(output.getvalue())
        commands = {check["name"]: check["command"] for check in receipt["checks"]}
        for tool, paths in run_infra_quality.REQUIRED_SCOPE.items():
            for path in paths:
                with self.subTest(tool=tool, path=path):
                    self.assertEqual(commands[tool].count(path), 1)

    def test_missing_required_ruff_implementation_fails_before_checks(self) -> None:
        original = run_infra_quality.RUFF_SCOPE
        run_infra_quality.RUFF_SCOPE = tuple(
            path for path in original if path != "infra/scripts/ci_audit_evidence.py"
        )
        try:
            with patch.object(run_infra_quality, "_tool", side_effect=lambda name: f"/tools/{name}"), patch.object(
                run_infra_quality, "_run"
            ) as run, patch("builtins.print") as output:
                self.assertEqual(run_infra_quality.run(), 1)
            run.assert_not_called()
            receipt = json.loads(output.call_args.args[0])
            self.assertEqual(receipt["error"], "REQUIRED_SCOPE_MISSING")
        finally:
            run_infra_quality.RUFF_SCOPE = original

    def test_missing_required_ruff_test_fails_before_checks(self) -> None:
        original = run_infra_quality.RUFF_SCOPE
        run_infra_quality.RUFF_SCOPE = tuple(
            path
            for path in original
            if path != "infra/scripts/tests/test_ci_audit_evidence.py"
        )
        try:
            with patch.object(run_infra_quality, "_tool", side_effect=lambda name: f"/tools/{name}"), patch.object(
                run_infra_quality, "_run"
            ) as run:
                self.assertEqual(run_infra_quality.run(), 1)
            run.assert_not_called()
        finally:
            run_infra_quality.RUFF_SCOPE = original

    def test_missing_required_mypy_implementation_fails_before_checks(self) -> None:
        original = run_infra_quality.DEFAULT_SCOPE
        reduced = tuple(
            path for path in original if path != "infra/scripts/ci_audit_evidence.py"
        )
        try:
            with patch.object(run_infra_quality, "_tool", side_effect=lambda name: f"/tools/{name}"), patch.object(
                run_infra_quality, "_run"
            ) as run:
                self.assertEqual(run_infra_quality.run(scope=reduced), 1)
            run.assert_not_called()
        finally:
            run_infra_quality.DEFAULT_SCOPE = original

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
