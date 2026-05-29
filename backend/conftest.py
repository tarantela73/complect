# backend/conftest.py
import pytest


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
