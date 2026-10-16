"""Behavioral tests for disposable Payments stand cleanup ownership."""

from __future__ import annotations

from argparse import Namespace
from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from infra.scripts.run_payments_gate import Stand


def _stand(tmp_path: Path) -> Stand:
    return Stand(
        Namespace(output=tmp_path, scope="full", fault=None, subnet="198.19.249.0/24")
    )


def _record(stand: Stand) -> dict[str, object]:
    record = {
        "kind": "container",
        "id": "container-owned",
        "labels": {"complect.payments_run": stand.run_id},
        "image": "backend-image-id",
        "mounts": [],
    }
    stand.owned.append(record)
    return record


def test_cleanup_refuses_foreign_identity_without_docker_removal(tmp_path, monkeypatch):
    stand = _stand(tmp_path)
    record = _record(stand)
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        stand,
        "inspect",
        lambda kind, identifier: {
            "Id": identifier,
            "Image": record["image"],
            "Mounts": [],
            "Config": {"Labels": {"complect.payments_run": "foreign-run"}},
        },
    )
    monkeypatch.setattr(stand, "docker", lambda *args, **kwargs: calls.append(args))

    assert stand.cleanup() is False
    assert calls == []
    assert "removed" not in record
    assert stand.report["phases"]["cleanup"]["status"] == "BLOCKED_STAND"
    assert (
        "identity mismatch" in stand.report["phases"]["cleanup"]["errors"][0]["error"]
    )


def test_cleanup_refuses_changed_identity_and_preserves_exact_diagnostic(
    tmp_path, monkeypatch
):
    stand = _stand(tmp_path)
    record = _record(stand)
    observed = {
        "Id": "replacement-container",
        "Image": record["image"],
        "Mounts": record["mounts"],
        "Config": {"Labels": record["labels"]},
    }
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(stand, "inspect", lambda kind, identifier: observed)
    monkeypatch.setattr(stand, "docker", lambda *args, **kwargs: calls.append(args))

    assert stand.cleanup() is False
    assert calls == []
    assert "removed" not in record
    assert stand.report["phases"]["cleanup"]["status"] == "BLOCKED_STAND"
    assert stand.report["phases"]["cleanup"]["errors"] == [
        {"id": record["id"], "error": "cleanup identity mismatch"}
    ]


def test_cleanup_refuses_extra_label_and_preserves_exact_diagnostic(
    tmp_path, monkeypatch
):
    stand = _stand(tmp_path)
    record = _record(stand)
    observed = {
        "Id": record["id"],
        "Image": record["image"],
        "Mounts": record["mounts"],
        "Config": {"Labels": {**record["labels"], "extra": "changed"}},
    }
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(stand, "inspect", lambda kind, identifier: observed)
    monkeypatch.setattr(stand, "docker", lambda *args, **kwargs: calls.append(args))

    assert stand.cleanup() is False
    assert calls == []
    assert "removed" not in record
    assert stand.report["phases"]["cleanup"]["errors"] == [
        {"id": record["id"], "error": "cleanup identity mismatch"}
    ]


def test_cleanup_refuses_changed_image_or_mounts_with_exact_diagnostic(
    tmp_path, monkeypatch
):
    for field, value in (("Image", "replacement-image"), ("Mounts", [{"Type": "volume"}])):
        stand = _stand(tmp_path / field.lower())
        record = _record(stand)
        observed = {
            "Id": record["id"],
            "Image": record["image"],
            "Mounts": deepcopy(record["mounts"]),
            "Config": {"Labels": record["labels"]},
        }
        observed[field] = value
        calls: list[tuple[object, ...]] = []
        monkeypatch.setattr(stand, "inspect", lambda kind, identifier: observed)
        monkeypatch.setattr(stand, "docker", lambda *args, **kwargs: calls.append(args))

        assert stand.cleanup() is False
        assert calls == []
        assert "removed" not in record
        assert stand.report["phases"]["cleanup"]["errors"] == [
            {"id": record["id"], "error": "cleanup image/mount mismatch"}
        ]


