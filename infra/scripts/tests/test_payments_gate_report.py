"""Контракт JSON-валидатора Payments gate."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "infra/scripts/payments_gate_report.py"


MANDATORY_PHASES = (
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
)


def valid_report() -> dict:
    phases = {name: {"status": "PASS"} for name in MANDATORY_PHASES}
    for name in ("payments", "http", "lifecycle_pytest", "lifecycle_unittest"):
        phases[name].update(
            manifest_path=f"evidence/{name}/manifest.json",
            tests={"collected": 1, "skipped": 0},
        )
    for name in ("payments", "http", "lifecycle_pytest"):
        report = [
            {"nodeid": f"{name}/test_ok", "when": when, "outcome": "passed"}
            for when in ("setup", "call", "teardown")
        ]
        phases[name]["manifest"] = {
            "collected": [f"{name}/test_ok"],
            "reports": report,
            "exit": 0,
            "skipped": 0,
        }
        phases[name].pop("tests")
    phases["lifecycle_unittest"]["tests"].update(
        {"passed": 1, "failures": 0, "errors": 0}
    )
    phases["http_control"]["control_evidence"] = {"status": 200}
    phases["http"]["http_evidence"] = {
        "attach": {"status": 200},
        "detach": {"status": 200},
        "conflicts": {
            "attach": {
                "status": 409,
                "code": "CONCURRENT_MODIFICATION",
                "primary_pid": "primary-1",
                "writer_pid": "writer-1",
                "before": {"version": 4, "business": "a", "outbox_count": 2},
                "after": {"version": 5, "business": "a", "outbox_count": 2},
            },
            "detach": {
                "status": 409,
                "code": "CONCURRENT_MODIFICATION",
                "primary_pid": "primary-1",
                "writer_pid": "writer-2",
                "before": {"version": 5, "business": "a", "outbox_count": 2},
                "after": {"version": 6, "business": "a", "outbox_count": 2},
            },
        },
    }

    def command(name, before_version, before_outbox):
        return {
            "name": name,
            "status": 200,
            "primary_alias": "default",
            "state_before": {"version": before_version},
            "state_after": {"version": before_version + 1},
            "outbox_before": before_outbox,
            "outbox_after": before_outbox + 1,
        }

    phases["physical_replica"]["replica_evidence"] = {
        "runs": [
            {
                "before": {"lsn": "1"},
                "after": {"lsn": "2"},
                "lag": {
                    "primary_lsn": "2",
                    "replica_lsn": "1",
                    "lag_bytes": 1,
                    "replay_paused": True,
                    "in_recovery": True,
                },
                "commands": [command("attach", 1, 0), command("detach", 2, 1)],
                "replay_resumed": True,
                "recovered": {
                    "commands": [command("attach", 3, 2), command("detach", 4, 3)]
                },
            },
            {
                "before": {"lsn": "3"},
                "after": {"lsn": "4"},
                "lag": {
                    "primary_lsn": "4",
                    "replica_lsn": "3",
                    "lag_bytes": 1,
                    "replay_paused": True,
                    "in_recovery": True,
                },
                "commands": [command("attach", 1, 4), command("detach", 2, 5)],
                "replay_resumed": True,
                "recovered": {
                    "commands": [command("attach", 3, 6), command("detach", 4, 7)]
                },
            },
        ],
        "replay_resumed": True,
        "synchronized": True,
    }
    phases["cleanup"]["errors"] = []
    return {
        "run_id": "payments-test-run",
        "status": "PASS",
        "source_identity": {"sha256": "a" * 64},
        "owned": [
            {"removed": True, "labels": {"complect.payments_run": "payments-test-run"}}
        ],
        "runtime_identity": {
            "principal": "complect_app_local",
            "session_user": "complect_app_local",
            "database": "payments_gate",
            "database_oid": 42,
            "privileges": [False] * 6,
        },
        "target_identity": {"database_oid": 42},
        "manifest": {"path": "manifest.json", "tests": ["payments"]},
        "evidence": {"path": "evidence", "files": ["manifest.json"]},
        "phases": phases,
    }


class PaymentsGateReportTests(unittest.TestCase):
    def test_runtime_database_name_and_oid_are_distinct_fields(self):
        from infra.scripts.payments_gate_report import validate_report

        report = valid_report()
        report["runtime_identity"].update(database="payments_gate", database_oid=42)
        self.assertEqual(validate_report(report), [])

    def test_accepts_complete_report(self):
        from infra.scripts.payments_gate_report import validate_report

        self.assertEqual(validate_report(valid_report()), [])

    def test_accepts_embedded_manifest_evidence(self):
        from infra.scripts.payments_gate_report import validate_report

        report = valid_report()
        for name in ("payments", "http", "lifecycle_pytest"):
            report["phases"][name]["manifest"] = {
                "collected": ["payments/a"],
                "reports": [
                    {"nodeid": "payments/a", "when": when, "outcome": "passed"}
                    for when in ("setup", "call", "teardown")
                ],
                "exit": 0,
                "skipped": 0,
            }
        self.assertEqual(validate_report(report), [])

    def test_rejects_duplicate_node_and_incomplete_lifecycle_report(self):
        from infra.scripts.payments_gate_report import validate_report

        report = valid_report()
        report["phases"]["payments"]["manifest"] = {
            "collected": ["payments/a"],
            "reports": [
                {"nodeid": "payments/a", "when": "setup", "outcome": "passed"},
                {"nodeid": "payments/a", "when": "setup", "outcome": "passed"},
            ],
            "exit": 0,
            "skipped": 0,
        }
        errors = validate_report(report)
        self.assertTrue(any("duplicate node" in error for error in errors))
        self.assertTrue(any("teardown" in error for error in errors))

    def test_rejects_false_http_and_replica_evidence(self):
        from infra.scripts.payments_gate_report import validate_report

        report = valid_report()
        report["phases"]["http"]["http_evidence"] = {
            "attach": {"status": 500},
            "detach": {"status": 200},
            "conflicts": {
                "attach": {
                    "status": 409,
                    "code": "WRONG_CODE",
                    "primary_pid": "same",
                    "writer_pid": "same",
                    "before": {"version": 4, "business": "a", "outbox_count": 2},
                    "after": {"version": 4, "business": "b", "outbox_count": 3},
                }
            },
        }
        report["phases"]["physical_replica"]["replica_evidence"] = {
            "runs": [{"before": {"lsn": "1"}, "after": {"lsn": "1"}}],
            "replay_resumed": False,
            "synchronized": False,
        }
        errors = validate_report(report)
        self.assertTrue(any("attach" in error and "200" in error for error in errors))
        self.assertTrue(any("CONCURRENT_MODIFICATION" in error for error in errors))
        self.assertTrue(any("writer_pid" in error for error in errors))
        self.assertTrue(any("replica" in error for error in errors))

    def test_rejects_http_evidence_missing_detach_conflict(self):
        from infra.scripts.payments_gate_report import validate_report

        report = valid_report()
        del report["phases"]["http"]["http_evidence"]["conflicts"]["detach"]
        self.assertTrue(
            any(
                "detach" in error and "conflict" in error
                for error in validate_report(report)
            )
        )

    def test_allows_blocked_report_without_successful_evidence(self):
        from infra.scripts.payments_gate_report import validate_report

        phases = {
            phase: {"status": "NOT_RUN", "reason": "preflight blocked"}
            for phase in MANDATORY_PHASES
        }
        phases["preflight"] = {
            "status": "BLOCKED_STAND",
            "reason": "Docker unavailable",
        }
        report = {
            "run_id": "blocked-run",
            "status": "BLOCKED_STAND",
            "phases": phases,
        }
        self.assertEqual(validate_report(report), [])

    def test_rejects_runtime_identity_with_privilege(self):
        from infra.scripts.payments_gate_report import validate_report

        report = valid_report()
        report["runtime_identity"]["privileges"][0] = True
        errors = validate_report(report)
        self.assertTrue(any("runtime identity" in error for error in errors))

    def test_rejects_non_numeric_replica_version_without_crashing(self):
        from infra.scripts.payments_gate_report import validate_report

        report = valid_report()
        report["phases"]["physical_replica"]["replica_evidence"]["runs"][0]["commands"][
            0
        ]["state_after"]["version"] = "2"
        errors = validate_report(report)
        self.assertTrue(any("version" in error for error in errors))

    def test_rejects_missing_mandatory_phase(self):
        from infra.scripts.payments_gate_report import validate_report

        report = valid_report()
        del report["phases"]["physical_replica"]
        errors = validate_report(report)
        self.assertTrue(
            any("missing phase: physical_replica" in error for error in errors)
        )

    def test_rejects_zero_or_skipped_tests(self):
        from infra.scripts.payments_gate_report import validate_report

        report = valid_report()
        report["phases"]["payments"].pop("manifest")
        report["phases"]["http"].pop("manifest")
        report["phases"]["payments"]["tests"] = {"collected": 0, "skipped": 0}
        report["phases"]["http"]["tests"] = {"collected": 2, "skipped": 1}
        errors = validate_report(report)
        self.assertTrue(
            any("payments" in error and "collected" in error for error in errors)
        )
        self.assertTrue(any("http" in error and "skipped" in error for error in errors))

    def test_rejects_evidence_without_manifest_or_phases(self):
        from infra.scripts.payments_gate_report import validate_report

        report = valid_report()
        del report["manifest"]
        report["evidence"] = {}
        errors = validate_report(report)
        self.assertTrue(any("manifest" in error for error in errors))
        self.assertTrue(any("evidence" in error for error in errors))

    def test_rejects_inconsistent_status_and_false_overall_pass(self):
        from infra.scripts.payments_gate_report import validate_report

        report = valid_report()
        report["phases"]["http"]["status"] = "NOT_RUN"
        errors = validate_report(report)
        self.assertTrue(any("overall PASS" in error for error in errors))

    def test_rejects_cleanup_error(self):
        from infra.scripts.payments_gate_report import validate_report

        report = valid_report()
        report["phases"]["cleanup"] = {
            "status": "PASS",
            "errors": [{"error": "rm failed"}],
        }
        errors = validate_report(report)
        self.assertTrue(any("cleanup" in error for error in errors))

    def test_cli_returns_nonzero_for_invalid_report(self):
        report = valid_report()
        report["phases"].pop("payments")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payments-report-invalid-test.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("missing phase: payments", result.stderr)


if __name__ == "__main__":
    unittest.main()
