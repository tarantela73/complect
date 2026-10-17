"""Payments CI executes the public full gate and retains failure evidence."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/payments-gate.yml"


class PaymentsCiWorkflowTests(unittest.TestCase):
    def test_every_main_pull_request_executes_full_local_gate(self):
        workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow["on"]["pull_request"], {"branches": ["main"]})
        self.assertEqual(workflow["permissions"], {"contents": "read", "actions": "read"})
        job = workflow["jobs"]["payments"]
        self.assertNotIn("if", job)
        self.assertNotIn("continue-on-error", job)
        steps = job["steps"]
        commands = [step.get("run", "") for step in steps]
        gates = [command for command in commands if "make test-payments-gate" in command]
        self.assertEqual(len(gates), 1)
        self.assertIn("set -euo pipefail", gates[0])
        self.assertIn("-m infra.scripts.ci_gate_producer", gates[0])
        self.assertIn("--artifact gate=gate/result.json", gates[0])
        adapter_step = next(step for step in job["steps"] if "-m infra.scripts.ci_gate_producer" in step.get("run", ""))
        self.assertEqual(adapter_step["env"]["GH_TOKEN"], "${{ github.token }}")
        self.assertNotIn("--scope stand", gates[0])
        self.assertTrue(any("backend/Dockerfile.dev" in command for command in commands))
        self.assertTrue(any("docker pull" in command for command in commands))
        for step in steps:
            self.assertNotIn("continue-on-error", step)
        uploads = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0]["if"], "always()")
        self.assertEqual(uploads[0]["with"]["if-no-files-found"], "error")
        for step in steps:
            if "uses" in step:
                self.assertRegex(step["uses"], r"@[0-9a-f]{40}$")

    def test_gate_exit_and_negative_scenario_survive_log_capture(self):
        workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
        command = next(step["run"] for step in workflow["jobs"]["payments"]["steps"]
                       if "make test-payments-gate" in step.get("run", ""))
        for status, fault in [(0, "none"), (23, "none"), (17, "prepare")]:
            with self.subTest(status=status, fault=fault), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                (directory / "payments-ci").mkdir()
                binary = directory / "make"
                binary.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\"\nprintf 'observed-gate-result\\n'\nexit "+str(status)+"\n")
                binary.chmod(0o700)
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
                result = subprocess.run(["bash", "-c", command], cwd=ROOT,
                    env={"PATH": str(directory)+os.pathsep+os.defpath,
                         "RUNNER_TEMP": temporary, "PAYMENTS_FAULT": fault,
                         "PAYMENTS_BACKEND_IMAGE": "backend:synthetic",
                         "PAYMENTS_POSTGRES_IMAGE": "postgres:synthetic",
                         "PAYMENTS_REDIS_IMAGE": "redis:synthetic"},
                    capture_output=True, text=True, timeout=10, check=False)
                self.assertEqual(result.returncode, status, result.stderr)
                log = (directory / "payments-ci/producer/run/command.log").read_text()
                self.assertIn("observed-gate-result", log)
                self.assertIn("test-payments-gate", log)
                self.assertEqual("--fault prepare" in log, fault == "prepare")
