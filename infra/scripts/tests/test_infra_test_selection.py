from __future__ import annotations

import unittest

from infra.scripts.infra_test_selection import (
    PAYMENTS_UNITTEST_NAMES,
    PYTEST_FILES,
    payments_unittest_names,
    pytest_paths,
)


class InfraTestSelectionTests(unittest.TestCase):
    def test_pytest_selection_is_shared_and_complete(self):
        self.assertEqual(len(PYTEST_FILES), 11)
        self.assertEqual(pytest_paths(root="/workspace")[0], "/workspace/infra/scripts/tests/test_pytest_lifecycle.py")
        self.assertIn("infra/scripts/tests/test_lifecycle_session.py", PYTEST_FILES)
        self.assertIn("infra/scripts/tests/test_resource_ownership_compatibility.py", PYTEST_FILES)
        self.assertIn("infra/scripts/tests/test_legacy_qualification.py", PYTEST_FILES)
        self.assertIn("infra/scripts/tests/test_qualification_processes.py", PYTEST_FILES)

    def test_payments_unittest_selection_includes_ownership(self):
        self.assertEqual(payments_unittest_names(), list(PAYMENTS_UNITTEST_NAMES))
        self.assertIn("infra.scripts.tests.test_docker_ownership.OwnershipProofTests", PAYMENTS_UNITTEST_NAMES)
