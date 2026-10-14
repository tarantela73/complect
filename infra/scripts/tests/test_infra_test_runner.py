from __future__ import annotations

import os
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infra.scripts import run_infra_tests


class InfraTestRunnerTests(unittest.TestCase):
    def _run_with_cleanup(self, removal, *, mode="unittest", test_exit=0, foreign=False):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            for destination in (output, output / "unittest", output / "pytest"):
                destination.mkdir(exist_ok=True)
                destination.joinpath("manifest.json").write_text('{"collected": 1, "passed": 1}')
            labels = {}
            commands = []

            def docker(command, **kwargs):
                commands.append(command)
                if command[1] == "create":
                    label = command[command.index("--label") + 1]
                    labels["complect.infra_test_run"] = label.split("=", 1)[1]
                    return subprocess.CompletedProcess(command, 0, "owned-container\n", "")
                if command[1] == "start":
                    return subprocess.CompletedProcess(command, test_exit, "test output", "test error")
                if command[1] == "inspect":
                    observed = {"complect.infra_test_run": "another-run"} if foreign else labels
                    resource = {"Id": "owned-container", "Config": {"Labels": observed}}
                    return subprocess.CompletedProcess(command, 0, json.dumps([resource]), "")
                self.assertEqual(command, ["docker", "rm", "-f", "owned-container"])
                if isinstance(removal, Exception):
                    raise removal
                return subprocess.CompletedProcess(command, removal)

            with patch.object(run_infra_tests, "_docker_endpoint_is_local"), patch(
                "infra.scripts.run_infra_tests.subprocess.run", side_effect=docker,
            ):
                code = run_infra_tests.run(mode, image="backend:test", output=output)
            receipts = {path.parent.name: json.loads(path.read_text()) for path in output.rglob("result.json")}
            return code, json.loads(output.joinpath("result.json").read_text()), receipts, commands

    def test_failed_removal_blocks_successful_run(self):
        code, receipt, _, commands = self._run_with_cleanup(23)
        self.assertEqual(code, 2)
        self.assertEqual(receipt["exit"], code)
        self.assertEqual(receipt["cleanup"], "BLOCKED_STAND")
        self.assertEqual(receipt["status"], "BLOCKED_STAND")
        self.assertIn("23", receipt["cleanup_error"])
        self.assertEqual([command[1] for command in commands], ["create", "start", "inspect", "rm"])

    def test_cleanup_exceptions_block_successful_run(self):
        for error in (OSError("removal unavailable"), subprocess.TimeoutExpired(["docker", "rm"], 60)):
            with self.subTest(error=type(error).__name__):
                code, receipt, _, _ = self._run_with_cleanup(error)
                self.assertEqual(code, 2)
                self.assertEqual(receipt["exit"], code)
                self.assertEqual(receipt["cleanup"], "BLOCKED_STAND")
                self.assertEqual(receipt["status"], "BLOCKED_STAND")
                self.assertEqual(receipt["cleanup_error"], str(error))

    def test_cleanup_failure_preserves_primary_test_failure(self):
        code, receipt, _, _ = self._run_with_cleanup(23, test_exit=17)
        self.assertEqual(code, 17)
        self.assertEqual(receipt["exit"], 17)
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(receipt["stderr"], "test error")
        self.assertEqual(receipt["cleanup"], "BLOCKED_STAND")
        self.assertIn("23", receipt["cleanup_error"])

    def test_all_fails_when_phase_cleanup_fails(self):
        code, receipt, phases, _ = self._run_with_cleanup(23, mode="all")
        self.assertEqual(code, 2)
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(receipt["phases"], {"unittest": 2, "pytest": 2})
        for phase in ("unittest", "pytest"):
            self.assertEqual(phases[phase]["cleanup"], "BLOCKED_STAND")
            self.assertEqual(phases[phase]["status"], "BLOCKED_STAND")

    def test_foreign_container_is_not_removed(self):
        code, receipt, _, commands = self._run_with_cleanup(0, foreign=True)
        self.assertEqual(code, 2)
        self.assertEqual(receipt["cleanup"], "BLOCKED_STAND")
        self.assertEqual(receipt["status"], "BLOCKED_STAND")
        self.assertEqual([command[1] for command in commands], ["create", "start", "inspect"])

    def test_successful_cleanup_keeps_success(self):
        code, receipt, _, _ = self._run_with_cleanup(0)
        self.assertEqual(code, 0)
        self.assertEqual(receipt["exit"], 0)
        self.assertEqual(receipt["cleanup"], "PASS")
        self.assertEqual(receipt["status"], "PASS")

    def test_unix_override_must_match_selected_context(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "unix:///other.sock"}), patch(
            "infra.scripts.run_infra_tests.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess([], 0, "local-tests\n", ""),
                subprocess.CompletedProcess([], 0, '"unix:///var/run/docker.sock"\n', ""),
            ],
        ) as process:
            with self.assertRaisesRegex(RuntimeError, "differs from selected context"):
                run_infra_tests._docker_endpoint_is_local()
            self.assertEqual(process.call_count, 2)

    def test_named_local_context_is_allowed(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "", "DOCKER_CONTEXT": "local-tests"}), patch(
            "infra.scripts.run_infra_tests.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess([], 0, "local-tests\n", ""),
                subprocess.CompletedProcess([], 0, '"unix:///var/run/docker.sock"\n', ""),
            ],
        ):
            run_infra_tests._docker_endpoint_is_local()

    def test_command_is_read_only_and_has_no_docker_socket_or_network(self):
        command = run_infra_tests.build_command(image="backend:test", mode="pytest", run_id="infra-test-x")
        self.assertIn("--network=none", command)
        self.assertIn("--read-only", command)
        self.assertTrue(any(value.endswith(":/workspace:ro") for value in command))
        self.assertIn("--pull=never", command)
        self.assertNotIn("/var/run/docker.sock", " ".join(command))

    def test_local_scope_is_explicit_for_full_discovery(self):
        command = run_infra_tests.build_command(
            image="backend:test", mode="unittest", scope="local", run_id="infra-test-x"
        )
        self.assertIn("--scope", command)
        self.assertEqual(command[command.index("--scope") + 1], "local")

    def test_non_local_endpoint_is_rejected_before_docker(self):
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://remote:2375"}, clear=False):
            with self.assertRaises(RuntimeError):
                run_infra_tests._docker_endpoint_is_local()

    def test_result_is_written_when_docker_process_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(run_infra_tests, "_docker_endpoint_is_local"), patch(
                "infra.scripts.run_infra_tests.subprocess.run",
                return_value=type("Completed", (), {"returncode": 17, "stdout": "", "stderr": "failed"})(),
            ) as process:
                self.assertEqual(run_infra_tests.run("unittest", image="backend:test", output=Path(directory)), 17)
                self.assertEqual(process.call_args.kwargs["timeout"], 1800)
                self.assertIn('"status": "FAIL"', (Path(directory) / "result.json").read_text())

    def test_manifest_extraction_validates_json_object(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            output.joinpath("manifest.json").write_text('{"collected": 1}')
            path = run_infra_tests.extract_manifest(output)
            self.assertEqual(path.name, "manifest.json")


if __name__ == "__main__":
    unittest.main()