def test_cleanup_inspects_every_resource_before_removing_in_reverse_order(
    tmp_path, monkeypatch
):
    stand = _stand(tmp_path)
    stand.owned.extend(
        [
            {
                "kind": "container",
                "id": "container-first",
                "labels": {"complect.payments_run": stand.run_id},
                "image": "image-first",
                "mounts": [],
            },
            {
                "kind": "network",
                "id": "network-middle",
                "labels": {"complect.payments_run": stand.run_id},
            },
            {
                "kind": "container",
                "id": "container-last",
                "labels": {"complect.payments_run": stand.run_id},
                "image": "image-last",
                "mounts": [],
            },
        ]
    )
    calls: list[tuple[object, ...]] = []

    def inspect(kind, identifier):
        calls.append(("inspect", kind, identifier))
        record = next(item for item in stand.owned if item["id"] == identifier)
        found = {"Id": identifier, "Name": identifier, "Labels": record["labels"]}
        if kind == "container":
            found.update(
                Config={"Labels": record["labels"]},
                Image=record["image"],
                Mounts=record["mounts"],
            )
        return found

    monkeypatch.setattr(stand, "inspect", inspect)
    monkeypatch.setattr(
        stand,
        "docker",
        lambda *args, **kwargs: calls.append(("remove", *args)),
    )

    assert stand.cleanup() is True
    assert calls == [
        ("inspect", "container", "container-last"),
        ("remove", "container", "rm", "-f", "container-last"),
        ("inspect", "network", "network-middle"),
        ("remove", "network", "rm", "network-middle"),
        ("inspect", "container", "container-first"),
        ("remove", "container", "rm", "-f", "container-first"),
    ]
    assert all(record["removed"] for record in stand.owned)


def test_cleanup_marks_blocked_when_docker_rm_fails(tmp_path, monkeypatch):
    stand = _stand(tmp_path)
    record = _record(stand)
    monkeypatch.setattr(
        stand,
        "inspect",
        lambda kind, identifier: {
            "Id": identifier,
            "Image": record["image"],
            "Mounts": record["mounts"],
            "Config": {"Labels": record["labels"]},
        },
    )

    def fail_rm(*args, **kwargs):
        raise subprocess.SubprocessError("docker rm refused")

    monkeypatch.setattr(stand, "docker", fail_rm)

    assert stand.cleanup() is False
    assert "removed" not in record
    assert stand.report["phases"]["cleanup"]["status"] == "BLOCKED_STAND"
    assert (
        "docker rm refused" in stand.report["phases"]["cleanup"]["errors"][0]["error"]
    )


def test_cleanup_preserves_primary_error_with_cleanup_error(tmp_path, monkeypatch):
    stand = _stand(tmp_path)
    record = _record(stand)
    primary_error = {"type": "StandError", "message": "primary phase failed"}
    stand.evidence.record(metadata={"primary_error": primary_error})
    monkeypatch.setattr(
        stand,
        "inspect",
        lambda kind, identifier: {
            "Id": identifier,
            "Image": record["image"],
            "Mounts": record["mounts"],
            "Config": {"Labels": record["labels"]},
        },
    )
    monkeypatch.setattr(
        stand,
        "docker",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("socket closed")),
    )

    assert stand.cleanup() is False
    assert stand.report["primary_error"] == primary_error
    assert "socket closed" in stand.report["phases"]["cleanup"]["errors"][0]["error"]


