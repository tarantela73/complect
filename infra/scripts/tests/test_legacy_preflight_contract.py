from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infra.scripts.legacy_preflight import BLOCKED_STAND, run_preflight
from infra.scripts.tests.test_legacy_preflight import FakeDocker, _ownership


class _ProbeVariant(FakeDocker):
    def __init__(self, ownership: dict[str, object], run_id: str, *, clamav: str = "PING\n", runtime: str | None = None, marker: str = "None\n", redis_name: str | None = None) -> None:
        super().__init__(ownership, run_id)
        self.clamav = clamav
        self.runtime = runtime
        self.marker = marker
        self.redis_name = redis_name

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        result = super().__call__(argv, **kwargs)
        if argv[:3] == ["docker", "inspect", "redis-id"] and self.redis_name is not None:
            payload = json.loads(result.stdout)
            payload[0]["Name"] = f"/{self.redis_name}"
            result = subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        if argv[:3] == ["docker", "exec", "clamav-id"]:
            return subprocess.CompletedProcess(argv, 0 if self.clamav == "PING\n" else 1, self.clamav, "")
        if argv[:3] == ["docker", "exec", "backend-id"] and self.runtime is not None:
            return subprocess.CompletedProcess(argv, 0, self.runtime, "")
        if argv[:3] == ["docker", "exec", "postgres_primary-id"] and any("to_regclass" in value for value in argv):
            return subprocess.CompletedProcess(argv, 0, self.marker, "")
        return result


