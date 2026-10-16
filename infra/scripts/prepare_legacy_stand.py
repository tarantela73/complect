"""Explicit preparation and ownership-checked cleanup of a disposable legacy stand."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import signal
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from infra.scripts import agent_harness
from infra.scripts.dev_environment import Commands, build_backend, configure, prepare_database_startup
from infra.scripts.legacy_preflight import require_original_nodes, verify_prepared_checkout
from infra.scripts.run_infra_tests import ROOT, _docker_endpoint_is_local

MANIFEST = ROOT / "infra/legacy133.json"


class StandInterrupted(Exception):
    """Preparation was interrupted after its receipt had been created."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _raise_sigterm(_signum, _frame):
    raise StandInterrupted("SIGTERM")


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def stand_commands(checkout: Path, registered: dict[str, Any]) -> Commands:
    """Use the checkout-local legacy template and its recorded override."""
    commands = Commands(checkout)
    template = checkout / "infra/legacy-stand.compose.yml"
    recorded_template = registered["compose_template"]
    if template != Path(recorded_template["path"]) or template.is_symlink():
        raise ValueError("legacy stand template path mismatch")
    if hashlib.sha256(template.read_bytes()).hexdigest() != recorded_template["sha256"]:
        raise ValueError("legacy stand template content mismatch")
    override = registered["compose_override"]
    path = Path(override["path"])
    if path != checkout.parent / "stand.compose.json" or path.is_symlink():
        raise ValueError("stand override path mismatch")
    if hashlib.sha256(path.read_bytes()).hexdigest() != override["sha256"]:
        raise ValueError("stand override content mismatch")
    prefix: list[str] = []
    skip_compose_file = False
    for argument in commands.prefix:
        if skip_compose_file:
            skip_compose_file = False
            continue
        if argument == "-f":
            skip_compose_file = True
            continue
        prefix.append(argument)
    commands.prefix = [*prefix, "-f", str(template), "-f", str(path), "--profile", "real-files"]
    return commands


def _copy_sources(root: Path, checkout: Path, snapshot: dict[str, Any]) -> None:
    checkout.mkdir()
    for relative in snapshot["files"]:
        source = root / relative
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            target.symlink_to(source.readlink())
        else:
            shutil.copy2(source, target)
    # Git metadata is disposable; source_digest uses git's file inventory.
    subprocess.run(["git", "init", "--quiet", str(checkout)], check=True, capture_output=True)


def _fingerprint(kind: str, resource: dict[str, Any]) -> dict[str, Any]:
    if kind == "container":
        return {key: resource[key] for key in ("Id", "Image", "Mounts")}
    if kind == "network":
        return {key: resource[key] for key in ("Id", "Name")}
    return {key: resource[key] for key in ("Name", "CreatedAt", "Driver", "Options")}


def _labels(kind: str, resource: dict[str, Any]) -> dict[str, str]:
    return (resource["Config"].get("Labels") if kind == "container" else resource.get("Labels")) or {}


def _assert_owned(kind: str, resource: dict[str, Any], receipt: dict[str, Any]) -> None:
    labels = _labels(kind, resource)
    if (labels.get("com.docker.compose.project") != receipt["project"]
            or labels.get("complect.checkout_id") != receipt["checkout_id"]):
        raise ValueError("stand resource ownership mismatch")


def _inspect_optional(commands: Commands, arguments: list[str]) -> dict[str, Any] | None:
    """Confirm absence by a successful bounded listing; failed probes are unknown."""
    kind = "container" if arguments[1] == "inspect" else arguments[1]
    identifier = arguments[-1]
    if kind == "container":
        listing = ["docker", "ps", "-aq", "--no-trunc", "--filter", f"id={identifier}"]
    elif kind == "network":
        listing = ["docker", "network", "ls", "-q", "--no-trunc", "--filter", f"id={identifier}"]
    elif kind == "volume":
        listing = ["docker", "volume", "ls", "-q", "--filter", f"name={identifier}"]
    elif kind == "image":
        listing = ["docker", "image", "ls", "-q", "--no-trunc", "--filter", f"reference={identifier}"]
    else:
        raise ValueError("unsupported stand resource kind")
    names = commands.run(listing).stdout.split()
    if not names or (kind == "volume" and identifier not in names):
        return None
    return json.loads(commands.run(arguments).stdout)[0]