def test_registration_and_cleanup_accept_only_mount_order_change(tmp_path, monkeypatch):
    stand = _stand(tmp_path)
    mounts = [
        {"Type": "bind", "Source": "/owned/b", "Destination": "/b", "RW": False, "Mode": "ro,z", "Propagation": "rprivate"},
        {"Type": "bind", "Source": "/owned/a", "Destination": "/a", "RW": True, "Mode": "z", "Propagation": "rprivate"},
    ]
    observed = {"Id": "owned-container", "Name": "/owned-container",
                "Image": "owned-image", "Mounts": deepcopy(mounts),
                "Config": {"Labels": {"complect.payments_run": stand.run_id}}}
    monkeypatch.setattr(stand, "inspect", lambda kind, identifier: deepcopy(observed))
    calls = []
    monkeypatch.setattr(stand, "docker", lambda *args, **kwargs: calls.append(args))
    assert stand.register("container", "owned-container") == "owned-container"
    observed["Mounts"].reverse()
    assert stand.cleanup() is True
    assert calls == [("container", "rm", "-f", "owned-container")]
    assert stand.owned[0]["removed"] is True
    assert sorted(stand.owned[0]["mounts"], key=lambda m: m["Source"]) == sorted(mounts, key=lambda m: m["Source"])


@pytest.mark.parametrize("field,value", [
    ("Source", "/foreign"), ("Destination", "/foreign"), ("Type", "volume"),
    ("RW", True), ("Mode", "rw"), ("Propagation", "rshared"), ("extra", "changed"),
])
def test_mount_order_canonicalization_preserves_every_fingerprint_field(
    tmp_path, monkeypatch, field, value
):
    stand = _stand(tmp_path)
    observed = {
        "Id": "owned-container", "Name": "/owned-container", "Image": "owned-image",
        "Config": {"Labels": {"complect.payments_run": stand.run_id}},
        "Mounts": [
            {"Type": "bind", "Source": "/owned/b", "Destination": "/b", "RW": False, "Mode": "ro,z", "Propagation": "rprivate"},
            {"Type": "bind", "Source": "/owned/a", "Destination": "/a", "RW": True, "Mode": "z", "Propagation": "rprivate"},
        ],
    }
    monkeypatch.setattr(stand, "inspect", lambda kind, identifier: deepcopy(observed))
    calls = []
    monkeypatch.setattr(stand, "docker", lambda *args, **kwargs: calls.append(args))
    stand.register("container", "owned-container")
    observed["Mounts"][0][field] = value
    observed["Mounts"].reverse()
    assert stand.cleanup() is False
    assert calls == []
    assert "removed" not in stand.owned[0]
    assert stand.report["phases"]["cleanup"]["errors"] == [
        {"id": "owned-container", "error": "cleanup image/mount mismatch"}
    ]


def test_infra_container_receives_offline_git_tools_without_owner_access(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from infra.scripts import run_infra_tests

    stand = _stand(tmp_path)
    stand.network = 'none'
    stand.args.backend_image = 'test-backend'
    stand.private = tmp_path / 'private'
    stand.control = tmp_path / 'control'
    stand.state = tmp_path / 'state'
    commands = []

    def create(suffix, image, options, command):
        commands.append(options)
        return 'test-runtime'

    monkeypatch.setattr(stand, 'create', create)
    monkeypatch.setattr(stand, 'docker', lambda *a, **k: SimpleNamespace(returncode=0, stdout='', stderr=''))
    monkeypatch.setattr(stand, 'inspect', lambda *a: {'State': {'ExitCode': 0}})
    monkeypatch.setattr(run_infra_tests, '_tool_mounts', lambda: [
        ('/tools/git', '/usr/local/bin/git'), ('/tools/git-upload-pack', '/usr/local/bin/git-upload-pack'),
        ('/tools/docker', '/usr/local/bin/docker')])

    stand.backend('infra-test', 'runtime', ['-c', 'pass'], infra_tools=True)
    assert '/tools/git:/usr/local/bin/git:ro' in commands[0]
    assert '/tools/git-upload-pack:/usr/local/bin/git-upload-pack:ro' in commands[0]
    assert not any('docker.sock' in arg or '/secrets/owner' in arg or '/tools/docker:' in arg for arg in commands[0])
    assert str(stand.state) + ':/state:ro,z' in commands[0]
    stand.backend('application-test', 'runtime', ['-c', 'pass'])
    assert not any('/tools/' in arg for arg in commands[1])
