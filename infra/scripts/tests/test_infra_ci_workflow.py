"""CI keeps the public local infrastructure gate and its failure evidence."""

from __future__ import annotations

import os
import subprocess
import sys
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
        self.assertEqual(workflow["permissions"], {"contents": "read", "actions": "read"})
        job = workflow["jobs"]["infra"]
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        commands = [step.get("run", "") for step in job["steps"]]
        gate = [command for command in commands if "make agent-infra-test" in command]
        self.assertEqual(len(gate), 1)
        self.assertIn("set -euo pipefail", gate[0])
        self.assertIn("-m infra.scripts.ci_gate_producer", gate[0])
        self.assertIn("--artifact gate=gate/result.json", gate[0])
        adapter_step = next(step for step in job["steps"] if "-m infra.scripts.ci_gate_producer" in step.get("run", ""))
        self.assertEqual(adapter_step["env"]["GH_TOKEN"], "${{ github.token }}")
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
            ("make", "make agent-infra-test", "producer/run/command.log"),
        ):
            with self.subTest(tool=tool), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                (directory / "infra-ci").mkdir()
                fake_tool = directory / tool
                fake_tool.write_text("#!/bin/sh\nprintf 'synthetic failure\\n'\nexit 23\n")
                fake_tool.chmod(0o755)
                if tool == "make":
                    fake_git = directory / "git"
                    fake_git.write_text("#!/bin/sh\nexit 0\n")
                    fake_git.chmod(0o755)
                wrapper = directory / "python3"
                wrapper.write_text(
                    "#!" + sys.executable + "\n"
                    "import pathlib,subprocess,sys\n"
                    "args=sys.argv[1:]; assert args[:3]==['-B','-m','infra.scripts.ci_gate_producer']\n"
                    "output=pathlib.Path(args[args.index('--output')+1]); output.mkdir(parents=True)\n"
                    "log=output/'run'/'command.log'; log.parent.mkdir()\n"
                    "with log.open('wb') as stream: result=subprocess.run(args[args.index('--')+1:],stdout=stream,stderr=subprocess.STDOUT)\n"
                    "sys.exit(result.returncode)\n"
                )
                wrapper.chmod(0o700)
                script = next(command for command in commands if selector in command)
                completed = subprocess.run(
                    ["bash", "-c", script],
                    cwd=ROOT,
                    env={
                        "PATH": str(directory) + os.pathsep + os.defpath,
                        "RUNNER_TEMP": temporary,
                        "INFRA_TEST_BACKEND_IMAGE": "infra-ci:synthetic",
                        "INFRA_COMPARISON_BASE": "synthetic-base",
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
