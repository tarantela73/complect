"""Behavior at bounded display boundaries; artifacts retain complete cleaned data."""
from __future__ import annotations

import tempfile
import copy
import json
import subprocess
import os
import sys
from unittest.mock import patch
import unittest
from pathlib import Path


class DiagnosticOutputTests(unittest.TestCase):
    def test_single_long_utf8_line_and_aggregate_byte_budget(self):
        from infra.scripts.diagnostic_output import error_view, DiagnosticBatch

        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            body = "начало " + "🧩" * 6000 + " конец"
            view = error_view(body, parent / "long.log")
            self.assertLessEqual(len(view.display.encode()), 4096)
            self.assertIn("конец", view.display)
            self.assertEqual((parent / "long.log").read_text(), body)
            first, second = "я" * 1400, "ю" * 1400
            batch = DiagnosticBatch()
            batch.add("first", first)
            batch.add("second", second)
            combined = batch.write(parent / "batch.log")
            self.assertTrue(combined.truncated)
            self.assertLessEqual(len(combined.display.encode()), 4096)
            self.assertIn(first, (parent / "batch.log").read_text())
            self.assertIn(second, (parent / "batch.log").read_text())

    def test_existing_or_source_artifact_is_never_overwritten(self):
        from infra.scripts.diagnostic_output import error_view, status_view

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            old = root / "old.log"
            old.write_text("preserve")
            with self.assertRaises(ValueError):
                error_view("new", old)
            self.assertEqual(old.read_text(), "preserve")
            with patch("subprocess.run") as git:
                with self.assertRaises(ValueError):
                    status_view(root, root / "new-source.txt")
            git.assert_not_called()
            self.assertFalse((root / "new-source.txt").exists())

    def test_source_guard_rejects_parent_traversal(self):
        from infra.scripts.diagnostic_output import artifact_destination

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".dev/test-artifacts").mkdir(parents=True)
            with self.assertRaises(ValueError):
                artifact_destination(root / ".dev/test-artifacts/../../source.txt", source_root=root)
            self.assertFalse((root / "source.txt").exists())

    def test_cli_selects_json_error_without_truncating_full_cleaned_artifact(self):
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            source, output = parent / "native.json", parent / "view.json"
            secret = "synthetic-private-value"
            original = {"status": "FAIL", "exit": 17, "primary_error": "причина " * 3000 + secret + " конец", "stdout": "весь stdout " * 1000, "stderr": "ошибка " + secret}
            source.write_text(json.dumps(original, ensure_ascii=False))
            before = source.read_bytes()
            completed = subprocess.run([sys.executable, "-B", "-m", "infra.scripts.diagnostic_output", "error", "--input", str(source), "--artifact", str(output), "--json-pointer", "/primary_error", "--redact-env", "COMPLECT_DIAGNOSTIC_TEST_SECRET"], env={**os.environ, "COMPLECT_DIAGNOSTIC_TEST_SECRET": secret}, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            self.assertLessEqual(len(completed.stdout.encode()), 4096)
            self.assertLessEqual(len(completed.stdout.splitlines()), 20)
            self.assertIn("конец", completed.stdout)
            self.assertNotIn(secret, completed.stdout)
            full = json.loads(output.read_text())
            self.assertEqual(full["stdout"], original["stdout"])
            self.assertEqual(full["stderr"], "ошибка [REDACTED]")
            self.assertEqual(full["exit"], 17)
            self.assertEqual(source.read_bytes(), before)

    def test_document_reads_share_one_budget_and_save_all_selected_sections(self):
        from infra.scripts.diagnostic_output import DiagnosticBatch, read_section

        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            first, second = parent / "one.md", parent / "two.md"
            first.write_text("# One\n```python\n## Chosen\n```\n## Chosen\n" + "первый раздел\n" * 100 + "## Other\nnot selected\n")
            second.write_text("# Two\n## Данные\n" + "второй раздел\n" * 100 + "## Other\nalso not selected\n")
            before = (first.read_bytes(), second.read_bytes())
            batch = DiagnosticBatch()
            batch.add(str(first), read_section(first, "Chosen"))
            batch.add(str(second), read_section(second, "Данные"))
            artifact = parent / "selected.log"
            view = batch.write(artifact)
            self.assertLessEqual(len(view.display.encode()), 4096)
            self.assertLessEqual(len(view.display.splitlines()), 20)
            self.assertIn("[TRUNCATED]", view.display)
            full = artifact.read_text()
            self.assertIn("первый раздел\n" * 100, full)
            self.assertIn("второй раздел\n" * 100, full)
            self.assertNotIn("not selected", full)
            self.assertNotIn("also not selected", full)
            self.assertEqual((first.read_bytes(), second.read_bytes()), before)
            self.assertGreater(len(full.encode()), 4096)

    def test_large_git_status_is_saved_and_only_scoped_paths_are_shown(self):
        from infra.scripts.diagnostic_output import status_view

        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            root = parent / "repo"
            root.mkdir()
            artifact = parent / "status.bin"
            status = " M owned/source.py\0R  owned/new name.py\0owned/old.py\0" + "".join("?? чужие/" + str(i) + ".txt\0" for i in range(600))

            def git(args, **kwargs):
                self.assertEqual(kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
                body = str(root) + "\n" if "rev-parse" in args else status
                return subprocess.CompletedProcess(args, 0, body.encode(), b"")

            with patch("subprocess.run", side_effect=git):
                view, code = status_view(root, artifact, scopes=["owned"])
            self.assertEqual(code, 0)
            meta = json.loads(view.display.splitlines()[0])
            self.assertEqual(meta["entries"], 602)
            self.assertEqual(meta["counts"]["??"], 600)
            self.assertEqual(meta["selected"], 2)
            self.assertIn("owned/source.py", view.display)
            self.assertIn("owned/new name.py", view.display)
            self.assertNotIn("чужие/599", view.display)
            self.assertEqual(artifact.read_bytes(), status.encode())
            self.assertLessEqual(len(view.display.encode()), 4096)

    def test_native_summary_keeps_full_cleaned_fields_and_original_exit(self):
        from infra.scripts.diagnostic_output import summary_view

        with tempfile.TemporaryDirectory() as raw:
            secret = 'пароль"\\\nтокен'
            native = {"status": "FAIL", "exit": 23, "primary_exit": 23,
                      "primary_error": "ошибка " * 3000 + secret + " конец",
                      "stdout": "данные " * 4000 + secret, "stderr": "полный stderr " + secret}
            before = copy.deepcopy(native)
            artifact = Path(raw) / "native-view.json"
            view = summary_view(native, artifact, attempt="a1", stage="run", exit_code=23, secrets=[secret])
            self.assertLessEqual(len(view.display.encode()), 4096)
            self.assertLessEqual(len(view.display.splitlines()), 20)
            self.assertIn("[TRUNCATED]", view.display)
            meta = json.loads(view.display.splitlines()[0])
            self.assertEqual(meta["exit"], 23)
            self.assertEqual(meta["status"], "FAIL")
            full = json.loads(artifact.read_text())
            self.assertEqual(full["stdout"], before["stdout"].replace(secret, "[REDACTED]"))
            self.assertEqual(full["stderr"], before["stderr"].replace(secret, "[REDACTED]"))
            self.assertEqual(full["primary_exit"], 23)
            self.assertEqual(native, before)
            self.assertNotIn(secret, artifact.read_text())
            self.assertIn("конец", view.display)

    def test_error_tail_is_bounded_and_full_utf8_artifact_is_retained(self):
        from infra.scripts.diagnostic_output import error_view

        with tempfile.TemporaryDirectory() as raw:
            artifact = Path(raw) / "full.log"
            body = "\n".join("ошибка " + str(i) for i in range(100)) + "\n"
            view = error_view(body, artifact)
            self.assertLessEqual(len(view.display.splitlines()), 20)
            self.assertLessEqual(len(view.display.encode("utf-8")), 4096)
            self.assertIn("[TRUNCATED]", view.display)
            self.assertIn("ошибка 99", view.display)
            self.assertIn(str(artifact), view.display)
            self.assertEqual(artifact.read_text(), body)
            self.assertTrue(view.truncated)


if __name__ == "__main__":
    unittest.main()
