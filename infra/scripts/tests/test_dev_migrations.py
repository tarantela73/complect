"""Public migration preflight contract; the runtime adapter uses real PostgreSQL."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class MigrationSafetyTests(unittest.TestCase):
    def load(self):
        spec = importlib.util.spec_from_file_location("dev_database", ROOT / "backend/config/dev_database.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_unknown_applied_migration_is_rejected(self):
        module = self.load()
        disk = {"users.0003": {"sha256": "a", "project": True}}
        with self.assertRaisesRegex(ValueError, "Unknown applied migration"):
            module.assess_migrations(disk, {"users.0003", "users.9999"}, {"approved": {"users.0003": "a"}}, None)

    def test_existing_schema_cannot_apply_a_bootstrap_only_fence(self):
        module = self.load()
        disk = {"users.0002": {"sha256": "a", "project": True}, "users.0003a": {"sha256": "b", "project": True}}
        policy = {"approved": {"users.0002": "a", "users.0003a": "b"}, "bootstrap_only": ["users.0003a"]}
        receipt = {"state": "ready", "applied": {"users.0002": "a"}}
        with self.assertRaisesRegex(ValueError, "Gated migration"):
            module.assess_migrations(disk, {"users.0002"}, policy, receipt)

    def test_applied_migration_content_drift_is_rejected(self):
        module = self.load()
        disk = {"objects.0006": {"sha256": "changed", "project": True}}
        receipt = {"state": "ready", "applied": {"objects.0006": "reviewed"}}
        with self.assertRaisesRegex(ValueError, "Applied migration content changed"):
            module.assess_migrations(disk, {"objects.0006"}, {"approved": {"objects.0006": "changed"}}, receipt)

    def test_only_known_complete_receipts_and_reviewed_forward_changes_are_allowed(self):
        module = self.load()
        disk = {"site.0001": {"sha256": "a", "project": True}, "site.0002": {"sha256": "b", "project": True}}
        policy = {"approved": {"site.0001": "a", "site.0002": "b"}}
        good = {"state": "ready", "applied": {"site.0001": "a"}}
        cases = [
            ({"site.0001"}, policy, None, "Unmanaged existing schema"),
            ({"site.0001"}, policy, {"state": "in_progress", "applied": {"site.0001": "a"}}, "Incomplete migration"),
            (set(), policy, good, "Migration receipt and ledger disagree"),
            ({"site.0001"}, {"approved": {"site.0001": "a"}}, good, "Unreviewed migration"),
        ]
        for applied, selected_policy, receipt, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                module.assess_migrations(disk, applied, selected_policy, receipt)
        self.assertEqual(module.assess_migrations(disk, {"site.0001"}, policy, good)["pending"], ["site.0002"])
        self.assertEqual(module.assess_migrations(disk, set(), policy, None)["pending"], ["site.0001", "site.0002"])


if __name__ == "__main__":
    unittest.main()
