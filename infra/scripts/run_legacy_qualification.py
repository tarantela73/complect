"""Qualify an explicit committed legacy133 source, or recover its cleanup only."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = 'infra/qualification-protocol.json'
REQUIRED = ('run_legacy_qualification.py', 'prepare_legacy_stand.py', 'run_legacy_regressions.py')


def write_record(path: Path, value: Any) -> None:
    """Durably replace a journal record, never following a destination symlink."""
    if path.is_symlink():
        raise ValueError('journal symlink refused')
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.pending', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def read_record(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError('missing or unsafe journal')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('malformed journal')
    return value


def git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(['git', '-C', str(root), *arguments], text=True, stderr=subprocess.PIPE).strip()


def freeze_source(root: Path, commit: str, directory: Path) -> dict[str, Any]:
    """Freeze a supported full commit without shared Git objects or dirty files."""
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('full commit SHA required')
    root = root.resolve()
    if directory.is_symlink() or directory.exists():
        raise ValueError('attempt directory must be fresh')
    directory = directory.absolute()
    if any(p.is_symlink() for p in directory.parents):
        raise ValueError('attempt path symlink refused')
    try:
        if git(root, 'rev-parse', commit + '^{commit}') != commit:
            raise ValueError('commit identity mismatch')
        if json.loads(git(root, 'show', f'{commit}:{PROTOCOL}')) != {'version': 1}:
            raise ValueError('unsupported qualification protocol')
        for name in REQUIRED:
            git(root, 'cat-file', '-e', f'{commit}:infra/scripts/{name}')
    except (subprocess.SubprocessError, ValueError) as error:
        raise ValueError('unsupported qualification commit') from error
    directory.mkdir(parents=True, mode=0o700)
    frozen = directory / 'frozen'
    subprocess.run(['git', 'clone', '--quiet', '--no-hardlinks', '--no-checkout', str(root), str(frozen)], check=True)
    git(frozen, 'checkout', '--quiet', '--detach', commit)
    if (frozen / '.git/objects/info/alternates').exists():
        raise ValueError('shared Git objects refused')
    state = {'version': 1, 'directory': str(directory), 'source_commit': commit,
             'source_tree': git(frozen, 'rev-parse', 'HEAD^{tree}'), 'phase': 'frozen'}
    write_record(directory / 'attempt.json', state)
    return state


def qualification_result(*, primary_exit: int, tests_passed: bool, cleanup_errors: list[str]) -> dict[str, Any]:
    """Preserve primary failure while cleanup uncertainty always forbids PASS."""
    passed = primary_exit == 0 and tests_passed and not cleanup_errors
    return {'version': 1, 'status': 'PASS' if passed else ('BLOCKED_STAND' if cleanup_errors or not primary_exit else 'FAIL'),
            'exit': primary_exit or (0 if passed else 2), 'primary_exit': primary_exit,
            'tests': 'PASS' if tests_passed else 'NOT_PASS',
            'cleanup': 'BLOCKED_STAND' if cleanup_errors else 'PASS', 'cleanup_errors': cleanup_errors}


def validate_attempt(directory: Path) -> dict[str, Any]:
    """Validate the attempt's source and retained snapshot before trusted handoff."""
    from infra.scripts import agent_harness

    if directory.is_symlink() or any(p.is_symlink() for p in directory.parents):
        raise ValueError('attempt path symlink refused')
    state = read_record(directory / 'attempt.json')
    if state.get('version') != 1 or state.get('directory') != str(directory):
        raise ValueError('attempt identity mismatch')
    frozen = directory / 'frozen'
    if frozen.is_symlink() or git(frozen, 'rev-parse', 'HEAD') != state['source_commit']:
        raise ValueError('frozen commit changed')
    if git(frozen, 'rev-parse', 'HEAD^{tree}') != state['source_tree'] or git(frozen, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('frozen source changed')
    if state.get('snapshot_sha256'):
        snapshot = directory / 'snapshot.json'
        if snapshot.is_symlink() or hashlib.sha256(snapshot.read_bytes()).hexdigest() != state['snapshot_sha256']:
            raise ValueError('snapshot changed')
        if agent_harness.verify_working_tree_snapshot(frozen, snapshot):
            raise ValueError('frozen snapshot mismatch')
    return state


@contextlib.contextmanager
def attempt_lock(directory: Path):
    """Lock only a regular private file owned by this user, without symlinks."""
    import fcntl

    fd = os.open(directory / 'attempt.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise ValueError('unsafe attempt lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _private_identity(path: Path) -> dict[str, int]:
    if path.is_symlink():
        raise ValueError('private path symlink refused')
    st = path.stat()
    if not path.is_dir() or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError('private directory ownership/mode mismatch')
    return {'device': st.st_dev, 'inode': st.st_ino}


def _preserve_and_remove_private(directory: Path, state: dict[str, Any]) -> None:
    import shutil

    private = directory / 'private'
    if not private.exists() and not private.is_symlink():
        return
    if _private_identity(private) != state.get('private_identity'):
        raise ValueError('private directory replaced')
    control = private / 'control'
    evidence = directory / 'recovered-runtime'
    try:
        for name in ('manifest.json', 'runtime.json', 'lifecycle.jsonl', 'target.json', 'privilege-probes.json'):
            source = control / name
            if source.is_symlink() or control.is_symlink():
                raise ValueError('runtime evidence symlink refused')
            if source.is_file():
                evidence.mkdir(exist_ok=True, mode=0o700)
                target = evidence / name
                if not target.exists():
                    shutil.copyfile(source, target)
                    target.chmod(0o600)
    finally:
        shutil.rmtree(private)



def _clean_attempt(directory: Path, state: dict[str, Any]) -> list[str]:
    """Quiesce host work and daemon containers before guarded full cleanup."""
    import shutil

    from infra.scripts import prepare_legacy_stand as stand
    from infra.scripts.qualification_processes import stop_work
    from infra.scripts.qualification_resources import prove_attempt_absent, verify_baseline

    errors: list[str] = []
    try:
        stop_work(directory)
        baseline = read_record(directory / 'baseline.json')
        baseline_errors = verify_baseline(baseline)
        if any('daemon' in e or 'verification failed' in e or 'schema invalid' in e for e in baseline_errors):
            raise ValueError('baseline/daemon identity unconfirmed')
        errors.extend(baseline_errors)
        ownership = directory / 'frozen/.dev/test-artifacts/stand/ownership.json'
        if ownership.exists():
            receipt = stand.recover_inventory(ownership)
            commands = stand.stand_commands(Path(receipt['root']), receipt)
            # Inventory includes detached lifecycle/oneshot containers left by killed clients.
            for record in receipt['resources']:
                if record['kind'] != 'container':
                    continue
                actual = stand._inspect_optional(commands, ['docker', 'inspect', record['id']])
                if actual is None:
                    continue
                stand._assert_owned('container', actual, receipt)
                if stand._fingerprint('container', actual) != record['fingerprint'] or stand._labels('container', actual) != record['labels']:
                    raise ValueError('container identity changed before stop')
                commands.run(['docker', 'stop', '--time', '5', actual['Id']], write=True, timeout=30)
            image = receipt.get('image')
            if image and any(item['id'] == image['id'] and not item['references'] for item in baseline.get('images', [])):
                # Removing the last new tag could also delete a previously untagged image.
                raise ValueError('stand image shares a previously untagged baseline identity')
            receipt = stand.cleanup(ownership)
            if receipt['cleanup'] != 'PASS':
                raise ValueError('guarded stand cleanup blocked')
            errors.extend(prove_attempt_absent(receipt))
            if errors:
                return errors
        elif state.get('phase') not in ('frozen', 'baseline', 'snapshot'):
            # A prepare intent without a receipt cannot establish resource ownership.
            raise ValueError('prepare inventory missing')
        errors.extend(verify_baseline(baseline))
    except Exception as error:
        errors.append(type(error).__name__ + ': ' + str(error))
    finally:
        # Credential deletion has its own guards and does not depend on Docker cleanup.
        try:
            _preserve_and_remove_private(directory, state)
        except Exception as error:
            errors.append('private cleanup: ' + type(error).__name__)
        secrets = directory / 'frozen/.dev/test-artifacts/stand/checkout/.dev/secrets'
        try:
            if any(p.is_symlink() for p in (secrets, *secrets.parents)):
                raise ValueError('stand secret ancestor symlink refused')
            if secrets.exists():
                _private_identity(secrets)
                shutil.rmtree(secrets)
        except Exception as error:
            errors.append('stand credentials cleanup: ' + type(error).__name__)
    return errors


class QualificationInterrupted(BaseException):
    def __init__(self, signum: int):
        self.signum = signum


def execute(directory: Path) -> int:
    """Execute the complete positive contract from this frozen source."""
    import signal

    from infra.scripts import agent_harness
    from infra.scripts.legacy_phase_contract import validate_legacy_phases
    from infra.scripts.qualification_processes import run_stage
    from infra.scripts.qualification_resources import capture_baseline

    state = validate_attempt(directory)
    if ROOT != directory / 'frozen' or state['phase'] != 'frozen':
        raise ValueError('frozen execution identity mismatch')
    with attempt_lock(directory):
        private = directory / 'private'
        private.mkdir(mode=0o700)
        state['private_identity'] = _private_identity(private)
        write_record(directory / 'attempt.json', state)
        write_record(directory / 'qualification.json', {'version': 1, 'status': 'BLOCKED_STAND',
                     'tests': 'NOT_RUN', 'cleanup': 'NOT_RUN', 'source_commit': state['source_commit']})
        primary_exit = 0
        primary_error = None
        tests_passed = False
        cleanup_errors: list[str] = []
        handlers = {}

        def interrupted(signum, _frame):
            raise QualificationInterrupted(signum)

        for signum in (signal.SIGINT, signal.SIGTERM):
            handlers[signum] = signal.signal(signum, interrupted)
        try:
            write_record(directory / 'baseline.json', capture_baseline())
            state['phase'] = 'baseline'
            write_record(directory / 'attempt.json', state)
            snapshot = directory / 'snapshot.json'
            write_record(snapshot, agent_harness.working_tree_snapshot(ROOT))
            state['snapshot_sha256'] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            state['phase'] = 'snapshot'
            write_record(directory / 'attempt.json', state)
            # An exact committed source diff is retained independently of mutable callers.
            patch = directory / 'review.patch'
            patch.write_text(git(ROOT, 'show', '--format=fuller', '--binary', state['source_commit']) + '\n')
            stand_directory = ROOT / '.dev/test-artifacts/stand'
            state['phase'] = 'prepare'
            write_record(directory / 'attempt.json', state)
            primary_exit = run_stage(directory, 'prepare', [sys.executable, '-B', '-m', 'infra.scripts.prepare_legacy_stand',
                'prepare', '--source-root', str(ROOT), '--snapshot', str(snapshot), '--directory', str(stand_directory)], ROOT)
            if primary_exit:
                raise RuntimeError('prepare failed; see processes/prepare.log')
            receipt = read_record(stand_directory / 'ownership.json')
            state['phase'] = 'test'
            write_record(directory / 'attempt.json', state)
            primary_exit = run_stage(directory, 'test', [sys.executable, '-B', '-m', 'infra.scripts.run_legacy_regressions',
                '--root', str(stand_directory / 'checkout'), '--ownership', str(stand_directory / 'ownership.json'),
                '--manifest', str(stand_directory / 'manifest.json'), '--output', str(stand_directory / 'results'),
                '--run-id', receipt['run_id'], '--snapshot', str(snapshot), '--review-patch', str(patch),
                '--source-root', str(ROOT), '--runtime-directory', str(private)], ROOT)
            if primary_exit:
                raise RuntimeError('original133 failed; see processes/test.log and results')
            result = read_record(stand_directory / 'results/result.json')
            phase_errors = validate_legacy_phases(read_record(stand_directory / 'results/manifest.json'))
            if result.get('status') != 'PASS' or result.get('exit') != 0 or phase_errors:
                raise ValueError('original133 receipt/399 phase grid invalid')
            if agent_harness.verify_working_tree_snapshot(ROOT, snapshot):
                raise ValueError('post-run frozen snapshot mismatch')
            tests_passed = True
        except QualificationInterrupted as error:
            primary_exit = 128 + error.signum
            primary_error = signal.Signals(error.signum).name
        except BaseException as error:
            primary_exit = primary_exit or (int(error.code) if isinstance(error, SystemExit) and isinstance(error.code, int) else 2)
            primary_error = type(error).__name__ + ': ' + str(error)
        finally:
            # A second signal must not interrupt the bounded cleanup transaction.
            for signum in handlers:
                signal.signal(signum, signal.SIG_IGN)
            state['primary_exit'] = primary_exit
            state['primary_error'] = primary_error
            write_record(directory / 'attempt.json', state)
            cleanup_errors = _clean_attempt(directory, state)
            result = qualification_result(primary_exit=primary_exit, tests_passed=tests_passed, cleanup_errors=cleanup_errors)
            result.update(source_commit=state['source_commit'], source_tree=state['source_tree'], primary_error=primary_error)
            write_record(directory / 'qualification.json', result)
            state['phase'] = 'finished'
            write_record(directory / 'attempt.json', state)
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
        print(json.dumps(result), flush=True)
        return int(result['exit'])


def recover(directory: Path) -> int:
    """Cleanup only, with an append-only recovery result and unchanged run verdict."""
    import uuid

    state = validate_attempt(directory)
    if ROOT != directory / 'frozen':
        raise ValueError('frozen recovery identity mismatch')
    with attempt_lock(directory):
        errors = _clean_attempt(directory, state)
        result = {'version': 1, 'operation': 'cleanup-only', 'status': 'BLOCKED_STAND' if errors else 'PASS',
                  'errors': errors, 'source_commit': state['source_commit'], 'original_verdict_unchanged': True}
        write_record(directory / ('recovery-' + uuid.uuid4().hex + '.json'), result)
        print(json.dumps(result), flush=True)
        return 2 if errors else 0


def handoff(directory: Path, operation: str) -> None:
    validate_attempt(directory)
    frozen = directory / 'frozen'
    os.chdir(frozen)
    environment = dict(os.environ, PYTHONPATH=str(frozen), PYTHONDONTWRITEBYTECODE='1', GIT_OPTIONAL_LOCKS='0')
    os.execve(sys.executable, [sys.executable, '-B', '-m', 'infra.scripts.run_legacy_qualification',
                             operation, '--directory', str(directory), '--frozen'], environment)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    run_parser = commands.add_parser('run')
    run_parser.add_argument('--source-commit')
    run_parser.add_argument('--directory', type=Path, required=True)
    run_parser.add_argument('--frozen', action='store_true', help=argparse.SUPPRESS)
    recovery = commands.add_parser('recover')
    recovery.add_argument('--directory', type=Path, required=True)
    recovery.add_argument('--frozen', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    directory = args.directory.absolute()
    try:
        if args.operation == 'run':
            if not args.frozen:
                freeze_source(ROOT, args.source_commit or '', directory)
                handoff(directory, 'run')
            return execute(directory)
        if not args.frozen:
            handoff(directory, 'recover')
        return recover(directory)
    except Exception as error:
        print(json.dumps({'status': 'BLOCKED_STAND', 'error': type(error).__name__, 'reason': str(error)}), flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())
