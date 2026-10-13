"""Behavioral tests for disposable Payments stand cleanup ownership."""

from __future__ import annotations

from argparse import Namespace
from copy import deepcopy
from pathlib import Path
import subprocess

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
