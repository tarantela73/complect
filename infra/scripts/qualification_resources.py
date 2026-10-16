"""Read-only Docker identity proofs for legacy qualification attempts."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from infra.scripts.run_infra_tests import _docker_endpoint_is_local

Runner = Callable[..., Any]


def _run_json(command: list[str], run: Runner) -> Any:
    try:
        completed = run(command, capture_output=True, text=True, check=True, timeout=60)
    except TypeError:
        completed = run(command)
    if isinstance(completed, (list, dict)):
        return completed
    stdout = getattr(completed, "stdout", completed)
    if isinstance(stdout, bytes):
        stdout = stdout.decode()
    if not isinstance(stdout, str):
        raise ValueError("Docker output is not text")
    return json.loads(stdout)


def _ids(kind: str, run: Runner) -> list[str]:
    command = {
        "container": ["docker", "ps", "-aq", "--no-trunc"],
        "network": ["docker", "network", "ls", "-q", "--no-trunc"],
        "volume": ["docker", "volume", "ls", "-q"],
    }[kind]
    try:
        completed = run(command, capture_output=True, text=True, check=True, timeout=60)
    except TypeError:
        completed = run(command)
    stdout = getattr(completed, "stdout", completed)
    if isinstance(stdout, bytes):
        stdout = stdout.decode()
    return str(stdout).split()


def _image_ids(run: Runner) -> list[str]:
    return _ids_from_command(["docker", "image", "ls", "-q", "--no-trunc"], run)


def _inspect(kind: str, identifiers: list[str], run: Runner) -> list[dict[str, Any]]:
    if not identifiers:
        return []
    prefix = [] if kind == "container" else [kind]
    value = _run_json(["docker", *prefix, "inspect", *identifiers], run)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"docker {kind} inspect returned invalid JSON")
    return value


def _labels(kind: str, resource: Mapping[str, Any]) -> dict[str, str]:
    raw = resource.get("Config", {}).get("Labels") if kind == "container" else resource.get("Labels")
    return dict(raw or {})


def _identity(kind: str, resource: Mapping[str, Any]) -> dict[str, Any]:
    if kind == "container":
        return {key: resource[key] for key in ("Id", "Image", "Mounts")}
    if kind == "network":
        return {key: resource[key] for key in ("Id", "Name")}
    return {key: resource[key] for key in ("Name", "CreatedAt", "Driver", "Options")}


def _resource_record(kind: str, resource: Mapping[str, Any]) -> dict[str, Any]:
    identity = _identity(kind, resource)
    key = resource["Id"] if kind != "volume" else resource["Name"]
    return {"kind": kind, "id": key, "fingerprint": identity, "labels": _labels(kind, resource)}


def _image_record(resource: Mapping[str, Any]) -> dict[str, Any]:
    references = sorted(set(resource.get("RepoTags") or ()) | set(resource.get("RepoDigests") or ()))
    return {"id": resource["Id"], "references": references}


def _daemon_id(run: Runner) -> str:
    command = ["docker", "info", "--format", "{{.ID}}"]
    try:
        completed = run(command, capture_output=True, text=True, check=True, timeout=60)
    except TypeError:
        completed = run(command)
    value = getattr(completed, "stdout", completed)
    if isinstance(value, bytes):
        value = value.decode()
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Docker daemon ID is missing")
    return value.strip()


def capture_baseline(*, run: Runner = subprocess.run) -> dict[str, Any]:
    """Capture all local Docker identities without mutating Docker state."""
    _docker_endpoint_is_local()
    resources: list[dict[str, Any]] = []
    for kind in ("container", "network", "volume"):
        resources.extend(_resource_record(kind, item) for item in _inspect(kind, _ids(kind, run), run))
    images = [_image_record(item) for item in _inspect("image", _image_ids(run), run)]
    return {"daemon_id": _daemon_id(run), "resources": resources, "images": images}


def verify_baseline(baseline: Mapping[str, Any], *, run: Runner = subprocess.run) -> list[str]:
    """Verify recorded identities; resources created after capture are allowed."""
    if (
        not isinstance(baseline, Mapping)
        or not isinstance(baseline.get("daemon_id"), str)
        or not isinstance(baseline.get("resources"), list)
        or not isinstance(baseline.get("images"), list)
    ):
        return ["baseline schema invalid"]
    errors: list[str] = []
    try:
        _docker_endpoint_is_local()
        if _daemon_id(run) != baseline["daemon_id"]:
            errors.append("daemon identity changed")
        current_baseline = capture_baseline(run=run)
        current = current_baseline["resources"]
    except Exception as exc:
        return [f"baseline verification failed: {type(exc).__name__}"]
    by_key = {(item["kind"], item["id"]): item for item in current}
    for expected in baseline.get("resources", []):
        key = (expected.get("kind"), expected.get("id"))
        actual = by_key.get(key)
        if actual is None:
            errors.append(f"missing baseline resource: {key[0]}:{key[1]}")
        elif actual != expected:
            errors.append(f"baseline resource changed: {key[0]}:{key[1]}")
    current_images = {item["id"]: set(item["references"]) for item in current_baseline["images"]}
    for expected in baseline["images"]:
        identifier = expected.get("id") if isinstance(expected, Mapping) else None
        references = expected.get("references") if isinstance(expected, Mapping) else None
        if not isinstance(identifier, str) or not isinstance(references, list):
            errors.append("baseline image schema invalid")
        elif identifier not in current_images:
            errors.append(f"missing baseline image: {identifier}")
        elif not set(references).issubset(current_images[identifier]):
            errors.append(f"baseline image references changed: {identifier}")
    return errors


def prove_attempt_absent(receipt: Mapping[str, Any], *, run: Runner = subprocess.run) -> list[str]:
    """Prove an attempt's owned resources and recorded image are absent."""
    errors: list[str] = []
    try:
        _docker_endpoint_is_local()
        labels = receipt.get("labels", {})
        if not labels and receipt.get("project") and receipt.get("checkout_id"):
            labels = {
                "com.docker.compose.project": str(receipt["project"]),
                "complect.checkout_id": str(receipt["checkout_id"]),
            }
        if not isinstance(labels, Mapping) or len(labels) < 2:
            return ["attempt ownership labels are incomplete"]
        for kind, list_command in (("container", "ps"), ("network", "network ls"), ("volume", "volume ls")):
            command = ["docker", *list_command.split(), "-q"]
            for key, value in labels.items():
                command.extend(["--filter", f"label={key}={value}"])
            if _ids_from_command(command, run):
                errors.append(f"residual owned {kind}")
        for record in receipt.get("resources", []):
            kind, identifier = record.get("kind"), record.get("id")
            if kind not in {"container", "network", "volume"} or not identifier:
                errors.append("invalid recorded resource")
                continue
            if _resource_exists(kind, identifier, run):
                try:
                    _inspect(kind, [identifier], run)
                except Exception:
                    errors.append(f"recorded resource inspection failed: {kind}:{identifier}")
                    continue
                errors.append(f"recorded resource remains: {kind}:{identifier}")
        image = receipt.get("image")
        if isinstance(image, Mapping) and image.get("reference"):
            reference = str(image["reference"])
            if _ids_from_command(
                ["docker", "image", "ls", "-q", "--no-trunc", "--filter", f"reference={reference}"], run
            ):
                try:
                    found = _run_json(["docker", "image", "inspect", reference], run)
                    if image.get("id") and found[0].get("Id") != image["id"]:
                        errors.append("recorded image identity changed")
                    errors.append("recorded image remains")
                except Exception:
                    errors.append("recorded image inspection failed")
    except Exception as exc:
        errors.append(f"attempt absence verification failed: {type(exc).__name__}")
    return errors


def _ids_from_command(command: list[str], run: Runner) -> list[str]:
    try:
        completed = run(command, capture_output=True, text=True, check=True, timeout=60)
    except TypeError:
        completed = run(command)
    stdout = getattr(completed, "stdout", completed)
    if isinstance(stdout, bytes):
        stdout = stdout.decode()
    return str(stdout).split()


def _resource_exists(kind: str, identifier: str, run: Runner) -> bool:
    if kind == "container":
        command = ["docker", "ps", "-aq", "--no-trunc", "--filter", f"id={identifier}"]
    elif kind == "network":
        command = ["docker", "network", "ls", "-q", "--no-trunc", "--filter", f"id={identifier}"]
    elif kind == "volume":
        command = ["docker", "volume", "ls", "-q", "--filter", f"name={identifier}"]
    else:
        return False
    return bool(_ids_from_command(command, run))
