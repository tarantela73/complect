"""Public CLI failures must produce an honest full-gate report."""

import json
import os
import subprocess
import sys
import tempfile
import shutil
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class FullGateCommandTests(unittest.TestCase):
    def test_missing_env_template_blocks_before_any_docker_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / "checkout"
            scripts = checkout / "infra/scripts"
            scripts.mkdir(parents=True)
            for name in ("run_payments_gate.py", "docker_ownership.py", "payments_gate_evidence.py"):
                shutil.copyfile(ROOT / "infra/scripts" / name, scripts / name)
            fake = Path(directory) / "docker"
            fake.write_text("#!/bin/sh\necho unexpected-docker-call >&2\nexit 7\n")
            fake.chmod(0o700)
            output = Path(directory) / "evidence"
            result = subprocess.run([sys.executable, "-B", str(scripts / "run_payments_gate.py"),
                                     "--scope", "full", "--output", str(output)],
                                    env={**os.environ, "PATH": directory + os.pathsep + os.environ["PATH"]},
                                    capture_output=True, text=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            report = json.loads((output / "result.json").read_text())
            self.assertEqual(report["status"], "BLOCKED_STAND")
            self.assertEqual(report["primary_error"]["reason"], "required checkout input missing: .env.dev.example")
            self.assertEqual(report["owned"], [])
            self.assertEqual(report["phases"]["cleanup"]["status"], "PASS")

    def test_missing_image_blocks_dependencies_and_records_cleanup(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("PAYMENTS_CLI_TEST_TMPDIR")) as directory:
            output = Path(directory) / "evidence"
            binary = Path(directory) / "docker"
            binary.write_text(
                "#!/usr/bin/env python3\nimport json,sys\n"
                "args=sys.argv[1:]\n"
                "if args == ['context','show']: print('default')\n"
                "elif args[:2] == ['context','inspect']: print(json.dumps([{'Endpoints':{'docker':{'Host':'unix:///selected.sock'}}}]))\n"
                "elif args[:1] == ['info']: print('test-daemon')\n"
                "else: sys.exit(1)\n"
            )
            binary.chmod(0o700)
            env = dict(
                os.environ,
                PATH=directory + os.pathsep + os.environ["PATH"],
                DOCKER_HOST="unix:///selected.sock",
                DOCKER_CONTEXT="default",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "infra/scripts/run_payments_gate.py"),
                    "--scope",
                    "full",
                    "--backend-image",
                    "complect-missing-gate-image:never",
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((output / "result.json").exists(), result.stderr)
            report = json.loads((output / "result.json").read_text())
            self.assertEqual(report["status"], "BLOCKED_STAND")
            self.assertEqual(report["primary_error"]["phase"], "preflight")
            self.assertIn("image", report["primary_error"]["reason"])
            self.assertEqual(report["phases"]["payments"]["status"], "NOT_RUN")
            self.assertIn("preflight", report["phases"]["payments"]["reason"])
            self.assertEqual(report["phases"]["cleanup"]["status"], "PASS")
            self.assertEqual(report["owned"], [])


if __name__ == "__main__":
    unittest.main()
