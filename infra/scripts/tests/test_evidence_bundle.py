from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infra.scripts.evidence_bundle import (
    EvidenceBundleError,
    build_reviewer_envelope,
    finish_run,
    read_exact_chunk,
    verify_reviewer_envelope,
    verify_post_run_sources,
)


class EvidenceBundleTests(unittest.TestCase):
    def test_exact_reader_never_returns_replacement_that_was_not_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            replacement = Path(directory) / "replacement"
            source.write_bytes(b"original")
            replacement.write_bytes(b"modified")
            expected = hashlib.sha256(b"original").hexdigest()
            original_open = Path.open
            opens = 0

            def open_with_external_replacement(path, *args, **kwargs):
                nonlocal opens
                if path == source:
                    opens += 1
                    if opens == 2:
                        replacement.replace(source)
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", open_with_external_replacement):
                self.assertEqual(read_exact_chunk(source, 0, 4, expected), b"orig")

    def test_exact_reader_authenticates_before_bounded_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.py"
            path.write_bytes(b"0123456789")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(read_exact_chunk(path, 2, 4, digest), b"2345")
            with self.assertRaises(EvidenceBundleError):
                read_exact_chunk(path, 0, 4, "0" * 64)
            with self.assertRaises(EvidenceBundleError):
                read_exact_chunk(path, 8, 4, digest)

    def test_envelope_requires_exact_artifacts_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.py"
            patch = root / "change.patch"
            result = root / "result.json"
            source.write_text("source\n")
            patch.write_text("patch\n")
            result.write_text("{}\n")
            envelope = build_reviewer_envelope(
                source_hashes={"source.py": source},
                patch_hashes={"change.patch": patch},
                image_id="sha256:image",
                run_id="run-1",
                result_paths={"gate": result},
            )
            self.assertEqual(verify_reviewer_envelope(envelope, root), [])
            result.write_text("changed\n")
            self.assertIn(
                "RESULT_HASH_MISMATCH", verify_reviewer_envelope(envelope, root)
            )

    def test_missing_original_is_a_blocking_error_without_latest_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            envelope = {
                "format": "complect-reviewer-envelope-v1",
                "source": {"missing.py": "a" * 64},
                "patch": {},
                "image_id": "sha256:image",
                "run_id": "run-1",
                "results": {},
            }
            errors = verify_reviewer_envelope(envelope, root)
            self.assertIn("SOURCE_ARTIFACT_MISSING", errors)

    def test_post_run_checks_snapshot_and_prepared_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "checkout"
            source.mkdir()
            tracked = source / "tracked.txt"
            tracked.write_text("before\n")
            digest = hashlib.sha256(tracked.read_bytes()).hexdigest()
            snapshot = root / "snapshot.json"
            snapshot.write_text(
                json.dumps(
                    {
                        "format": "complect-working-tree-snapshot-v1",
                        "files": {
                            "tracked.txt": {
                                "kind": "file",
                                "mode": 0o644,
                                "sha256": digest,
                            }
                        },
                    }
                )
            )
            tracked.write_text("after\n")
            errors = verify_post_run_sources(root, source, snapshot)
            self.assertIn("SNAPSHOT_MISMATCH", errors)
            self.assertIn("CHECKOUT_CONTENT_MISMATCH", errors)

    def test_invalid_envelope_raises_when_built(self) -> None:
        with self.assertRaises(EvidenceBundleError):
            build_reviewer_envelope({}, {}, "", "", {})

    def test_empty_source_and_patch_sections_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text("{}\n")
            with self.assertRaises(EvidenceBundleError):
                build_reviewer_envelope({}, {}, "image", "run", {"result": path})
            envelope = {
                "format": "complect-reviewer-envelope-v1",
                "source": {},
                "patch": {},
                "image_id": "image",
                "run_id": "run",
                "results": {"result": {"path": "result.json", "sha256": "0" * 64}},
            }
            self.assertIn("SOURCE_HASHES_REQUIRED", verify_reviewer_envelope(envelope, Path(directory)))

    def test_envelope_rejects_path_escape_and_malformed_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.py").write_text("source\n")
            envelope = {
                "format": "complect-reviewer-envelope-v1",
                "source": {"../source.py": "bad"},
                "patch": {"/etc/passwd": "0" * 64},
                "image_id": "image",
                "run_id": "run",
                "results": {"result": {"path": "../result.json", "sha256": "0" * 64}},
            }
            errors = verify_reviewer_envelope(envelope, root)
            self.assertIn("SOURCE_PATH_INVALID", errors)
            self.assertIn("PATCH_PATH_INVALID", errors)
            self.assertIn("RESULT_PATH_INVALID", errors)

    def test_finish_run_blocks_cleanup_failure_and_preserves_primary_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            envelope_path = root / "envelope.json"
            source = root / "source.py"
            patch = root / "change.patch"
            result = root / "result.json"
            source.write_text("source\n")
            patch.write_text("patch\n")
            result.write_text("result\n")
            envelope = build_reviewer_envelope({"source.py": source}, {"change.patch": patch}, "image", "run", {"result": result})
            manifest.write_text(json.dumps({"exit": 0}))
            receipt = finish_run(manifest, envelope_path, envelope, root, True)
            self.assertEqual(receipt["exit"], 2)
            self.assertEqual(receipt["status"], "BLOCKED_STAND")
            manifest.write_text(json.dumps({"exit": 17}))
            receipt = finish_run(manifest, envelope_path, envelope, root, True)
            self.assertEqual(receipt["exit"], 17)
            manifest.write_text(json.dumps({"exit": 1}))
            receipt = finish_run(manifest, envelope_path, envelope, root, True)
            self.assertEqual(receipt["exit"], 1)

    def test_envelope_binds_actual_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.py"
            patch = root / "change.patch"
            result = root / "result.json"
            source.write_text("source\n")
            patch.write_text("patch\n")
            result.write_text("result\n")
            envelope = build_reviewer_envelope({"source.py": source}, {"change.patch": patch}, "image", "run", {"result": result})
            source.write_text("drift\n")
            self.assertIn("SOURCE_HASH_MISMATCH", verify_reviewer_envelope(envelope, root))


if __name__ == "__main__":
    unittest.main()
