from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infra.scripts import verify_legacy_preflight_refusals as refusals


class LegacyPreflightRefusalTests(unittest.TestCase):
    def test_runner_resolves_snapshot_for_frozen_source_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "snapshot.json"
            snapshot.write_text("{}")
            source_root = root / "frozen-source"
            source_root.mkdir()
            stand = root / "stand"
            stand.mkdir()
            receipt = stand / "ownership.json"
            receipt.write_text('{"run_id": "run-1"}')
            (stand / "manifest.json").write_text("{}")

            captured = {}

            def fake_run(command, **kwargs):
                captured["command"] = command
                return subprocess.CompletedProcess(command, 2, "", "")

            with patch.object(refusals.subprocess, "run", side_effect=fake_run):
                refusals._run_runner(
                    receipt=receipt,
                    stand_dir=stand,
                    snapshot=Path("snapshot.json"),
                    source_root=source_root,
                    preflight_only=True,
                )

            command = captured["command"]
            self.assertEqual(command[command.index("--snapshot") + 1], str(Path("snapshot.json").resolve()))
            self.assertEqual(command[command.index("--source-root") + 1], str(source_root))

    def test_public_verify_reads_runner_evidence_from_stand_output(self):
        temp_root = Path(tempfile.mkdtemp(prefix="refusal-root-"))
        parent = temp_root / ".dev/test-artifacts"
        parent.mkdir(parents=True)
        directory = parent / "candidate"
        snapshot = temp_root / "snapshot.json"
        snapshot.write_text("{}")

        def fake_prepare(_source, _snapshot, stand, *, manifest):
            stand.mkdir(parents=True)
            (stand / "manifest.json").write_text('{"collected": 133}\n')
            (stand / "ownership.json").write_text(json.dumps({
                "run_id": "run-1", "root": str(stand / "checkout"),
                "project": "owned", "checkout_id": "checkout",
                "containers": {"redis": {"id": "redis-id", "image": "image-id", "mounts": []}},
                "redis": {"id": "redis-id"},
            }))

        calls = []

        def fake_runner(*, stand_dir, preflight_only=False, **_kwargs):
            calls.append(preflight_only)
            if len(calls) == 1:
                self.assertTrue(preflight_only, "refusal requires a ready baseline first")
                evidence = stand_dir / "baseline-evidence"
                evidence.mkdir()
                (evidence / "preflight.json").write_text(json.dumps({"status": "PASS", "errors": []}))
                return type("Result", (), {"returncode": 0})()
            evidence = stand_dir / "refusal-evidence"
            evidence.mkdir()
            (evidence / "preflight.json").write_text(
                json.dumps({"status": "BLOCKED_STAND", "errors": ["EXPECTED"]})
            )
            return type("Result", (), {"returncode": 2})()

        def fake_mutation(_receipt):
            return lambda: None

        sentinel_commands = []
        sentinel_value = ""

        def docker_run(command, **_kwargs):
            nonlocal sentinel_value
            operation = command[4]
            sentinel_commands.append(operation)
            if operation == "SET":
                sentinel_value = command[5]
            return subprocess.CompletedProcess(command, 0, "OK\n" if operation == "SET" else sentinel_value + "\n", "")

        def observe_owned(*_args):
            return [{"Id": "redis-id", "Image": "image-id", "Mounts": []}]

        commands = SimpleNamespace(ownership=SimpleNamespace(owned=observe_owned), run=docker_run)

        with patch.object(refusals, "ROOT", temp_root), \
             patch.object(refusals, "prepare", side_effect=fake_prepare), \
             patch.object(refusals, "cleanup", return_value={"cleanup": "PASS"}), \
             patch.object(refusals, "_run_runner", side_effect=fake_runner), \
             patch.object(refusals, "stand_commands", return_value=commands), \
             patch.object(refusals, "_capture_effects", return_value=((), ())), \
             patch.object(refusals, "_assert_effects_unchanged"), \
             patch.object(refusals, "_break_redis_identity", side_effect=fake_mutation), \
             patch.object(refusals, "SCENARIOS", (refusals.Scenario("redis_identity", "EXPECTED"),)):
            result = refusals.verify(snapshot=snapshot, directory=directory, source_root=temp_root)

        self.assertEqual(sentinel_commands, ["SET", "GET"])
        self.assertEqual(calls, [True, False])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["scenarios"][0]["status"], "PASS")
        self.assertEqual(json.loads((directory / "redis_identity/result.json").read_text())["status"], "PASS")

    def test_public_verify_child_runner_writes_refusal_json_before_docker(self):
        actual_source = refusals.ROOT
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / ".dev/test-artifacts/invalid-snapshot"
            snapshot = root / "snapshot.json"
            snapshot.write_text("{}")

            def provision(_source, _snapshot, stand, *, manifest):
                (stand / "checkout").mkdir(parents=True)
                (stand / "ownership.json").write_text(json.dumps({"run_id": "fixture"}))
                (stand / "manifest.json").write_bytes(manifest.read_bytes())

            with patch.dict(os.environ, {"PYTHONPATH": ""}), \
                 patch.object(refusals, "ROOT", root), \
                 patch.object(refusals, "prepare", side_effect=provision), \
                 patch.object(refusals, "cleanup", return_value={"cleanup": "PASS"}), \
                 patch.object(refusals, "SCENARIOS", (refusals.Scenario("redis_identity", "EXPECTED"),)):
                with self.assertRaisesRegex(RuntimeError, "blocked"):
                    refusals.verify(snapshot=snapshot, directory=directory, source_root=actual_source)
            evidence = directory / "redis_identity/baseline-evidence/preflight.json"
            self.assertTrue(evidence.is_file(), "child runner must produce the public refusal receipt")
            result = json.loads(evidence.read_text())
            self.assertEqual(result["status"], "BLOCKED_STAND")
            self.assertIn("SNAPSHOT_FORMAT_INVALID", result["errors"])

    def test_public_verify_records_primary_and_cleanup_failures(self):
        temp_root = Path(tempfile.mkdtemp(prefix="refusal-root-"))
        parent = temp_root / ".dev/test-artifacts"
        parent.mkdir(parents=True)
        directory = parent / "candidate"
        snapshot = temp_root / "snapshot.json"
        snapshot.write_text("{}")

        def fake_prepare(_source, _snapshot, stand, *, manifest):
            stand.mkdir(parents=True)
            (stand / "ownership.json").write_text(json.dumps({"run_id": "run-1"}))

        with patch.object(refusals, "ROOT", temp_root), \
             patch.object(refusals, "prepare", side_effect=fake_prepare), \
             patch.object(refusals, "cleanup", side_effect=RuntimeError("cleanup")), \
             patch.object(refusals, "SCENARIOS", (refusals.Scenario("redis_identity", "EXPECTED"),)):
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                refusals.verify(snapshot=snapshot, directory=directory, source_root=temp_root)

        record = json.loads((directory / "redis_identity/result.json").read_text())
        self.assertEqual(record["status"], "BLOCKED_STAND")
        self.assertIn("error", record)
        self.assertEqual(record["cleanup_error"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
