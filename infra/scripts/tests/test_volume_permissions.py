from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infra.scripts import volume_permissions


class VolumePermissionsTests(unittest.TestCase):
    def test_explicit_volume_scope_allows_legacy_stand_two_volume_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "state").mkdir()
            (root / "backend").mkdir()
            with patch.object(volume_permissions.os, "chown") as chown, patch.object(Path, "chmod"):
                volume_permissions.enforce_volume_ownership(1234, 2345, ("state", "backend"), root=root)
            self.assertEqual(chown.call_count, 2)

    def test_unknown_volume_is_rejected_by_cli_parser(self) -> None:
        with self.assertRaises(SystemExit):
            volume_permissions.parse_arguments(["1000", "1000", "--volumes", "arbitrary-path"])

    def test_default_scope_retains_all_development_volumes(self) -> None:
        uid, gid, names = volume_permissions.parse_arguments(["1000", "1000"])
        self.assertEqual((uid, gid), (1000, 1000))
        self.assertEqual(names, volume_permissions.DEFAULT_VOLUMES)


class FailedPrepareCleanupTests(unittest.TestCase):
    def test_cleanup_result_is_preserved_in_blocked_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ownership.json"
            receipt = {"status": "BLOCKED_STAND", "interruption": {"reason": "RuntimeError"}}
            path.write_text("{}\n")
            with patch("infra.scripts.prepare_legacy_stand.cleanup", return_value={
                "cleanup": "PASS", "cleanup_errors": [], "removed": ["container:owned"]
            }):
                from infra.scripts.prepare_legacy_stand import _cleanup_failed_prepare

                _cleanup_failed_prepare(path, receipt)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["cleanup"], "PASS")
            self.assertEqual(saved["removed"], ["container:owned"])


if __name__ == "__main__":
    unittest.main()
