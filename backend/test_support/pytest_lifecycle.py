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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

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


def _configured_client() -> LifecycleClient | None:
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


@pytest.fixture(scope="session", name="pytest_lifecycle")
def external_lifecycle_client() -> LifecycleClient | None:
    """Return the configured external lifecycle client, when enabled."""

    return _configured_client()


@pytest.fixture(scope="session")
def django_db_setup(request: pytest.FixtureRequest, django_db_blocker: Any) -> Iterator[None]:
    """Use the host-provisioned, already-migrated database.

    The fixture intentionally does not call pytest-django's setup_databases:
    that path creates a database and later flushes it with the application
    connection.  The host runner has already authenticated the exact
    disposable target and owns its migrator credentials.
    """

    client = _configured_client()
    if client is None:
        # This plugin is opt-in so ordinary local pytest runs retain the
        # standard pytest-django database lifecycle.
        yield
        return
    state = request.config.stash.get(_STATE_KEY, None) or _LifecycleState(client)
    request.config.stash[_STATE_KEY] = state
    try:
        response = client.request("flush")
        state.target = response["identity"]
    except Exception:
        state.failed = True
        raise
    yield
    if state.failed:
        return
    try:
        client.request("flush")
    except Exception:
        state.failed = True
        raise


class _LifecycleState:
    def __init__(self, client: LifecycleClient) -> None:
        self.client = client
        self.failed = False
        self.target: dict[str, Any] | None = None


_STATE_KEY: pytest.StashKey[_LifecycleState] = pytest.StashKey()


def _state(config: pytest.Config) -> _LifecycleState | None:
    return config.stash.get(_STATE_KEY, None)


@pytest.fixture
def lifecycle_fixture(pytestconfig: pytest.Config, request: pytest.FixtureRequest) -> Iterator[None]:
    """Prepare and clean a named host fixture around one test.

    Tests request this fixture together with ``@pytest.mark.lifecycle_fixture``;
    the marker value is a fixed fixture identifier, never SQL.
    """

    marker = request.node.get_closest_marker("lifecycle_fixture")
    fixture = marker.args[0] if marker and marker.args else None
    if not isinstance(fixture, str):
        raise LifecycleError("lifecycle_fixture requires one fixture name")
    client = _configured_client()
    if client is None:
        raise LifecycleError(f"{_SOCKET_ENV} is required for lifecycle_fixture")
    state = _state(pytestconfig)
    try:
        client.request("fixture_prepare", fixture=fixture)
        yield
    except BaseException:
        if state:
            state.failed = True
        raise
    else:
        try:
            client.request("fixture_cleanup", fixture=fixture)
        except BaseException:
            if state:
                state.failed = True
            raise


def pytest_runtest_setup(item: pytest.Item) -> None:
    state = _state(item.config)
    if state and state.failed:
        pytest.exit("external lifecycle failed; stopping pytest to protect database provenance")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    state = _state(session.config)
    if state and state.failed and exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def _patch_flush(connection: Any, client: LifecycleClient, state: _LifecycleState) -> tuple[str, Any] | None:
    """Route Django's per-test flush hook outside the application process."""

    operations = connection.ops
    if getattr(operations, "_external_lifecycle_patched", False):
        return None

    def external_flush(*args: Any, **kwargs: Any) -> list[str]:
        try:
            if state.target is None:
                raise LifecycleError("missing independently verified lifecycle target")
            settings = connection.settings_dict
            if (
                settings["NAME"] != state.target["database"]
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
                state.target["database"],
                state.target["database_oid"],
                "complect_app_local",
                "complect_app_local",
            ):
                raise LifecycleError("runtime connection target mismatch")
            client.request("flush")
        except BaseException:
            state.failed = True
            raise
        return []

    if hasattr(operations, "execute_sql_flush"):
        original = operations.execute_sql_flush
        operations.execute_sql_flush = external_flush
        operations._external_lifecycle_original = original
        operations._external_lifecycle_patched = True
        return ("execute_sql_flush", original)
    raise LifecycleError("configured Django backend has no execute_sql_flush seam")


@pytest.fixture(scope="session", autouse=True)
def install_external_flush(
    pytest_lifecycle: LifecycleClient | None,
    django_db_blocker: Any,
    pytestconfig: pytest.Config,
) -> Iterator[None]:
    if pytest_lifecycle is None:
        yield
        return
    state = _LifecycleState(pytest_lifecycle)
    pytestconfig.stash[_STATE_KEY] = state
    # New aliases and thread-local wrappers must use the same lifecycle seam.
    from django.db import connections
    from django.db.backends.signals import connection_created

    originals: list[tuple[Any, str, Any]] = []

    def install(sender: Any, connection: Any, **kwargs: Any) -> None:
        original = _patch_flush(connection, pytest_lifecycle, state)
        if original is not None:
            originals.append((connection.ops, original[0], original[1]))

    connection_created.connect(install, weak=False)
    try:
        with django_db_blocker.unblock():
            for connection in connections.all():
                install(None, connection)
        yield
    finally:
        connection_created.disconnect(install)
        for operations, name, original in originals:
            setattr(operations, name, original)
            operations._external_lifecycle_patched = False