def _inventory(commands: Commands, receipt: dict[str, Any]) -> None:
    """Register partial preparation as well as successful resources before cleanup."""
    receipt["inventory_complete"] = False
    receipt["inventory_status"] = "in_progress"
    resources: list[dict[str, Any]] = []
    receipt["resources"] = resources
    for kind, listing in (("container", ["ps", "-aq"]), ("network", ["network", "ls", "-q"]),
                          ("volume", ["volume", "ls", "-q"])):
        names = commands.run(["docker", *listing, "--filter", f"label=com.docker.compose.project={commands.project}"]).stdout.split()
        for name in names:
            prefix = [] if kind == "container" else [kind]
            actual = _inspect_optional(commands, ["docker", *prefix, "inspect", name])
            if actual is None:
                continue
            _assert_owned(kind, actual, receipt)
            resources.append({"kind": kind, "id": actual.get("Id", actual.get("Name")),
                              "fingerprint": _fingerprint(kind, actual), "labels": _labels(kind, actual)})
    receipt["resources"] = resources
    reference = f"{commands.project}_backend:dev"
    image = _inspect_optional(commands, ["docker", "image", "inspect", reference])
    known = receipt.get("image")
    if image is not None:
        if known and (receipt.get("image_removed") or image["Id"] != known["id"]):
            raise ValueError("stand image identity changed")
        receipt["image"] = {"reference": reference, "id": image["Id"]}
        receipt.pop("image_removed", None)
    else:
        receipt.pop("image", None)
    removed = set(receipt.get("removed", []))
    reappeared = {
        record["kind"] + ":" + record["id"]
        for record in resources
        if record["kind"] + ":" + record["id"] in removed
    }
    if reappeared:
        receipt["inventory_error"] = "removed_resource_reappeared"
        raise ValueError("removed resource reappeared")
    receipt["inventory_complete"] = True
    receipt["inventory_status"] = "complete"


def cleanup(receipt_path: Path) -> dict[str, Any]:
    """Remove only recorded resources whose identity and ownership still match."""
    _docker_endpoint_is_local()
    receipt = json.loads(receipt_path.read_text())
    checkout = Path(receipt["root"])
    if checkout != receipt_path.resolve().parent / "checkout" or checkout.is_symlink():
        raise ValueError("stand receipt/root mismatch")
    commands = stand_commands(checkout, receipt)
    if commands.project != receipt["project"] or commands.values["DEV_CHECKOUT_ID"] != receipt["checkout_id"]:
        raise ValueError("stand configuration identity mismatch")
    errors = [{"error": receipt["inventory_error"]}] if receipt.get("inventory_error") else []
    if receipt.get("inventory_complete") is not True:
        errors.append({"error": "inventory_incomplete"})
    removed = set(receipt.get("removed", []))
    for record in receipt.get("resources", []):
        key = record["kind"] + ":" + record["id"]
        if key in removed:
            continue
        try:
            kind = record["kind"]
            prefix = [] if kind == "container" else [kind]
            actual = _inspect_optional(commands, ["docker", *prefix, "inspect", record["id"]])
            if actual is None:
                removed.add(key)
                receipt["removed"] = sorted(removed)
                _write(receipt_path, receipt)
                continue
            _assert_owned(kind, actual, receipt)
            if _fingerprint(kind, actual) != record["fingerprint"] or _labels(kind, actual) != record["labels"]:
                raise ValueError("stand resource identity changed")
            operation = ["rm", "-f"] if kind == "container" else [kind, "rm"]
            commands.run(["docker", *operation, record["id"]], write=True)
            removed.add(key)
            receipt["removed"] = sorted(removed)
            _write(receipt_path, receipt)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append({"resource": key, "error": type(exc).__name__})
        except Exception as exc:
            # DevError carries safe diagnostics, but receipts need no command text.
            errors.append({"resource": key, "error": type(exc).__name__})
    image = receipt.get("image")
    if image and not receipt.get("image_removed"):
        try:
            actual = _inspect_optional(commands, ["docker", "image", "inspect", image["reference"]])
            if actual is None:
                receipt["image_removed"] = True
                _write(receipt_path, receipt)
                image = None
            else:
                if actual["Id"] != image["id"]:
                    raise ValueError("stand image identity changed")
                commands.run(["docker", "image", "rm", image["reference"]], write=True)
                receipt["image_removed"] = True
                _write(receipt_path, receipt)
        except Exception as exc:
            errors.append({"resource": "image", "error": type(exc).__name__})
    receipt["cleanup"] = "BLOCKED_STAND" if errors else "PASS"
    receipt["cleanup_errors"] = errors
    _write(receipt_path, receipt)
    return receipt


