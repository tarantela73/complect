from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "agent_harness.py"


class SnapshotCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "tracked.txt").write_text("content\n")

        git = shutil.which("git")
        if git is None:
            self.skipTest("git is required for the public CLI fixture")
        subprocess.run([git, "init", "-q", str(self.root)], check=True)
        subprocess.run([git, "-C", str(self.root), "add", "tracked.txt"], check=True)
        subprocess.run(
            [git, "-C", str(self.root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "initial"],
            check=True,
        )

    def run_cli(self, *arguments: str) -> tuple[int, str]:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *arguments],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(SCRIPT.parents[2])},
            check=False,
        )
        return completed.returncode, completed.stdout

    def test_default_record_snapshot_still_prints_full_manifest(self) -> None:
        destination = self.root.parent / "snapshot.json"
        code, output = self.run_cli("--record-snapshot", str(destination))

        self.assertEqual(code, 0)
        printed = json.loads(output)
        self.assertEqual(printed, json.loads(destination.read_text()))
        self.assertIn("files", printed)

    def test_summary_prints_saved_path_and_hash_without_inventory(self) -> None:
        for index in range(250):
            (self.root / f"inventory-{index}.txt").write_text("x")
        destination = self.root.parent / "snapshot.json"
        code, output = self.run_cli("--record-snapshot", str(destination), "--snapshot-summary")

        self.assertEqual(code, 0)
        summary = json.loads(output)
        self.assertEqual(summary, {
            "status": "PASS",
            "path": str(destination),
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        })
        self.assertNotIn("files", summary)
        self.assertLess(len(output), 256)

    def test_summary_requires_record_snapshot(self) -> None:
        code, _ = self.run_cli("--snapshot-summary")
        self.assertEqual(code, 2)

    def test_snapshot_write_failure_is_nonzero(self) -> None:
        destination = self.root / "occupied" / "snapshot.json"
        (self.root / "occupied").write_text("not a directory")

        code, output = self.run_cli("--record-snapshot", str(destination), "--snapshot-summary")

        self.assertEqual(code, 1)
        self.assertIn("snapshot failed", output)

    def test_stale_verification_remains_a_failure(self) -> None:
        destination = self.root.parent / "snapshot.json"
        self.assertEqual(self.run_cli("--record-snapshot", str(destination))[0], 0)
        (self.root / "tracked.txt").write_text("changed\n")
        code, output = self.run_cli("--verify-snapshot", str(destination))

        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
