"""Public, immutable acceptance matrix checks for #75."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def put(path: Path, value: object) -> str:
    path.write_text(json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AcceptanceMatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.identity = {"source_commit": "a" * 40, "source_tree": "b" * 40}
        quote = "Полный профиль проверяет четыре артефакта."
        source_hash = put(self.root / "spec.md", quote)
        proof_hash = put(self.root / "proof.json", {"source_identity": self.identity, "status": "PASS", "exit": 0})
        self.requirements = {"format": "complect-acceptance-matrix-requirements-v1", "sources": [{"path": "spec.md", "sha256": source_hash}], "criteria": [{"issue": 71, "id": "71-1", "quote": quote, "source": "spec.md", "identity_pointer": "/source_identity", "source_identity": self.identity, "facts": {"/status": "PASS", "/exit": 0}}]}
        self.requirements_hash = put(self.root / "requirements.json", self.requirements)
        self.matrix = {"format": "complect-acceptance-matrix-v1", "requirements_sha256": self.requirements_hash, "rows": [{"issue": 71, "id": "71-1", "quote": quote, "status": "PASS", "evidence": {"path": "proof.json", "sha256": proof_hash}}]}

    def run_matrix(self):
        matrix_hash = put(self.root / "matrix.json", self.matrix)
        return subprocess.run([sys.executable, "-B", "-m", "infra.scripts.acceptance_matrix", "--requirements", str(self.root / "requirements.json"), "--requirements-sha256", self.requirements_hash, "--matrix", str(self.root / "matrix.json"), "--matrix-sha256", matrix_hash, "--root", str(self.root)], capture_output=True, text=True)

    def test_complete_verified_matrix_passes_without_mutating_inputs(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        completed = self.run_matrix()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual((result["coverage"], result["integrity"], result["behavior"], result["status"]), ("PASS", "PASS", "PASS", "PASS"))
        for name, content in before.items():
            self.assertEqual((self.root / name).read_bytes(), content)


    def test_non_finite_numbers_are_not_valid_authenticated_json(self):
        self.requirements["criteria"][0]["source_identity"] = {"source_commit": float("nan")}
        self.requirements_hash = put(self.root / "requirements.json", self.requirements)
        self.matrix["requirements_sha256"] = self.requirements_hash
        self.matrix["rows"][0]["evidence"]["sha256"] = put(self.root / "proof.json", {"source_identity": {"source_commit": float("nan")}, "status": "PASS", "exit": 0})
        result = self.run_matrix()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)["integrity"], "FAIL")


    def test_missing_duplicate_unknown_and_range_rows_fail_coverage(self):
        row = self.matrix["rows"][0]
        cases = [[], [row, copy.deepcopy(row)], [{**row, "id": "71-2"}], [{**row, "id": "71-1..7"}]]
        for rows in cases:
            with self.subTest(rows=rows):
                self.matrix["rows"] = rows
                result = self.run_matrix()
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(json.loads(result.stdout)["coverage"], "FAIL")

    def test_changed_row_quote_and_issue_fail_coverage(self):
        row = copy.deepcopy(self.matrix["rows"][0])
        for field, value in [("quote", "Другая цитата"), ("issue", 72), ("issue", True)]:
            with self.subTest(field=field, value=value):
                self.matrix["rows"] = [{**row, field: value}]
                result = self.run_matrix()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)["coverage"], "FAIL")

    def test_changed_requirement_document_is_not_accepted(self):
        (self.root / "spec.md").write_text("changed requirement")
        result = self.run_matrix()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["integrity"], "FAIL")

    def test_unpinned_requirements_change_is_not_accepted(self):
        self.requirements["criteria"][0]["quote"] = "changed"
        put(self.root / "requirements.json", self.requirements)
        result = self.run_matrix()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["integrity"], "FAIL")

    def test_requirement_binding_must_match_independent_pin(self):
        self.matrix["requirements_sha256"] = "0" * 64
        result = self.run_matrix()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["integrity"], "FAIL")

    def test_missing_and_corrupt_evidence_block_positive_rows(self):
        proof = self.root / "proof.json"
        for change in [lambda: proof.write_text("corrupt"), lambda: proof.unlink()]:
            with self.subTest(change=change):
                change()
                result = self.run_matrix()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)["integrity"], "FAIL")

    def test_foreign_source_identity_fails_even_with_fresh_artifact_hash(self):
        self.matrix["rows"][0]["evidence"]["sha256"] = put(self.root / "proof.json", {"source_identity": {"source_commit": "c" * 40, "source_tree": "b" * 40}, "status": "PASS", "exit": 0})
        result = self.run_matrix()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EVIDENCE_SOURCE_MISMATCH", json.loads(result.stdout)["errors"])

    def test_false_or_failing_fact_does_not_become_expected_integer_zero(self):
        for exit_code in [False, 1]:
            with self.subTest(exit_code=exit_code):
                self.matrix["rows"][0]["evidence"]["sha256"] = put(self.root / "proof.json", {"source_identity": self.identity, "status": "PASS", "exit": exit_code})
                result = self.run_matrix()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("EVIDENCE_FACT_MISMATCH", json.loads(result.stdout)["errors"])

    def test_full_structure_with_nonpass_criterion_has_separate_failed_behavior(self):
        for status in ["FAIL", "BLOCKED_STAND", "NOT_RUN", "PARTIAL", "FAIL_BASELINE"]:
            with self.subTest(status=status):
                self.matrix["rows"][0]["status"] = status
                completed = self.run_matrix()
                result = json.loads(completed.stdout)
                self.assertEqual(completed.returncode, 1)
                self.assertEqual((result["coverage"], result["integrity"], result["behavior"], result["status"]), ("PASS", "PASS", "FAIL", "FAIL"))

    def test_symlink_and_parent_escape_are_rejected(self):
        (self.root / "alias.json").symlink_to(self.root / "proof.json")
        for name in ["alias.json", "../proof.json", str(self.root / "proof.json")]:
            with self.subTest(name=name):
                self.matrix["rows"][0]["evidence"]["path"] = name
                result = self.run_matrix()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)["integrity"], "FAIL")

    def test_evidence_commands_are_never_executed(self):
        marker = self.root / "forbidden-marker"
        self.matrix["rows"][0]["evidence"]["sha256"] = put(self.root / "proof.json", {"source_identity": self.identity, "status": "PASS", "exit": 0, "command": [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('bad')"]})
        result = self.run_matrix()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(marker.exists())

    def test_duplicate_expected_ids_and_json_keys_are_rejected(self):
        self.requirements["criteria"].append(copy.deepcopy(self.requirements["criteria"][0]))
        self.requirements_hash = put(self.root / "requirements.json", self.requirements)
        self.matrix["requirements_sha256"] = self.requirements_hash
        result = self.run_matrix()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CRITERION_DUPLICATE", json.loads(result.stdout)["errors"])
        text = '{"format":"fake","format":"complect-acceptance-matrix-requirements-v1"}'
        self.requirements_hash = put(self.root / "requirements.json", text)
        self.matrix["requirements_sha256"] = self.requirements_hash
        result = self.run_matrix()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("JSON_DUPLICATE_KEY", json.loads(result.stdout)["errors"])


    def test_failure_retains_parent_pins_and_authentication_boundary(self):
        (self.root / "proof.json").unlink()
        completed = self.run_matrix()
        result = json.loads(completed.stdout)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(result["requirements_sha256"], self.requirements_hash)
        self.assertEqual(result["matrix_sha256"], hashlib.sha256((self.root / "matrix.json").read_bytes()).hexdigest())
        self.assertTrue(result["requirements_authenticated"])
        self.assertTrue(result["matrix_authenticated"])
        (self.root / "requirements.json").unlink()
        result = json.loads(self.run_matrix().stdout)
        self.assertEqual(result["requirements_sha256"], self.requirements_hash)
        self.assertFalse(result["requirements_authenticated"])


if __name__ == "__main__":
    unittest.main()