class LegacyPreflightNegativeMatrix(unittest.TestCase):
    def _fixture(self) -> tuple[Path, dict[str, object], Path, Path, Path]:
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        source = root / "checkout"
        source.mkdir()
        tracked = source / "tracked.txt"
        tracked.write_text("valid prepared checkout\n", encoding="utf-8")
        snapshot = root / "snapshot.json"
        snapshot.write_text(json.dumps({
            "format": "complect-working-tree-snapshot-v1",
            "files": {"tracked.txt": {
                "kind": "file", "mode": 0o644,
                "sha256": hashlib.sha256(tracked.read_bytes()).hexdigest(),
            }},
        }), encoding="utf-8")
        ownership = _ownership(root)
        ownership_path = root / "ownership.json"
        ownership_path.write_text(json.dumps(ownership), encoding="utf-8")
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({"collected": ["synthetic-node"]}), encoding="utf-8")
        self.addCleanup(directory.cleanup)
        return root, ownership, ownership_path, snapshot, manifest

    def _run(self, mutate=None, *, snapshot=True, manifest=True, docker=None):
        root, ownership, ownership_path, snapshot_path, manifest_path = self._fixture()
        if mutate is not None:
            mutate(ownership)
            ownership_path.write_text(json.dumps(ownership), encoding="utf-8")
        adapter = docker or _ProbeVariant(ownership, "run-1")
        output = root / "out"
        with patch("infra.scripts.legacy_preflight._snapshot_errors", return_value=[]), \
             patch("infra.scripts.legacy_preflight.require_original_nodes", return_value=[]):
            result = run_preflight(
                root=root,
                source_root=root / "checkout",
                snapshot=snapshot_path if snapshot else None,
                ownership=ownership_path,
                output=output,
                run_id="run-1",
                manifest=manifest_path if manifest else None,
                docker_run=adapter,
            )
        evidence = json.loads((output / "preflight.json").read_text(encoding="utf-8"))
        return result, evidence, adapter

    def _assert_blocked_safe(self, result, evidence, adapter, code: str) -> None:
        self.assertEqual(result.status, BLOCKED_STAND)
        self.assertEqual(evidence["status"], BLOCKED_STAND)
        self.assertIn(code, evidence["errors"])
        encoded = json.dumps(evidence)
        self.assertNotIn("password", encoded.lower())
        self.assertNotIn("token", encoded.lower())
        forbidden = {"rm", "start", "create", "flushall", "drop", "truncate", "pytest"}
        for call in getattr(adapter, "calls", []):
            self.assertFalse(any(str(part).lower() in forbidden for part in call))

    def test_missing_snapshot_blocks_before_mutating_phase(self) -> None:
        result, evidence, adapter = self._run(snapshot=False)
        self._assert_blocked_safe(result, evidence, adapter, "SNAPSHOT_REQUIRED")

    def test_missing_manifest_blocks_with_safe_json(self) -> None:
        result, evidence, adapter = self._run(manifest=False)
        self._assert_blocked_safe(result, evidence, adapter, "ORIGINAL_MANIFEST_REQUIRED")

    def test_empty_manifest_schema_blocks(self) -> None:
        root, ownership, ownership_path, snapshot, manifest = self._fixture()
        manifest.write_text("{}", encoding="utf-8")
        adapter = _ProbeVariant(ownership, "run-1")
        with patch("infra.scripts.legacy_preflight._snapshot_errors", return_value=[]):
            result = run_preflight(root=root, source_root=root / "checkout", snapshot=snapshot, ownership=ownership_path, output=root / "out", run_id="run-1", manifest=manifest, docker_run=adapter)
        evidence = json.loads((root / "out" / "preflight.json").read_text())
        self._assert_blocked_safe(result, evidence, adapter, "ORIGINAL_MANIFEST_INVALID")

    def test_stale_host_resource_blocks(self) -> None:
        def mutate(ownership):
            ownership["containers"]["backend"]["mounts"] = [{"source": "/stale", "destination": "/workspace"}]
        result, evidence, adapter = self._run(mutate)
        self._assert_blocked_safe(result, evidence, adapter, "RESOURCE_IDENTITY_MISMATCH")

    def test_changed_checkout_copy_blocks(self) -> None:
        root, ownership, ownership_path, snapshot, manifest = self._fixture()
        (root / "checkout" / "tracked.txt").write_text("changed\n", encoding="utf-8")
        adapter = _ProbeVariant(ownership, "run-1")
        with patch("infra.scripts.legacy_preflight._snapshot_errors", return_value=[]), patch("infra.scripts.legacy_preflight.require_original_nodes", return_value=[]):
            result = run_preflight(root=root, source_root=root / "checkout", snapshot=snapshot, ownership=ownership_path, output=root / "out", run_id="run-1", manifest=manifest, docker_run=adapter)
        evidence = json.loads((root / "out" / "preflight.json").read_text())
        self._assert_blocked_safe(result, evidence, adapter, "CHECKOUT_CONTENT_MISMATCH")

    def test_redis_database_uuid_pair_mismatch_blocks(self) -> None:
        def mutate(ownership):
            ownership["redis"]["name"] = "redis-ffffffffffffffffffffffffffffffff"
        root, ownership, ownership_path, snapshot, manifest = self._fixture()
        mutate(ownership)
        ownership_path.write_text(json.dumps(ownership), encoding="utf-8")
        adapter = _ProbeVariant(ownership, "run-1", redis_name=ownership["redis"]["name"])
        with patch("infra.scripts.legacy_preflight._snapshot_errors", return_value=[]), patch("infra.scripts.legacy_preflight.require_original_nodes", return_value=[]):
            result = run_preflight(root=root, source_root=root / "checkout", snapshot=snapshot, ownership=ownership_path, output=root / "out", run_id="run-1", manifest=manifest, docker_run=adapter)
        evidence = json.loads((root / "out" / "preflight.json").read_text())
        self._assert_blocked_safe(result, evidence, adapter, "REDIS_DATABASE_PAIR_MISMATCH")

    def test_canonical_hyphenated_redis_name_passes(self) -> None:
        root, ownership, ownership_path, snapshot, manifest = self._fixture()
        canonical_name = "complect-iam-test-01234567-89ab-cdef-0123-456789abcdef"
        ownership["redis"]["name"] = canonical_name
        ownership_path.write_text(json.dumps(ownership), encoding="utf-8")
        adapter = _ProbeVariant(ownership, "run-1", redis_name=canonical_name)
        with patch(
            "infra.scripts.legacy_preflight._snapshot_errors", return_value=[]
        ), patch(
            "infra.scripts.legacy_preflight.require_original_nodes", return_value=[]
        ):
            result = run_preflight(
                root=root,
                source_root=root / "checkout",
                snapshot=snapshot,
                ownership=ownership_path,
                output=root / "out",
                run_id="run-1",
                manifest=manifest,
                docker_run=adapter,
            )
        self.assertEqual(result.status, "PASS")

    def test_noncanonical_redis_name_blocks(self) -> None:
        root, ownership, ownership_path, snapshot, manifest = self._fixture()
        noncanonical_name = "complect-iam-test-01234567-89ab-cdef-0123-456789ab"
        ownership["redis"]["name"] = noncanonical_name
        ownership_path.write_text(json.dumps(ownership), encoding="utf-8")
        adapter = _ProbeVariant(ownership, "run-1", redis_name=noncanonical_name)
        with patch(
            "infra.scripts.legacy_preflight._snapshot_errors", return_value=[]
        ), patch(
            "infra.scripts.legacy_preflight.require_original_nodes", return_value=[]
        ):
            result = run_preflight(
                root=root,
                source_root=root / "checkout",
                snapshot=snapshot,
                ownership=ownership_path,
                output=root / "out",
                run_id="run-1",
                manifest=manifest,
                docker_run=adapter,
            )
        evidence = json.loads((root / "out" / "preflight.json").read_text())
        self._assert_blocked_safe(
            result, evidence, adapter, "REDIS_DATABASE_PAIR_MISMATCH"
        )

    def test_runtime_wrong_flags_blocks(self) -> None:
        runtime = "test_iam_5_8_0123456789abcdef0123456789abcdef|42|True|False|False|False|False|False|complect_app_local|complect_app_local\n"
        root, ownership, ownership_path, snapshot, manifest = self._fixture()
        adapter = _ProbeVariant(ownership, "run-1", runtime=runtime)
        with patch("infra.scripts.legacy_preflight._snapshot_errors", return_value=[]), patch("infra.scripts.legacy_preflight.require_original_nodes", return_value=[]):
            result = run_preflight(root=root, source_root=root / "checkout", snapshot=snapshot, ownership=ownership_path, output=root / "out", run_id="run-1", manifest=manifest, docker_run=adapter)
        evidence = json.loads((root / "out" / "preflight.json").read_text())
        self._assert_blocked_safe(result, evidence, adapter, "RUNTIME_PERMISSIONS_MISMATCH")

    def test_clamav_not_ready_blocks(self) -> None:
        root, ownership, ownership_path, snapshot, manifest = self._fixture()
        adapter = _ProbeVariant(ownership, "run-1", clamav="NOT READY\n")
        with patch("infra.scripts.legacy_preflight._snapshot_errors", return_value=[]), patch("infra.scripts.legacy_preflight.require_original_nodes", return_value=[]):
            result = run_preflight(root=root, source_root=root / "checkout", snapshot=snapshot, ownership=ownership_path, output=root / "out", run_id="run-1", manifest=manifest, docker_run=adapter)
        evidence = json.loads((root / "out" / "preflight.json").read_text())
        self._assert_blocked_safe(result, evidence, adapter, "CLAMAV_UNREADY")

    def test_existing_foreign_marker_blocks(self) -> None:
        root, ownership, ownership_path, snapshot, manifest = self._fixture()
        adapter = _ProbeVariant(ownership, "run-1", marker="foreign-run\n")
        with patch("infra.scripts.legacy_preflight._snapshot_errors", return_value=[]), patch("infra.scripts.legacy_preflight.require_original_nodes", return_value=[]):
            result = run_preflight(root=root, source_root=root / "checkout", snapshot=snapshot, ownership=ownership_path, output=root / "out", run_id="run-1", manifest=manifest, docker_run=adapter)
        evidence = json.loads((root / "out" / "preflight.json").read_text())
        self._assert_blocked_safe(result, evidence, adapter, "DATABASE_MARKER_FOREIGN")

    def test_remote_docker_host_blocks_before_container_probes(self) -> None:
        root, ownership, ownership_path, snapshot, manifest = self._fixture()
        adapter = _ProbeVariant(ownership, "run-1")
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://remote.example:2375"}), patch(
            "infra.scripts.legacy_preflight._snapshot_errors", return_value=[]
        ), patch(
            "infra.scripts.legacy_preflight.require_original_nodes", return_value=[]
        ):
            result = run_preflight(
                root=root,
                source_root=root / "checkout",
                snapshot=snapshot,
                ownership=ownership_path,
                output=root / "out",
                run_id="run-1",
                manifest=manifest,
                docker_run=adapter,
            )
        evidence = json.loads((root / "out" / "preflight.json").read_text())
        self._assert_blocked_safe(result, evidence, adapter, "DOCKER_REMOTE_ENDPOINT")
        self.assertFalse(any(call[:2] == ("docker", "inspect") for call in adapter.calls))
        self.assertFalse(any(call[:2] == ("docker", "exec") for call in adapter.calls))

    def test_unix_docker_host_mismatch_blocks_before_container_probes(self) -> None:
        root, ownership, ownership_path, snapshot, manifest = self._fixture()
        adapter = _ProbeVariant(ownership, "run-1")
        with patch.dict(
            os.environ, {"DOCKER_HOST": "unix:///tmp/other-docker.sock"}
        ), patch(
            "infra.scripts.legacy_preflight._snapshot_errors", return_value=[]
        ), patch(
            "infra.scripts.legacy_preflight.require_original_nodes", return_value=[]
        ):
            result = run_preflight(
                root=root,
                source_root=root / "checkout",
                snapshot=snapshot,
                ownership=ownership_path,
                output=root / "out",
                run_id="run-1",
                manifest=manifest,
                docker_run=adapter,
            )
        evidence = json.loads((root / "out" / "preflight.json").read_text())
        self._assert_blocked_safe(result, evidence, adapter, "DOCKER_ENDPOINT_MISMATCH")
        self.assertFalse(any(call[:2] == ("docker", "inspect") for call in adapter.calls))
        self.assertFalse(any(call[:2] == ("docker", "exec") for call in adapter.calls))

    def test_docker_probe_timeout_is_safe_and_blocks(self) -> None:
        root, ownership, ownership_path, snapshot, manifest = self._fixture()

        def timeout(*argv: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(argv, 30)

        with patch(
            "infra.scripts.legacy_preflight._snapshot_errors", return_value=[]
        ), patch(
            "infra.scripts.legacy_preflight.require_original_nodes", return_value=[]
        ):
            result = run_preflight(
                root=root,
                source_root=root / "checkout",
                snapshot=snapshot,
                ownership=ownership_path,
                output=root / "out",
                run_id="run-1",
                manifest=manifest,
                docker_run=timeout,
            )
        evidence = json.loads((root / "out" / "preflight.json").read_text())
        self._assert_blocked_safe(result, evidence, timeout, "DOCKER_ENDPOINT_PROBE_TIMEOUT")

    def test_missing_docker_binary_writes_safe_receipt(self) -> None:
        root, ownership, ownership_path, snapshot, manifest = self._fixture()

        def unavailable(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("docker")

        with patch(
            "infra.scripts.legacy_preflight._snapshot_errors", return_value=[]
        ), patch(
            "infra.scripts.legacy_preflight.require_original_nodes", return_value=[]
        ):
            result = run_preflight(
                root=root,
                source_root=root / "checkout",
                snapshot=snapshot,
                ownership=ownership_path,
                output=root / "out",
                run_id="run-1",
                manifest=manifest,
                docker_run=unavailable,
            )
        evidence = json.loads((root / "out" / "preflight.json").read_text())
        self._assert_blocked_safe(result, evidence, unavailable, "DOCKER_ENDPOINT_PROBE_FAILED")


if __name__ == "__main__":
    unittest.main()
