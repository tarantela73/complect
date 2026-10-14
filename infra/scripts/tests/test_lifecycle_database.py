"""Authority and cleanup failure tests; no database mutation in unit checks."""

from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from infra.scripts.run_legacy_regressions import ROOT, verify_owned
from test_support import lifecycle_database as lifecycle

TARGET = {
    "database": "complect_db",
    "database_oid": 16400,
    "cluster": "123",
    "user": "complect_migrator",
    "session_user": "complect_migrator",
    "run_id": "owned",
}


@pytest.mark.parametrize("key", list(TARGET))
def test_foreign_target_fails_before_flush(key, monkeypatch):
    foreign = dict(TARGET, **{key: "foreign"})
    monkeypatch.setattr(lifecycle, "database_identity", lambda: foreign)
    flush = Mock()
    monkeypatch.setattr(lifecycle, "call_command", flush)
    with pytest.raises(lifecycle.TargetMismatchError):
        lifecycle.perform({"op": "flush", "run_id": "owned"}, TARGET)
    flush.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {"op": "sql", "run_id": "owned"},
        {"op": "flush", "run_id": "foreign"},
        {"op": "flush", "run_id": "owned", "sql": "TRUNCATE anything"},
        {"op": "fixture_prepare", "run_id": "owned", "fixture": "users_users"},
    ],
)
def test_request_cannot_select_arbitrary_sql_target(payload, monkeypatch):
    probe = Mock()
    monkeypatch.setattr(lifecycle, "database_identity", probe)
    with pytest.raises(lifecycle.TargetMismatchError):
        lifecycle.perform(payload, TARGET)
    probe.assert_not_called()


def test_cleanup_failure_poison_stops_further_operations(monkeypatch):
    operation = Mock(side_effect=RuntimeError("failed flush"))
    monkeypatch.setattr(lifecycle, "perform", operation)
    service = lifecycle.LifecycleService(TARGET)
    assert service.handle({"op": "flush", "run_id": "owned"})["ok"] is False
    assert service.failed
    assert service.handle({"op": "flush", "run_id": "owned"})["ok"] is False
    operation.assert_called_once()


@pytest.mark.parametrize("extra_table", ["spatial_ref_sys", "unknown_application_table"])
def test_flush_preserves_postgis_and_rejects_unknown_tables(extra_table, monkeypatch):
    from config import runtime_role_grants

    monkeypatch.setattr(lifecycle, "database_identity", lambda: TARGET)
    monkeypatch.setattr(runtime_role_grants, "_tables", lambda: ["django_migrations", "app_table"])
    connection = MagicMock()
    public_tables = [
        "django_migrations", "app_table", *lifecycle.FIXTURES, extra_table,
    ]
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [(table,) for table in public_tables]
    cursor.fetchone.return_value = (0,)
    monkeypatch.setattr(lifecycle, "connection", connection)
    flush = Mock()
    monkeypatch.setattr(lifecycle, "call_command", flush)
    if extra_table == "unknown_application_table":
        with pytest.raises(RuntimeError, match="unregistered application table"):
            lifecycle.perform({"op": "flush", "run_id": "owned"}, TARGET)
        flush.assert_not_called()
        assert cursor.execute.call_count == 1
    else:
        assert lifecycle.perform({"op": "flush", "run_id": "owned"}, TARGET)["ok"]
        flush.assert_called_once()
        assert all("spatial_ref_sys" not in str(call) for call in cursor.execute.call_args_list)


def test_resource_identity_requires_exact_id_mount_image_and_labels():
    root = ROOT / ".dev/test-artifacts/owned/checkout"
    actual = {
        "postgres_primary": {
            "Id": "owned-id",
            "Image": "image-id",
            "Mounts": [{"Name": "owned-volume"}],
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "owned-project",
                    "complect.checkout_id": "owned-checkout",
                }
            },
        }
    }
    recorded = {
        "root": str(root),
        "project": "owned-project",
        "checkout_id": "owned-checkout",
        "containers": {
            "postgres_primary": {
                "id": "owned-id",
                "image": "image-id",
                "mounts": [{"Name": "owned-volume"}],
            }
        },
    }
    verify_owned(actual, recorded, root)
    for key in ("Id", "Image", "Mounts"):
        foreign = copy.deepcopy(actual)
        foreign["postgres_primary"][key] = "foreign"
        with pytest.raises(RuntimeError, match="identity"):
            verify_owned(foreign, recorded, root)
    with pytest.raises(RuntimeError, match="outside"):
        verify_owned(actual, recorded, Path("/persistent/dev"))


