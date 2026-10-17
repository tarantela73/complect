from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ImplementStartupTests(unittest.TestCase):
    def fixture(self, parent: Path) -> Path:
        root = parent / "checkout"
        (root / "docs/agents").mkdir(parents=True)
        source_root = Path(__file__).resolve().parents[3]
        shutil.copyfile(source_root / "docs/agents/diagnostic-output.md", root / "docs/agents/diagnostic-output.md")
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        return root

    def run_startup(self, root: Path, output: Path, command: list[str], secret: str | None = None) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
        if secret is not None:
            env["STARTUP_TEST_SECRET"] = secret
        args = [sys.executable, "-B", "-m", "infra.scripts.implement_startup",
                "--root", str(root), "--output-dir", str(output)]
        if secret is not None:
            args += ["--redact-env", "STARTUP_TEST_SECRET"]
        args += ["--", *command]
        return subprocess.run(
            args,
            cwd=root, env=env, text=True, capture_output=True,
        )

    def test_public_startup_saves_redacted_full_outputs_and_one_bounded_summary(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.fixture(Path(raw))
            output = Path(raw) / "artifacts"
            output.mkdir()
            secret = "startup-secret-value"
            completed = self.run_startup(
                root, output,
                [sys.executable, "-c", "import sys; print('secret=' + __import__('os').environ['STARTUP_TEST_SECRET']); print('x' * 9000)"],
                secret,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertLessEqual(len(completed.stdout.encode()), 4096)
            self.assertIn("[TRUNCATED]", completed.stdout)
            command = (output / "command.json").read_text()
            self.assertNotIn(secret, command)
            self.assertIn("[REDACTED]", command)
            summary = (output / "summary.txt").read_text()
            self.assertIn("command", summary)
            self.assertTrue((output / "status.bin").exists())

    def test_primary_nonzero_is_preserved(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.fixture(Path(raw))
            output = Path(raw) / "artifacts"
            output.mkdir()
            completed = self.run_startup(root, output, [sys.executable, "-c", "print('failed'); raise SystemExit(17)"])
            self.assertEqual(completed.returncode, 17, completed.stdout + completed.stderr)
            native = json.loads((output / "command.json").read_text())
            self.assertEqual(native["primary_exit"], 17)

    def test_diagnostic_write_failure_blocks_successful_primary(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.fixture(Path(raw))
            output = Path(raw) / "artifacts"
            output.mkdir()
            (output / "command.json").write_text("occupied")
            completed = self.run_startup(root, output, [sys.executable, "-c", "print('ok')"])
            self.assertEqual(completed.returncode, 2)

    def test_selected_document_sections_are_saved_fully_and_share_summary_budget(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.fixture(Path(raw))
            output = Path(raw) / "artifacts"
            output.mkdir()
            completed = subprocess.run(
                [sys.executable, "-B", "-m", "infra.scripts.implement_startup",
                 "--root", str(root), "--output-dir", str(output),
                 "--section", "docs/agents/diagnostic-output.md", "Штатное наблюдение",
                 "--", sys.executable, "-c", "print('ok')"],
                cwd=root, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3])},
                text=True, capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertIn("Штатное наблюдение", (output / "documents.txt").read_text())
            self.assertIn("documents", (output / "summary.txt").read_text())
            self.assertLessEqual(len(completed.stdout.encode()), 4096)

    def test_signal_exit_is_preserved_in_artifact_and_mapped_for_cli(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.fixture(Path(raw))
            output = Path(raw) / "artifacts"
            output.mkdir()
            completed = self.run_startup(
                root, output,
                [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"],
            )
            self.assertEqual(completed.returncode, 143)
            native = json.loads((output / "command.json").read_text())
            self.assertEqual(native["primary_exit"], -15)

    def test_symlink_root_and_output_are_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.fixture(Path(raw))
            parent = Path(raw)
            real = parent / "real"
            real.mkdir()
            output = parent / "output"
            output.mkdir()
            root_link = parent / "root-link"
            root_link.symlink_to(root, target_is_directory=True)
            completed = subprocess.run(
                [sys.executable, "-B", "-m", "infra.scripts.implement_startup",
                 "--root", str(root_link), "--output-dir", str(output), "--",
                 sys.executable, "-c", "raise SystemExit(19)"],
                cwd=root, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3])},
                text=True, capture_output=True,
            )
            self.assertEqual(completed.returncode, 2)

    def test_secret_in_command_arguments_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.fixture(Path(raw))
            output = Path(raw) / "artifacts"
            output.mkdir()
            completed = self.run_startup(
                root, output,
                [sys.executable, "-c", "raise SystemExit(19)", "secret-value"],
                "secret-value",
            )
            self.assertEqual(completed.returncode, 2)
            self.assertFalse((output / "command.json").exists())

    def test_missing_required_document_blocks_command_before_marker(self):
        with tempfile.TemporaryDirectory() as raw:
            root = self.fixture(Path(raw))
            output = Path(raw) / "artifacts"
            output.mkdir()
            marker = Path(raw) / "marker"
            completed = subprocess.run(
                [sys.executable, "-B", "-m", "infra.scripts.implement_startup",
                 "--root", str(root), "--output-dir", str(output),
                 "--section", "docs/agents/diagnostic-output.md", "MISSING HEADING",
                 "--", sys.executable, "-c", f"open({str(marker)!r}, 'w').write('started')"],
                cwd=root, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3])},
                text=True, capture_output=True,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertFalse(marker.exists())
            self.assertFalse((output / "command.json").exists())


if __name__ == "__main__":
    unittest.main()
