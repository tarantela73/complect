"""Контракт накопителя доказательств Payments gate."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HISTORICAL = (
    ROOT / "infra/scripts/tests/fixtures/payments_evidence/full_valid_report.json"
)


class PaymentsEvidenceTests(unittest.TestCase):
    def test_raw_http_manifest_uses_existing_observed_evidence(self):
        from infra.scripts.payments_gate_evidence import PaymentsReport

        expected = json.loads(HISTORICAL.read_text())["phases"]["http"]
        report = PaymentsReport("http-run", "full")
        report.record(
            "http",
            status="PASS",
            manifest=expected["manifest"],
            manifest_path="http.json",
        )
        self.assertEqual(
            report.snapshot()["phases"]["http"]["http_evidence"],
            expected["http_evidence"],
        )

    def test_stand_does_not_claim_full_json_validation(self):
        from infra.scripts.payments_gate_evidence import PaymentsReport

        report = PaymentsReport("stand-run", "stand")
        report.record(metadata={"stand_status": "PASS"})
        for phase in ("preflight", "migrations", "identity", "http_control"):
            report.record(phase, status="PASS")
        report.record("cleanup", errors=[])
        result = report.finish()
        self.assertEqual(
            result.report["phases"]["json_validation"],
            {"status": "NOT_RUN", "reason": "not executed"},
        )
        self.assertEqual(result.exit_code, 0)

    def test_initial_report_has_full_phase_shape(self):
        from infra.scripts.payments_gate_evidence import PaymentsReport

        report = PaymentsReport("run-1", "full")
        snapshot = report.snapshot()
        self.assertEqual(snapshot["run_id"], "run-1")
        self.assertEqual(snapshot["scope"], "issues-18-20")
        self.assertEqual(snapshot["status"], "BLOCKED_STAND")
        self.assertEqual(
            set(snapshot["phases"]),
            {
                "preflight",
                "migrations",
                "identity",
                "http_control",
                "payments",
                "http",
                "physical_replica",
                "lifecycle_pytest",
                "lifecycle_unittest",
                "json_validation",
                "cleanup",
            },
        )

    def test_snapshot_is_deep_copy_and_phase_progress_is_replaced(self):
        from infra.scripts.payments_gate_evidence import PaymentsReport

        report = PaymentsReport("run-2", "full")
        report.record(
            "physical_replica",
            status="NOT_RUN",
            reason="lag",
            replica_evidence={"runs": []},
        )
        first = report.snapshot()
        report.record("physical_replica", status="PASS", replica_evidence={"runs": []})
        first["phases"]["physical_replica"]["replica_evidence"]["runs"].append("x")
        self.assertEqual(
            report.snapshot()["phases"]["physical_replica"]["status"], "PASS"
        )
        self.assertEqual(first["phases"]["physical_replica"]["status"], "NOT_RUN")

    def test_manifest_skipped_is_derived_and_http_conflicts_normalized(self):
        from infra.scripts.payments_gate_evidence import PaymentsReport

        manifest = {
            "collected": ["test_attach"],
            "reports": [
                {
                    "nodeid": "test_attach",
                    "when": "call",
                    "outcome": "passed",
                    "user_properties": [
                        ["http_evidence", {"conflicts": {"attach": {"status": 409}}}]
                    ],
                }
            ],
            "exit": 0,
        }
        report = PaymentsReport("run-3", "full")
        report.record(
            "payments", status="PASS", manifest=manifest, manifest_path="payments.json"
        )
        phase = report.snapshot()["phases"]["payments"]
        self.assertEqual(phase["manifest"]["skipped"], 0)
        self.assertNotIn("tests", phase)

    def test_primary_error_survives_cleanup_error(self):
        from infra.scripts.payments_gate_evidence import PaymentsReport

        report = PaymentsReport("run-4", "full")
        report.record(metadata={"primary_error": ValueError("socket failed")})
        report.record(
            "cleanup", status="BLOCKED_STAND", errors=[{"error": "cleanup failed"}]
        )
        result = report.finish()
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.report["primary_error"]["type"], "ValueError")
        self.assertEqual(
            result.report["phases"]["cleanup"]["errors"][0]["error"], "cleanup failed"
        )

    def test_successful_stand_is_partial_with_zero_exit(self):
        from infra.scripts.payments_gate_evidence import PaymentsReport

        report = PaymentsReport("run-5", "stand")
        report.record(metadata={"stand_status": "PASS"})
        for phase in ("preflight", "migrations", "identity", "http_control"):
            report.record(phase, status="PASS")
        report.record("cleanup", status="PASS", errors=[])
        result = report.finish()
        self.assertEqual(result.report["status"], "PARTIAL")
        self.assertEqual(result.exit_code, 0)

    def test_blocked_preflight_has_valid_json_without_identity(self):
        from infra.scripts.payments_gate_evidence import PaymentsReport

        report = PaymentsReport("run-6", "full")
        report.record("preflight", status="BLOCKED_STAND", reason="Docker unavailable")
        result = report.finish()
        self.assertEqual(result.report["status"], "BLOCKED_STAND")
        self.assertEqual(result.report["phases"]["json_validation"]["status"], "PASS")

    def test_finish_freezes_report(self):
        from infra.scripts.payments_gate_evidence import PaymentsReport

        report = PaymentsReport("run-7", "stand")
        report.record("preflight", status="PASS")
        report.finish()
        with self.assertRaises(RuntimeError):
            report.record("payments", status="PASS")

    def test_factwise_full_report_matches_historical_literal(self):
        from infra.scripts.payments_gate_evidence import PaymentsReport

        expected = json.loads(HISTORICAL.read_text())
        report = PaymentsReport(expected["run_id"], "full")
        top_keys = {
            "owned",
            "docker_endpoint",
            "images",
            "source_identity",
            "uid_gid",
            "subnet",
            "target_identity",
            "runtime_identity",
            "stand_status",
        }
        report.record(
            metadata={key: expected[key] for key in top_keys if key in expected}
        )
        for phase, facts in expected["phases"].items():
            report.record(phase, **facts)
        actual = report.finish().report
        self.assertEqual(actual, expected)

    def test_stand_cli_rejects_unknown_partial_status_with_validator_message(self):
        report = {
            "run_id": "partial-run",
            "status": "PARTIAL",
            "phases": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.json"
            path.write_text(json.dumps(report))
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "infra/scripts/payments_gate_report.py"),
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid overall status: 'PARTIAL'", result.stderr)


if __name__ == "__main__":
    unittest.main()
