"""Public receipt acceptance uses producer contracts, never command execution."""

from __future__ import annotations
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from infra.scripts.delivery_evidence import accept_receipts


class DeliveryEvidenceTests(unittest.TestCase):
    def test_real_harness_shape_without_synthetic_status_is_accepted(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            log = root / "harness.log"
            log.write_text("26 Python, 21 Node, 0 skips\n")
            receipt = root / "receipt.json"
            sources = {"infra/scripts/agent_harness.py": "a" * 64}
            payload = {
                "producer": "agent_harness.py --record",
                "run_id": "receipt-1",
                "cwd": str(root),
                "command": "bash infra/scripts/check_agent_harness.sh",
                "exit_code": 0,
                "log": "harness.log",
                "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
                "sources": sources,
                "sources_unchanged_during_run": True,
                "verification": {"python_tests": 26, "node_tests": 21, "skips": 0},
                "verification_status": "PASS",
                "erp_runtime": "NOT_RUN",
            }
            receipt.write_text(json.dumps(payload))
            requirements = {
                "version": 1,
                "source": {"sources": sources},
                "checks": [
                    {
                        "id": "harness",
                        "producer": "harness",
                        "execution": {"kind": "docker", "command": payload["command"]},
                    }
                ],
            }
            result = accept_receipts(requirements, [receipt])
            self.assertEqual(result["status"], "PASS", result)
            for field, replacement in (
                ("verification", {"python_tests": 26, "node_tests": 21, "skips": 1}),
                ("exit_code", False),
                ("sources_unchanged_during_run", False),
            ):
                changed = dict(payload)
                changed[field] = replacement
                receipt.write_text(json.dumps(changed))
                outcome = accept_receipts(requirements, [receipt])
                self.assertEqual(outcome["status"], "BLOCKED_STAND", outcome)
            receipt.write_text(json.dumps(payload))
            log.write_text("another log")
            self.assertEqual(
                accept_receipts(requirements, [receipt])["status"], "BLOCKED_STAND"
            )

    def test_infrastructure_leaf_requires_manifest_source_cleanup_and_matching_execution(
        self,
    ):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            manifest = {
                "collected": 1,
                "passed": 1,
                "skipped": 0,
                "errors": 0,
                "failures": 0,
            }
            (root / "manifest.json").write_text(json.dumps(manifest))
            selection = {
                "version": 1,
                "status": "PASS",
                "comparison_base": "a" * 40,
                "source_identity": {"sha256": "b" * 64},
                "sources_unchanged": True,
                "required_gates": ["agent-infra-test"],
                "results": [],
            }
            (root / "selection.json").write_text(json.dumps(selection))
            receipt = root / "result.json"
            payload = {
                "run_id": "infra-test-fixture",
                "mode": "unittest",
                "image": "test-image",
                "status": "PASS",
                "exit": 0,
                "cleanup": "PASS",
                "manifest": "manifest.json",
            }
            receipt.write_text(json.dumps(payload))
            requirements = {
                "version": 1,
                "source": {"producer_sha256": "b" * 64},
                "checks": [
                    {
                        "id": "infra-unit",
                        "producer": "infrastructure",
                        "execution": {
                            "kind": "docker",
                            "mode": "unittest",
                            "image": "test-image",
                        },
                        "artifacts": {
                            "selection": {
                                "path": str(root / "selection.json"),
                                "sha256": hashlib.sha256(
                                    (root / "selection.json").read_bytes()
                                ).hexdigest(),
                            }
                        },
                    }
                ],
            }
            result = accept_receipts(requirements, [receipt])
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["receipts"][0]["comparison_base"], "a" * 40)
            for key in ("cleanup", "manifest", "exit", "image", "run_id"):
                bad = dict(payload)
                bad.pop(key)
                receipt.write_text(json.dumps(bad))
                self.assertEqual(
                    accept_receipts(requirements, [receipt])["status"],
                    "BLOCKED_STAND",
                    key,
                )

    def test_unknown_incomplete_required_and_duplicate_checks_fail_closed(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            receipt = root / "result.json"
            receipt.write_text('{"status":"PASS","command":"touch SHOULD_NOT_EXIST"}')
            req = {
                "version": 1,
                "source": {"producer_sha256": "b" * 64},
                "checks": [
                    {
                        "id": "infra",
                        "producer": "infrastructure",
                        "execution": {"kind": "docker"},
                    }
                ],
            }
            self.assertEqual(accept_receipts(req, [receipt])["status"], "BLOCKED_STAND")
            self.assertEqual(accept_receipts(req, [])["status"], "BLOCKED_STAND")
            req["checks"].append(req["checks"][0])
            self.assertEqual(
                accept_receipts(req, [receipt, receipt])["status"], "BLOCKED_STAND"
            )
            self.assertFalse((root / "SHOULD_NOT_EXIST").exists())
            expected = root / "requirements.json"
            expected.write_text(json.dumps(req))
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "infra.scripts.delivery_evidence",
                    "accept",
                    "--requirements",
                    str(expected),
                    "--receipt",
                    str(receipt),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["status"], "BLOCKED_STAND")

    def test_payments_uses_existing_full_report_validator_and_preserves_limits(self):
        from infra.scripts.tests.test_payments_gate_report import valid_report

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            receipt = root / "result.json"
            payload = valid_report()
            payload["scope"] = "issues-18-20"
            payload["limits"] = {
                key: "NOT_RUN"
                for key in ("PRODUCTION_RLS", "ERP_ACCEPTANCE", "KONG_JWT_RBAC_E2E")
            }
            receipt.write_text(json.dumps(payload))
            req = {
                "version": 1,
                "source": {"producer_sha256": "a" * 64},
                "checks": [
                    {
                        "id": "payments",
                        "producer": "payments",
                        "execution": {
                            "kind": "docker",
                            "command": "make test-payments-gate",
                        },
                    }
                ],
            }
            result = accept_receipts(req, [receipt])
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["receipts"][0]["limits"], payload["limits"])
            for bad in ({}, {"status": "PASS"}):
                payload["phases"]["cleanup"] = bad
                receipt.write_text(json.dumps(payload))
                self.assertEqual(
                    accept_receipts(req, [receipt])["status"], "BLOCKED_STAND"
                )

    def test_legacy_requires_bound_identity_and_exact_original133_manifest(self):
        from infra.scripts.tests.test_attempt_identity import AttemptIdentityTests, git
        from infra.scripts.run_legacy_qualification import freeze_source, write_record
        from infra.scripts.legacy_phase_contract import ORIGINAL_NODE_IDS

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source, sha = AttemptIdentityTests().source(root)
            attempt = root / "attempt"
            freeze_source(source, sha, attempt)
            tree = git(source, "rev-parse", "HEAD^{tree}")
            payload = {
                "version": 1,
                "source_commit": sha,
                "source_tree": tree,
                "status": "PASS",
                "exit": 0,
                "primary_exit": 0,
                "tests": "PASS",
                "cleanup": "PASS",
                "cleanup_errors": [],
            }
            write_record(attempt / "qualification.json", payload)
            manifest = {
                "collected": list(ORIGINAL_NODE_IDS),
                "reports": [
                    {"nodeid": node, "when": when, "outcome": "passed"}
                    for node in ORIGINAL_NODE_IDS
                    for when in ("setup", "call", "teardown")
                ],
                "exit": 0,
            }
            f = attempt / "frozen/.dev/test-artifacts/stand/results/manifest.json"
            f.parent.mkdir(parents=True)
            write_record(f, manifest)
            req = {
                "version": 1,
                "source": {"source_commit": sha, "source_tree": tree},
                "checks": [
                    {
                        "id": "legacy",
                        "producer": "legacy",
                        "attempt": str(attempt),
                        "execution": {
                            "kind": "committed",
                            "command": "legacy-stand-qualify",
                        },
                        "artifacts": {
                            "manifest": {
                                "path": str(f),
                                "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                            }
                        },
                    }
                ],
            }
            result = accept_receipts(req, [attempt / "qualification.json"])
            self.assertEqual(result["status"], "PASS", result)
            replacement = root / "foreign-manifest.json"
            replacement.write_bytes(f.read_bytes())
            req["checks"][0]["artifacts"]["manifest"]["path"] = str(replacement)
            self.assertEqual(
                accept_receipts(req, [attempt / "qualification.json"])["status"],
                "BLOCKED_STAND",
            )
            req["checks"][0]["artifacts"]["manifest"]["path"] = str(f)
            (attempt / "identity.json").unlink()
            self.assertEqual(
                accept_receipts(req, [attempt / "qualification.json"])["status"],
                "BLOCKED_STAND",
            )

    def test_full_infrastructure_requires_complete_grid_and_verified_baseline_diagnostics(
        self,
    ):
        from infra.scripts.tests.test_attempt_identity import AttemptIdentityTests
        from infra.scripts.tests.test_payments_gate_report import valid_report
        from infra.scripts.infra_quality_coverage import prepare_input

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source, sha = AttemptIdentityTests().source(root)
            from infra.scripts.tests.test_attempt_identity import git

            (source / "infra/scripts/sample.py").write_text('value: int = "bad"\n')
            git(source, "add", ".")
            git(source, "commit", "-qm", "known baseline diagnostic")
            sha = git(source, "rev-parse", "HEAD")
            (source / "infra/scripts/sample.py").write_text(
                '# header\nvalue: int = "bad"\n'
            )
            output = root / "gate"

            def save(name, value):
                path = output / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value))
                return path

            for mode in ("pytest", "unittest"):
                if mode == "pytest":
                    manifest = {
                        "collected": ["test_ok"],
                        "reports": [
                            {"nodeid": "test_ok", "when": phase, "outcome": "passed"}
                            for phase in ("setup", "call", "teardown")
                        ],
                        "exit": 0,
                    }
                else:
                    manifest = {
                        "collected": 1,
                        "passed": 1,
                        "skipped": 0,
                        "errors": 0,
                        "failures": 0,
                    }
                save(mode + "/manifest.json", manifest)
                save(
                    mode + "/result.json",
                    {
                        "mode": mode,
                        "run_id": mode,
                        "image": "offline-image",
                        "status": "PASS",
                        "exit": 0,
                        "cleanup": "PASS",
                        "manifest": "manifest.json",
                    },
                )
            save(
                "quality/result.json",
                {
                    "status": "PASS",
                    "checks": [
                        {"name": tool, "command": [tool], "status": "PASS", "exit": 0}
                        for tool in ("ruff", "mypy", "actionlint")
                    ],
                },
            )
            package = output / "coverage/input"
            prepare_input(base=sha, output=package, root=source)
            inputs = json.loads((package / "input.json").read_text())
            (output / "coverage/after").mkdir()
            (output / "coverage/after/input.json").write_bytes(
                (package / "input.json").read_bytes()
            )
            report = {
                "version": 1,
                "status": "PASS",
                "source_identity": inputs["source_identity"],
                "comparison_base": sha,
                "unknown_paths": [],
                "changed_files": ["infra/scripts/sample.py"],
                "files": {
                    "infra/scripts/sample.py": {
                        "source_sha256": hashlib.sha256(
                            (source / "infra/scripts/sample.py").read_bytes()
                        ).hexdigest(),
                        "baseline_sha256": inputs["baseline_inventory"][
                            "infra/scripts/sample.py"
                        ]["sha256"],
                        "status": "PASS",
                        "checks": {
                            tool: {
                                "command": [tool],
                                "exit": 0,
                                "diagnostics": [],
                                "stderr": "",
                            }
                            for tool in ("ruff", "mypy")
                        },
                    }
                },
            }
            row = report["files"]["infra/scripts/sample.py"]
            row["status"] = "FAIL_BASELINE"
            row["baseline"] = "MATCHED"
            diagnostic = 'error: Incompatible types in assignment (expression has type "str", variable has type "int")  [assignment]'
            row["checks"]["mypy"].update(
                exit=1, diagnostics=["infra/scripts/sample.py:2: " + diagnostic]
            )
            row["baseline_checks"] = {
                "ruff": {
                    "command": ["ruff"],
                    "exit": 0,
                    "diagnostics": [],
                    "stderr": "",
                },
                "mypy": {
                    "command": ["mypy"],
                    "exit": 1,
                    "diagnostics": ["infra/scripts/sample.py:1: " + diagnostic],
                    "stderr": "",
                },
            }
            save("coverage/coverage.json", report)
            save(
                "coverage/result.json",
                {
                    "status": "PASS",
                    "exit": 0,
                    "sources_unchanged": True,
                    "command": ["coverage"],
                },
            )
            selection = {
                "version": 1,
                "status": "PASS",
                "comparison_base": sha,
                "changed_paths": ["infra/scripts/sample.py"],
                "source_identity": {"sha256": "b" * 64},
                "sources_unchanged": True,
                "required_gates": ["agent-infra-test", "test-payments-gate"],
                "results": [
                    {
                        "gate": "test-payments-gate",
                        "command": ["make", "test-payments-gate"],
                        "exit": 0,
                        "status": "PASS",
                    }
                ],
            }
            selection_path = save("delivery/selection.json", selection)
            payments = valid_report()
            payments["scope"] = "issues-18-20"
            payments["source_identity"] = {"sha256": "b" * 64}
            save("delivery/payments/result.json", payments)
            save(
                "payments-context/run-fixture/context.json",
                {
                    "status": "PASS",
                    "cleanup": "PASS",
                    "source_identity": {"sha256": "b" * 64},
                },
            )
            save(
                "recovery/result.json",
                {"status": "PASS", "exit": 0, "command": ["recovery"]},
            )
            gate = save(
                "result.json",
                {
                    "status": "PASS",
                    "phases": {
                        phase: 0
                        for phase in (
                            "quality",
                            "unittest",
                            "pytest",
                            "coverage",
                            "payments_context",
                            "recovery",
                            "delivery",
                        )
                    },
                },
            )
            req = {
                "version": 1,
                "source": {"producer_sha256": "b" * 64},
                "checks": [
                    {
                        "id": "infra-full",
                        "producer": "infrastructure",
                        "source_root": str(source),
                        "execution": {
                            "kind": "docker",
                            "command": "make agent-infra-test",
                        },
                        "artifacts": {"selection": {"path": str(selection_path)}},
                    }
                ],
            }
            from infra.scripts.gate_source_proof import capture, finish

            before = capture(source, output, "sources-before.json")
            names = [
                "quality/result.json",
                "unittest/result.json",
                "unittest/manifest.json",
                "pytest/result.json",
                "pytest/manifest.json",
                "coverage/result.json",
                "coverage/coverage.json",
                "coverage/input/input.json",
                "coverage/after/input.json",
                "recovery/result.json",
                "delivery/selection.json",
                "delivery/payments/result.json",
                "payments-context/run-fixture/context.json",
            ]
            finish(
                root=source,
                output=output,
                before=before["snapshot"],
                gate_exit=0,
                artifacts={n: output / n for n in names},
            )
            result = accept_receipts(req, [gate])
            self.assertEqual(result["status"], "PASS", result)
            proof_path = output / "source-proof.json"
            intact_proof = proof_path.read_bytes()
            changed = json.loads(intact_proof)
            changed["artifacts"].pop("pytest/manifest.json")
            proof_path.write_text(json.dumps(changed))
            self.assertEqual(accept_receipts(req, [gate])["status"], "BLOCKED_STAND")
            proof_path.write_bytes(intact_proof)

            def reseal():
                finish(
                    root=source,
                    output=output,
                    before=before["snapshot"],
                    gate_exit=0,
                    artifacts={n: output / n for n in names},
                )

            # Even a hash-bound MATCHED label cannot replace the original diagnostics.
            original_baseline = row.pop("baseline_checks")
            save("coverage/coverage.json", report)
            reseal()
            self.assertEqual(accept_receipts(req, [gate])["status"], "BLOCKED_STAND")
            row["baseline_checks"] = original_baseline
            row["checks"]["mypy"]["diagnostics"] = [
                "infra/scripts/sample.py:2: error: another regression  [assignment]"
            ]
            save("coverage/coverage.json", report)
            reseal()
            self.assertEqual(accept_receipts(req, [gate])["status"], "BLOCKED_STAND")
            row["checks"]["mypy"]["diagnostics"] = [
                "infra/scripts/sample.py:2: " + diagnostic
            ]
            save("coverage/coverage.json", report)
            unit = output / "unittest/manifest.json"
            bad = json.loads(unit.read_text())
            bad["skipped"] = 1
            unit.write_text(json.dumps(bad))
            reseal()
            self.assertEqual(accept_receipts(req, [gate])["status"], "BLOCKED_STAND")

    def test_scope_exclusion_requires_parent_reason_without_fake_receipt(self):
        req = {
            "version": 1,
            "source": {"producer_sha256": "a" * 64},
            "checks": [
                {
                    "id": "not-applicable",
                    "producer": "payments",
                    "applicable": False,
                    "scope_reason": "Documentation-only scope explicitly agreed before dispatch",
                }
            ],
        }
        result = accept_receipts(req, [])
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["receipts"][0]["status"], "NOT_APPLICABLE")
        req["checks"][0].pop("scope_reason")
        self.assertEqual(accept_receipts(req, [])["status"], "BLOCKED_STAND")

    def test_negative_scenario_recovery_cannot_mix_attempts_of_same_commit(self):
        from infra.scripts.tests.test_attempt_identity import AttemptIdentityTests, git
        from infra.scripts.run_legacy_qualification import freeze_source, write_record

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source, sha = AttemptIdentityTests().source(root)
            tree = git(source, "rev-parse", "HEAD^{tree}")
            first = root / "first"
            second = root / "second"
            for attempt in (first, second):
                freeze_source(source, sha, attempt)
                payload = {
                    "version": 1,
                    "source_commit": sha,
                    "source_tree": tree,
                    "status": "BLOCKED_STAND",
                    "exit": 23,
                    "primary_exit": 23,
                    "tests": "NOT_PASS",
                    "cleanup": "BLOCKED_STAND",
                    "cleanup_errors": ["guard"],
                }
                write_record(attempt / "qualification.json", payload)
                for number in (1, 2):
                    write_record(
                        attempt / f"recovery-{number}.json",
                        {
                            "version": 1,
                            "operation": "cleanup-only",
                            "status": "PASS",
                            "errors": [],
                            "source_commit": sha,
                            "original_verdict_unchanged": True,
                        },
                    )

            def scenario(attempt):
                identity = json.loads((attempt / "identity.json").read_text())
                value = {
                    "status": "PASS",
                    "source_commit": sha,
                    "source_tree": tree,
                    "attempt_id": identity["attempt_id"],
                    "initial": payload,
                    "recovery_artifacts": {},
                }
                for key, number in (("recovery", 1), ("repeat", 2)):
                    file = attempt / f"recovery-{number}.json"
                    value[key] = json.loads(file.read_text())
                    value["recovery_artifacts"][key] = {
                        "path": file.name,
                        "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                    }
                return value

            proof = root / "scenario.json"
            proof.write_text(json.dumps(scenario(first)))
            req = {
                "version": 1,
                "source": {"source_commit": sha, "source_tree": tree},
                "checks": [
                    {
                        "id": "negative",
                        "producer": "legacy",
                        "attempt": str(first),
                        "execution": {
                            "kind": "committed",
                            "command": "legacy-recovery-scenario",
                        },
                        "expected_product": {
                            "status": "BLOCKED_STAND",
                            "primary_exit": 23,
                        },
                        "artifacts": {"scenario": {"path": str(proof)}},
                    }
                ],
            }
            result = accept_receipts(req, [first / "qualification.json"])
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["receipts"][0]["status"], "BLOCKED_STAND")
            proof.write_text(json.dumps(scenario(second)))
            self.assertEqual(
                accept_receipts(req, [first / "qualification.json"])["status"],
                "BLOCKED_STAND",
            )
            value = scenario(first)
            value["recovery_artifacts"]["repeat"] = value["recovery_artifacts"][
                "recovery"
            ]
            proof.write_text(json.dumps(value))
            self.assertEqual(
                accept_receipts(req, [first / "qualification.json"])["status"],
                "BLOCKED_STAND",
            )

    def test_real_early_matrix_selects_success_without_inventing_recovery(self):
        from infra.scripts.tests.test_attempt_identity import AttemptIdentityTests, git
        from infra.scripts.run_legacy_qualification import freeze_source, write_record

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source, sha = AttemptIdentityTests().source(root)
            tree = git(source, "rev-parse", "HEAD^{tree}")
            attempt = root / "attempt"
            freeze_source(source, sha, attempt)
            product = {
                "version": 1,
                "source_commit": sha,
                "source_tree": tree,
                "status": "FAIL",
                "exit": 137,
                "primary_exit": 137,
                "tests": "NOT_PASS",
                "cleanup": "PASS",
                "cleanup_errors": [],
            }
            write_record(attempt / "qualification.json", product)
            identity = json.loads((attempt / "identity.json").read_text())
            case = {
                "status": "PASS",
                "early_failure": True,
                "mode": "success",
                "initial": product,
                "source_commit": sha,
                "source_tree": tree,
                "attempt_id": identity["attempt_id"],
                "recovery_artifacts": {},
            }
            matrix = root / "matrix.json"
            matrix.write_text(
                json.dumps(
                    {
                        "status": "PASS",
                        "matrix": {
                            "success": case,
                            "blocked": {"status": "PASS"},
                            "tamper": {"status": "PASS"},
                        },
                    }
                )
            )
            req = {
                "version": 1,
                "source": {"source_commit": sha, "source_tree": tree},
                "checks": [
                    {
                        "id": "early-success",
                        "producer": "legacy",
                        "scenario_case": "success",
                        "attempt": str(attempt),
                        "expected_product": {
                            "status": "FAIL",
                            "primary_exit": 137,
                            "cleanup": "PASS",
                        },
                        "execution": {
                            "kind": "committed",
                            "command": "legacy-recovery-scenario",
                        },
                        "artifacts": {"scenario": {"path": str(matrix)}},
                    }
                ],
            }
            result = accept_receipts(req, [attempt / "qualification.json"])
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["receipts"][0]["status"], "FAIL")
            req["checks"][0]["scenario_case"] = "unknown"
            self.assertEqual(
                accept_receipts(req, [attempt / "qualification.json"])["status"],
                "BLOCKED_STAND",
            )