def test_arbitrary_133_nodes_do_not_replace_original_manifest():
    from infra.scripts.run_legacy_regressions import require_original_nodes

    with pytest.raises(RuntimeError, match="exact original"):
        require_original_nodes([f"tests.py::test_{n}" for n in range(133)])


@pytest.mark.parametrize("failure_phase", ["prepare-secrets", "start-lifecycle"])
def test_runner_failure_preserves_primary_error_and_cleans_created_resources(
    tmp_path, monkeypatch, failure_phase
):
    import json
    import shutil
    import sys
    from types import SimpleNamespace
    from infra.scripts import run_legacy_regressions as runner

    monkeypatch.setattr(runner, "ROOT", tmp_path)
    # This test exercises downstream prepare/start failure cleanup.  The
    # dedicated preflight matrix owns qualification behavior; keep this fault
    # injection focused on preserving the primary error and cleanup.
    monkeypatch.setattr(runner, "run_preflight", lambda **kwargs: SimpleNamespace(status="PASS"))
    root = tmp_path / ".dev/test-artifacts/task/checkout"
    root.mkdir(parents=True)
    secret_dir = root / ".dev/secrets"
    secret_dir.mkdir(parents=True)
    for name in (
        "app_postgres_password",
        "django_secret_key",
        "gateway_token",
        "minio_password",
        "postgres_password",
    ):
        (secret_dir / name).write_text("synthetic-fixture-only")
    output = root.parent / "result"
    owned = {
        "root": str(root),
        "project": "owned",
        "checkout_id": "checkout",
        "containers": {"backend": {"id": "backend-id", "image": "image-id", "mounts": []}},
        "database": TARGET,
        "redis": {"id": "redis-id", "name": "redis", "network": "owned"},
    }
    ownership = root.parent / "ownership.json"
    ownership.write_text(json.dumps(owned))
    manifest = root.parent / "nodes.json"
    manifest.write_text(json.dumps({"collected": ["fixture"]}))
    monkeypatch.setattr(runner, "require_original_nodes", lambda nodes: None)
    removed = []
    control_paths = []
    actual_mkdtemp = runner.tempfile.mkdtemp

    def make_control(**kwargs):
        path = actual_mkdtemp(**kwargs)
        control_paths.append(Path(path))
        return path

    monkeypatch.setattr(runner.tempfile, "mkdtemp", make_control)

    class Commands:
        isolated_e2e = True
        project = "owned"

        def __init__(self, root):
            self.server_created = False

        def containers(self):
            return {"backend": {"Id": "backend-id", "Image": "image-id", "Mounts": [],
                                "Config": {"Labels": {"com.docker.compose.project": "owned",
                                                       "complect.checkout_id": "checkout"}}}}

        def configuration(self):
            return {}

        def own_resources(self, config):
            pass

        def run(self, command, **kwargs):
            if command[:2] == ["docker", "inspect"]:
                if command[2] == "redis-id":
                    data = {
                        "Id": "redis-id",
                        "Name": "/redis",
                        "Config": {
                            "Labels": {
                                "complect.checkout_id": "checkout",
                                "complect.task_run": "owned-run",
                            }
                        },
                        "NetworkSettings": {"Networks": {"owned": {}}},
                    }
                elif self.server_created:
                    data = {
                        "Id": "created-id",
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project": "owned",
                                "complect.checkout_id": "checkout",
                            }
                        },
                    }
                else:
                    return SimpleNamespace(returncode=1, stdout="")
                return SimpleNamespace(returncode=0, stdout=json.dumps([data]))
            if command[:2] == ["docker", "exec"]:
                return SimpleNamespace(returncode=0, stdout="appendonly\nyes\n")
            removed.append(command)
            return SimpleNamespace(returncode=0, stdout="")

        def compose(self, *arguments, **kwargs):
            if "-d" in arguments:
                self.server_created = True
                raise RuntimeError("primary start failure")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runner, "Commands", Commands)
    if failure_phase == "prepare-secrets":
        monkeypatch.setattr(
            shutil,
            "copyfile",
            lambda *args: (_ for _ in ()).throw(OSError("primary secret failure")),
        )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runner",
            "--root",
            str(root),
            "--ownership",
            str(ownership),
            "--manifest",
            str(manifest),
            "--output",
            str(output),
            "--run-id",
            "owned-run",
            "--review-patch",
            str(manifest),
        ],
    )
    expected = OSError if failure_phase == "prepare-secrets" else RuntimeError
    with pytest.raises(expected, match="primary"):
        runner.main()
    assert json.loads((output / "failure.json").read_text())["phase"] == failure_phase
    assert control_paths and all(not path.exists() for path in control_paths)
    if failure_phase == "start-lifecycle":
        assert ["docker", "rm", "created-id"] in removed
