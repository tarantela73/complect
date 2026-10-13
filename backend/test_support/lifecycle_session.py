"""External database lifecycle for the restricted pytest runtime.

The application connection is deliberately not granted ``TEMP`` or
``TRUNCATE``.  When ``PYTEST_LIFECYCLE_SOCKET`` is configured, all database
reset requests are sent to the host runner over a small, line-delimited JSON
protocol.  The runner owns the disposable database identity and performs the
operation with the migrator connection.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SOCKET_ENV = "PYTEST_LIFECYCLE_SOCKET"
_RUN_ID_ENV = "PYTEST_LIFECYCLE_RUN_ID"
_TIMEOUT_ENV = "PYTEST_LIFECYCLE_TIMEOUT"
_ALLOWED_OPERATIONS = frozenset({"flush", "fixture_prepare", "fixture_cleanup"})


class LifecycleError(RuntimeError):
    """A lifecycle request was rejected or could not be completed."""


@dataclass(frozen=True, slots=True)
class LifecycleRequest:
    operation: str
    fixture: str | None = None
    run_id: str | None = None

    def as_payload(self) -> dict[str, str]:
        if self.operation not in _ALLOWED_OPERATIONS:
            raise LifecycleError(f"unsupported lifecycle operation: {self.operation}")
        payload = {"op": self.operation}
        if self.fixture is not None:
            if not self.fixture or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in self.fixture):
                raise LifecycleError("fixture name is not a safe protocol identifier")
            payload["fixture"] = self.fixture
        if self.run_id:
            payload["run_id"] = self.run_id
        return payload


class LifecycleClient:
    """Synchronous, fixed-operation client for the host lifecycle runner."""

    def __init__(
        self, socket_path: str | os.PathLike[str], *, run_id: str | None = None, timeout: float = 30.0
    ) -> None:
        path = Path(socket_path)
        if not path.is_absolute():
            raise LifecycleError("lifecycle socket path must be absolute")
        self.socket_path = path
        self.run_id = run_id
        self.timeout = timeout

    def request(self, operation: str, *, fixture: str | None = None) -> dict[str, Any]:
        request = LifecycleRequest(operation, fixture, self.run_id).as_payload()
        encoded = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                channel.settimeout(self.timeout)
                channel.connect(str(self.socket_path))
                channel.sendall(encoded)
                response = self._read_response(channel)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise LifecycleError(f"lifecycle {operation} transport failed: {exc}") from exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            detail = (
                response.get("error", "invalid lifecycle response")
                if isinstance(response, dict)
                else "invalid lifecycle response"
            )
            raise LifecycleError(f"lifecycle {operation} rejected: {detail}")
        return response

    @staticmethod
    def _read_response(channel: socket.socket) -> Any:
        data = bytearray()
        while len(data) <= 64 * 1024:
            chunk = channel.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if b"\n" in chunk:
                break
        if not data or b"\n" not in data:
            raise LifecycleError("lifecycle response was not a complete JSON line")
        return json.loads(bytes(data).split(b"\n", 1)[0])


def configured_client() -> LifecycleClient | None:
    socket_path = os.environ.get(_SOCKET_ENV)
    if not socket_path:
        return None
    try:
        timeout = float(os.environ.get(_TIMEOUT_ENV, "30"))
    except ValueError as exc:
        raise LifecycleError(f"invalid {_TIMEOUT_ENV}") from exc
    if timeout <= 0:
        raise LifecycleError("lifecycle timeout must be positive")
    return LifecycleClient(socket_path, run_id=os.environ.get(_RUN_ID_ENV), timeout=timeout)



class LifecycleSession:
    """Own the runtime lifecycle sequence without depending on pytest hooks."""

    def __init__(self, client: LifecycleClient):
        self._client = client
        self._failed = False
        self._target: dict[str, Any] | None = None

    @property
    def stop_requested(self) -> bool:
        return self._failed

    def prepare(self, client: LifecycleClient | None = None) -> None:
        try:
            response = (client or self._client).request("flush")
            self._target = response["identity"]
        except Exception:
            self._failed = True
            raise

    def finish(self, client: LifecycleClient | None = None) -> None:
        if self._failed:
            return
        try:
            (client or self._client).request("flush")
        except Exception:
            self._failed = True
            raise

    @contextmanager
    def fixture(self, name: str, client: LifecycleClient | None = None) -> Iterator[None]:
        selected = client or self._client
        try:
            selected.request("fixture_prepare", fixture=name)
            yield
        except BaseException:
            self._failed = True
            raise
        else:
            try:
                selected.request("fixture_cleanup", fixture=name)
            except BaseException:
                self._failed = True
                raise

    def flush(self, connection: Any, client: LifecycleClient | None = None) -> list[str]:
        try:
            if self._target is None:
                raise LifecycleError("missing independently verified lifecycle target")
            settings = connection.settings_dict
            if (
                settings["NAME"] != self._target["database"]
                or settings["HOST"] != "postgres_primary"
                or str(settings["PORT"]) != "5432"
                or settings["USER"] != "complect_app_local"
            ):
                raise LifecycleError("runtime alias target mismatch")
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_database(),oid::integer,current_user,session_user "
                    "FROM pg_database WHERE datname=current_database()"
                )
                identity = cursor.fetchone()
            if identity != (
                self._target["database"],
                self._target["database_oid"],
                "complect_app_local",
                "complect_app_local",
            ):
                raise LifecycleError("runtime connection target mismatch")
            (client or self._client).request("flush")
        except BaseException:
            self._failed = True
            raise
        return []
