from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from test_support.pytest_lifecycle import (
    LifecycleClient,
    LifecycleError,
    LifecycleRequest,
)


TARGET = {"database": "owned", "database_oid": 1}


def _fake_connection(ops):
    from contextlib import nullcontext
    from types import SimpleNamespace

    cursor = SimpleNamespace(
        execute=lambda *args: None,
        fetchone=lambda: ("owned", 1, "complect_app_local", "complect_app_local"),
    )
    return SimpleNamespace(
        ops=ops,
        settings_dict={
            "NAME": "owned",
            "HOST": "postgres_primary",
            "PORT": "5432",
            "USER": "complect_app_local",
        },
        cursor=lambda: nullcontext(cursor),
    )


def _server(
    tmp_path: Path, response: dict[str, object]
) -> tuple[Path, list[dict[str, object]], threading.Thread]:
    path = tmp_path / "lifecycle.sock"
    received: list[dict[str, object]] = []
    ready = threading.Event()

    def serve() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            listener.listen(1)
            ready.set()
            with listener.accept()[0] as channel:
                received.append(json.loads(channel.recv(4096)))
                channel.sendall((json.dumps(response) + "\n").encode())

    thread = threading.Thread(target=serve)
    thread.start()
    assert ready.wait(timeout=2)
    return path, received, thread


def test_protocol_has_only_fixed_operations() -> None:
    assert LifecycleRequest("flush", run_id="run-1").as_payload() == {
        "op": "flush",
        "run_id": "run-1",
    }
    with pytest.raises(LifecycleError):
        LifecycleRequest("sql", fixture="select 1").as_payload()
    with pytest.raises(LifecycleError):
        LifecycleRequest("fixture_prepare", fixture="bad name").as_payload()


def test_client_sends_run_scoped_fixture_request(tmp_path: Path) -> None:
    path, received, thread = _server(tmp_path, {"ok": True})
    client = LifecycleClient(path, run_id="run-1")
    assert client.request("fixture_prepare", fixture="person_profile") == {"ok": True}
    thread.join(timeout=2)
    assert received == [
        {"op": "fixture_prepare", "fixture": "person_profile", "run_id": "run-1"}
    ]


def test_client_rejects_failed_host_response(tmp_path: Path) -> None:
    path, _, thread = _server(
        tmp_path, {"ok": False, "error": "target identity mismatch"}
    )
    with pytest.raises(LifecycleError, match="target identity mismatch"):
        LifecycleClient(path).request("flush")
    thread.join(timeout=2)


def test_client_requires_absolute_socket() -> None:
    with pytest.raises(LifecycleError, match="absolute"):
        LifecycleClient("relative.sock")


def test_flush_failure_marks_state_and_does_not_return_sql() -> None:
    from test_support.pytest_lifecycle import _LifecycleState, _patch_flush

    class Ops:
        def sql_flush(self, *_args: object, **_kwargs: object) -> list[str]:
            return ["sql-flush-original"]

        def execute_sql_flush(self, *_args: object, **_kwargs: object) -> list[str]:
            return ["TRUNCATE"]

    class FailingClient:
        def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise LifecycleError("host cleanup failed")

    state = _LifecycleState(FailingClient())
    state.target = TARGET
    ops = Ops()
    original = _patch_flush(_fake_connection(ops), state.client, state)
    assert original is not None
    assert ops.sql_flush([]) == ["sql-flush-original"]
    with pytest.raises(LifecycleError, match="host cleanup failed"):
        ops.execute_sql_flush([])
    assert state.failed is True
    assert original[1]([]) == ["TRUNCATE"]


