"""B1 migration policy contract at the public assess_migrations seam."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXPAND = "users.0003b_cleanup_admission_expand"
INDEXES = "users.0003b1_cleanup_admission_indexes"
FOUNDATION = (EXPAND, INDEXES)


def load_assessor():
    spec = importlib.util.spec_from_file_location("dev_database", ROOT / "config/dev_database.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.assess_migrations


def migration_disk():
    paths = {
        EXPAND: ROOT / "apps/users/migrations/0003b_cleanup_admission_expand.py",
        INDEXES: ROOT / "apps/users/migrations/0003b1_cleanup_admission_indexes.py",
    }
    return {
        name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "project": True} for name, path in paths.items()
    }


BASELINE = "users.baseline"


def managed_disk():
    disk = migration_disk()
    disk[BASELINE] = {"sha256": "base", "project": True}
    return disk


def ready_receipt(applied: set[str], disk: dict) -> dict:
    return {"state": "ready", "applied": {name: disk[name]["sha256"] for name in applied}}


class CleanupFoundationPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assess = staticmethod(load_assessor())
        cls.disk = migration_disk()
        cls.managed = managed_disk()
        cls.policy = json.loads((ROOT / "config/dev_migration_policy.json").read_text())
        cls.policy["approved"][BASELINE] = "base"

    def test_policy_contains_exact_foundation_hashes_and_bootstrap_gates(self):
        expected = {
            EXPAND: "b2cdcb849cab7259ac3e7bd39e25520d716d7702dac8ebddc7f2a7d345438931",
            INDEXES: "45946cef3f3d26896320cc132d52cb42b962b8ec4171a86d8aa4b4d1c889c991",
        }
        self.assertEqual({name: self.policy["approved"].get(name) for name in FOUNDATION}, expected)
        self.assertTrue(set(FOUNDATION) <= set(self.policy["bootstrap_only"]))

    def test_matrix_a_fresh_install_is_allowed(self):
        self.assertEqual(
            self.assess(self.disk, set(), self.policy, None)["pending"],
            sorted(FOUNDATION),
        )

    def test_complete_foundation_with_matching_receipt_is_ready(self):
        self.assertEqual(
            self.assess(self.managed, set(self.managed), self.policy, ready_receipt(set(self.managed), self.managed))[
                "pending"
            ],
            [],
        )

    def test_matrix_c_existing_schema_without_receipt_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unmanaged existing schema"):
            self.assess(self.managed, {BASELINE}, self.policy, None)

    def test_matrix_b_managed_schema_with_correct_receipt_cannot_upgrade(self):
        with self.assertRaisesRegex(ValueError, "Gated migration"):
            self.assess(self.managed, {BASELINE}, self.policy, ready_receipt({BASELINE}, self.managed))

    def test_matrix_d_partial_bootstrap_is_rejected(self):
        receipt = {"state": "in_progress", "applied": {}}
        with self.assertRaisesRegex(ValueError, "Incomplete migration batch"):
            self.assess(self.managed, {BASELINE, EXPAND}, self.policy, receipt)

    def test_receipt_ledger_disagreement_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Migration receipt and ledger disagree"):
            self.assess(self.managed, {BASELINE, EXPAND}, self.policy, ready_receipt({BASELINE}, self.managed))

    def test_unknown_pending_migration_is_unreviewed(self):
        disk = copy.deepcopy(self.disk)
        disk["users.unknown"] = {"sha256": "unknown", "project": True}
        with self.assertRaisesRegex(ValueError, "Unreviewed migration"):
            self.assess(disk, set(), self.policy, None)

    def test_changed_pending_pinned_content_is_unreviewed(self):
        disk = copy.deepcopy(self.disk)
        disk[INDEXES]["sha256"] = "changed"
        with self.assertRaisesRegex(ValueError, "Unreviewed migration"):
            self.assess(disk, set(), self.policy, None)

    def test_matrix_e_unknown_applied_migration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown applied migration"):
            self.assess(self.managed, {BASELINE, "users.unknown"}, self.policy, None)

    def test_matrix_f_changed_pinned_content_is_rejected(self):
        disk = copy.deepcopy(self.managed)
        disk[EXPAND]["sha256"] = "changed"
        receipt = ready_receipt({EXPAND}, self.managed)
        with self.assertRaisesRegex(ValueError, "Applied migration content changed"):
            self.assess(disk, {EXPAND}, self.policy, receipt)

    def test_missing_approved_hash_is_rejected_at_public_gate(self):
        policy = copy.deepcopy(self.policy)
        policy["approved"].pop(INDEXES, None)
        with self.assertRaisesRegex(ValueError, "Unreviewed migration"):
            self.assess(self.disk, set(), policy, None)

    def test_wrong_approved_hash_is_rejected_at_public_gate(self):
        policy = copy.deepcopy(self.policy)
        policy["approved"][INDEXES] = "0" * 64
        with self.assertRaisesRegex(ValueError, "Unreviewed migration"):
            self.assess(self.disk, set(), policy, None)


if __name__ == "__main__":
    unittest.main()
