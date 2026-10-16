from __future__ import annotations

import ast
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infra.scripts.legacy_preflight import (
    BLOCKED_STAND,
    PASS,
    _check_redis,
    _docker_json,
    run_preflight,
    verify_prepared_checkout,
)


def _ownership(root: Path) -> dict[str, object]:
    containers: dict[str, object] = {}
    for service in ("backend", "postgres_primary", "clamav"):
        identifier = f"{service}-id"
        containers[service] = {
            "id": identifier,
            "image": f"{service}-image",
            "mounts": [],
        }
    return {
        "project": "complect-test",
        "checkout_id": "checkout-test",
        "root": str(root),
        "containers": containers,
        "database": {
            "database": "test_iam_5_8_0123456789abcdef0123456789abcdef",
            "database_oid": 42,
            "cluster": "cluster-1",
            "user": "complect_migrator",
            "session_user": "complect_migrator",
        },
        "redis": {"id": "redis-id", "name": "complect-iam-test-0123456789abcdef0123456789abcdef", "network": "test-net"},
    }


class FakeDocker:
    def __init__(self, ownership: dict[str, object], run_id: str) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.ownership = ownership
        self.run_id = run_id

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(tuple(argv))
        if argv[:3] == ["docker", "context", "show"]:
            return subprocess.CompletedProcess(argv, 0, "default\n", "")
        if argv[:3] == ["docker", "context", "inspect"]:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps("unix:///var/run/docker.sock"), ""
            )
        if argv[:2] == ["docker", "inspect"]:
            identifier = argv[2]
            if identifier == "redis-id":
                value = {
                    "Id": "redis-id",
                    "Name": f"/{self.ownership['redis']['name']}",
                    "Config": {"Labels": {"complect.checkout_id": "checkout-test", "complect.task_run": self.run_id}},
                    "NetworkSettings": {"Networks": {"test-net": {}}},
                }
            else:
                service = identifier.removesuffix("-id")
                value = {
                    "Id": identifier,
                    "Image": f"{service}-image",
                    "Config": {"Labels": {"complect.checkout_id": "checkout-test", "com.docker.compose.project": "complect-test"}},
                    "Mounts": [],
                }
            return subprocess.CompletedProcess(argv, 0, json.dumps([value]), "")
        if argv[:3] == ["docker", "exec", "clamav-id"]:
            return subprocess.CompletedProcess(argv, 0, "PING\n", "")
        if argv[:3] == ["docker", "exec", "redis-id"]:
            return subprocess.CompletedProcess(argv, 0, "appendonly\nyes\n", "")
        if argv[:3] == ["docker", "exec", "postgres_primary-id"]:
            if any("to_regclass" in value for value in argv):
                return subprocess.CompletedProcess(argv, 0, "None\n", "")
            return subprocess.CompletedProcess(argv, 0, "test_iam_5_8_0123456789abcdef0123456789abcdef|42|cluster-1|complect_migrator|complect_migrator\n", "")
        if argv[:3] == ["docker", "exec", "backend-id"]:
            return subprocess.CompletedProcess(argv, 0, "test_iam_5_8_0123456789abcdef0123456789abcdef|42|False|False|False|False|False|False|complect_app_local|complect_app_local\n", "")
        return subprocess.CompletedProcess(argv, 1, "", "probe failed")


