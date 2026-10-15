from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import pytest

from test_support.lifecycle_session import LifecycleError, LifecycleRequest, LifecycleSession
import test_support.pytest_lifecycle as pytest_lifecycle_module
from test_support.pytest_lifecycle import LifecycleClient


TARGET = {"database": "owned", "database_oid": 1}


class RecordingClient:
    def __init__(self, responses: list[dict[str, object]] | None = None) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.responses = list(responses or [{"ok": True, "identity": TARGET}])

    def request(self, operation: str, *, fixture: str | None = None) -> dict[str, object]:
        self.calls.append((operation, fixture))
        response = self.responses.pop(0) if self.responses else {"ok": True}
        if response.get("error"):
            raise LifecycleError(str(response["error"]))
        return response


def _fake_connection(ops: object, *, database: str = "owned") -> SimpleNamespace:
    cursor = SimpleNamespace(
        execute=lambda *args: None,
        fetchone=lambda: (database, 1, "complect_app_local", "complect_app_local"),
    )
    return SimpleNamespace(
        ops=ops,
        settings_dict={
            "NAME": database,
            "HOST": "postgres_primary",
            "PORT": "5432",
            "USER": "complect_app_local",
        },
        cursor=lambda: nullcontext(cursor),
    )


def _server(tmp_path: Path, response: dict[str, object] | list[dict[str, object]]):
    path = tmp_path / "lifecycle.sock"
    received: list[dict[str, object]] = []
    ready = threading.Event()

    def serve() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            listener.listen(1)
            ready.set()
            responses = response if isinstance(response, list) else [response]
            for item in responses:
                with listener.accept()[0] as channel:
                    received.append(json.loads(channel.recv(4096)))
                    channel.sendall((json.dumps(item) + "\n").encode())

    thread = threading.Thread(target=serve)
    thread.daemon = True
    thread.start()
    assert ready.wait(timeout=2)
    return path, received, thread


def test_protocol_has_only_fixed_operations() -> None:
    assert LifecycleRequest("flush", run_id="run-1").as_payload() == {
        "op": "flush", "run_id": "run-1"
    }
    with pytest.raises(LifecycleError):
        LifecycleRequest("sql", fixture="select 1").as_payload()
    with pytest.raises(LifecycleError):
        LifecycleRequest("fixture_prepare", fixture="bad name").as_payload()


def test_client_sends_run_scoped_fixture_request(tmp_path: Path) -> None:
    path, received, thread = _server(tmp_path, {"ok": True})
    LifecycleClient(path, run_id="run-1").request("fixture_prepare", fixture="files")
    thread.join(timeout=2)
    assert received == [{"op": "fixture_prepare", "fixture": "files", "run_id": "run-1"}]


def test_client_rejects_failed_host_response(tmp_path: Path) -> None:
    path, _, thread = _server(tmp_path, {"ok": False, "error": "target identity mismatch"})
    with pytest.raises(LifecycleError, match="target identity mismatch"):
        LifecycleClient(path).request("flush")
    thread.join(timeout=2)


def test_client_requires_absolute_socket() -> None:
    with pytest.raises(LifecycleError, match="absolute"):
        LifecycleClient("relative.sock")


def test_session_prepare_failure_poisons_and_finish_skips() -> None:
    client = RecordingClient([{"error": "host cleanup failed"}])
    session = LifecycleSession(client)  # type: ignore[arg-type]
    with pytest.raises(LifecycleError, match="host cleanup failed"):
        session.prepare()
    session.finish()
    assert session.stop_requested
    assert client.calls == [("flush", None)]


def test_session_fixture_cleanup_failure_poisons_and_preserves_error() -> None:
    client = RecordingClient([
        {"ok": True, "identity": TARGET}, {"ok": True}, {"error": "cleanup failed"}
    ])
    session = LifecycleSession(client)  # type: ignore[arg-type]
    session.prepare()
    with pytest.raises(LifecycleError, match="cleanup failed"):
        with session.fixture("files"):
            pass
    assert session.stop_requested
    assert client.calls == [
        ("flush", None), ("fixture_prepare", "files"), ("fixture_cleanup", "files")
    ]


