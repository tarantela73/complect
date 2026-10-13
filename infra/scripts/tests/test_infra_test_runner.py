from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infra.scripts import run_infra_tests


class InfraTestRunnerTests(unittest.TestCase):
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
