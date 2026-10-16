# backend/conftest.py
import os
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.db import connection

pytest_plugins = ["test_support.pytest_lifecycle"] if os.environ.get("PYTEST_LIFECYCLE_SOCKET") else []


@pytest.fixture(autouse=True)
def _pin_gateway_token(settings):
    settings.EXPECTED_GATEWAY_TOKEN = "complect_internal_secure_mesh_token_2026"


@pytest.fixture(scope="session", autouse=True)
def install_pgcrypto(django_db_blocker):
    with django_db_blocker.unblock():
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")


@pytest.fixture(autouse=True)
def _ensure_pgcrypto(request):
    """Ensure pgcrypto extension is available for each test.

    The session-scoped install_pgcrypto should cover this, but transaction=True
    tests may use connections where the extension isn't visible. This function-scoped
    fixture guarantees availability without error on every test run.

    Gated on the django_db marker: without the gate the unconditional
    connection.cursor() runs in setup of EVERY test, and pytest-django raises
    RuntimeError("Database access not allowed") for the ~267 non-DB tests
    (OpenAPI/DTO snapshots, pure units) — mass setup ERRORs across the suite.
    """
    if request.node.get_closest_marker("django_db") is None:
        return
    with connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")


@pytest.fixture(autouse=True)
def _block_outbox_live_dispatch():
    """Prevent on_commit _fire_dispatcher from queuing tasks to the live celery_worker.

    complect_celery_worker shares the same Redis broker as tests. Without this patch,
    push_to_outbox() fires apply_async on commit, the live worker grabs SELECT FOR UPDATE,
    and direct dispatcher calls in tests see 0 rows via SKIP LOCKED — intermittent failure.
    """
    from apps.common.tasks.outbox_dispatcher import run_outbox_polling_dispatcher_task

    with patch.object(run_outbox_polling_dispatcher_task, "apply_async", return_value=None):
        yield


def pytest_collection_modifyitems(items):
    for item in items:
        marker = item.get_closest_marker("django_db")
        if marker is not None:
            existing_dbs = marker.kwargs.get("databases", ["default"])
            if "replica" not in existing_dbs:
                item.add_marker(
                    pytest.mark.django_db(
                        transaction=marker.kwargs.get("transaction", False),
                        databases=[*existing_dbs, "replica"],
                    ),
                    append=False,
                )


@pytest.fixture
def ciphertext_table(request):
    """Provision only approved fixtures through the external lifecycle session."""
    @contextmanager
    def provision(name):
        if name not in {"old_person_ciphertext", "pii_legacy_text"}:
            raise ValueError("unknown ciphertext fixture")
        from test_support.pytest_lifecycle import LifecycleError, _state
        if not os.environ.get("PYTEST_LIFECYCLE_SOCKET"):
            raise LifecycleError("ciphertext fixtures require the external disposable lifecycle runner")
        client = request.getfixturevalue("pytest_lifecycle")
        state = _state(request.config)
        if state is None:
            raise LifecycleError("ciphertext fixture requires a prepared lifecycle session")
        with state.fixture(name, client):
            yield
    return provision
