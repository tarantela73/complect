# backend/conftest.py
from unittest.mock import patch

import pytest
from django.db import connection


@pytest.fixture(scope="session", autouse=True)
def install_pgcrypto(django_db_blocker):
    with django_db_blocker.unblock():
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
