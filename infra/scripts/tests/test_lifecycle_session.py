"""Lifecycle sequence is exercised through the public session interface."""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from test_support.lifecycle_session import LifecycleError, LifecycleSession


def connection():
    cursor = SimpleNamespace(
        execute=lambda *args: None,
        fetchone=lambda: ("owned", 1, "complect_app_local", "complect_app_local"),
    )
    return SimpleNamespace(
        settings_dict={"NAME": "owned", "HOST": "postgres_primary", "PORT": "5432", "USER": "complect_app_local"},
        cursor=lambda: nullcontext(cursor),
    )


def test_session_and_named_fixture_follow_the_existing_request_sequence():
    calls = []

    class Client:
        def request(self, operation, **kwargs):
            calls.append((operation, kwargs))
            return {"ok": True, "identity": {"database": "owned", "database_oid": 1}}

    session = LifecycleSession(Client())
    session.prepare()
    with session.fixture("pii_legacy_text"):
        calls.append(("test-body", {}))
    session.finish()
    assert calls == [
        ("flush", {}),
        ("fixture_prepare", {"fixture": "pii_legacy_text"}),
        ("test-body", {}),
        ("fixture_cleanup", {"fixture": "pii_legacy_text"}),
        ("flush", {}),
    ]
    assert session.stop_requested is False


def test_runtime_flush_checks_target_and_keeps_failure_terminal():
    calls = []

    class Client:
        def request(self, operation, **kwargs):
            calls.append(operation)
            if len(calls) > 1:
                raise LifecycleError("host cleanup failed")
            return {"identity": {"database": "owned", "database_oid": 1}}

    session = LifecycleSession(Client())
    session.prepare()
    with pytest.raises(LifecycleError, match="host cleanup failed"):
        session.flush(connection())
    assert session.stop_requested is True
    session.finish()
    assert calls == ["flush", "flush"]


@pytest.mark.parametrize("phase", ["initial", "final"])
def test_session_flush_failure_requests_stop_without_retrying_at_finish(phase):
    calls = []
    error = LifecycleError("cleanup failed")

    class Client:
        def request(self, operation, **kwargs):
            calls.append(operation)
            if phase == "initial" or len(calls) > 1:
                raise error
            return {"identity": {"database": "owned", "database_oid": 1}}

    session = LifecycleSession(Client())
    with pytest.raises(LifecycleError) as caught:
        session.prepare()
        session.finish()
    assert caught.value is error
    assert session.stop_requested is True
    session.finish()
    assert calls == (["flush"] if phase == "initial" else ["flush", "flush"])


@pytest.mark.parametrize("phase", ["prepare", "body", "cleanup"])
def test_named_fixture_failure_poisoning_preserves_the_original_exception(phase):
    calls = []
    error = KeyboardInterrupt("fixture aborted")

    class Client:
        def request(self, operation, **kwargs):
            calls.append(operation)
            if operation == "fixture_" + phase:
                raise error
            return {"ok": True}

    session = LifecycleSession(Client())
    with pytest.raises(KeyboardInterrupt) as caught:
        with session.fixture("pii_legacy_text"):
            calls.append("body")
            if phase == "body":
                raise error
    assert caught.value is error
    assert session.stop_requested
    session.finish()
    assert calls == {
        "prepare": ["fixture_prepare"],
        "body": ["fixture_prepare", "body"],
        "cleanup": ["fixture_prepare", "body", "fixture_cleanup"],
    }[phase]


@pytest.mark.parametrize("field,value", [
    ("NAME", "foreign"), ("HOST", "postgres_replica"),
    ("PORT", "15432"), ("USER", "complect_migrator"),
])
def test_foreign_runtime_alias_is_refused_before_connection_or_host_request(field, value):
    calls = []

    class Client:
        def request(self, operation, **kwargs):
            calls.append(operation)
            return {"identity": {"database": "owned", "database_oid": 1}}

    session = LifecycleSession(Client())
    session.prepare()
    foreign = connection()
    foreign.settings_dict[field] = value
    foreign.cursor = lambda: pytest.fail("foreign alias must not be queried")
    with pytest.raises(LifecycleError, match="runtime alias target mismatch"):
        session.flush(foreign)
    assert session.stop_requested
    assert calls == ["flush"]


def test_runtime_connection_identity_is_reverified_before_host_flush():
    calls = []

    class Client:
        def request(self, operation, **kwargs):
            calls.append(operation)
            return {"identity": {"database": "owned", "database_oid": 1}}

    session = LifecycleSession(Client())
    session.prepare()
    foreign = connection()
    cursor = SimpleNamespace(execute=lambda *args: None, fetchone=lambda: ("owned", 2, "complect_app_local", "complect_app_local"))
    foreign.cursor = lambda: nullcontext(cursor)
    with pytest.raises(LifecycleError, match="runtime connection target mismatch"):
        session.flush(foreign)
    assert session.stop_requested
    assert calls == ["flush"]


def test_runtime_flush_requires_independently_verified_target():
    session = LifecycleSession(SimpleNamespace(request=lambda *args: pytest.fail("unverified flush")))
    with pytest.raises(LifecycleError, match="missing independently verified lifecycle target"):
        session.flush(connection())
    assert session.stop_requested


def test_verified_runtime_flush_returns_no_privileged_sql():
    calls = []

    class Client:
        def request(self, operation, **kwargs):
            calls.append(operation)
            return {"identity": {"database": "owned", "database_oid": 1}}

    session = LifecycleSession(Client())
    session.prepare()
    assert session.flush(connection()) == []
    assert not session.stop_requested
    assert calls == ["flush", "flush"]
