"""Isolate Buildx service writes while retaining Docker connection/auth inputs."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any


class HostPreparationError(ValueError):
    def __init__(self, record: dict[str, Any], cleanup_errors: list[str]):
        super().__init__(
            "BUILDX_CONFIG preparation failed; host environment cleanup unconfirmed"
        )
        self.record = record
        self.cleanup_errors = cleanup_errors


def _safe_directory(path: Path) -> os.stat_result:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("host environment symlink refused")
    info = path.stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ValueError("host environment ownership/mode mismatch")
    return info


def cleanup_host_environment(base: Path, record: dict[str, Any]) -> list[str]:
    """Delete only the originally created private directory; fail closed on replacement."""
    try:
        base = base.absolute()
        if not isinstance(record, dict):
            raise ValueError("host environment record invalid")
        if record.get("path") != str(base):
            raise ValueError("host environment path mismatch")
        if not base.exists() and not base.is_symlink():
            return []
        info = _safe_directory(base)
        if (info.st_dev, info.st_ino, info.st_uid) != (
            record.get("device"),
            record.get("inode"),
            record.get("uid"),
        ):
            raise ValueError("host environment replaced")
        marker = base / ".owner"
        if (
            marker.is_symlink()
            or not marker.is_file()
            or marker.read_text() != record.get("token")
        ):
            raise ValueError("host environment ownership token mismatch")
        shutil.rmtree(base)
        return []
    except (OSError, ValueError):
        return ["host environment cleanup unconfirmed"]


class HostDockerEnvironment:
    def __init__(self, base: Path, environment: dict[str, str], record: dict[str, Any]):
        self.base, self.environment, self.record = base, environment, record

    @contextlib.contextmanager
    def activated(self) -> Iterator[None]:
        previous = os.environ.get("BUILDX_CONFIG")
        os.environ["BUILDX_CONFIG"] = self.environment["BUILDX_CONFIG"]
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("BUILDX_CONFIG", None)
            else:
                os.environ["BUILDX_CONFIG"] = previous

    def cleanup(self) -> list[str]:
        return cleanup_host_environment(self.base, self.record)


def _probe_writable(path: Path, setting: str) -> None:
    try:
        if not path.is_dir():
            raise OSError("directory required")
        with tempfile.TemporaryFile(dir=path) as probe:
            probe.write(b"probe")
            probe.flush()
            os.fsync(probe.fileno())
    except OSError:
        raise ValueError(setting + " directory is not writable; no fallback") from None


def _check_builder_selection(values: dict[str, str]) -> None:
    if "BUILDX_CONFIG" in values:
        return
    config = Path(
        values.get(
            "DOCKER_CONFIG", str(Path(values.get("HOME", str(Path.home()))) / ".docker")
        )
    )
    try:
        selected_context = values.get("DOCKER_CONTEXT", "default")
        if "DOCKER_CONTEXT" not in values and (config / "config.json").exists():
            selected_context = (
                json.loads((config / "config.json").read_text()).get("currentContext")
                or "default"
            )
        current = config / "buildx/current"
        name = json.loads(current.read_text()).get("Name") if current.exists() else ""
        if name is not None and not isinstance(name, str):
            raise ValueError("invalid builder selection")
        chosen = values.get("BUILDX_BUILDER") or name
        if chosen not in (None, "", "default", selected_context):
            raise ValueError("custom builder selection")
    except (OSError, ValueError, AttributeError):
        raise ValueError(
            "Cannot preserve Buildx selection; set an explicit writable BUILDX_CONFIG"
        ) from None


def prepare_host_environment(
    base: Path, environment: Mapping[str, str] | None = None
) -> HostDockerEnvironment:
    """Prepare one private attempt; never copy credentials or change Docker config."""
    values = dict(os.environ if environment is None else environment)
    for setting in ("DOCKER_CONFIG", "BUILDX_CONFIG"):
        if setting in values:
            if not values[setting]:
                raise ValueError(setting + " must name a writable directory")
            _probe_writable(Path(values[setting]), setting)
    _check_builder_selection(values)
    base = base.absolute()
    if any(p.is_symlink() for p in (base, *base.parents)):
        raise ValueError("host environment symlink refused")
    base.mkdir(mode=0o700, parents=True)
    info = _safe_directory(base)
    token = uuid.uuid4().hex
    record = {
        "path": str(base),
        "device": info.st_dev,
        "inode": info.st_ino,
        "uid": info.st_uid,
        "token": token,
    }
    try:
        (base / ".owner").write_text(token)
        if "BUILDX_CONFIG" not in values:
            buildx = base / "buildx"
            buildx.mkdir(mode=0o700)
            _probe_writable(buildx, "BUILDX_CONFIG")
            values["BUILDX_CONFIG"] = str(buildx)
    except (OSError, ValueError) as error:
        cleanup_errors = cleanup_host_environment(base, record)
        if cleanup_errors:
            raise HostPreparationError(record, cleanup_errors) from None
        raise error
    return HostDockerEnvironment(base, values, record)
