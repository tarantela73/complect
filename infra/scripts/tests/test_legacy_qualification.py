"""Public qualification seams; no development services or Docker socket."""
import json
import subprocess

import pytest

from infra.scripts.run_legacy_qualification import freeze_source


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def source(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.email', 'test@example.invalid')
    git(root, 'config', 'user.name', 'Test')
    (root / 'infra/scripts').mkdir(parents=True)
    for name in ('run_legacy_qualification.py', 'prepare_legacy_stand.py', 'run_legacy_regressions.py'):
        (root / 'infra/scripts' / name).write_text('version = "chosen"\n')
    (root / 'infra/qualification-protocol.json').write_text('{"version": 1}\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'chosen source')
    return root, git(root, 'rev-parse', 'HEAD')


def test_freeze_is_independent_of_mutable_source_and_shared_git_objects(tmp_path):
    root, sha = source(tmp_path)
    attempt = tmp_path / 'attempt'
    state = freeze_source(root, sha, attempt)
    frozen = attempt / 'frozen'
    (root / 'infra/scripts/run_legacy_qualification.py').write_text('dirty = True\n')
    assert git(frozen, 'rev-parse', 'HEAD') == sha
    assert (frozen / 'infra/scripts/run_legacy_qualification.py').read_text() == 'version = "chosen"\n'
    assert not (frozen / '.git/objects/info/alternates').exists()
    root.rename(tmp_path / 'source-moved')
    git(frozen, 'fsck', '--no-reflogs')
    assert state['source_commit'] == sha
    assert json.loads((attempt / 'attempt.json').read_text())['source_commit'] == sha
    assert not (attempt / 'qualification.json').exists()
    with pytest.raises(ValueError, match='fresh'):
        freeze_source(tmp_path / 'source-moved', sha, attempt)


def test_unsupported_commit_rejected_without_creating_attempt(tmp_path):
    root, _ = source(tmp_path)
    (root / 'infra/qualification-protocol.json').unlink()
    git(root, 'add', '-u')
    git(root, 'commit', '-qm', 'unsupported')
    destination = tmp_path / 'attempt'
    with pytest.raises(ValueError, match='unsupported'):
        freeze_source(root, git(root, 'rev-parse', 'HEAD'), destination)
    assert not destination.exists()


def test_primary_exit_survives_blocked_cleanup():
    from infra.scripts.run_legacy_qualification import qualification_result
    result = qualification_result(primary_exit=1, tests_passed=False, cleanup_errors=['ownership unknown'])
    assert result['status'] == 'BLOCKED_STAND'
    assert result['exit'] == 1
    assert result['primary_exit'] == 1
    result = qualification_result(primary_exit=0, tests_passed=True, cleanup_errors=['residual'])
    assert result['exit'] == 2
    assert result['status'] == 'BLOCKED_STAND'
    assert qualification_result(primary_exit=0, tests_passed=True, cleanup_errors=[])['status'] == 'PASS'


def test_recovery_removes_private_credentials_even_when_docker_proof_is_blocked(tmp_path, monkeypatch):
    from infra.scripts import run_legacy_qualification as qualification
    root, sha = source(tmp_path)
    attempt = tmp_path / 'attempt'
    state = freeze_source(root, sha, attempt)
    private = attempt / 'private'
    private.mkdir(mode=0o700)
    (private / 'owner-secrets').mkdir(mode=0o700)
    (private / 'owner-secrets/db_password').write_text('synthetic-secret')
    st = private.stat()
    state['private_identity'] = {'device': st.st_dev, 'inode': st.st_ino}
    qualification.write_record(attempt / 'attempt.json', state)
    qualification.write_record(attempt / 'baseline.json', {})
    qualification.write_record(attempt / 'qualification.json', {'status': 'BLOCKED_STAND'})
    monkeypatch.setattr(qualification, 'ROOT', attempt / 'frozen')
    assert qualification.recover(attempt) == 2
    assert not private.exists()
    assert json.loads((attempt / 'qualification.json').read_text()) == {'status': 'BLOCKED_STAND'}
