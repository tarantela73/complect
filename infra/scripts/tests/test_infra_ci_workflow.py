"""CI keeps the public local infrastructure gate and its failure evidence."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/infra-tests.yml"


class InfraCiWorkflowTests(unittest.TestCase):
    def test_every_main_pull_request_uses_the_local_gate(self):
        workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow["on"]["pull_request"], {"branches": ["main"]})
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        job = workflow["jobs"]["infra"]
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        commands = [step.get("run", "") for step in job["steps"]]
        gate = [command for command in commands if "make agent-infra-test" in command]
        self.assertEqual(len(gate), 1)
        self.assertIn("set -euo pipefail", gate[0])
        self.assertNotIn("continue-on-error", job)
        for step in job["steps"]:
            self.assertNotIn("continue-on-error", step)
        build = [command for command in commands if "docker build" in command]
        self.assertEqual(len(build), 2)
        backend_build = next(command for command in build if "backend/Dockerfile.dev" in command)
        quality_build = next(command for command in build if "infra/Dockerfile.tests" in command)
        self.assertIn('"$INFRA_TEST_BACKEND_IMAGE"', backend_build)
        self.assertIn('--build-arg BASE_IMAGE="$INFRA_TEST_BACKEND_IMAGE"', quality_build)
        self.assertIn('"$INFRA_TEST_QUALITY_IMAGE"', quality_build)

    def test_failures_survive_log_capture_and_evidence_upload_is_unconditional(self):
        workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
        steps = workflow["jobs"]["infra"]["steps"]
        upload = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(upload), 1)
        self.assertEqual(upload[0]["if"], "always()")
        self.assertRegex(upload[0]["uses"], r"^actions/upload-artifact@[0-9a-f]{40}$")
        self.assertEqual(upload[0]["with"]["path"], "${{ runner.temp }}/infra-ci")
        self.assertEqual(upload[0]["with"]["if-no-files-found"], "error")
        commands = [step.get("run", "") for step in steps]
        for tool, selector, logfile in (
            ("docker", "docker build", "build.log"),
            ("make", "make agent-infra-test", "tests.log"),
        ):
            with self.subTest(tool=tool), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                (directory / "infra-ci").mkdir()
                fake_tool = directory / tool
                fake_tool.write_text("#!/bin/sh\nprintf 'synthetic failure\\n'\nexit 23\n")
                fake_tool.chmod(0o755)
                script = next(command for command in commands if selector in command)
                completed = subprocess.run(
                    ["bash", "-c", script],
                    cwd=ROOT,
                    env={
                        "PATH": str(directory) + os.pathsep + os.defpath,
                        "RUNNER_TEMP": temporary,
                        "INFRA_TEST_BACKEND_IMAGE": "infra-ci:synthetic",
                    },
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(completed.returncode, 23, completed.stderr)
                self.assertIn("synthetic failure", (directory / "infra-ci" / logfile).read_text())


if __name__ == "__main__":
    unittest.main()