def recover_inventory(receipt_path: Path) -> dict[str, Any]:
    """Refresh a partial receipt after an interruption; do not resume preparation."""
    _docker_endpoint_is_local()
    receipt = json.loads(receipt_path.read_text())
    checkout = Path(receipt["root"])
    if checkout != receipt_path.resolve().parent / "checkout" or checkout.is_symlink():
        raise ValueError("stand receipt/root mismatch")
    commands = stand_commands(checkout, receipt)
    if commands.project != receipt["project"] or commands.values["DEV_CHECKOUT_ID"] != receipt["checkout_id"]:
        raise ValueError("stand configuration identity mismatch")
    receipt["inventory_complete"] = False
    receipt["inventory_status"] = "in_progress"
    _write(receipt_path, receipt)
    for record in receipt.get("resources", []):
        kind = record["kind"]
        prefix = [] if kind == "container" else [kind]
        key = f"{kind}:{record['id']}"
        actual = _inspect_optional(commands, ["docker", *prefix, "inspect", record["id"]])
        if key in set(receipt.get("removed", [])) and actual is not None:
            raise ValueError("removed stand resource is still present")
        if actual is None:
            continue
        _assert_owned(kind, actual, receipt)
        if (_fingerprint(kind, actual) != record["fingerprint"]
                or _labels(kind, actual) != record["labels"]):
            raise ValueError("stand resource identity changed")
    _inventory(commands, receipt)
    receipt.pop("inventory_error", None)
    receipt.pop("cleanup_errors", None)
    receipt["recovery"] = {
        "explicit": True,
        "project": commands.project,
        "checkout_id": commands.values["DEV_CHECKOUT_ID"],
    }
    _write(receipt_path, receipt)
    return receipt


def _cleanup_failed_prepare(path: Path, receipt: dict[str, Any]) -> None:
    try:
        receipt.update(cleanup(path))
    except BaseException as cleanup_error:
        receipt["cleanup"] = "BLOCKED_STAND"
        receipt["cleanup_errors"] = [{"error": type(cleanup_error).__name__}]
    _write(path, receipt)


