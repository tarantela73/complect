"""Behavioral checks of the offline CI audit public boundary (#47)."""
from __future__ import annotations

import hashlib
import copy
import subprocess
import sys
import json
import tempfile
import unittest
from pathlib import Path

from infra.scripts.ci_audit_evidence import verify_ci_audit

HEAD = "a" * 40
BASE = "b" * 40
MERGE = "c" * 40
TREE = "d" * 40


def artifact(root: Path, name: str, value: object) -> dict:
    path = root / name
    path.write_text(value if isinstance(value, str) else json.dumps(value))
    return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def packet(root: Path) -> tuple[dict, dict]:
    identity = {"repository": "tarantela73/complect", "run_id": 10, "run_attempt": 1,
                "job_id": 20, "head_sha": HEAD, "base_sha": BASE,
                "checkout_sha": MERGE, "tree_sha": TREE}
    receipt = artifact(root, "receipt.json", {"status": "PASS", "exit": 0})
    snapshot = {"head": MERGE, "tree": TREE,
                "files": {"source.py": {"kind": "file", "mode": "100644", "sha256": "e" * 64}}}
    before = artifact(root, "before.json", snapshot)
    after = artifact(root, "after.json", snapshot)
    proof = artifact(root, "proof.json", {
        "format": "complect-ci-source-proof-v1", **identity, "status": "PASS",
        "source_before": before, "source_after": after, "artifacts": {"receipt": receipt},
    })
    log = artifact(root, "checkout.log", "checkout\n[command]/usr/bin/git log -1 --format=%H\n" + MERGE + "\n")
    log["checkout_line"] = 3
    requirements = {
        "format": "complect-ci-audit-requirements-v1",
        "jobs": [{"name": "infrastructure", **identity, "artifacts": ["receipt"]}],
    }
    report = {"format": "complect-ci-audit-report-v1", "jobs": [{
        "name": "infrastructure", **identity, "status": "completed", "conclusion": "success",
        "verdict": "PASS", "exit_code": 0, "log": log, "source_proof": proof,
        "artifacts": {"receipt": receipt},
    }]}
    return requirements, report


def specialized_packet(root: Path) -> tuple[dict, dict]:
    requirements, report = packet(root)
    requirements["profile"] = "ci_gate_producer"
    requirements["jobs"][0]["artifacts"] = ["receipt", "runner_receipt", "command_log", "gate"]
    report["profile"] = "ci_gate_producer"
    job = report["jobs"][0]
    receipt = json.loads((root / "receipt.json").read_text())
    receipt["profile"] = "ci_gate_producer"
    receipt_desc = artifact(root, "receipt.json", receipt)
    job["artifacts"]["receipt"] = receipt_desc
    for name, content in (("runner_receipt", "runner\n"), ("command_log", "gate\n"), ("gate", "PASS\n")):
        job["artifacts"][name] = artifact(root, name + ".txt", content)
    proof = json.loads((root / "proof.json").read_text())
    proof["profile"] = "ci_gate_producer"
    proof["artifacts"] = job["artifacts"]
    job["source_proof"] = artifact(root, "proof.json", proof)
    job["source_proof"] = artifact(root, "proof-final.json", proof)
    return requirements, report


