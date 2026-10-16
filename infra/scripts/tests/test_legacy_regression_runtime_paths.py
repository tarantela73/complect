from pathlib import Path

import pytest

from infra.scripts.run_legacy_regressions import create_runtime_paths


def test_custom_runtime_paths_are_private_fixed_children(tmp_path):
    runtime = tmp_path / "attempt-private"
    runtime.mkdir(mode=0o700)

    control, owner_secrets = create_runtime_paths(runtime)

    assert control == runtime / "control"
    assert owner_secrets == runtime / "owner-secrets"
    assert control.is_dir()
    assert owner_secrets.is_dir()
    assert control.stat().st_mode & 0o777 == 0o700
    assert owner_secrets.stat().st_mode & 0o777 == 0o700

    with pytest.raises(FileExistsError):
        create_runtime_paths(runtime)


def test_custom_runtime_paths_remove_partial_child_when_owner_creation_fails(tmp_path, monkeypatch):
    runtime = tmp_path / "attempt-private"
    runtime.mkdir(mode=0o700)
    original_mkdir = Path.mkdir

    def fail_owner_creation(self, *args, **kwargs):
        if self == runtime / "owner-secrets":
            raise OSError("owner mkdir failed")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_owner_creation)

    with pytest.raises(OSError, match="owner mkdir failed"):
        create_runtime_paths(runtime)

    assert not (runtime / "control").exists()
    assert not (runtime / "owner-secrets").exists()


def test_default_runtime_paths_remain_distinct_private_temp_directories(monkeypatch):
    paths = []
    original_mkdtemp = __import__("tempfile").mkdtemp

    def record_mkdtemp(*args, **kwargs):
        path = original_mkdtemp(*args, **kwargs)
        paths.append(Path(path))
        return path

    monkeypatch.setattr("infra.scripts.run_legacy_regressions.tempfile.mkdtemp", record_mkdtemp)

    control, owner_secrets = create_runtime_paths(None)
    try:
        assert (control, owner_secrets) == tuple(paths)
        assert control != owner_secrets
        assert control.is_dir() and owner_secrets.is_dir()
        assert control.stat().st_mode & 0o777 == 0o700
        assert owner_secrets.stat().st_mode & 0o777 == 0o700
    finally:
        control.rmdir()
        owner_secrets.rmdir()