def prepare(source_root: Path, snapshot_path: Path, directory: Path, *, manifest: Path = MANIFEST) -> dict[str, Any]:
    source_root, snapshot_path, directory = source_root.resolve(), snapshot_path.resolve(), directory.resolve()
    snapshot = json.loads(snapshot_path.read_text())
    if snapshot.get("format") != "complect-working-tree-snapshot-v1":
        raise ValueError("explicit working-tree snapshot required")
    errors = agent_harness.verify_working_tree_snapshot(source_root, snapshot_path)
    if errors:
        raise ValueError("source snapshot is stale")
    nodes = json.loads(manifest.read_text())["collected"]
    if require_original_nodes(nodes):
        raise ValueError("original 133-node manifest required")
    if not directory.is_relative_to(ROOT / ".dev/test-artifacts") or directory.exists():
        raise ValueError("stand directory must be fresh under .dev/test-artifacts")
    _docker_endpoint_is_local()
    directory.mkdir(parents=True, mode=0o700)
    checkout = directory / "checkout"
    _copy_sources(source_root, checkout, snapshot)
    if verify_prepared_checkout(checkout, snapshot_path):
        raise ValueError("prepared source copy mismatch")
    shutil.copy2(snapshot_path, directory / "snapshot.json")
    _write(directory / "manifest.json", {"collected": nodes})
    configure(checkout, isolated_e2e=True)
    identifier = uuid.uuid4()
    database = "test_iam_5_8_" + identifier.hex
    services: dict[str, Any] = {name: {"environment": {"POSTGRES_DB": database}}
                                for name in ("postgres_primary", "postgres_replica", "backend", "migrate")}
    services["postgres_primary"]["healthcheck"] = {
        "test": ["CMD", "pg_isready", "-t", "2", "-h", "localhost", "-U", "complect_migrator", "-d", database],
    }
    services["postgres_replica"]["healthcheck"] = {
        "test": ["CMD-SHELL", f'test "$$(psql -X -U complect_migrator -d {database} -Atc \'SELECT pg_is_in_recovery()\')" = t'],
    }
    for service in ("backend", "migrate"):
        services[service]["environment"]["PUBLIC_SITE_TENANT_ID"] = str(identifier)
    services["redis"] = {"container_name": "complect-iam-test-" + str(identifier),
                         "labels": {"complect.task_run": identifier.hex}}
    override = directory / "stand.compose.json"
    _write(override, {"services": services})
    template = checkout / "infra/legacy-stand.compose.yml"
    if template.is_symlink() or not template.is_file():
        raise ValueError("prepared checkout is missing legacy stand template")
    base = Commands(checkout)
    receipt: dict[str, Any] = {
        "root": str(checkout), "source_root": str(source_root), "run_id": identifier.hex,
        "project": base.project, "checkout_id": base.values["DEV_CHECKOUT_ID"],
        "compose_template": {"path": str(template), "sha256": hashlib.sha256(template.read_bytes()).hexdigest()},
        "compose_override": {"path": str(override), "sha256": hashlib.sha256(override.read_bytes()).hexdigest()},
        "status": "BLOCKED_STAND", "resources": [],
        "inventory_complete": False, "inventory_status": "unknown",
    }
    path = directory / "ownership.json"
    _write(path, receipt)
    commands = stand_commands(checkout, receipt)
    previous_sigterm = signal.signal(
        signal.SIGTERM,
        _raise_sigterm,
    )
    try:
        with (directory / "prepare.log").open("w") as log, contextlib.redirect_stdout(log):
            build_backend(commands)
            prepare_database_startup(commands)
            commands.compose("up", "-d", "--wait", "--wait-timeout", "360", "backend", "clamav", write=True, timeout=600)
        containers = commands.containers()
        receipt["containers"] = {service: {"id": item["Id"], "image": item["Image"], "mounts": item["Mounts"]}
                                 for service, item in containers.items()}
        for item in containers.values():
            _assert_owned("container", item, receipt)
        postgres = containers["postgres_primary"]
        query = "SELECT d.oid, s.system_identifier::text, current_user, session_user FROM pg_database d CROSS JOIN pg_control_system() s WHERE d.datname=current_database()"
        identity = commands.run(["docker", "exec", postgres["Id"], "psql", "-X", "-At", "-U", "complect_migrator", "-d", database, "-c", query]).stdout.strip().split("|")
        receipt["database"] = dict(zip(("database_oid", "cluster", "user", "session_user"), identity, strict=True), database=database)
        receipt["database"]["database_oid"] = int(receipt["database"]["database_oid"])
        redis = containers["redis"]
        networks = list(redis["NetworkSettings"]["Networks"])
        if len(networks) != 1:
            raise ValueError("stand redis network mismatch")
        receipt["redis"] = {"id": redis["Id"], "name": redis["Name"].lstrip("/"), "network": networks[0]}
        receipt["status"] = "PASS"
        _inventory(commands, receipt)
        _write(path, receipt)
        return receipt
    except BaseException as exc:
        receipt["status"] = "BLOCKED_STAND"
        receipt["interruption"] = {"reason": getattr(exc, "reason", type(exc).__name__)}
        receipt["error"] = type(exc).__name__
        receipt["error_message"] = commands.redact(str(exc))
        _write(path, receipt)
        try:
            _inventory(commands, receipt)
        except Exception as inventory_error:
            receipt["inventory_error"] = type(inventory_error).__name__
        _write(path, receipt)
        _cleanup_failed_prepare(path, receipt)
        raise
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("prepare")
    create.add_argument("--source-root", type=Path, default=ROOT)
    create.add_argument("--snapshot", type=Path, required=True)
    create.add_argument("--directory", type=Path, required=True)
    create.add_argument("--manifest", type=Path, default=MANIFEST)
    remove = commands.add_parser("cleanup")
    remove.add_argument("--ownership", type=Path, required=True)
    recover = commands.add_parser("recover-inventory")
    recover.add_argument("--ownership", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            prepare(args.source_root, args.snapshot, args.directory, manifest=args.manifest)
            print(json.dumps({"status": "PASS", "ownership": str(args.directory / "ownership.json")}))
            return 0
        if args.command == "recover-inventory":
            result = recover_inventory(args.ownership.resolve())
            print(json.dumps({"status": result["inventory_status"], "ownership": str(args.ownership)}))
            return 0 if result.get("inventory_complete") is True else 2
        result = cleanup(args.ownership.resolve())
        print(json.dumps({"status": result["cleanup"], "errors": result["cleanup_errors"]}))
        return 0 if result["cleanup"] == "PASS" else 2
    except BaseException as exc:
        print(json.dumps({"status": "BLOCKED_STAND", "error": type(exc).__name__, "message": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