class CiAuditTests(unittest.TestCase):
    def test_required_profile_rejects_generic_packet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements, report = packet(root)
            result = verify_ci_audit(requirements, report, root, required_profile="ci_gate_producer")
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["errors"], ["REQUIRED_PROFILE_MISSING"])

    def test_required_profile_is_retained_when_resuming_continuation(self):
        from infra.scripts.ci_audit_evidence import make_ci_audit_continuation
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements, report = packet(root)
            pending = copy.deepcopy(report)
            pending["jobs"][0].update(status="in_progress", conclusion=None)
            point = make_ci_audit_continuation(requirements, pending, root)
            result = verify_ci_audit(requirements, report, root, continuation=point,
                                     required_profile="ci_gate_producer")
            self.assertEqual(result["status"], "FAIL")
            self.assertEqual(result["errors"], ["REQUIRED_PROFILE_MISSING"])

    def test_specialized_profile_requires_marker_and_complete_artifact_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements, report = packet(root)
            requirements["profile"] = "ci_gate_producer"
            requirements["jobs"][0]["artifacts"] = ["receipt", "runner_receipt", "command_log", "gate"]
            self.assertEqual(verify_ci_audit(requirements, report, root)["status"], "BLOCKED_EVIDENCE")
            self.assertEqual(verify_ci_audit({**requirements, "profile": "unknown"}, report, root)["status"], "FAIL")
            report["profile"] = "ci_gate_producer"
            self.assertEqual(verify_ci_audit(requirements, report, root)["status"], "FAIL")

    def test_complete_specialized_packet_passes_and_returns_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            requirements, report = specialized_packet(Path(directory))
            result = verify_ci_audit(requirements, report, Path(directory))
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["profile"], "ci_gate_producer")

    def test_specialized_checkpoint_resume_preserves_profile(self):
        from infra.scripts.ci_audit_evidence import make_ci_audit_continuation
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements, report = specialized_packet(root)
            pending = copy.deepcopy(report)
            pending["jobs"][0].update(status="in_progress", conclusion=None)
            point = make_ci_audit_continuation(requirements, pending, root)
            self.assertEqual(point["requirements"]["profile"], "ci_gate_producer")
            self.assertEqual(point["report"]["profile"], "ci_gate_producer")
            result = verify_ci_audit(requirements, report, root, continuation=point)
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["profile"], "ci_gate_producer")

    def test_generic_profile_remains_readable_without_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements, report = packet(root)
            self.assertEqual(verify_ci_audit(requirements, report, root)["status"], "PASS")

    def test_missing_file_arriving_after_checkpoint_is_a_valid_resume(self):
        from infra.scripts.ci_audit_evidence import make_ci_audit_continuation

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements, report = packet(root)
            receipt = root / "receipt.json"
            saved = receipt.read_bytes()
            receipt.unlink()
            point = make_ci_audit_continuation(requirements, report, root)
            self.assertEqual(point["status"], "BLOCKED_EVIDENCE")
            receipt.write_bytes(saved)
            result = verify_ci_audit(requirements, report, root, continuation=point)
            self.assertEqual(result["status"], "PASS", result)

    def test_next_checkpoint_keeps_previously_verified_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "package"
            root.mkdir()
            requirements, completed = packet(root)
            pending = copy.deepcopy(completed)
            pending["jobs"][0].update(status="in_progress", conclusion=None)
            req, report_path = parent / "requirements.json", root / "report.json"
            req.write_text(json.dumps(requirements))
            report_path.write_text(json.dumps(pending))
            base = [sys.executable, "-B", "-m", "infra.scripts.ci_audit_evidence", "--root", str(root), "--requirements", str(req), "--report", str(report_path)]
            first = parent / "first.json"
            saved = subprocess.run([*base, "--output", str(parent / "r1.json"), "--save-continuation", str(first)], capture_output=True, text=True)
            self.assertEqual(saved.returncode, 3, saved.stdout)
            pin = json.loads(saved.stdout)["continuation_sha256"]
            pending["jobs"][0].pop("log")
            report_path.write_text(json.dumps(pending))
            second = parent / "second.json"
            saved = subprocess.run([*base, "--output", str(parent / "r2.json"), "--save-continuation", str(second), "--continuation", str(first), "--continuation-sha256", pin], capture_output=True, text=True)
            self.assertEqual(saved.returncode, 3, saved.stdout)
            latest = json.loads(second.read_text())
            new_log = artifact(root, "final-checkout.log", (root / "checkout.log").read_text())
            new_log["checkout_line"] = 3
            completed["jobs"][0]["log"] = new_log
            (root / "checkout.log").write_text("changed historical checked log")
            result = verify_ci_audit(requirements, completed, root, continuation=latest)
            self.assertEqual(result["status"], "FAIL", result)

    def test_all_mandatory_jobs_keep_waiting_until_conclusions_and_evidence(self):
        from infra.scripts.ci_audit_evidence import make_ci_audit_continuation

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements, report = packet(root)
            second = copy.deepcopy(requirements["jobs"][0])
            second.update(name="payments", run_id=11, job_id=30)
            requirements["jobs"].append(second)
            job = {key: value for key, value in second.items() if key != "artifacts"}
            job.update(status="in_progress", conclusion=None)
            report["jobs"].append(job)
            point = make_ci_audit_continuation(requirements, report, root)
            self.assertTrue(point["missing"]["payments"])
            for _ in range(5):
                result = verify_ci_audit(requirements, report, root, continuation=point)
                self.assertEqual(result["status"], "PENDING", result)
            job.update(status="completed", conclusion="success", verdict="PASS", exit_code=0)
            self.assertEqual(verify_ci_audit(requirements, report, root, continuation=point)["status"], "BLOCKED_EVIDENCE")
            receipt = artifact(root, "payments-receipt.json", {"status": "PASS", "exit": 0})
            job["artifacts"] = {"receipt": receipt}
            log = artifact(root, "payments.log", (root / "checkout.log").read_text())
            log["checkout_line"] = 3
            job["log"] = log
            proof = json.loads((root / "proof.json").read_text())
            proof.update(run_id=11, job_id=30, artifacts=job["artifacts"])
            job["source_proof"] = artifact(root, "payments-proof.json", proof)
            result = verify_ci_audit(requirements, report, root, continuation=point)
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(len(result["jobs"]), 2)
            job["conclusion"] = "failure"
            result = verify_ci_audit(requirements, report, root, continuation=point)
            self.assertEqual(result["status"], "FAIL", result)
            self.assertEqual(result["jobs"][1]["original"]["conclusion"], "failure")

    def test_new_source_or_rerun_requires_a_new_checkpoint(self):
        from infra.scripts.ci_audit_evidence import make_ci_audit_continuation

        for field, value in (("head_sha", "f" * 40), ("run_id", 11), ("run_attempt", 2), ("job_id", 21)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                parent = Path(directory)
                old_root, new_root = parent / "old", parent / "new"
                old_root.mkdir()
                new_root.mkdir()
                requirements, old = packet(old_root)
                old["jobs"][0].update(status="queued", conclusion=None)
                point = make_ci_audit_continuation(requirements, old, old_root)
                before = copy.deepcopy(point)
                fresh_requirements, fresh = packet(new_root)
                fresh_requirements["jobs"][0][field] = value
                fresh["jobs"][0][field] = value
                proof = json.loads((new_root / "proof.json").read_text())
                proof[field] = value
                fresh["jobs"][0]["source_proof"] = artifact(new_root, "proof.json", proof)
                self.assertEqual(verify_ci_audit(fresh_requirements, fresh, new_root)["status"], "PASS")
                rejected = verify_ci_audit(fresh_requirements, fresh, new_root, continuation=point)
                self.assertEqual(rejected["status"], "FAIL", rejected)
                self.assertEqual(point, before)
                pending = copy.deepcopy(fresh)
                pending["jobs"][0].update(status="in_progress", conclusion=None)
                new_point = make_ci_audit_continuation(fresh_requirements, pending, new_root)
                accepted = verify_ci_audit(fresh_requirements, fresh, new_root, continuation=new_point)
                self.assertEqual(accepted["status"], "PASS", accepted)
                proof[field] = requirements["jobs"][0][field]
                fresh["jobs"][0]["source_proof"] = artifact(new_root, "proof.json", proof)
                self.assertEqual(verify_ci_audit(fresh_requirements, fresh, new_root, continuation=new_point)["status"], "FAIL")

    def test_cli_saves_and_resumes_pending_with_an_independent_checkpoint_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "package"
            root.mkdir()
            requirements, report = packet(root)
            req, observed = parent / "requirements.json", root / "report.json"
            req.write_text(json.dumps(requirements))
            report["jobs"][0].update(status="in_progress", conclusion=None)
            observed.write_text(json.dumps(report))
            point, output = parent / "checkpoint.json", parent / "pending.json"
            base = [sys.executable, "-B", "-m", "infra.scripts.ci_audit_evidence", "--root", str(root), "--requirements", str(req), "--report", str(observed)]
            pending = subprocess.run([*base, "--output", str(output), "--save-continuation", str(point)], capture_output=True, text=True)
            self.assertEqual(pending.returncode, 3, pending.stdout + pending.stderr)
            summary = json.loads(pending.stdout)
            pin = summary["continuation_sha256"]
            self.assertEqual(hashlib.sha256(point.read_bytes()).hexdigest(), pin)
            original = point.read_bytes()
            for extra in (["--continuation", str(point)], ["--save-continuation", str(point)], ["--save-continuation", str(observed)]):
                rejected = subprocess.run([*base, "--output", str(parent / "must-not-exist.json"), *extra], capture_output=True, text=True)
                self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
                self.assertFalse((parent / "must-not-exist.json").exists())
                self.assertEqual(point.read_bytes(), original)
            same = parent / "same.json"
            rejected = subprocess.run([*base, "--output", str(same), "--save-continuation", str(same)], capture_output=True, text=True)
            self.assertEqual(rejected.returncode, 1)
            self.assertFalse(same.exists())
            report["jobs"][0].update(status="completed", conclusion="success")
            observed.write_text(json.dumps(report))
            resumed = subprocess.run([*base, "--output", str(parent / "complete.json"), "--continuation", str(point), "--continuation-sha256", pin], capture_output=True, text=True)
            self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
            self.assertEqual(json.loads(resumed.stdout)["status"], "PASS")
            self.assertEqual(point.read_bytes(), original)
            point.write_bytes(original + b" ")
            rejected = subprocess.run([*base, "--output", str(parent / "tampered.json"), "--continuation", str(point), "--continuation-sha256", pin], capture_output=True, text=True)
            self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
            self.assertEqual(json.loads(rejected.stdout)["status"], "FAIL")

    def test_blocked_terminal_metadata_can_be_saved_and_rechecked(self):
        from infra.scripts.ci_audit_evidence import make_ci_audit_continuation

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements, completed = packet(root)
            partial = copy.deepcopy(completed)
            partial["jobs"][0]["conclusion"] = None
            self.assertEqual(verify_ci_audit(requirements, partial, root)["status"], "BLOCKED_EVIDENCE")
            point = make_ci_audit_continuation(requirements, partial, root)
            self.assertEqual(point["status"], "BLOCKED_EVIDENCE")
            result = verify_ci_audit(requirements, completed, root, continuation=point)
            self.assertEqual(result["status"], "PASS", result)

    def test_checkpoint_rejects_extra_or_invalid_state_fields(self):
        from infra.scripts.ci_audit_evidence import make_ci_audit_continuation

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements, completed = packet(root)
            pending = copy.deepcopy(completed)
            pending["jobs"][0].update(status="queued", conclusion=None)
            point = make_ci_audit_continuation(requirements, pending, root)
            for case in ("report_extra", "status", "status_type", "summary_type", "missing_content", "format", "unknown_label"):
                with self.subTest(case=case):
                    broken = copy.deepcopy(point)
                    if case == "report_extra":
                        broken["report"]["jobs"][0]["untrusted_payload"] = "must not be carried"
                    elif case == "status":
                        broken["status"] = "PASS"
                    elif case == "status_type":
                        broken["status"] = []
                    elif case == "summary_type":
                        broken["missing"] = []
                    elif case == "missing_content":
                        broken["missing"]["infrastructure"] = ["tampered"]
                    elif case == "format":
                        broken["format"] = "legacy"
                    else:
                        broken["checked"]["infrastructure"]["unrequested"] = {"path": "checkout.log", "sha256": "e" * 64}
                    result = verify_ci_audit(requirements, completed, root, continuation=broken)
                    self.assertEqual(result["status"], "FAIL", result)

    def test_resume_reauthenticates_previously_checked_files(self):
        from infra.scripts.ci_audit_evidence import make_ci_audit_continuation

        for case, expected in (("changed", "FAIL"), ("missing", "BLOCKED_EVIDENCE")):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                requirements, completed = packet(root)
                pending = copy.deepcopy(completed)
                pending["jobs"][0].update(status="in_progress", conclusion=None)
                point = make_ci_audit_continuation(requirements, pending, root)
                log = root / "checkout.log"
                fresh = artifact(root, "fresh-checkout.log", log.read_text())
                fresh["checkout_line"] = 3
                completed["jobs"][0]["log"] = fresh
                if case == "changed":
                    log.write_text("tampered old log")
                else:
                    log.unlink()
                self.assertEqual(verify_ci_audit(requirements, completed, root)["status"], "PASS")
                result = verify_ci_audit(requirements, completed, root, continuation=point)
                self.assertEqual(result["status"], expected, result)

    def test_pending_checkpoint_roundtrip_resumes_the_same_attempt(self):
        from infra.scripts.ci_audit_evidence import make_ci_audit_continuation

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements, completed = packet(root)
            pending = copy.deepcopy(completed)
            pending["jobs"][0].update(status="in_progress", conclusion=None)
            original = copy.deepcopy((requirements, pending))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            point = make_ci_audit_continuation(requirements, pending, root)
            self.assertEqual(point["status"], "PENDING")
            self.assertTrue(point["checked"]["infrastructure"])
            restored = json.loads(json.dumps(point))
            result = verify_ci_audit(requirements, completed, root, continuation=restored)
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["jobs"][0]["identity"]["run_attempt"], 1)
            self.assertEqual((requirements, pending), original)
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_additional_required_artifact_is_verified_and_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements, report = packet(root)
            requirements["jobs"][0]["artifacts"].append("details")
            job = report["jobs"][0]
            job["artifacts"]["details"] = artifact(root, "details.txt", "test details\n")
            proof = json.loads((root / "proof.json").read_text())
            proof["artifacts"] = job["artifacts"]
            job["source_proof"] = artifact(root, "proof.json", proof)
            self.assertEqual(verify_ci_audit(requirements, report, root)["status"], "PASS")
            (root / "details.txt").unlink()
            self.assertEqual(verify_ci_audit(requirements, report, root)["status"], "BLOCKED_EVIDENCE")

    def test_cli_writes_a_separate_result_and_refuses_to_overwrite_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "package"
            root.mkdir()
            requirements, report = packet(root)
            req = parent / "requirements.json"
            req.write_text(json.dumps(requirements))
            observed = root / "report.json"
            observed.write_text(json.dumps(report))
            output = parent / "audit.json"
            command = [sys.executable, "-m", "infra.scripts.ci_audit_evidence", "--root", str(root),
                       "--requirements", str(req), "--report", str(observed), "--output", str(output)]
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(json.loads(output.read_text())["status"], "PASS")
            self.assertEqual(json.loads(completed.stdout)["status"], "PASS")
            for target in (observed, output, req):
                original = target.read_bytes()
                rejected = subprocess.run(command[:-1] + [str(target)], capture_output=True, text=True)
                self.assertEqual(rejected.returncode, 1)
                self.assertEqual(original, target.read_bytes())

    def test_invalid_selection_cannot_silently_pass(self):
        for changed in ("empty", "duplicate", "bool_id", "missing_identity", "no_receipt", "duplicate_artifact", "no_format"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                requirements, report = packet(root)
                if changed == "empty":
                    requirements["jobs"] = []
                elif changed == "duplicate":
                    requirements["jobs"] *= 2
                elif changed == "bool_id":
                    requirements["jobs"][0]["run_attempt"] = True
                elif changed == "missing_identity":
                    requirements["jobs"][0].pop("job_id")
                elif changed == "no_receipt":
                    requirements["jobs"][0]["artifacts"] = []
                elif changed == "duplicate_artifact":
                    requirements["jobs"][0]["artifacts"] *= 2
                else:
                    requirements.pop("format")
                self.assertEqual(verify_ci_audit(requirements, report, root)["status"], "FAIL")

    def test_duplicate_unexpected_or_malformed_jobs_fail(self):
        for changed in ("duplicate", "extra", "not_object", "bool_id", "not_list"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                requirements, report = packet(root)
                if changed == "duplicate":
                    report["jobs"] *= 2
                elif changed == "extra":
                    extra = copy.deepcopy(report["jobs"][0])
                    extra["name"] = "unrequested"
                    report["jobs"].append(extra)
                elif changed == "not_object":
                    report["jobs"] = [None]
                elif changed == "bool_id":
                    report["jobs"][0]["run_attempt"] = True
                else:
                    report["jobs"] = {}
                self.assertEqual(verify_ci_audit(requirements, report, root)["status"], "FAIL")

    def test_unsafe_paths_and_aliases_fail(self):
        for changed in ("absolute", "traversal", "symlink", "alias"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                requirements, report = packet(root)
                entry = report["jobs"][0]["log"]
                if changed == "absolute":
                    entry["path"] = str(root / "checkout.log")
                elif changed == "traversal":
                    entry["path"] = "../" + root.name + "/checkout.log"
                elif changed == "symlink":
                    (root / "alias.log").symlink_to(root / "checkout.log")
                    entry["path"] = "alias.log"
                else:
                    proof = json.loads((root / "proof.json").read_text())
                    proof["source_after"] = proof["source_before"]
                    report["jobs"][0]["source_proof"] = artifact(root, "proof.json", proof)
                self.assertEqual(verify_ci_audit(requirements, report, root)["status"], "FAIL")

    def test_missing_metadata_is_blocked_and_legacy_report_is_not_upgraded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements, report = packet(root)
            report["jobs"][0].pop("job_id")
            self.assertEqual(verify_ci_audit(requirements, report, root)["status"], "BLOCKED_EVIDENCE")
            legacy = {"verdict": "PASS", "verification": {"ci_logs_downloaded": True}, "runs": {}}
            self.assertEqual(verify_ci_audit(requirements, legacy, root)["status"], "BLOCKED_EVIDENCE")

    def test_running_ci_stays_pending_and_terminal_failures_never_pass(self):
        for status in ("queued", "in_progress", "waiting"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                requirements, report = packet(root)
                job = report["jobs"][0]
                job.update(status=status, conclusion=None)
                for field in ("log", "source_proof", "artifacts", "verdict", "exit_code"):
                    job.pop(field)
                result = verify_ci_audit(requirements, report, root)
                self.assertEqual(result["status"], "PENDING", result)
                self.assertEqual(result["jobs"][0]["original"]["status"], status)
        for conclusion in ("failure", "cancelled", "timed_out", "neutral", "skipped", "action_required"):
            with self.subTest(conclusion=conclusion), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                requirements, report = packet(root)
                report["jobs"][0]["conclusion"] = conclusion
                result = verify_ci_audit(requirements, report, root)
                self.assertEqual(result["status"], "FAIL", result)
                self.assertEqual(result["jobs"][0]["original"]["conclusion"], conclusion)

    def test_hash_valid_but_wrong_checkout_proof_or_receipt_fails(self):
        for changed in ("log_sha", "log_command", "log_line", "proof_identity", "proof_status",
                        "snapshot_head", "snapshot_tree", "source_drift", "proof_receipt",
                        "receipt_verdict", "receipt_exit"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                requirements, report = packet(root)
                job = report["jobs"][0]
                proof = json.loads((root / "proof.json").read_text())
                if changed.startswith("log_"):
                    text = (root / "checkout.log").read_text()
                    if changed == "log_sha":
                        text = text.replace(MERGE, HEAD)
                    elif changed == "log_command":
                        text = text.replace("[command]/usr/bin/git log -1 --format=%H", "echo declaration")
                    job["log"] = artifact(root, "checkout.log", text)
                    job["log"]["checkout_line"] = 2 if changed == "log_line" else 3
                elif changed == "proof_identity":
                    proof["run_attempt"] = 2
                elif changed == "proof_status":
                    proof["status"] = "FAIL"
                elif changed.startswith("snapshot_") or changed == "source_drift":
                    snapshot = json.loads((root / "after.json").read_text())
                    if changed == "source_drift":
                        snapshot["files"]["source.py"]["sha256"] = "f" * 64
                    else:
                        snapshot["head" if changed == "snapshot_head" else "tree"] = HEAD
                    proof["source_after"] = artifact(root, "after.json", snapshot)
                    if changed != "source_drift":
                        proof["source_before"] = artifact(root, "before.json", snapshot)
                elif changed == "proof_receipt":
                    proof["artifacts"]["receipt"] = artifact(root, "foreign.json", {"status": "PASS", "exit": 0})
                else:
                    job["artifacts"]["receipt"] = artifact(root, "receipt.json", {
                        "status": "FAIL" if changed == "receipt_verdict" else "PASS",
                        "exit": 7 if changed == "receipt_exit" else 0,
                    })
                    proof["artifacts"] = job["artifacts"]
                job["source_proof"] = artifact(root, "proof.json", proof)
                self.assertEqual(verify_ci_audit(requirements, report, root)["status"], "FAIL")

    def test_changed_artifacts_or_foreign_job_identity_fail(self):
        for changed in ("checkout.log", "proof.json", "receipt.json"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                requirements, report = packet(root)
                with (root / changed).open("a") as stream:
                    stream.write("tamper")
                self.assertEqual(verify_ci_audit(requirements, report, root)["status"], "FAIL")
        for field, value in (("repository", "other/repo"), ("run_id", 11), ("run_attempt", 2),
                             ("job_id", 21), ("head_sha", BASE), ("base_sha", HEAD),
                             ("checkout_sha", HEAD), ("tree_sha", HEAD)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                requirements, report = packet(root)
                report["jobs"][0][field] = value
                self.assertEqual(verify_ci_audit(requirements, report, root)["status"], "FAIL")

    def test_missing_required_job_or_evidence_blocks_pass(self):
        for missing in ("job", "log", "proof", "before", "receipt"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                requirements, report = packet(root)
                report["ci_logs_downloaded"] = True
                if missing == "job":
                    report["jobs"].clear()
                else:
                    (root / {"log": "checkout.log", "proof": "proof.json",
                             "before": "before.json", "receipt": "receipt.json"}[missing]).unlink()
                result = verify_ci_audit(requirements, report, root)
                self.assertEqual(result["status"], "BLOCKED_EVIDENCE", result)

    def test_complete_merge_checkout_packet_passes_without_writing_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            requirements, report = packet(root)
            original = {p.name: p.read_bytes() for p in root.iterdir()}
            result = verify_ci_audit(requirements, report, root)
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["jobs"][0]["original"], {
                "status": "completed", "conclusion": "success", "verdict": "PASS", "exit_code": 0,
            })
            self.assertEqual(original, {p.name: p.read_bytes() for p in root.iterdir()})


if __name__ == "__main__":
    unittest.main()
