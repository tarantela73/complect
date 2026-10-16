"""Attempt identity through public freeze and status CLI; no daemon or services."""

from __future__ import annotations
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from infra.scripts.run_legacy_qualification import freeze_source

ROOT = Path(__file__).resolve().parents[3]


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


class AttemptIdentityTests(unittest.TestCase):
    def source(self, base, modern=True):
        root = base / "source"
        root.mkdir()
        git(root, "init", "-q")
        git(root, "config", "user.name", "Test")
        git(root, "config", "user.email", "test@example.invalid")
        (root / ".gitignore").write_text(".dev/\n")
        (root / "infra/scripts").mkdir(parents=True)
        for name in (
            "run_legacy_qualification.py",
            "prepare_legacy_stand.py",
            "run_legacy_regressions.py",
        ):
            (root / "infra/scripts" / name).write_text("# fixture source\n")
        (root / "infra/qualification-protocol.json").write_text('{"version":1}')
        if modern:
            (root / "infra/attempt-identity-protocol.json").write_text('{"version":1}')
        git(root, "add", ".")
        git(root, "commit", "-qm", "fixture")
        return root, git(root, "rev-parse", "HEAD")

    def status(self, directory):
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "infra.scripts.run_legacy_qualification",
                "status",
                "--directory",
                str(directory),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        return result.returncode, json.loads(result.stdout)

    def test_two_attempts_same_commit_have_distinct_identity_and_readonly_status(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            root, sha = self.source(base)
            first, second = base / "first", base / "second"
            freeze_source(root, sha, first)
            freeze_source(root, sha, second)
            a = json.loads((first / "identity.json").read_text())
            b = json.loads((second / "identity.json").read_text())
            self.assertNotEqual(a["attempt_id"], b["attempt_id"])
            before = {
                str(p.relative_to(first)): p.read_bytes()
                for p in first.rglob("*")
                if p.is_file()
            }
            code, value = self.status(first)
            self.assertEqual(code, 0, value)
            self.assertEqual(value["attempt_id"], a["attempt_id"])
            self.assertEqual(value["source_commit"], sha)
            self.assertEqual(value["phase"], "frozen")
            self.assertEqual(value["original_verdict"], "NOT_RUN")
            self.assertEqual(
                before,
                {
                    str(p.relative_to(first)): p.read_bytes()
                    for p in first.rglob("*")
                    if p.is_file()
                },
            )

    def test_new_missing_identity_blocks_but_legacy_keeps_prior_contract(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            root, sha = self.source(base)
            attempt = base / "attempt"
            freeze_source(root, sha, attempt)
            (attempt / "identity.json").unlink()
            code, value = self.status(attempt)
            self.assertEqual(code, 2, value)
            self.assertEqual(value["status"], "BLOCKED_STAND")
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            root, sha = self.source(base, modern=False)
            attempt = base / "attempt"
            freeze_source(root, sha, attempt)
            (attempt / "qualification.json").write_text('{"status":"BLOCKED_STAND"}')
            code, value = self.status(attempt)
            self.assertEqual(code, 0, value)
            self.assertEqual(value["binding"], "LEGACY")
            self.assertEqual(value["original_verdict"], "BLOCKED_STAND")
            self.assertFalse((attempt / "identity.json").exists())

    def test_artifact_swap_or_unbound_new_log_blocks_without_rewriting_evidence(self):
        from infra.scripts.run_legacy_qualification import write_record

        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            root, sha = self.source(base)
            attempt = base / "attempt"
            freeze_source(root, sha, attempt)
            write_record(
                attempt / "qualification.json", {"status": "FAIL", "primary_exit": 23}
            )
            before = (attempt / "qualification.json").read_bytes()
            code, value = self.status(attempt)
            self.assertEqual(code, 0, value)
            self.assertEqual(value["original_verdict"], "FAIL")
            self.assertIn("qualification.json", value["artifacts"])
            (attempt / "qualification.json").write_text('{"status":"PASS"}')
            code, value = self.status(attempt)
            self.assertEqual(code, 2, value)
            (attempt / "qualification.json").write_bytes(before)
            (attempt / "processes").mkdir(mode=0o700)
            (attempt / "processes/prepare.log").write_text("foreign log")
            code, value = self.status(attempt)
            self.assertEqual(code, 2, value)
            self.assertEqual((attempt / "qualification.json").read_bytes(), before)

    def test_changed_attempt_id_or_source_anchor_blocks(self):
        from infra.scripts.run_legacy_qualification import write_record

        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            root, sha = self.source(base)
            attempt = base / "attempt"
            freeze_source(root, sha, attempt)
            write_record(attempt / "qualification.json", {"status": "FAIL"})
            original = (attempt / "identity.json").read_bytes()
            for field, replacement in (
                ("attempt_id", "11111111-1111-4111-8111-111111111111"),
                ("source_commit", "a" * 40),
                ("version", True),
            ):
                value = json.loads(original)
                value[field] = replacement
                (attempt / "identity.json").write_text(json.dumps(value))
                code, outcome = self.status(attempt)
                self.assertEqual(code, 2, outcome)
                (attempt / "identity.json").write_bytes(original)

    def test_real_stage_binds_log_and_rejects_later_replacement(self):
        from infra.scripts.qualification_processes import run_stage

        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            root, _ = self.source(base)
            for module in (
                "run_legacy_qualification.py",
                "qualification_processes.py",
                "attempt_identity.py",
            ):
                (root / "infra/scripts" / module).write_bytes(
                    (ROOT / "infra/scripts" / module).read_bytes()
                )
            git(root, "add", ".")
            git(root, "commit", "-qm", "real supervisor")
            sha = git(root, "rev-parse", "HEAD")
            attempt = base / "attempt"
            freeze_source(root, sha, attempt)
            self.assertEqual(
                run_stage(
                    attempt,
                    "prepare",
                    [sys.executable, "-c", "print('stage proof')"],
                    attempt / "frozen",
                ),
                0,
            )
            code, value = self.status(attempt)
            self.assertEqual(code, 0, value)
            binding = value["artifacts"]["processes/prepare.log"]
            self.assertEqual(binding["stage"], "prepare")
            self.assertEqual(binding["attempt_id"], value["attempt_id"])
            (attempt / "processes/prepare.log").write_text("another attempt")
            code, value = self.status(attempt)
            self.assertEqual(code, 2, value)
