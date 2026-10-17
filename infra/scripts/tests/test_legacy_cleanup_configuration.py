import shutil
from pathlib import Path

import pytest

from infra.scripts.dev_environment import Commands, DevError, configure


def test_cleanup_configuration_allows_missing_credentials_but_keeps_identity_checks(tmp_path):
    shutil.copy(Path(__file__).resolve().parents[3] / ".env.dev.example", tmp_path / ".env.dev.example")
    configure(tmp_path)
    shutil.rmtree(tmp_path / ".dev/secrets")

    commands = Commands(tmp_path, require_credentials=False)

    assert commands.values["COMPOSE_PROJECT_NAME"] == commands.project
    assert commands.values["DEV_CHECKOUT_ID"]


def test_default_configuration_still_requires_credentials(tmp_path):
    shutil.copy(Path(__file__).resolve().parents[3] / ".env.dev.example", tmp_path / ".env.dev.example")
    configure(tmp_path)
    shutil.rmtree(tmp_path / ".dev/secrets")

    with pytest.raises((DevError, FileNotFoundError)):
        Commands(tmp_path)


def test_cleanup_configuration_rejects_symlink_private_state(tmp_path):
    shutil.copy(Path(__file__).resolve().parents[3] / '.env.dev.example', tmp_path / '.env.dev.example')
    configure(tmp_path)
    shutil.rmtree(tmp_path / '.dev/secrets')
    (tmp_path / '.dev/secrets').symlink_to(tmp_path / 'missing')
    with pytest.raises((DevError, FileNotFoundError)):
        Commands(tmp_path, require_credentials=False)