def test_session_flush_rejects_foreign_connection_without_host_request() -> None:
    client = RecordingClient([{"ok": True, "identity": TARGET}])
    session = LifecycleSession(client)  # type: ignore[arg-type]
    session.prepare()
    connection = _fake_connection(SimpleNamespace(execute_sql_flush=lambda sql: []), database="foreign")
    with pytest.raises(LifecycleError, match="target mismatch"):
        session.flush(connection)
    assert session.stop_requested
    assert client.calls == [("flush", None)]


def test_session_flush_routes_reset_and_returns_no_sql() -> None:
    client = RecordingClient([{"ok": True, "identity": TARGET}, {"ok": True}])
    session = LifecycleSession(client)  # type: ignore[arg-type]
    session.prepare()
    assert session.flush(_fake_connection(SimpleNamespace(execute_sql_flush=lambda sql: ["local"]))) == []
    assert client.calls == [("flush", None), ("flush", None)]


def test_plugin_registration_has_no_unknown_pytest_hooks() -> None:
    manager = pytest.PytestPluginManager()
    manager.register(pytest_lifecycle_module)
    manager.check_pending()


def test_cleanup_error_is_reported_and_next_test_never_runs(tmp_path: Path) -> None:
    path, _, thread = _server(tmp_path, [
        {"ok": True, "identity": TARGET},
        {"ok": True},
        {"ok": False, "error": "cleanup failed"},
    ])
    (tmp_path / "conftest.py").write_text(
        "import pytest\n"
        "pytest_plugins = ['test_support.pytest_lifecycle']\n"
        "@pytest.fixture(scope='session')\n"
        "def django_db_blocker():\n"
        "    class Blocker:\n"
        "        def unblock(self):\n"
        "            return __import__('contextlib').nullcontext()\n"
        "    return Blocker()\n"
        "@pytest.fixture(autouse=True)\n"
        "def external_cleanup(lifecycle_fixture):\n"
        "    yield\n",
        encoding="utf-8",
    )
    (tmp_path / "test_behavior.py").write_text(
        "import pytest\nfrom pathlib import Path\n"
        "@pytest.mark.lifecycle_fixture('files')\n"
        "def test_first():\n    Path('first-called').write_text('yes')\n"
        "def test_second():\n    Path('second-called').write_text('should never run')\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["PYTEST_LIFECYCLE_SOCKET"] = str(path)
    environment["DJANGO_SETTINGS_MODULE"] = "config.settings.local"
    environment.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-vv", "-o", "cache_dir=/tmp/stop-after-cleanup-cache", "test_behavior.py"],
        cwd=tmp_path, env=environment, text=True, capture_output=True, timeout=30
    )
    thread.join(timeout=2)
    assert result.returncode != 0
    assert (tmp_path / "first-called").exists(), result.stdout + result.stderr
    assert not (tmp_path / "second-called").exists()
    assert "test_second" in result.stdout


def test_public_fixture_and_connection_hook_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    from django.db import connections
    from django.db.backends.signals import connection_created
    from test_support.pytest_lifecycle import install_external_flush

    class Ops:
        def execute_sql_flush(self, sql: object) -> list[str]:
            return ["runtime-original"]

    initial = _fake_connection(Ops())
    later = _fake_connection(Ops())
    monkeypatch.setattr(connections, "all", lambda: [initial])
    client = RecordingClient([{"ok": True, "identity": TARGET}])
    config = SimpleNamespace(stash=pytest.Stash())
    blocker = SimpleNamespace(unblock=lambda: nullcontext())
    runner = install_external_flush.__wrapped__(client, blocker, config)
    next(runner)
    try:
        connection_created.send(sender=type(later), connection=later)
        with pytest.raises(LifecycleError, match="missing independently verified"):
            initial.ops.execute_sql_flush([])
        with pytest.raises(LifecycleError, match="missing independently verified"):
            later.ops.execute_sql_flush([])
    finally:
        runner.close()
    assert initial.ops.execute_sql_flush([]) == ["runtime-original"]
    assert later.ops.execute_sql_flush([]) == ["runtime-original"]
