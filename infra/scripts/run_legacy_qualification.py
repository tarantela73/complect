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
from typing import Any, Sequence

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
    from infra.scripts.attempt_identity import record_artifact
    record_artifact(path)


def read_record(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError('missing or unsafe journal')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('malformed journal')
    return value


def git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(['git', '-C', str(root), *arguments], text=True, stderr=subprocess.PIPE).strip()


def freeze_source(root: Path, commit: str, directory: Path, *, preparation_log: Path | None = None, redact_env: Sequence[str] = ()) -> dict[str, Any]:
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
    command = ['git', 'clone', '--quiet', '--no-hardlinks', '--no-checkout', str(root), str(frozen)]
    if preparation_log is None:
        subprocess.run(command, check=True)
    else:
        from infra.scripts.diagnostic_output import present, secret_values
        completed = subprocess.run(command, capture_output=True, check=False)
        present((completed.stdout + completed.stderr).decode('utf-8'), preparation_log,
                header={'stage': 'freeze', 'exit': completed.returncode}, secrets=secret_values(redact_env))
        completed.check_returncode()
    git(frozen, 'checkout', '--quiet', '--detach', commit)
    if (frozen / '.git/objects/info/alternates').exists():
        raise ValueError('shared Git objects refused')
    state = {'version': 1, 'directory': str(directory), 'source_commit': commit,
             'source_tree': git(frozen, 'rev-parse', 'HEAD^{tree}'), 'phase': 'frozen', 'stages': []}
    write_record(directory / 'attempt.json', state)
    from infra.scripts.attempt_identity import create_identity
    create_identity(directory, state)
    return state


def qualification_result(*, primary_exit: int, tests_passed: bool, cleanup_errors: list[str]) -> dict[str, Any]:
    """Preserve primary failure while cleanup uncertainty always forbids PASS."""
    passed = primary_exit == 0 and tests_passed and not cleanup_errors
    return {'version': 1, 'status': 'PASS' if passed else ('BLOCKED_STAND' if cleanup_errors or not primary_exit else 'FAIL'),
            'exit': primary_exit or (0 if passed else 2), 'primary_exit': primary_exit,
            'tests': 'PASS' if tests_passed else 'NOT_PASS',
            'cleanup': 'BLOCKED_STAND' if cleanup_errors else 'PASS', 'cleanup_errors': cleanup_errors}


def validate_attempt(directory: Path, *, verify_identity: bool = True) -> dict[str, Any]:
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
    from infra.scripts.attempt_identity import validate_identity
    if verify_identity:
        validate_identity(directory, state)
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



def image_removal_allowed(baseline: dict[str, Any], image: dict[str, Any]) -> bool:
    """Admit removal only of a new tag that cannot delete a baseline image."""
    return not any(item['id'] == image['id'] and
                   (not item['references'] or image['reference'] in item['references'])
                   for item in baseline['images'])


def _clean_attempt(directory: Path, state: dict[str, Any]) -> list[str]:
    """Quiesce host work and daemon containers before guarded full cleanup."""
    import shutil

    from infra.scripts import prepare_legacy_stand as stand
    from infra.scripts.qualification_processes import stop_work
    from infra.scripts.qualification_resources import prove_attempt_absent, verify_baseline

    errors: list[str] = []
    try:
        stages = state.get('stages')
        if not isinstance(stages, list) or any(name not in ('prepare', 'test') for name in stages):
            raise ValueError('stage journal list invalid')
        for name in stages:
            if not (directory / 'processes' / (name + '.json')).is_file() or not (directory / 'processes' / (name + '.intent.json')).is_file():
                raise ValueError('stage journal missing: ' + name)
        stop_work(directory)
        baseline = read_record(directory / 'baseline.json')
        baseline_errors = verify_baseline(baseline)
        if any('daemon' in e or 'verification failed' in e or 'schema invalid' in e for e in baseline_errors):
            raise ValueError('baseline/daemon identity unconfirmed')
        errors.extend(baseline_errors)
        ownership = directory / 'frozen/.dev/test-artifacts/stand/ownership.json'
        if ownership.exists():
            receipt = stand.recover_inventory(ownership)
            commands = stand.stand_commands(Path(receipt['root']), receipt, require_credentials=False)
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
            if image and not image_removal_allowed(baseline, image):
                # Removing the last new tag could also delete a previously untagged image.
                raise ValueError('stand image removal would touch baseline identity/reference')
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
    if state.get('host_environment') is not None:
        from infra.scripts.docker_host_environment import cleanup_host_environment
        cleanup_errors = cleanup_host_environment(directory / 'host-private', state['host_environment'])
        errors.extend(cleanup_errors)
    elif (directory / 'host-private').exists() or (directory / 'host-private').is_symlink():
        errors.append('host environment ownership missing')
    return errors


class QualificationInterrupted(BaseException):
    def __init__(self, signum: int):
        self.signum = signum


def execute(directory: Path, *, summary_output: Path | None = None, redact_env: Sequence[str] = ()) -> int:
    """Execute the complete positive contract from this frozen source."""
    import signal

    from infra.scripts import agent_harness
    from infra.scripts.legacy_phase_contract import validate_legacy_phases
    from infra.scripts.qualification_processes import run_stage
    from infra.scripts.qualification_resources import capture_baseline
    from infra.scripts.docker_host_environment import HostPreparationError, prepare_host_environment

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
        host_scope = contextlib.ExitStack()

        def interrupted(signum, _frame):
            raise QualificationInterrupted(signum)

        for signum in (signal.SIGINT, signal.SIGTERM):
            handlers[signum] = signal.signal(signum, interrupted)
        try:
            write_record(directory / 'baseline.json', capture_baseline())
            state['phase'] = 'baseline'
            write_record(directory / 'attempt.json', state)
            try:
                host = prepare_host_environment(directory / 'host-private')
            except HostPreparationError as error:
                cleanup_errors.extend(error.cleanup_errors)
                state['host_environment_prepare_cleanup_errors'] = list(error.cleanup_errors)
                state['host_environment'] = error.record
                write_record(directory / 'attempt.json', state)
                raise
            state['host_environment'] = host.record
            write_record(directory / 'attempt.json', state)
            host_scope.enter_context(host.activated())
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
            state['stages'].append('prepare')
            write_record(directory / 'attempt.json', state)
            primary_exit = run_stage(directory, 'prepare', [sys.executable, '-B', '-m', 'infra.scripts.prepare_legacy_stand',
                'prepare', '--source-root', str(ROOT), '--snapshot', str(snapshot), '--directory', str(stand_directory)], ROOT)
            if primary_exit:
                raise RuntimeError('prepare failed; see processes/prepare.log')
            receipt = read_record(stand_directory / 'ownership.json')
            state['phase'] = 'test'
            state['stages'].append('test')
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
            # Restore caller configuration before any cleanup or evidence write.
            # This keeps restoration alive even when finalization itself fails.
            host_scope.close()
            # A second signal must not interrupt the bounded cleanup transaction.
            for signum in handlers:
                signal.signal(signum, signal.SIG_IGN)
            state['primary_exit'] = primary_exit
            state['primary_error'] = primary_error
            write_record(directory / 'attempt.json', state)
            cleanup_errors.extend(_clean_attempt(directory, state))
            try:
                from infra.scripts.attempt_identity import bind_runtime_artifacts
                bind_runtime_artifacts(directory)
            except Exception as error:
                cleanup_errors.append('artifact binding: ' + type(error).__name__ + ': ' + str(error))
            result = qualification_result(primary_exit=primary_exit, tests_passed=tests_passed, cleanup_errors=cleanup_errors)
            result.update(source_commit=state['source_commit'], source_tree=state['source_tree'], primary_error=primary_error)
            write_record(directory / 'qualification.json', result)
            state['phase'] = 'finished'
            write_record(directory / 'attempt.json', state)
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
        return _console(result, directory, 'run', int(result['exit']), summary_output, redact_env)


def recover(directory: Path, *, summary_output: Path | None = None, redact_env: Sequence[str] = ()) -> int:
    """Cleanup only, with an append-only recovery result and unchanged run verdict."""
    import uuid

    state = validate_attempt(directory)
    if ROOT != directory / 'frozen':
        raise ValueError('frozen recovery identity mismatch')
    with attempt_lock(directory):
        errors = _clean_attempt(directory, state)
        if not errors:
            try:
                from infra.scripts.attempt_identity import bind_runtime_artifacts
                bind_runtime_artifacts(directory)
            except Exception as error:
                errors.append('artifact binding: ' + type(error).__name__ + ': ' + str(error))
        result = {'version': 1, 'operation': 'cleanup-only', 'status': 'BLOCKED_STAND' if errors else 'PASS',
                  'errors': errors, 'source_commit': state['source_commit'], 'original_verdict_unchanged': True}
        write_record(directory / ('recovery-' + uuid.uuid4().hex + '.json'), result)
        return _console(result, directory, 'recover', 2 if errors else 0, summary_output, redact_env)


def handoff(directory: Path, operation: str, *, summary_output: Path | None = None, redact_env: Sequence[str] = ()) -> None:
    validate_attempt(directory)
    frozen = directory / 'frozen'
    os.chdir(frozen)
    environment = dict(os.environ, PYTHONPATH=str(frozen), PYTHONDONTWRITEBYTECODE='1', GIT_OPTIONAL_LOCKS='0')
    command = [sys.executable, '-B', '-m', 'infra.scripts.run_legacy_qualification', operation, '--directory', str(directory), '--frozen']
    if summary_output is not None:
        command += ['--summary-output', str(summary_output)]
        for name in redact_env:
            command += ['--redact-env', name]
    os.execve(sys.executable, command, environment)



def _read_diagnostic_manifest(path: Path) -> dict[str, Any] | None:
    """Read a bounded regular file through descriptor-bound, no-follow ancestors."""
    limit = 1024 * 1024
    path = path.absolute()
    try:
        directory_fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for name in path.parts[1:-1]:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = child
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                    return None
                data = stream.read(limit + 1)
                if len(data) > limit:
                    return None
                value = json.loads(data)
                return value if isinstance(value, dict) else None
        finally:
            os.close(directory_fd)
    except (OSError, ValueError, RecursionError):
        return None


def _diagnostic_metadata(directory: Path, result: dict[str, Any]) -> dict[str, Any]:
    """Expose bounded counters and stable artifact pointers beside the native verdict."""
    def bounded_count(parent: Path, suffix: str, *, prefix: str = "", limit: int = 1024) -> int | str:
        if not parent.is_dir() or parent.is_symlink():
            return "NOT_AVAILABLE"
        count = 0
        matches = 0
        try:
            with os.scandir(parent) as entries:
                for entry in entries:
                    count += 1
                    if count > limit:
                        return "NOT_AVAILABLE_SCAN_LIMIT"
                    if entry.is_file(follow_symlinks=False) and entry.name.startswith(prefix) and entry.name.endswith(suffix):
                        matches += 1
            return matches
        except OSError:
            return "NOT_AVAILABLE"

    raw_errors = result.get("cleanup_errors") if "cleanup_errors" in result else result.get("errors")
    errors = raw_errors if isinstance(raw_errors, list) else None
    counts: dict[str, int | str] = {
        "cleanup_errors": len(errors) if errors is not None else "NOT_AVAILABLE",
        "processes": bounded_count(directory / "processes", ".json"),
        "recovery_receipts": bounded_count(directory, ".json", prefix="recovery-"),
    }
    manifest = directory / "frozen/.dev/test-artifacts/stand/results/manifest.json"
    value = _read_diagnostic_manifest(manifest)
    counts["collected133"] = len(value["collected"]) if value is not None and isinstance(value.get("collected"), list) else "NOT_AVAILABLE"
    counts["reports399"] = len(value["reports"]) if value is not None and isinstance(value.get("reports"), list) else "NOT_AVAILABLE"
    artifacts: dict[str, str] = {}
    fixed = {
        "attempt": directory / "attempt.json",
        "qualification": directory / "qualification.json",
        "processes": directory / "processes",
        "manifest": manifest,
    }
    for name, path in fixed.items():
        if path.exists() and not path.is_symlink():
            artifacts[name] = str(path)
    if isinstance(counts["recovery_receipts"], int) and counts["recovery_receipts"]:
        artifacts["recovery_directory"] = str(directory)
    return {"diagnostic_counts": counts, "diagnostic_artifacts": artifacts}


def _console(result: dict[str, Any], directory: Path, operation: str, code: int, output: Path | None, names: Sequence[str]) -> int:
    if output is None:
        print(json.dumps(result), flush=True)
        return code
    try:
        from infra.scripts.diagnostic_output import summary_view, secret_values
        view_data = {**result, **_diagnostic_metadata(directory, result)}
        preparation = output.with_name(output.name + '.prepare.log')
        if operation == 'run' and preparation.is_file():
            view_data['diagnostic_preparation_log'] = str(preparation)
        view = summary_view(view_data, output, attempt=directory.name, stage=operation,
                            exit_code=code, secrets=secret_values(names))
        print(view.display, end='', flush=True)
        return code
    except (OSError, ValueError, UnicodeError):
        print(json.dumps({'status': 'BLOCKED_EVIDENCE', 'primary_exit': code, 'exit': code or 2,
                          'error': 'DIAGNOSTIC_OUTPUT_UNAVAILABLE'}), flush=True)
        return code or 2


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
    status_parser = commands.add_parser('status')
    status_parser.add_argument('--directory', type=Path, required=True)
    for command in (run_parser, recovery, status_parser):
        command.add_argument('--summary-output', type=Path)
        command.add_argument('--redact-env', action='append', default=[])
    args = parser.parse_args(argv)
    directory = args.directory.absolute()
    summary_output = args.summary_output.absolute() if args.summary_output is not None else None
    try:
        if args.redact_env and summary_output is None:
            raise ValueError('REDACTION_REQUIRES_SUMMARY_OUTPUT')
        if summary_output is not None:
            from infra.scripts.diagnostic_output import artifact_destination, secret_values
            artifact_destination(summary_output, source_root=ROOT)
            secret_values(args.redact_env)
        if args.operation == 'status':
            from infra.scripts.attempt_identity import status
            view = status(directory)
            return _console(view, directory, 'status', 0 if view['status'] == 'PASS' else 2,
                            summary_output, args.redact_env)
        if args.operation == 'run':
            if not args.frozen:
                preparation = summary_output.with_name(summary_output.name + '.prepare.log') if summary_output is not None else None
                if preparation is not None:
                    artifact_destination(preparation, source_root=ROOT)
                freeze_source(ROOT, args.source_commit or '', directory, preparation_log=preparation, redact_env=args.redact_env)
                if summary_output is not None:
                    try:
                        git(directory / 'frozen', 'cat-file', '-e', 'HEAD:infra/scripts/diagnostic_output.py')
                    except subprocess.SubprocessError:
                        raise ValueError('SUMMARY_MODE_UNSUPPORTED_COMMIT') from None
                handoff(directory, 'run', summary_output=summary_output, redact_env=args.redact_env)
            return execute(directory, summary_output=summary_output, redact_env=args.redact_env)
        if not args.frozen:
            if summary_output is not None:
                try:
                    git(directory / 'frozen', 'cat-file', '-e', 'HEAD:infra/scripts/diagnostic_output.py')
                except subprocess.SubprocessError:
                    raise ValueError('SUMMARY_MODE_UNSUPPORTED_COMMIT') from None
            handoff(directory, 'recover', summary_output=summary_output, redact_env=args.redact_env)
        return recover(directory, summary_output=summary_output, redact_env=args.redact_env)
    except Exception as error:
        result = {'status': 'BLOCKED_STAND', 'error': type(error).__name__, 'reason': str(error)}
        return _console(result, directory, args.operation, 2, summary_output, args.redact_env)


if __name__ == '__main__':
    sys.exit(main())
