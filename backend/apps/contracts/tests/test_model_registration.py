"""Regression coverage for Contracts model registration at Django startup."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest


EXPECTED_MIGRATED_MODELS = {
    ("contracts.Contract", "contracts_contracts"),
    ("contracts.ContractAttachment", "contracts_attachments"),
}


def test_migrated_contract_models_are_registered_after_fresh_django_startup():
    """Django must know every migrated Contracts table before transactional flush."""
    probe = textwrap.dedent(
        """
        import os

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")

        import django

        django.setup()

        from django.apps import apps

        registered = {
            (model._meta.label, model._meta.db_table)
            for model in apps.get_app_config("contracts").get_models()
        }
        expected = {
            ("Contract", "contracts.Contract", "contracts_contracts"),
            ("ContractAttachment", "contracts.ContractAttachment", "contracts_attachments"),
        }
        looked_up = {
            (name, model._meta.label, model._meta.db_table)
            for name in ("Contract", "ContractAttachment")
            for model in (apps.get_model("contracts", name),)
        }
        assert {
            (label, table)
            for _, label, table in expected
        } <= registered, (registered, expected)
        assert looked_up == expected
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        env=os.environ.copy(),
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.django_db
def test_migrated_contract_tables_are_in_django_flush_input():
    """Transactional teardown must know both sides of the physical FK."""
    from django.db import connection

    flush_tables = set(connection.introspection.django_table_names(only_existing=True))

    assert {table for _, table in EXPECTED_MIGRATED_MODELS} <= flush_tables
