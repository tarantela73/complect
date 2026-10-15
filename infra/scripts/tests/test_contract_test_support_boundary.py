"""Focused negative coverage for the adopted TF-37 allowlist."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CHECKER = ROOT / "infra/scripts/ddd_boundary_linter.py"


class ContractTestSupportBoundaryTests(unittest.TestCase):
    def run_checker(
        self, consumer: str, *, runtime: str = "", neighbor: str = "", closure: str = ""
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "backend/apps/contracts/tests").mkdir(parents=True)
            (workspace / "backend/apps/files/tests").mkdir(parents=True)
            (workspace / "backend/apps/finance/tests").mkdir(parents=True)
            (workspace / "backend/apps/files/tests/factories.py").write_text(
                "from apps.files.models.file_record import FileRecord\n", encoding="utf-8"
            )
            (workspace / "backend/apps/finance/tests/factories.py").write_text(
                "from apps.finance.models.transaction import Transaction\n", encoding="utf-8"
            )
            (workspace / "backend/apps/contracts/tests/test_files_finance_boundary.py").write_text(
                consumer, encoding="utf-8"
            )
            if neighbor:
                (workspace / "backend/apps/contracts/tests/neighbor.py").write_text(
                    neighbor, encoding="utf-8"
                )
            if closure:
                (workspace / "backend/apps/contracts/tests/test_closure_service.py").write_text(
                    closure, encoding="utf-8"
                )
            if runtime:
                (workspace / "backend/apps/files").mkdir(parents=True, exist_ok=True)
                (workspace / "backend/apps/files/api.py").write_text(runtime, encoding="utf-8")
            checker = workspace / "infra/scripts/ddd_boundary_linter.py"
            checker.parent.mkdir(parents=True)
            shutil.copy2(CHECKER, checker)
            return subprocess.run(
                [sys.executable, str(checker)], cwd=workspace, text=True, capture_output=True, check=False
            )

    def test_exact_owner_symbols_are_allowed(self) -> None:
        result = self.run_checker(
            "from apps.files.tests.factories import make_file\n"
            "from apps.finance.tests.factories import make_posted_transaction\n"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_alias_wildcard_and_neighbor_symbol_are_rejected(self) -> None:
        cases = (
            "from apps.files.tests.factories import make_file as build_file\n",
            "from apps.finance.tests.factories import *\n",
            "from apps.files.tests.factories import other_builder\n",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertNotEqual(self.run_checker(source).returncode, 0)

    def test_runtime_support_import_is_rejected(self) -> None:
        result = self.run_checker(
            "from apps.files.tests.factories import make_file\n",
            runtime="from apps.files.tests.factories import make_file\n",
        )
        self.assertNotEqual(result.returncode, 0)

    def test_neighbor_consumer_is_rejected(self) -> None:
        result = self.run_checker(
            "",
            neighbor="from apps.files.tests.factories import make_file\n",
        )
        self.assertNotEqual(result.returncode, 0)

    def test_users_factory_exact_symbol_is_allowed_only_for_two_consumers(self) -> None:
        result = self.run_checker(
            "from apps.users.tests.iam_factories import make_contracts_boundary_actor\n",
            closure="from apps.users.tests.iam_factories import make_contracts_boundary_actor\n",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rejected = self.run_checker(
            "",
            neighbor="from apps.users.tests.iam_factories import make_contracts_boundary_actor\n",
        )
        self.assertNotEqual(rejected.returncode, 0)

    def test_dynamic_helper_and_reexport_paths_fail_closed(self) -> None:
        cases = (
            "import importlib\nimportlib.import_module('apps.files.tests.factories')\n",
            "import builtins\nbuiltins.__import__('apps.finance.tests.factories')\n",
            "import importlib\nname = 'apps.files.tests.factories'\nimportlib.import_module(name)\n",
            "from importlib import import_module as load\nload('apps.files.tests.factories')\n",
            "import importlib\n"
            "importlib.import_module('apps.users.tests.iam_factories.make_contracts_boundary_actor')\n",
            "from apps.files.tests.factories import make_file\n__all__ = ['make_file']\n",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertNotEqual(self.run_checker(source).returncode, 0)


if __name__ == "__main__":
    unittest.main()