def test_flush_hook_is_restored_after_session() -> None:
    from test_support.pytest_lifecycle import _LifecycleState, _patch_flush

    class Ops:
        def execute_sql_flush(self, *_args: object, **_kwargs: object) -> list[str]:
            return ["original"]

    class Client:
        def request(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            return {"ok": True}

    ops = Ops()
    connection = _fake_connection(ops)
    state = _LifecycleState(Client())
    state.target = TARGET
    original = _patch_flush(connection, state.client, state)
    assert original is not None
    ops.execute_sql_flush([])
    assert ops.execute_sql_flush([]) == []
    setattr(ops, original[0], original[1])
    ops._external_lifecycle_patched = False
    assert ops.execute_sql_flush([]) == ["original"]


def test_failed_state_stops_next_test_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_support.pytest_lifecycle import (
        _LifecycleState,
        _STATE_KEY,
        pytest_runtest_setup,
    )

    class Config:
        def __init__(self) -> None:
            self.stash = pytest.Stash()

    class Item:
        config = Config()

    state = _LifecycleState(object())
    state.failed = True
    Item.config.stash[_STATE_KEY] = state
    stopped: list[str] = []
    monkeypatch.setattr(pytest, "exit", lambda message: stopped.append(message))
    pytest_runtest_setup(Item())
    assert stopped == [
        "external lifecycle failed; stopping pytest to protect database provenance"
    ]


def test_plugin_registration_has_no_unknown_pytest_hooks():
    from test_support import pytest_lifecycle

    manager = pytest.PytestPluginManager()
    manager.register(pytest_lifecycle)
    manager.check_pending()


def test_cleanup_error_is_reported_and_next_test_never_runs(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    path, _, thread = _server(tmp_path, {"ok": False, "error": "cleanup failed"})
    (tmp_path / "conftest.py").write_text(
        """import pytest
from test_support.pytest_lifecycle import LifecycleClient, _LifecycleState, _STATE_KEY, _patch_flush
from infra.scripts.tests.test_pytest_lifecycle import TARGET, _fake_connection
pytest_plugins = ["test_support.pytest_lifecycle"]
@pytest.fixture(autouse=True)
def external_cleanup(request):
 class Ops:
  def execute_sql_flush(self, sql):
   raise AssertionError("runtime must not execute privileged flush")
 ops=Ops()
 state=_LifecycleState(LifecycleClient("SOCKET"))
 state.target=TARGET
 request.config.stash[_STATE_KEY]=state
 _patch_flush(_fake_connection(ops),state.client,state)
 yield
 ops.execute_sql_flush(["TRUNCATE"])
""".replace("SOCKET", str(path))
    )
    (tmp_path / "test_behavior.py").write_text("""from pathlib import Path
def test_first():
 Path("first-called").write_text("yes")
def test_second():
 Path("second-called").write_text("should never run")
""")
    environment = dict(os.environ)
    environment.pop("PYTEST_LIFECYCLE_SOCKET", None)
    environment.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-o",
            "cache_dir=/tmp/stop-after-cleanup-cache",
            "test_behavior.py",
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    thread.join(timeout=2)
    assert result.returncode != 0
    assert (tmp_path / "first-called").exists()
    assert not (tmp_path / "second-called").exists()
    assert "cleanup failed" in result.stdout
    assert "stopping pytest" in result.stdout


def test_new_alias_and_thread_wrapper_use_external_flush_and_restore(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from django.db import connections
    from django.db.backends.signals import connection_created
    from test_support.pytest_lifecycle import install_external_flush, _STATE_KEY

    calls = []

    class Client:
        def request(self, op):
            calls.append(op)
            return {"ok": True}

    class Ops:
        def execute_sql_flush(self, sql):
            return ["runtime-original"]

    initial = _fake_connection(Ops())
    monkeypatch.setattr(connections, "all", lambda: [initial])
    config = SimpleNamespace(stash=pytest.Stash())
    blocker = SimpleNamespace(unblock=lambda: nullcontext())
    installed = install_external_flush.__wrapped__(Client(), blocker, config)
    next(installed)
    state = config.stash[_STATE_KEY]
    state.target = TARGET
    later = _fake_connection(Ops())
    values = []

    def in_other_thread():
        connection_created.send(sender=type(later), connection=later)
        values.append(later.ops.execute_sql_flush(["TRUNCATE"]))

    worker = threading.Thread(target=in_other_thread)
    worker.start()
    worker.join(timeout=2)
    assert values == [[]]
    assert calls == ["flush"]
    with pytest.raises(StopIteration):
        next(installed)
    assert initial.ops.execute_sql_flush([]) == ["runtime-original"]
    assert later.ops.execute_sql_flush([]) == ["runtime-original"]


def test_foreign_runtime_alias_is_rejected_without_lifecycle_call():
    from test_support.pytest_lifecycle import _LifecycleState, _patch_flush
    from unittest.mock import Mock

    class Ops:
        def execute_sql_flush(self, sql):
            raise AssertionError("runtime must not flush")

    client = Mock()
    state = _LifecycleState(client)
    state.target = TARGET
    foreign = _fake_connection(Ops())
    foreign.settings_dict["NAME"] = "foreign"
    _patch_flush(foreign, client, state)
    with pytest.raises(LifecycleError, match="target mismatch"):
        foreign.ops.execute_sql_flush([])
    assert state.failed
    client.request.assert_not_called()
