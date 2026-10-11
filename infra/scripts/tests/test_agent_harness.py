from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "agent_harness.py"
spec = importlib.util.spec_from_file_location("agent_harness", SCRIPT)
assert spec and spec.loader
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class AgentHarnessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "owned.py").write_text("owned")
        (self.root / "test.log").write_text(
            "Ran 1 test in 0.001s\n\nOK\n# Subtest: genuine regression\n# tests 1\n# pass 1\n# fail 0\n"
            "# skipped 0\n# cancelled 0\nHARNESS_GATE: PASS; ERP_ACCEPTANCE: NOT_RUN\n"
        )
        for name in harness.VERIFICATION_INPUTS:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("verification input fixture")

    def feature(self, identifier="feat-001", status="not-started", dependencies=None):
        return {
            "id": identifier,
            "name": "Behavior",
            "description": "Verifiable result",
            "status": status,
            "dependencies": dependencies or [],
            "files": ["owned.py"],
            "acceptance_criteria": ["Required behavior works"],
            "evidence": [
                {
                    "command": "bash infra/scripts/check_agent_harness.sh",
                    "producer": "agent_harness.py --record",
                    "timed_out": False,
                    "sources_unchanged_during_run": True,
                    "erp_runtime": "NOT_RUN",
                    "verification_status": "PASS",
                    "verification": {"python_tests": 1, "node_tests": 1, "skips": 0},
                    "exit_code": 0,
                    "log": "test.log",
                    "sha256": hashlib.sha256((self.root / "test.log").read_bytes()).hexdigest(),
                    "sources": {
                        name: hashlib.sha256((self.root / name).read_bytes()).hexdigest()
                        for name in {"owned.py", *harness.VERIFICATION_INPUTS}
                    },
                }
            ],
        }

    def errors(self, *features):
        return harness.state_errors(self.root, {"features": list(features)})

    def test_valid_dependency_chain(self):
        self.assertEqual(
            self.errors(self.feature(status="done"), self.feature("feat-002", "in-progress", ["feat-001"])), []
        )

    def test_empty_and_invalid_state(self):
        for state in (None, [], {}, {"features": []}, {"features": [None]}):
            with self.subTest(state=state):
                self.assertTrue(harness.state_errors(self.root, state))

    def test_invalid_status_and_duplicate_id(self):
        self.assertTrue(self.errors(self.feature(status="passing")))
        self.assertTrue(self.errors(self.feature(), self.feature()))

    def test_multiple_active_features(self):
        self.assertTrue(self.errors(self.feature(status="in-progress"), self.feature("feat-002", "in-progress")))

    def test_unknown_self_and_incomplete_dependencies(self):
        for dependencies in (["feat-999"], ["feat-001"]):
            self.assertTrue(self.errors(self.feature(dependencies=dependencies)))
        self.assertTrue(self.errors(self.feature(), self.feature("feat-002", "done", ["feat-001"])))

    def test_dependency_cycle(self):
        self.assertTrue(
            self.errors(self.feature(dependencies=["feat-002"]), self.feature("feat-002", dependencies=["feat-001"]))
        )

    def test_done_requires_successful_evidence(self):
        for evidence in ([], [{}], [{"command": "false", "exit_code": 1}]):
            feature = self.feature(status="done")
            feature["evidence"] = evidence
            self.assertTrue(self.errors(feature))

    def test_modified_or_missing_log(self):
        feature = self.feature(status="done")
        (self.root / "test.log").write_text("changed")
        self.assertTrue(self.errors(feature))
        (self.root / "test.log").unlink()
        self.assertTrue(self.errors(feature))

    def test_source_changes_invalidate_evidence(self):
        feature = self.feature(status="done")
        (self.root / "owned.py").write_text("new behavior")
        self.assertTrue(self.errors(feature))

    def test_absolute_traversal_and_symlink_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "external"
            target.write_text("external")
            (self.root / "link").symlink_to(target)
            for name in (str(target), "../external", "link"):
                self.assertIsNone(harness.local_file(self.root, name))

    def test_malformed_fields_do_not_crash(self):
        for field, value in (
            ("status", []),
            ("dependencies", [None]),
            ("files", [False]),
            ("acceptance_criteria", None),
        ):
            feature = self.feature()
            feature[field] = value
            if field == "status":
                feature["dependencies"] = ["feat-002"]
            self.assertTrue(self.errors(feature))

    def test_empty_repository_fails_assessment(self):
        result = harness.assess(self.root)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["erp_runtime"], "NOT_RUN")

    def test_evidence_is_data_never_executed(self):
        feature = self.feature(status="done")
        feature["evidence"][0]["command"] = "touch sentinel"
        self.assertTrue(self.errors(feature))
        self.assertFalse((self.root / "sentinel").exists())

    def test_failed_or_boolean_exit_code_rejected(self):
        for code in (1, False, "0"):
            feature = self.feature(status="done")
            feature["evidence"][0]["exit_code"] = code
            self.assertTrue(self.errors(feature))

    def test_unrelated_source_cannot_cover_owned_file(self):
        feature = self.feature(status="done")
        (self.root / "unrelated.py").write_text("unrelated")
        feature["evidence"][0]["sources"] = {"unrelated.py": hashlib.sha256(b"unrelated").hexdigest()}
        self.assertTrue(self.errors(feature))

    def test_skill_provenance_detects_changes_and_extra_files(self):
        base = self.root / ".agents/skills/harness-creator"
        base.mkdir(parents=True)
        source = base / "SKILL.md"
        source.write_text("skill")
        manifest = self.root / "docs/agents/harness-skill-provenance.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps({"files": [{"path": "SKILL.md", "installed_sha256": hashlib.sha256(b"skill").hexdigest()}]})
        )
        self.assertEqual(harness.provenance_errors(self.root), [])
        source.write_text("changed")
        self.assertTrue(harness.provenance_errors(self.root))
        source.write_text("skill")
        (base / "unexpected").write_text("extra")
        self.assertTrue(harness.provenance_errors(self.root))

    def test_container_archive_and_argv_exclude_erp_secrets_and_mounts(self):
        repository = SCRIPT.parents[2]
        script = self.root / "infra/scripts/check_agent_harness.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repository / "infra/scripts/check_agent_harness.sh", script)
        for name in (
            "AGENTS.md",
            "Makefile",
            "feature_list.json",
            "progress.md",
            "session-handoff.md",
            "init.sh",
            "docs/agents/harness.md",
            "docs/agents/harness-assessment.md",
            "docs/agents/capabilities.md",
            "docs/agents/harness-skill-provenance.json",
            "infra/scripts/agent_harness.py",
            "infra/scripts/tests/test_agent_harness.py",
            "infra/scripts/tests/harness_skill.test.mjs",
        ):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture")
        for name in (
            "docs/agents/harness-evidence",
            ".agents/skills/harness-creator",
            ".git",
            ".aws",
            "backend",
            "frontend",
        ):
            (self.root / name).mkdir(parents=True, exist_ok=True)
            (self.root / name / "private").write_text("private")
        (self.root / ".env").write_text("private")
        binary = self.root / "bin"
        binary.mkdir()
        docker = binary / "docker"
        docker.write_text(
            """#!/usr/bin/env python3
import sys, os, json
from pathlib import Path
p = Path(os.environ['PROBE'])
with p.open('a') as f:
    f.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'image':
    print('sha256:' + 'a' * 64)
else:
    data = sys.stdin.buffer.read()
    with (p.parent / 'archives').open('ab') as f:
        f.write(len(data).to_bytes(8, 'big') + data)
"""
        )
        docker.chmod(0o755)
        probe = self.root / "calls.jsonl"
        result = subprocess.run(
            ["bash", str(script)],
            env={**os.environ, "PATH": f"{binary}:{os.environ['PATH']}", "PROBE": str(probe)},
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in probe.read_text().splitlines()]
        for call in calls:
            if call[0] == "image":
                self.assertIn("@sha256:", call[-1])
                continue
            self.assertEqual(call[call.index("--network") + 1], "none")
            self.assertIn("--read-only", call)
            self.assertIn("no-new-privileges", call)
            self.assertNotIn("-v", call)
            self.assertNotIn("--mount", call)
        data = (self.root / "archives").read_bytes()
        while data:
            length = int.from_bytes(data[:8], "big")
            with tarfile.open(fileobj=io.BytesIO(data[8 : 8 + length])) as archive:
                names = archive.getnames()
                for name in names:
                    self.assertNotIn(name.split("/")[0], {".env", ".aws", ".git", "backend", "frontend"})
            data = data[8 + length :]
        self.assertIn("ERP_ACCEPTANCE: NOT_RUN", result.stdout)

    def test_timeout_preserves_partial_output_and_failed_receipt(self):
        (self.root / "feature_list.json").write_text(json.dumps({"features": [self.feature(status="in-progress")]}))
        error = subprocess.TimeoutExpired(["bash"], 180, output=b"partial stdout", stderr=b"partial stderr")
        with (
            patch("sys.argv", ["agent_harness.py", "--root", str(self.root), "--record"]),
            patch.object(harness, "assess", return_value={"errors": []}),
            patch.object(harness.subprocess, "run", side_effect=error),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(harness.main(), 124)
        receipts = list((self.root / "docs/agents/harness-evidence").glob("*.json"))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text())
        self.assertEqual(receipt["exit_code"], 124)
        self.assertTrue(receipt["timed_out"])
        log = (self.root / receipt["log"]).read_text()
        self.assertIn("partial stdout", log)
        self.assertIn("partial stderr", log)

    def test_completion_rejects_missing_checks_and_zero_tests(self):
        feature = self.feature(status="done")
        (self.root / "test.log").write_text("Only structural validation passed\n")
        feature["evidence"][0]["sha256"] = hashlib.sha256((self.root / "test.log").read_bytes()).hexdigest()
        self.assertTrue(self.errors(feature))
        (self.root / "test.log").write_text(
            "Ran 0 tests in 0.00s\nOK\n# tests 0\n# pass 0\n# fail 0\n# skipped 0\n"
            "HARNESS_GATE: PASS; ERP_ACCEPTANCE: NOT_RUN\n"
        )
        feature["evidence"][0]["sha256"] = hashlib.sha256((self.root / "test.log").read_bytes()).hexdigest()
        self.assertTrue(self.errors(feature))

    def test_completion_requires_independent_verification_inputs(self):
        feature = self.feature(status="done")
        sources = feature["evidence"][0]["sources"]
        sources.pop("infra/scripts/tests/test_agent_harness.py")
        self.assertTrue(self.errors(feature))
        for name in ("infra/scripts/tests/test_agent_harness.py", "infra/scripts/check_agent_harness.sh", "Makefile"):
            feature = self.feature(status="done")
            path = self.root / name
            path.write_text("changed after verification")
            self.assertTrue(self.errors(feature))
            path.write_text("verification input fixture")

    def test_empty_node_file_is_not_a_test_execution(self):
        text = (
            (self.root / "test.log")
            .read_text()
            .replace("# Subtest: genuine regression", "# Subtest: tests/empty.test.mjs")
        )
        with self.assertRaises(ValueError):
            harness.verification_summary(text)

    def test_skipped_or_incomplete_summaries_are_rejected(self):
        text = (self.root / "test.log").read_text()
        for broken in (
            text.replace("# skipped 0", "# skipped 1"),
            text.replace("# cancelled 0\n", ""),
            text.replace("\nOK\n", "\nOK (skipped=1)\n"),
        ):
            with self.assertRaises(ValueError):
                harness.verification_summary(broken)


    def test_snapshot_requires_exact_current_inventory(self):
        (self.root / "tracked.py").write_text("tracked")
        (self.root / "new.py").write_text("untracked")
        (self.root / ".env").write_text("secret")
        (self.root / ".env.example").write_text("template")
        (self.root / ".env.dev.example").write_text("template")
        (self.root / ".codex").mkdir()
        (self.root / ".codex/config.toml").write_text("secret")
        manifest = self.root / "snapshot.json"
        git_outputs = {
            ("rev-parse", "--show-toplevel"): str(self.root) + "\n",
            ("ls-files", "--cached", "--others", "--exclude-standard", "-z"): "tracked.py\0new.py\0.env\0.env.example\0.env.dev.example\0.codex/config.toml\0",
            ("ls-files", "--deleted", "-z"): "removed.py\0",
            ("diff", "--name-only", "--diff-filter=D", "HEAD", "--"): "",
            ("rev-parse", "HEAD"): "head\n",
        }
        with (
            patch.object(harness, "_git", side_effect=lambda _root, *args: git_outputs[args]),
            patch.object(harness, "_git_bytes", return_value=b"index"),
        ):
            manifest.write_text(json.dumps(harness.working_tree_snapshot(self.root)))
            self.assertEqual(harness.verify_working_tree_snapshot(self.root, manifest), [])
            (self.root / "tracked.py").write_text("changed")
            self.assertTrue(harness.verify_working_tree_snapshot(self.root, manifest))
        self.assertNotIn(".env", json.loads(manifest.read_text())["files"])
        self.assertIn(".env.example", json.loads(manifest.read_text())["files"])
        self.assertIn(".env.dev.example", json.loads(manifest.read_text())["files"])
        self.assertNotIn(".codex/config.toml", json.loads(manifest.read_text())["files"])
        self.assertIn("new.py", json.loads(manifest.read_text())["files"])
        self.assertIn("removed.py", json.loads(manifest.read_text())["deleted"])

    def test_snapshot_rejects_symlink_outside_checkout(self):
        link = self.root / "link"
        link.symlink_to(Path("/tmp/harness-secret-target"))
        git_outputs = {
            ("rev-parse", "--show-toplevel"): str(self.root) + "\n",
            ("ls-files", "--cached", "--others", "--exclude-standard", "-z"): "link\0",
            ("ls-files", "--deleted", "-z"): "",
            ("diff", "--name-only", "--diff-filter=D", "HEAD", "--"): "",
            ("rev-parse", "HEAD"): "head\n",
        }
        with (
            patch.object(harness, "_git", side_effect=lambda _root, *args: git_outputs[args]),
            patch.object(harness, "_git_bytes", return_value=b"index"),
        ):
            with self.assertRaises(ValueError):
                harness.working_tree_snapshot(self.root)

    def test_snapshot_records_symlink_target_identity(self):
        (self.root / "a").write_text("same")
        (self.root / "b").write_text("same")
        (self.root / "link").symlink_to("a")
        git_outputs = {
            ("rev-parse", "--show-toplevel"): str(self.root) + "\n",
            ("ls-files", "--cached", "--others", "--exclude-standard", "-z"): "a\0b\0link\0",
            ("ls-files", "--deleted", "-z"): "",
            ("diff", "--name-only", "--diff-filter=D", "HEAD", "--"): "",
            ("rev-parse", "HEAD"): "head\n",
        }
        with (
            patch.object(harness, "_git", side_effect=lambda _root, *args: git_outputs[args]),
            patch.object(harness, "_git_bytes", return_value=b"index"),
        ):
            manifest = harness.working_tree_snapshot(self.root)
        self.assertEqual(manifest["files"]["link"]["target"], "a")
        (self.root / "link").unlink()
        (self.root / "link").symlink_to("b")
        with (
            patch.object(harness, "_git", side_effect=lambda _root, *args: git_outputs[args]),
            patch.object(harness, "_git_bytes", return_value=b"index"),
        ):
            current = harness.working_tree_snapshot(self.root)
        self.assertNotEqual(manifest["files"]["link"], current["files"]["link"])


    def test_snapshot_includes_only_referenced_ignored_harness_receipt_logs(self):
        log = self.root / "docs/agents/harness-evidence/current.log"
        log.parent.mkdir(parents=True)
        log.write_text("verified harness output")
        (log.parent / "unreferenced.log").write_text("unrelated ignored data")
        (self.root / "feature_list.json").write_text(json.dumps({"features": [
            {"evidence": [{"log": "docs/agents/harness-evidence/current.log"},
                          {"log": ".dev/runtime.log"}]}]}))
        git_outputs = {
            ("rev-parse", "--show-toplevel"): str(self.root) + "\n",
            ("ls-files", "--cached", "--others", "--exclude-standard", "-z"): "owned.py\0feature_list.json\0",
            ("ls-files", "--deleted", "-z"): "",
            ("diff", "--name-only", "--diff-filter=D", "HEAD", "--"): "",
            ("rev-parse", "HEAD"): "head\n",
        }
        with (patch.object(harness, "_git", side_effect=lambda _root, *args: git_outputs[args]),
              patch.object(harness, "_git_bytes", return_value=b"index")):
            current = harness.working_tree_snapshot(self.root)
            self.assertIn("docs/agents/harness-evidence/current.log", current["files"])
            self.assertNotIn("docs/agents/harness-evidence/unreferenced.log", current["files"])
            self.assertNotIn(".dev/runtime.log", current["files"])
            manifest = self.root / "manifest.json"
            manifest.write_text(json.dumps(current))
            log.write_text("changed evidence")
            self.assertTrue(harness.verify_working_tree_snapshot(self.root, manifest))


if __name__ == "__main__":
    unittest.main()