class LegacyPreflightTests(unittest.TestCase):
    def test_missing_snapshot_blocks_and_writes_safe_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ownership = _ownership(root)
            ownership_path = root / "ownership.json"
            ownership_path.write_text(json.dumps(ownership))
            docker = FakeDocker(ownership, "run-1")
            result = run_preflight(
                root=root,
                source_root=root,
                snapshot=None,
                ownership=ownership_path,
                output=root / "out",
                run_id="run-1",
                docker_run=docker,
            )
            self.assertEqual(result.status, BLOCKED_STAND)
            self.assertIn("SNAPSHOT_REQUIRED", result.errors)
            evidence = json.loads((root / "out" / "preflight.json").read_text())
            self.assertEqual(evidence["status"], BLOCKED_STAND)
            self.assertFalse(any("password" in error.lower() for error in evidence["errors"]))
        self.assertTrue(
            all(
                call[:2] == ("docker", "inspect")
                or call[:3] == ("docker", "context", "inspect")
                or call[:3]
                    in (
                        ("docker", "exec", "clamav-id"),
                        ("docker", "exec", "redis-id"),
                        ("docker", "exec", "postgres_primary-id"),
                        ("docker", "exec", "backend-id"),
                    )
                    for call in docker.calls
                )
            )

    def test_prepared_checkout_mismatch_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "checkout"
            source.mkdir()
            target = source / "tracked.txt"
            target.write_text("changed")
            snapshot = root / "snapshot.json"
            snapshot.write_text(json.dumps({"files": {"tracked.txt": {"kind": "file", "mode": 0o644, "sha256": "wrong"}}}))
            self.assertEqual(verify_prepared_checkout(source, snapshot), ["CHECKOUT_CONTENT_MISMATCH"])

    def test_successful_preflight_has_no_mutating_docker_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ownership = _ownership(root)
            ownership_path = root / "ownership.json"
            ownership_path.write_text(json.dumps(ownership))
            snapshot = root / "snapshot.json"
            snapshot.write_text(json.dumps({"files": {}}))
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"collected": []}))
            docker = FakeDocker(ownership, "run-1")
            with patch("infra.scripts.legacy_preflight._snapshot_errors", return_value=[]), patch("infra.scripts.legacy_preflight.require_original_nodes", return_value=[]):
                result = run_preflight(
                    root=root,
                    source_root=root,
                    snapshot=snapshot,
                    ownership=ownership_path,
                    output=root / "out",
                    run_id="run-1",
                    manifest=manifest,
                    docker_run=docker,
                )
            self.assertEqual(result.status, PASS)
            self.assertTrue(all("FLUSHALL" not in call and "create" not in call for call in docker.calls))
            runtime = [
                call
                for call in docker.calls
                if call[:3] == ("docker", "exec", "backend-id")
            ]
            self.assertTrue(runtime)
            script = runtime[-1][-1]
            ast.parse(script)
            self.assertIn("host='postgres_primary'", script)
            self.assertIn("port=5432", script)
            self.assertIn("dbname=os.environ['POSTGRES_DB']", script)
            self.assertIn("user='complect_app_local'", script)
            self.assertIn("DATABASE_PASSWORD_FILE", script)
            self.assertIn("connect_timeout=10", script)
            self.assertIn("default_transaction_read_only=on", script)
            self.assertIn("statement_timeout=10000", script)

    def test_docker_json_timeout_is_safe(self) -> None:
        def timeout(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(argv, 30)

        with self.assertRaisesRegex(RuntimeError, "DOCKER_PROBE_TIMEOUT"):
            _docker_json(timeout, "inspect", "owned-id")

    def test_redis_aof_timeout_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ownership = _ownership(root)

            def timeout(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                if argv[:2] == ["docker", "inspect"]:
                    payload = [{
                        "Id": "redis-id",
                        "Name": f"/{ownership['redis']['name']}",
                        "Config": {"Labels": {
                            "complect.checkout_id": "checkout-test",
                            "complect.task_run": "run-1",
                        }},
                        "NetworkSettings": {"Networks": {"test-net": {}}},
                    }]
                    return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
                raise subprocess.TimeoutExpired(argv, 30)

            self.assertEqual(
                _check_redis(timeout, ownership, "run-1"),
                ["REDIS_AOF_PROBE_TIMEOUT"],
            )

    def test_runtime_permission_mismatch_blocks_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ownership = _ownership(root)
            ownership_path = root / "ownership.json"
            ownership_path.write_text(json.dumps(ownership))
            snapshot = root / "snapshot.json"
            snapshot.write_text(json.dumps({"files": {}}))
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"collected": []}))
            docker = FakeDocker(ownership, "run-1")

            def probe(
                argv: list[str], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                result = docker(argv, **kwargs)
                if argv[:3] == ["docker", "exec", "backend-id"]:
                    return subprocess.CompletedProcess(
                        argv,
                        0,
                        "owned|42|True|False|False|False|False|True|complect_app_local|complect_app_local\n",
                        "",
                    )
                return result

            with patch("infra.scripts.legacy_preflight._snapshot_errors", return_value=[]), patch("infra.scripts.legacy_preflight.require_original_nodes", return_value=[]):
                result = run_preflight(
                    root=root,
                    source_root=root,
                    snapshot=snapshot,
                    ownership=ownership_path,
                    output=root / "out",
                    run_id="run-1",
                    manifest=manifest,
                    docker_run=probe,
                )
            self.assertEqual(result.status, BLOCKED_STAND)
            self.assertIn("RUNTIME_PERMISSIONS_MISMATCH", result.errors)
            self.assertTrue(all("FLUSHALL" not in call and "create" not in call for call in docker.calls))


if __name__ == "__main__":
    unittest.main()
