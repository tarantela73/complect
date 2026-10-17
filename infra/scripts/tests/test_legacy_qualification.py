"""Public qualification seams; no development services or Docker socket."""
import json
import os
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


def test_recovery_rejects_symlink_lock_before_cleanup(tmp_path, monkeypatch):
    from infra.scripts import run_legacy_qualification as qualification
    root, sha = source(tmp_path)
    attempt = tmp_path / 'attempt'
    freeze_source(root, sha, attempt)
    external = tmp_path / 'external-lock'
    external.write_text('preserved')
    (attempt / 'attempt.lock').symlink_to(external)
    monkeypatch.setattr(qualification, 'ROOT', attempt / 'frozen')
    with pytest.raises((ValueError, OSError)):
        qualification.recover(attempt)
    assert external.read_text() == 'preserved'
    assert not list(attempt.glob('recovery-*.json'))


def test_image_removal_preserves_existing_ids_and_references():
    from infra.scripts.run_legacy_qualification import image_removal_allowed
    baseline = {'images': [{'id': 'shared', 'references': ['old:tag']}, {'id': 'untagged', 'references': []}]}
    assert not image_removal_allowed(baseline, {'id': 'shared', 'reference': 'old:tag'})
    assert image_removal_allowed(baseline, {'id': 'shared', 'reference': 'attempt:new'})
    assert not image_removal_allowed(baseline, {'id': 'untagged', 'reference': 'attempt:new'})
    assert image_removal_allowed(baseline, {'id': 'new-image', 'reference': 'attempt:new'})


def test_recovery_blocks_when_declared_stage_journal_is_missing(tmp_path, monkeypatch):
    from infra.scripts import run_legacy_qualification as qualification
    root, sha = source(tmp_path)
    attempt = tmp_path / 'attempt'
    state = freeze_source(root, sha, attempt)
    state['stages'] = ['prepare']
    state['phase'] = 'prepare'
    qualification.write_record(attempt / 'attempt.json', state)
    monkeypatch.setattr(qualification, 'ROOT', attempt / 'frozen')
    assert qualification.recover(attempt) == 2
    result = json.loads(next(attempt.glob('recovery-*.json')).read_text())
    assert any('stage journal missing' in error for error in result['errors'])


@pytest.mark.parametrize("human", [False, True])
def test_prepare_rollback_failure_survives_successful_cleanup_retry(tmp_path, monkeypatch, capsys, human):
    import shutil
    from unittest.mock import patch
    from infra.scripts import run_legacy_qualification as qualification

    root, sha = source(tmp_path)
    attempt = tmp_path / 'attempt'
    freeze_source(root, sha, attempt)
    monkeypatch.setattr(qualification, 'ROOT', attempt / 'frozen')
    real_popen = subprocess.Popen

    class DockerProcess:
        def __init__(self, args, text):
            self.args = args
            self.text = text
            self.returncode = 99 if 'build' in args else 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def communicate(self, *_args, **_kwargs):
            args = self.args[1:]
            value = ('default' if args[:2] == ['context', 'show'] else
                     json.dumps('unix:///var/run/docker.sock') if args[:2] == ['context', 'inspect'] else
                     'proof-daemon' if args[:1] == ['info'] else '')
            return (value + '\n', '') if self.text else ((value + '\n').encode(), b'')

        def poll(self):
            return self.returncode

    def spawn(args, **kwargs):
        if args[0] == 'docker':
            return DockerProcess(args, kwargs.get('text') or kwargs.get('encoding'))
        return real_popen(args, **kwargs)

    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    for name in ('DOCKER_CONFIG', 'BUILDX_CONFIG', 'DOCKER_HOST', 'DOCKER_CONTEXT'):
        monkeypatch.delenv(name, raising=False)
    real_remove = shutil.rmtree
    calls = []

    def remove(path, *args, **kwargs):
        calls.append(str(path))
        if len(calls) == 1:
            raise OSError('first rollback failed')
        return real_remove(path, *args, **kwargs)

    with patch('subprocess.Popen', side_effect=spawn), patch('tempfile.TemporaryFile', side_effect=OSError('probe failed')), patch('shutil.rmtree', side_effect=remove):
        assert qualification.execute(attempt, **({"summary_output": tmp_path / "human-view.json"} if human else {})) == 2
    result = json.loads((attempt / 'qualification.json').read_text())
    assert result['primary_exit'] == 2
    assert result['cleanup'] == 'BLOCKED_STAND'
    assert 'host environment cleanup unconfirmed' in result['cleanup_errors']
    assert not (attempt / 'host-private').exists()
    assert 'BUILDX_CONFIG' not in os.environ
    displayed = capsys.readouterr().out
    if human:
        assert len(displayed.encode()) <= 4096
        assert len(displayed.splitlines()) <= 20
        assert json.loads(displayed.splitlines()[0])['exit'] == 2
        full = json.loads((tmp_path / 'human-view.json').read_text())
        for key, value in result.items():
            assert full[key] == value
    else:
        assert json.loads(displayed) == result


