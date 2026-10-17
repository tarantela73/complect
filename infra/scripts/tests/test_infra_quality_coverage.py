"""Black-box tests for the infrastructure coverage CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "infra_quality_coverage.py"


class CoverageCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Path(self.directory.name) / "repo"
        self.repo.mkdir()
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "coverage-test")
        self._write("infra/mypy.ini", "[mypy]\npython_version = 3.12\n")
        self._write("infra/scripts/example.py", "value: int = 'bad'\n")
        self._git("add", ".")
        self._git("commit", "-qm", "base")
        self.base = self._git("rev-parse", "HEAD").strip()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, text=True, capture_output=True, check=True).stdout

    def _write(self, path: str, content: str) -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def _run(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.repo), *args],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def _finish_change(self) -> None:
        self._git("add", ".")

    def test_same_baseline_diagnostic_is_reported_as_fail_baseline(self) -> None:
        self._write("infra/scripts/example.py", "value: int = 'bad'\n# changed\n")
        self._finish_change()
        result = self._run("--base", self.base, "--report", str(self.repo / "report.json"))
        receipt = json.loads((self.repo / "report.json").read_text())
        self.assertEqual(result.returncode, 0, json.dumps(receipt, ensure_ascii=False))
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["files"]["infra/scripts/example.py"]["status"], "FAIL_BASELINE")

    def test_unchanged_error_after_inserted_lines_matches_real_baseline(self) -> None:
        self._write("infra/scripts/example.py", "# inserted before old code\n\nvalue: int = 'bad'\n")
        result = self._run("--base", self.base, "--report", str(self.repo / "report.json"))
        receipt = json.loads((self.repo / "report.json").read_text())
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(receipt["files"]["infra/scripts/example.py"]["status"], "FAIL_BASELINE")

    def test_identical_message_on_changed_source_line_is_new_error(self) -> None:
        self._write("infra/scripts/example.py", "other: int = 'bad'\n")
        result = self._run("--base", self.base, "--report", str(self.repo / "report.json"))
        receipt = json.loads((self.repo / "report.json").read_text())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt["files"]["infra/scripts/example.py"]["status"], "FAIL")

    def test_ruff_and_mypy_locations_shift_together_without_new_errors(self) -> None:
        self._write("infra/scripts/example.py", "import os\nvalue: int = 'bad'\n")
        self._git("add", ".")
        self._git("commit", "-qm", "baseline with unused import")
        base = self._git("rev-parse", "HEAD").strip()
        self._write("infra/scripts/example.py", "# harmless insertion\nimport os\nvalue: int = 'bad'\n")
        result = self._run("--base", base, "--report", str(self.repo / "report.json"))
        receipt = json.loads((self.repo / "report.json").read_text())
        self.assertEqual(result.returncode, 0, result.stdout)
        file_report = receipt["files"]["infra/scripts/example.py"]
        self.assertEqual(file_report["status"], "FAIL_BASELINE")
        self.assertEqual(file_report["checks"]["ruff"]["exit"], 1)
        self.assertEqual(file_report["checks"]["mypy"]["exit"], 1)

    def test_equal_size_and_mtime_sources_produce_fresh_diagnostic_locations(self) -> None:
        path = self.repo / "infra/scripts/new.py"
        self._write("infra/scripts/new.py", "\nvalue: int = 'bad'\n")
        original = path.stat()
        first = self._run("--base", self.base, "--report", str(self.repo / "report.json"))
        self.assertEqual(first.returncode, 1)
        self._write("infra/scripts/new.py", "value: int = 'bad'\n\n")
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertEqual(path.stat().st_size, original.st_size)
        self.assertEqual(path.stat().st_mtime_ns, original.st_mtime_ns)
        result = self._run("--base", self.base, "--report", str(self.repo / "report.json"))
        self.assertEqual(result.returncode, 1)
        receipt = json.loads((self.repo / "report.json").read_text())
        diagnostics = receipt["files"]["infra/scripts/new.py"]["checks"]["mypy"]["diagnostics"]
        self.assertEqual(len(diagnostics), 1)
        self.assertIn("infra/scripts/new.py:1: error:", diagnostics[0])

    def test_same_count_different_diagnostic_blocks(self) -> None:
        self._write("infra/scripts/example.py", "value: str = 1\n# changed\n")
        self._finish_change()
        result = self._run("--base", self.base, "--report", str(self.repo / "report.json"))
        receipt = json.loads((self.repo / "report.json").read_text())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertEqual(receipt["files"]["infra/scripts/example.py"]["baseline"], "CHANGED_DIAGNOSTICS")

    def test_new_file_and_changed_test_are_checked(self) -> None:
        self._write("infra/scripts/new.py", "value: int = 1\n")
        self._write("infra/scripts/tests/test_new.py", "def test_one() -> None:\n    assert True\n")
        self._finish_change()
        result = self._run("--base", self.base, "--report", str(self.repo / "report.json"))
        receipt = json.loads((self.repo / "report.json").read_text())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("infra/scripts/new.py", receipt["files"])
        self.assertIn("infra/scripts/tests/test_new.py", receipt["files"])

    def test_unknown_script_path_blocks_and_deleted_file_is_removed(self) -> None:
        (self.repo / "infra/scripts/example.py").unlink()
        self._write("infra/scripts/bad.sh", "echo bad\n")
        self._finish_change()
        result = self._run("--base", self.base, "--report", str(self.repo / "report.json"))
        receipt = json.loads((self.repo / "report.json").read_text())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("infra/scripts/bad.sh", receipt["unknown_paths"])
        self.assertEqual(receipt["files"]["infra/scripts/example.py"]["status"], "REMOVED")

    def test_missing_tool_is_blocked(self) -> None:
        self._write("infra/scripts/new.py", "value: int = 1\n")
        self._finish_change()
        result = self._run("--base", self.base, "--report", str(self.repo / "report.json"), env={"PATH": "/nonexistent"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BLOCKED_TOOLING", result.stdout)

    def test_corrupt_prepared_inventory_is_blocked(self) -> None:
        self._write("infra/scripts/new.py", "value: int = 1\n")
        self._finish_change()
        with tempfile.TemporaryDirectory() as package_dir:
            package = Path(package_dir)
            prepared = self._run("--base", self.base, "--prepare-input", str(package))
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            input_path = package / "input.json"
            payload = json.loads(input_path.read_text())
            payload["changes"] = payload["changes"][:-1]
            input_path.write_text(json.dumps(payload))
            result = self._run(
                "--coverage-input", str(input_path), "--report", str(self.repo / "report.json")
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BLOCKED", result.stdout)


if __name__ == "__main__":
    unittest.main()
