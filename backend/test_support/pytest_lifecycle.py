"""Pytest adapter for the external restricted-runtime lifecycle session."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from .lifecycle_session import (
    _SOCKET_ENV,
    LifecycleClient,
    LifecycleError,
    LifecycleSession,
)
from .lifecycle_session import (
    LifecycleRequest as LifecycleRequest,
)
from .lifecycle_session import (
    configured_client as _configured_client,
)


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
    state = request.config.stash.get(_STATE_KEY, None) or LifecycleSession(client)
    request.config.stash[_STATE_KEY] = state
    state.prepare(client)
    yield
    state.finish(client)


_STATE_KEY: pytest.StashKey[LifecycleSession] = pytest.StashKey()


def _state(config: pytest.Config) -> LifecycleSession | None:
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
    state = _state(pytestconfig) or LifecycleSession(client)
    with state.fixture(fixture, client):
        yield

def pytest_runtest_setup(item: pytest.Item) -> None:
    state = _state(item.config)
    if state and state.stop_requested:
        pytest.exit("external lifecycle failed; stopping pytest to protect database provenance")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    state = _state(session.config)
    if state and state.stop_requested and exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def _patch_flush(connection: Any, client: LifecycleClient, state: LifecycleSession) -> tuple[str, Any] | None:
    """Route Django's per-test flush hook outside the application process."""

    operations = connection.ops
    if getattr(operations, "_external_lifecycle_patched", False):
        return None

    def external_flush(*args: Any, **kwargs: Any) -> list[str]:
        return state.flush(connection, client)

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
    state = LifecycleSession(pytest_lifecycle)
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