def test_status_summary_retains_full_native_view_and_exit(tmp_path, monkeypatch, capsys):
    from infra.scripts import run_legacy_qualification as qualification

    root, sha = source(tmp_path)
    attempt = tmp_path / 'attempt'
    freeze_source(root, sha, attempt)
    monkeypatch.setattr(qualification, 'ROOT', attempt / 'frozen')
    original_code = qualification.main(['status', '--directory', str(attempt)])
    native = json.loads(capsys.readouterr().out)
    output = tmp_path / 'status-view.json'
    code = qualification.main(['status', '--directory', str(attempt), '--summary-output', str(output)])
    displayed = capsys.readouterr().out
    assert code == original_code
    assert len(displayed.encode()) <= 4096
    assert len(displayed.splitlines()) <= 20
    assert json.loads(displayed.splitlines()[0])['exit'] == code
    full = json.loads(output.read_text())
    for key, value in native.items():
        assert full[key] == value
    metadata = json.loads(displayed.splitlines()[0])
    assert metadata['diagnostic_counts']['collected133'] == 'NOT_AVAILABLE'
    assert 'attempt' in metadata['diagnostic_artifacts']


def test_status_summary_bounds_recovery_census_and_preserves_exit(tmp_path, monkeypatch, capsys):
    from infra.scripts import run_legacy_qualification as qualification

    root, sha = source(tmp_path)
    attempt = tmp_path / 'attempt'
    freeze_source(root, sha, attempt)
    for index in range(1025):
        (attempt / f'recovery-{index:04d}.json').write_text('{}')
    monkeypatch.setattr(qualification, 'ROOT', attempt / 'frozen')
    code = qualification.main(['status', '--directory', str(attempt), '--summary-output', str(tmp_path / 'bounded.json')])
    displayed = capsys.readouterr().out
    assert code == 0
    metadata = json.loads(displayed.splitlines()[0])
    assert metadata['diagnostic_counts']['recovery_receipts'] == 'NOT_AVAILABLE_SCAN_LIMIT'
    assert 'recovery_directory' not in metadata['diagnostic_artifacts']


def test_native_status_counts_only_recovery_receipts(tmp_path, capsys):
    """Other attempt JSON artifacts are not recovery attempts."""
    from infra.scripts import run_legacy_qualification as qualification
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    (attempt / "attempt.json").write_text("{}")
    (attempt / "unrelated.json").write_text("{}")
    (attempt / "recovery-a.json").write_text("{}")
    output = tmp_path / "summary.json"
    code = qualification.main(["status", "--directory", str(attempt), "--summary-output", str(output)])
    assert code == 2
    metadata = json.loads(capsys.readouterr().out.splitlines()[0])
    assert metadata["exit"] == 2
    assert metadata["diagnostic_counts"]["recovery_receipts"] == 1


@pytest.mark.parametrize("unsafe", ["symlink", "oversized"])
def test_native_status_rejects_unsafe_diagnostic_manifest(tmp_path, capsys, unsafe):
    from infra.scripts import run_legacy_qualification as qualification
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    (attempt / "attempt.json").write_text("{}")
    manifest = attempt / "frozen/.dev/test-artifacts/stand/results/manifest.json"
    manifest.parent.mkdir(parents=True)
    data = json.dumps({"collected": ["unexpected"], "reports": ["unexpected"]})
    if unsafe == "symlink":
        external = tmp_path / "external.json"
        external.write_text(data)
        manifest.symlink_to(external)
    else:
        manifest.write_text(" " * (1024 * 1024) + data)
    output = tmp_path / "summary.json"
    assert qualification.main(["status", "--directory", str(attempt), "--summary-output", str(output)]) == 2
    metadata = json.loads(capsys.readouterr().out.splitlines()[0])
    assert metadata["exit"] == 2
    assert metadata["diagnostic_counts"]["collected133"] == "NOT_AVAILABLE"
    assert metadata["diagnostic_counts"]["reports399"] == "NOT_AVAILABLE"
