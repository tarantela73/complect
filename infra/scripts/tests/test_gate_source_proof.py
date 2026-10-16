from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infra.scripts import gate_source_proof


class GateSourceProofTests(unittest.TestCase):
    def _snapshot(self, root: Path, files: dict[str, dict[str, str]]) -> dict[str, object]:
        return {"format": "complect-working-tree-snapshot-v1", "head": "h", "index_entries_sha256": "i",
                "index_flags_sha256": "f", "files": files, "deleted": [], "excluded": [".dev"]}

    def test_unchanged_source_writes_hash_bound_pass_sidecar(self):
        with tempfile.TemporaryDirectory() as temp:
            root, output = Path(temp) / "root", Path(temp) / "out"
            root.mkdir()
            output.mkdir()
            before = self._snapshot(root, {"Makefile": {"sha256": "a"}})
            (output / "sources-before.json").write_text(json.dumps(before))
            artifact = output / "quality.json"
            artifact.write_text('{"status":"PASS"}\n')
            with patch.object(gate_source_proof.agent_harness, "working_tree_snapshot", return_value=before):
                status, code = gate_source_proof.finish(root=root, output=output, before=before, gate_exit=0,
                                                        artifacts={"quality": artifact})
            self.assertEqual((status, code), ("PASS", 0))
            proof = json.loads((output / "source-proof.json").read_text())
            self.assertEqual(proof["version"], 1)
            self.assertEqual(proof["artifacts"]["quality.json"], gate_source_proof.sha256_file(artifact))
            self.assertEqual(gate_source_proof.verify(output)["artifacts"]["quality.json"], proof["artifacts"]["quality.json"])

    def test_makefile_change_blocks_source_proof_and_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            root, output = Path(temp) / "root", Path(temp) / "out"
            root.mkdir()
            output.mkdir()
            before = self._snapshot(root, {"Makefile": {"sha256": "a"}})
            after = self._snapshot(root, {"Makefile": {"sha256": "b"}})
            (output / "sources-before.json").write_text(json.dumps(before))
            with patch.object(gate_source_proof.agent_harness, "working_tree_snapshot", return_value=after):
                status, code = gate_source_proof.finish(root=root, output=output, before=before, gate_exit=0, artifacts={})
            self.assertEqual((status, code), ("FAIL", 2))

    def test_verify_rejects_changed_sidecar_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            root, output = Path(temp) / "root", Path(temp) / "out"
            root.mkdir()
            output.mkdir()
            before = self._snapshot(root, {})
            (output / "sources-before.json").write_text(json.dumps(before))
            artifact = output / "quality.json"
            artifact.write_text('{"status":"PASS"}\n')
            with patch.object(gate_source_proof.agent_harness, "working_tree_snapshot", return_value=before):
                gate_source_proof.finish(root=root, output=output, before=before, gate_exit=0, artifacts={"quality": artifact})
            artifact.write_text('{"status":"FAIL"}\n')
            with self.assertRaises(ValueError):
                gate_source_proof.verify(output)

    def test_real_git_makefile_mutation_blocks_proof(self):
        with tempfile.TemporaryDirectory() as temp:
            root, output = Path(temp) / "root", Path(temp) / "out"
            root.mkdir()
            output.mkdir()
            (root / "Makefile").write_text("all:\n\t@true\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Proof"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "proof@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(root), "add", "Makefile"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)
            before = gate_source_proof.capture(root, output, "sources-before.json")
            (root / "Makefile").write_text("all:\n\t@false\n")
            status, code = gate_source_proof.finish(root=root, output=output,
                                                    before=before["snapshot"], gate_exit=0, artifacts={})
            self.assertEqual((status, code), ("FAIL", 2))

