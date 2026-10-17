"""Public host acceptance selection and shared Docker contexts."""
from __future__ import annotations
import unittest
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch
from infra.scripts import run_infra_tests
from infra.scripts.infra_test_selection import required_gates
from infra.scripts.run_payments_gate import Stand

class InfraAcceptanceTests(unittest.TestCase):
    def test_shared_selection_and_adapters_require_both_existing_gates(self):
        for path in ("infra/scripts/infra_test_selection.py", "backend/test_support/payments_infra.py", "infra/scripts/run_payments_gate.py", "infra/scripts/run_infra_tests.py", "infra/scripts/tests/test_qualification_processes.py"):
            with self.subTest(path=path):
                self.assertEqual(required_gates([path]), ["agent-infra-test", "test-payments-gate"])

    def test_unrelated_change_keeps_infrastructure_gate(self):
        self.assertEqual(required_gates(["docs/readme.md"]), ["agent-infra-test"])

    def test_unknown_or_unsafe_change_is_not_silently_accepted(self):
        for path in ("../outside.py", "/absolute.py", "", "infra/scripts/tests/new_test.py"):
            with self.subTest(path=path):
                if path.startswith("infra/"):
                    self.assertEqual(required_gates([path]), ["agent-infra-test", "test-payments-gate"])
                else:
                    with self.assertRaises(ValueError):
                        required_gates([path])

    def test_payments_has_fast_public_context(self):
        self.assertTrue(callable(getattr(Stand, "run_infra_only", None)))


class DeliveryTests(unittest.TestCase):
    def test_required_gate_failure_and_partial_receipt_block_delivery(self):
        for command_exit, verdict in ((23, "FAIL"), (0, "PARTIAL"), (0, "PASS")):
            with self.subTest(command_exit=command_exit, verdict=verdict), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                subprocess.run(["git", "init", "-q", str(root)], check=True)
                subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "base"], check=True)
                (root / "infra/scripts").mkdir(parents=True)
                (root / "infra/scripts/new.py").write_text("changed = True")
                binary = root / "make"
                binary.write_text("#!/usr/bin/env python3\nimport json, pathlib, sys, shlex\narg=shlex.split(sys.argv[2].split('=',1)[1])\nout=pathlib.Path(arg[arg.index('--output')+1])\nout.mkdir(parents=True)\n(out/'result.json').write_text(json.dumps({'status': "+repr(verdict)+"}))\nraise SystemExit("+str(command_exit)+")\n")
                binary.chmod(0o700)
                with patch.object(run_infra_tests, "ROOT", root), patch.dict(os.environ, {"PATH": str(root) + os.pathsep + os.environ["PATH"]}):
                    code = run_infra_tests.run_delivery(base="HEAD", image="backend:test", output=root / ".dev/result")
                self.assertEqual(code, command_exit or (0 if verdict == "PASS" else 2))
                receipt = json.loads((root / ".dev/result/selection.json").read_text())
                self.assertEqual(receipt["required_gates"], ["agent-infra-test", "test-payments-gate"])
                self.assertEqual(receipt["results"][0]["status"], verdict)
