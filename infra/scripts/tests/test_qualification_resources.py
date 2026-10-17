import json
from types import SimpleNamespace

from infra.scripts import qualification_resources as resources


def _docker_fixture():
    return {
        "container": {"Id": "c1", "Image": "sha256:image", "Mounts": [], "Config": {"Labels": {"a": "1", "b": "2"}}},
        "network": {"Id": "n1", "Name": "net", "Labels": {"a": "1", "b": "2"}},
        "volume": {"Name": "v1", "CreatedAt": "now", "Driver": "local", "Options": {}, "Labels": {"a": "1", "b": "2"}},
    }


def test_capture_baseline_records_daemon_and_canonical_fingerprints(monkeypatch):
    fixture = _docker_fixture()
    monkeypatch.setattr(resources, "_docker_endpoint_is_local", lambda: None)

    def run(command, **_kwargs):
        if command[1:3] == ["ps", "-aq"]:
            return SimpleNamespace(stdout="c1\n")
        if command[1:4] == ["network", "ls", "-q"]:
            return SimpleNamespace(stdout="n1\n")
        if command[1:4] == ["volume", "ls", "-q"]:
            return SimpleNamespace(stdout="v1\n")
        if command[1:5] == ["image", "ls", "-q", "--no-trunc"]:
            return SimpleNamespace(stdout="img1\n")
        if command[1:3] == ["info", "--format"]:
            return SimpleNamespace(stdout="daemon-1\n")
        if command[1] == "inspect":
            return SimpleNamespace(stdout=json.dumps([fixture["container"]]))
        if command[1:3] == ["network", "inspect"]:
            return SimpleNamespace(stdout=json.dumps([fixture["network"]]))
        if command[1:3] == ["volume", "inspect"]:
            return SimpleNamespace(stdout=json.dumps([fixture["volume"]]))
        if command[1:3] == ["image", "inspect"]:
            return SimpleNamespace(stdout=json.dumps([{"Id": "img1", "RepoTags": ["complect:dev"], "RepoDigests": ["complect@sha256:1"]}]))
        raise AssertionError(command)

    baseline = resources.capture_baseline(run=run)
    assert baseline["daemon_id"] == "daemon-1"
    assert {item["id"] for item in baseline["resources"]} == {"c1", "n1", "v1"}
    assert baseline["images"] == [{"id": "img1", "references": ["complect:dev", "complect@sha256:1"]}]
    assert "Env" not in json.dumps(baseline)


def test_verify_allows_new_resources_but_reports_missing_or_changed(monkeypatch):
    monkeypatch.setattr(resources, "_docker_endpoint_is_local", lambda: None)
    baseline = {"daemon_id": "daemon-1", "resources": [{"kind": "container", "id": "c1", "fingerprint": {"Id": "c1", "Image": "i1", "Mounts": []}, "labels": {"a": "1", "b": "2"}}], "images": []}

    def run(command, **_kwargs):
        if command[1:3] == ["info", "--format"]:
            return SimpleNamespace(stdout="daemon-1\n")
        if command[1:3] == ["ps", "-aq"]:
            return SimpleNamespace(stdout="c1\nnew\n")
        if command[1] == "inspect":
            return SimpleNamespace(stdout=json.dumps([{"Id": "c1", "Image": "i2", "Mounts": [], "Config": {"Labels": {"a": "1", "b": "2"}}}, {"Id": "new", "Image": "i", "Mounts": [], "Config": {"Labels": {}}}]))
        if command[1:4] == ["network", "ls", "-q"] or command[1:4] == ["volume", "ls", "-q"]:
            return SimpleNamespace(stdout="")
        if command[1:5] == ["image", "ls", "-q", "--no-trunc"]:
            return SimpleNamespace(stdout="")
        raise AssertionError(command)

    errors = resources.verify_baseline(baseline, run=run)
    assert any("changed" in error for error in errors)


def test_absence_proof_uses_two_labels_and_fails_closed(monkeypatch):
    monkeypatch.setattr(resources, "_docker_endpoint_is_local", lambda: None)
    seen = []

    def run(command, **_kwargs):
        seen.append(command)
        return SimpleNamespace(stdout="")

    assert resources.prove_attempt_absent({"labels": {"a": "1", "b": "2"}}, run=run) == []
    listing = [command for command in seen if command[:2] == ["docker", "ps"]][0]
    assert "label=a=1" in listing and "label=b=2" in listing
    assert resources.prove_attempt_absent({"labels": {"a": "1"}}, run=run) == ["attempt ownership labels are incomplete"]


def test_absence_proof_accepts_missing_recorded_resources(monkeypatch):
    monkeypatch.setattr(resources, "_docker_endpoint_is_local", lambda: None)

    def run(command, **_kwargs):
        return SimpleNamespace(stdout="")

    receipt = {
        "labels": {"a": "1", "b": "2"},
        "resources": [{"kind": "container", "id": "gone"}],
        "image": {"reference": "complect:attempt", "id": "sha256:gone"},
    }
    assert resources.prove_attempt_absent(receipt, run=run) == []


def test_verify_rejects_missing_baseline_schema(monkeypatch):
    monkeypatch.setattr(resources, "_docker_endpoint_is_local", lambda: None)
    assert resources.verify_baseline({"daemon_id": "daemon-1"}, run=lambda *_args, **_kwargs: None) == [
        "baseline schema invalid"
    ]


def test_verify_rejects_missing_baseline_images(monkeypatch):
    monkeypatch.setattr(resources, "_docker_endpoint_is_local", lambda: None)
    assert resources.verify_baseline({"daemon_id": "daemon-1", "resources": []}, run=lambda *_args, **_kwargs: None) == [
        "baseline schema invalid"
    ]


def test_verify_inspects_only_baseline_ids_not_transient_new_resources(monkeypatch):
    monkeypatch.setattr(resources, '_docker_endpoint_is_local', lambda: None)
    baseline = {'daemon_id': 'daemon-1', 'resources': [
        {'kind': 'container', 'id': 'stable', 'fingerprint': {'Id': 'stable', 'Image': 'image', 'Mounts': []}, 'labels': {}}
    ], 'images': []}

    def run(command, **_kwargs):
        if command[1:3] == ['info', '--format']:
            return SimpleNamespace(stdout='daemon-1')
        if command[1:] == ['inspect', 'stable']:
            return SimpleNamespace(stdout=json.dumps([{'Id': 'stable', 'Image': 'image', 'Mounts': [], 'Config': {'Labels': {}}}]))
        raise AssertionError('must not enumerate transient new resources')

    assert resources.verify_baseline(baseline, run=run) == []
