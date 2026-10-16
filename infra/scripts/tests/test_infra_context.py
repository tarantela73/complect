"""The shared suite must work with each supported adapter's actual cwd/mounts."""
import errno
import subprocess
from pathlib import Path


def test_offline_git_and_fixture_paths_work_from_adapter_cwd(tmp_path):
    assert Path.cwd() in {Path("/app"), Path("/workspace")}
    assert Path("/workspace/infra/scripts/infra_test_selection.py").is_file()
    subprocess.run(["git", "--version"], check=True, capture_output=True)
    subprocess.run(["git", "init", "-q", str(tmp_path / "fixture")], check=True, capture_output=True)
    assert (tmp_path / "fixture/.git/HEAD").is_file()


def test_test_source_is_read_only_and_has_no_owner_or_daemon_socket():
    assert not Path("/var/run/docker.sock").exists()
    assert not Path("/run/owner").exists()
    assert not Path("/run/complect-secrets/bootstrap_app_postgres_password").exists()
    source = Path("/workspace/infra/scripts/.must-remain-read-only")
    try:
        source.write_text("read-only probe")
    except OSError as error:
        assert error.errno in {errno.EROFS, errno.EACCES, errno.EPERM}
    else:
        source.unlink()
        raise AssertionError("adapter allowed writes to source")
