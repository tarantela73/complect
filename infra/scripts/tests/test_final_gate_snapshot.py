"""Final-gate behavior through the existing public working-tree snapshot seam."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infra.scripts import agent_harness as harness


class FinalGateSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = (
            "source.py",
            "tests/test_source.py",
            ".github/workflows/check.yml",
            "infra/config.ini",
            "docs/agents/instructions.md",
            "untracked.py",
        )
        for name in self.inputs:
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("original\n")
        self.outputs = {
            ("rev-parse", "--show-toplevel"): str(self.root) + "\n",
            ("ls-files", "--cached", "--others", "--exclude-standard", "-z"): "\0".join(
                self.inputs
            )
            + "\0",
            ("ls-files", "--deleted", "-z"): "",
            ("diff", "--name-only", "--diff-filter=D", "HEAD", "--"): "",
            ("rev-parse", "HEAD"): "committed-head\n",
        }
        self.index = {
            ("ls-files", "--stage", "-z"): b"index entries",
            ("ls-files", "-v", "-z"): b"index flags",
        }
        # Git is the external boundary; snapshot calculation and verification run unchanged.
        self.enterContext(
            patch.object(
                harness, "_git", side_effect=lambda _root, *args: self.outputs[args]
            )
        )
        self.enterContext(
            patch.object(
                harness, "_git_bytes", side_effect=lambda _root, *args: self.index[args]
            )
        )
        self.manifest = self.root / ".dev/test-artifacts/attempt-1/snapshot.json"
        self.manifest.parent.mkdir(parents=True)
        self.original = harness.working_tree_snapshot(self.root)
        self.manifest.write_text(json.dumps(self.original))

    def test_every_input_category_must_remain_frozen(self):
        for name in self.inputs:
            with self.subTest(name=name):
                p = self.root / name
                p.write_text("changed during gate\n")
                self.assertTrue(
                    harness.verify_working_tree_snapshot(self.root, self.manifest)
                )
                p.write_text("original\n")
                self.assertEqual(
                    harness.verify_working_tree_snapshot(self.root, self.manifest), []
                )
        p = self.root / self.inputs[0]
        previous = p.stat().st_mode
        p.chmod(0o755)
        self.assertTrue(harness.verify_working_tree_snapshot(self.root, self.manifest))
        p.chmod(previous)
        self.assertEqual(json.loads(self.manifest.read_text()), self.original)

    def test_excluded_logs_and_receipts_do_not_change_source_identity(self):
        names = (
            ".dev/test-artifacts/attempt-1/command.log",
            ".dev/test-artifacts/attempt-1/receipt.json",
        )
        inventory = ("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        for name in names:
            p = self.root / name
            p.write_text("first output")
            self.outputs[inventory] += name + "\0"
            self.assertEqual(
                harness.verify_working_tree_snapshot(self.root, self.manifest), []
            )
            p.write_text("continued output")
            self.assertEqual(
                harness.verify_working_tree_snapshot(self.root, self.manifest), []
            )
        self.assertEqual(harness.working_tree_snapshot(self.root), self.original)

    def test_head_index_inventory_and_deletion_each_invalidate_the_seal(self):
        for key in (("rev-parse", "HEAD"), ("ls-files", "--deleted", "-z")):
            with self.subTest(key=key):
                previous = self.outputs[key]
                self.outputs[key] = "changed\n"
                self.assertTrue(
                    harness.verify_working_tree_snapshot(self.root, self.manifest)
                )
                self.outputs[key] = previous
        for key in self.index:
            with self.subTest(key=key):
                previous = self.index[key]
                self.index[key] = b"changed index"
                self.assertTrue(
                    harness.verify_working_tree_snapshot(self.root, self.manifest)
                )
                self.index[key] = previous
        # Native Harness recorder JSON is source-owned, unlike .dev result artifacts.
        name = "docs/agents/harness-evidence/new.json"
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
        self.outputs[
            ("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        ] += name + "\0"
        self.assertTrue(harness.verify_working_tree_snapshot(self.root, self.manifest))
        self.assertEqual(json.loads(self.manifest.read_text()), self.original)

    def test_new_snapshot_admits_finished_changes_without_rewriting_history(self):
        p = self.root / self.inputs[0]
        p.write_text("finished fix\n")
        self.assertTrue(harness.verify_working_tree_snapshot(self.root, self.manifest))
        second = self.manifest.parent.parent / "attempt-2/snapshot.json"
        second.parent.mkdir()
        second.write_text(json.dumps(harness.working_tree_snapshot(self.root)))
        self.assertEqual(harness.verify_working_tree_snapshot(self.root, second), [])
        self.assertTrue(harness.verify_working_tree_snapshot(self.root, self.manifest))
        self.assertEqual(json.loads(self.manifest.read_text()), self.original)


if __name__ == "__main__":
    unittest.main()
