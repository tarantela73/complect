from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import contextlib
import io
import sys
from pathlib import Path
from unittest.mock import patch
from infra.scripts.legacy_phase_contract import ORIGINAL_NODE_IDS

from infra.scripts.evidence_bundle import (
    EvidenceBundleError,
    build_reviewer_envelope,
    finish_run,
    read_exact_chunk,
    verify_reviewer_envelope,
    verify_post_run_sources,
    LEGACY_RESULT_CONTRACT,
    LEGACY_REQUIRED_RESULTS,
    main,
)


class EvidenceBundleTests(unittest.TestCase):
    def legacy_packet(self, root: Path):
        paths = {name: root / name for name in LEGACY_REQUIRED_RESULTS}
        payloads = {
            "manifest.json": {
                "exit": 0, "collected": list(ORIGINAL_NODE_IDS),
                "reports": [{"nodeid": node, "when": phase, "outcome": "passed"}
                            for node in ORIGINAL_NODE_IDS for phase in ("setup", "call", "teardown")],
            },
            "post-run-sources.json": {"status": "PASS", "errors": []},
            "privilege-probes.json": [{"sqlstate": "42501"}],
            "target.json": {"run_id": "run"},
            "run-cleanup.json": {"status": "PASS", "errors": []},
            "result.json": {"status": "BLOCKED_STAND", "exit": 2},
        }
        for name, path in paths.items():
            path.write_text(json.dumps(payloads[name]) + "\n" if name in payloads else '{"operation":"flush"}\n')
        source = root / "source.py"
        source.write_text("source\n")
        patch_file = root / "change.patch"
        patch_file.write_text("patch\n")
        return build_reviewer_envelope(
            {"source": source}, {"patch": patch_file}, "image", "run", paths,
            result_contract=LEGACY_RESULT_CONTRACT,
        )

    def test_finish_publishes_hash_bound_terminal_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            envelope = self.legacy_packet(root)
            receipt = finish_run(root / "manifest.json", root / "envelope.json", envelope, root, False)
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(json.loads((root / "result.json").read_text()), receipt)
            published = json.loads((root / "envelope.json").read_text())
            self.assertEqual(verify_reviewer_envelope(published, root), [])
            self.assertEqual(published["results"]["result.json"]["sha256"], hashlib.sha256((root / "result.json").read_bytes()).hexdigest())

    def test_required_results_cannot_alias_one_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            envelope = self.legacy_packet(root)
            finish_run(root / "manifest.json", root / "envelope.json", envelope, root, False)
            published = json.loads((root / "envelope.json").read_text())
            published["results"]["target.json"] = published["results"]["lifecycle.jsonl"]
            self.assertIn("RESULT_REQUIRED_PATH_DUPLICATE", verify_reviewer_envelope(published, root))

    def test_finish_rejects_incomplete_phases_and_preserves_pytest_exit(self) -> None:
        for pytest_exit in (0, 7):
            with self.subTest(pytest_exit=pytest_exit), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                envelope = self.legacy_packet(root)
                manifest_path = root / "manifest.json"
                manifest = json.loads(manifest_path.read_text())
                manifest["reports"].pop()
                manifest["exit"] = pytest_exit
                manifest_path.write_text(json.dumps(manifest))
                envelope["results"]["manifest.json"]["sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                before = manifest_path.read_bytes()
                receipt = finish_run(manifest_path, root / "envelope.json", envelope, root, False)
                self.assertEqual(receipt["status"], "BLOCKED_STAND")
                self.assertEqual(receipt["exit"], pytest_exit or 2)
                self.assertEqual(manifest_path.read_bytes(), before)
                self.assertTrue(any("missing phase:" in error for error in receipt["envelope_errors"]))
                published = json.loads((root / "envelope.json").read_text())
                self.assertEqual(verify_reviewer_envelope(published, root, integrity_only=True), [])
                self.assertTrue(verify_reviewer_envelope(published, root))

    def test_finish_preserves_malformed_manifest_and_records_diagnostics(self) -> None:
        for raw in ('{"exit":"bad","collected":null,"reports":null}', '[]', '{invalid'):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                envelope = self.legacy_packet(root)
                manifest_path = root / "manifest.json"
                manifest_path.write_text(raw)
                envelope["results"]["manifest.json"]["sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                receipt = finish_run(manifest_path, root / "envelope.json", envelope, root, False)
                self.assertEqual(receipt["status"], "BLOCKED_STAND")
                self.assertEqual(manifest_path.read_text(), raw)
                self.assertTrue(receipt["envelope_errors"])


    def test_envelope_publication_failure_cannot_accept_old_packet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            envelope = self.legacy_packet(root)
            finish_run(root / "manifest.json", root / "envelope.json", envelope, root, False)
            published = json.loads((root / "envelope.json").read_text())
            original_replace = Path.replace

            def failing_replace(path, target):
                if Path(target).name == "envelope.json":
                    raise OSError("publication failed")
                return original_replace(path, target)

            with patch.object(Path, "replace", failing_replace), self.assertRaises(OSError):
                finish_run(root / "manifest.json", root / "envelope.json", published, root, True)
            self.assertTrue(verify_reviewer_envelope(json.loads((root / "envelope.json").read_text()), root))

    def test_cli_requires_contract_and_has_explicit_old_integrity_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact"
            artifact.write_text("{}")
            envelope = build_reviewer_envelope({"source": artifact}, {"patch": artifact}, "image", "run", {"result": artifact})
            path = root / "envelope.json"
            path.write_text(json.dumps(envelope))
            command = ["evidence_bundle", "verify", "--envelope", str(path), "--root", str(root)]
            output = io.StringIO()
            with patch.object(sys, "argv", command), contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
                main()
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("RESULT_CONTRACT_REQUIRED", output.getvalue())
            with patch.object(sys, "argv", command + ["--integrity-only"]), contextlib.redirect_stdout(io.StringIO()):
                main()


    def test_old_envelope_requires_explicit_integrity_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact.json"
            artifact.write_text("{}\n")
            envelope = build_reviewer_envelope(
                {"source": artifact}, {"patch": artifact}, "image", "run",
                {"result": artifact},
            )
            self.assertIn("RESULT_CONTRACT_REQUIRED", verify_reviewer_envelope(envelope, root))
            self.assertEqual(verify_reviewer_envelope(envelope, root, integrity_only=True), [])

    def test_legacy_contract_requires_each_record_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.py"
            source.write_text("source\n")
            patch_file = root / "change.patch"
            patch_file.write_text("patch\n")
            paths = {name: root / name for name in LEGACY_REQUIRED_RESULTS}
            for path in paths.values():
                path.write_text("{}\n")
            for name in LEGACY_REQUIRED_RESULTS:
                with self.subTest(name=name, missing="record"):
                    envelope = build_reviewer_envelope(
                        {"source": source}, {"patch": patch_file}, "image", "run", paths,
                        result_contract=LEGACY_RESULT_CONTRACT,
                    )
                    del envelope["results"][name]
                    self.assertIn(f"RESULT_REQUIRED_MISSING:{name}", verify_reviewer_envelope(envelope, root))
                with self.subTest(name=name, missing="file"):
                    envelope = build_reviewer_envelope(
                        {"source": source}, {"patch": patch_file}, "image", "run", paths,
                        result_contract=LEGACY_RESULT_CONTRACT,
                    )
                    paths[name].unlink()
                    self.assertIn("RESULT_ARTIFACT_MISSING", verify_reviewer_envelope(envelope, root))
                    paths[name].write_text("{}\n")
                with self.subTest(name=name, missing="hash"):
                    envelope = build_reviewer_envelope(
                        {"source": source}, {"patch": patch_file}, "image", "run", paths,
                        result_contract=LEGACY_RESULT_CONTRACT,
                    )
                    paths[name].write_text("changed\n")
                    self.assertIn("RESULT_HASH_MISMATCH", verify_reviewer_envelope(envelope, root))
                    paths[name].write_text("{}\n")


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
            self.assertEqual(verify_reviewer_envelope(envelope, root, integrity_only=True), [])
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
