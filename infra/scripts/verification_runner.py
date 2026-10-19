"""Execute explicit checks; verify receipts without replaying their commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from infra.scripts.evidence_bundle import read_exact_chunk

FORMAT = "complect-verification-run-v1"


class VerificationError(ValueError):
    """The declared check cannot be recorded safely."""


def _source_names(sources: list[str]) -> list[str]:
    if (
        not isinstance(sources, list)
        or not sources
        or any(
            not isinstance(n, str)
            or not n
            or Path(n).is_absolute()
            or ".." in Path(n).parts
            for n in sources
        )
        or len({str(Path(n)) for n in sources}) != len(sources)
    ):
        raise VerificationError("SOURCE_SELECTION_INVALID")
    return sorted(sources)


def _snapshot(
    root: Path, sources: list[str], *, allow_missing: bool = False
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in _source_names(sources):
        path = root / name
        try:
            if (
                any(p.is_symlink() for p in (path, *path.parents))
                or not path.resolve().is_relative_to(root)
                or not stat.S_ISREG(path.stat().st_mode)
            ):
                raise VerificationError("REGULAR_SOURCE_REQUIRED")
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            result[name] = {"sha256": digest, "mode": path.stat().st_mode}
        except (OSError, VerificationError):
            if not allow_missing:
                raise VerificationError("SOURCE_UNAVAILABLE") from None
            result[name] = {"error": "SOURCE_UNAVAILABLE"}
    return result


def _outcome(record: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    code = record.get("exit")
    return {
        **record,
        "integrity": "FAIL" if errors else "PASS",
        "behavior": ("NOT_RUN" if "exit" in record and code is None else "UNKNOWN")
        if type(code) is not int
        else "PASS"
        if code == 0
        else "FAIL",
        "expectation": "PASS"
        if type(code) is int
        and type(record.get("expected_exit")) is int
        and code == record["expected_exit"]
        else "FAIL",
        "errors": sorted(set(errors)),
    }


def _secrets(
    names: list[str] | None, command: list[str], metadata: list[str]
) -> list[bytes]:
    values = []
    for name in names or []:
        value = os.environ.get(name)
        if not value:
            raise VerificationError("REDACTION_ENV_UNAVAILABLE")
        if any(value in argument for argument in command):
            raise VerificationError("SECRET_IN_COMMAND_FORBIDDEN")
        if any(value in item for item in metadata):
            raise VerificationError("SECRET_IN_METADATA_FORBIDDEN")
        values.append(value.encode())
    return sorted(set(values), key=len, reverse=True)


def _validate_execution_context(
    *,
    root: Path,
    cwd: Path,
    output: Path,
    sources: list[str],
    declared_checkout: Path | None,
    required_inputs: list[Path] | None,
) -> tuple[Path, Path, Path]:
    """Validate all known paths before creating output or starting a command.

    ``declared_checkout`` is an explicit acceptance opt-in.  The historical
    runner remains permissive when it is omitted, while acceptance runs bind
    both the source root and command cwd to one checkout.
    """
    def has_symlink_component(path: Path) -> bool:
        current = path.absolute()
        return any(part.is_symlink() for part in (current, *current.parents))

    if (
        has_symlink_component(root)
        or has_symlink_component(cwd)
        or (declared_checkout is not None and has_symlink_component(declared_checkout))
    ):
        raise VerificationError("SYMLINK_PATH_FORBIDDEN")
    root, cwd, output = root.resolve(), cwd.resolve(), output.absolute()
    if declared_checkout is not None:
        checkout = declared_checkout.resolve()
        if root != checkout or cwd != checkout:
            raise VerificationError("DECLARED_CHECKOUT_MISMATCH")
    if not root.is_dir() or not cwd.is_dir():
        raise VerificationError("CHECKOUT_OR_CWD_MISSING")
    _source_names(sources)
    for item in required_inputs or []:
        path = Path(item)
        if not path.is_absolute():
            path = root / path
        if has_symlink_component(path) or not path.is_file():
            raise VerificationError("REQUIRED_INPUT_MISSING")
    if declared_checkout is not None and has_symlink_component(output):
        raise VerificationError("SYMLINK_PATH_FORBIDDEN")
    candidate = output.resolve()
    if candidate.exists():
        raise FileExistsError(candidate)
    if candidate.is_relative_to(root):
        relative = candidate.relative_to(root).as_posix()
        allowed = relative == ".dev/test-artifacts" or relative.startswith(
            ".dev/test-artifacts/"
        )
        if not allowed:
            raise VerificationError("OUTPUT_INSIDE_SOURCE_AREA")
    return root, cwd, output


def _capture(
    command: list[str], cwd: Path, log: Path, secrets: list[bytes]
) -> int | None:
    pattern = re.compile(b"|".join(re.escape(s) for s in secrets)) if secrets else None
    keep = max((len(s) - 1 for s in secrets), default=0)
    with log.open("xb") as stream:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except OSError:
            return None
        with process:
            assert process.stdout is not None
            buffer = b""
            while chunk := process.stdout.read(65536):
                buffer += chunk
                cut = max(0, len(buffer) - keep)
                if pattern is not None:
                    for match in pattern.finditer(buffer):
                        if match.start() < cut < match.end():
                            cut = match.start()
                            break
                prefix, buffer = buffer[:cut], buffer[cut:]
                stream.write(pattern.sub(b"[REDACTED]", prefix) if pattern else prefix)
            stream.write(pattern.sub(b"[REDACTED]", buffer) if pattern else buffer)
            return process.wait()


def run_check(
    command: list[str],
    *,
    root: Path,
    cwd: Path,
    sources: list[str],
    output: Path,
    expected_exit: int = 0,
    redact_env: list[str] | None = None,
    on_exit: Callable[[int | None], None] | None = None,
    declared_checkout: Path | None = None,
    required_inputs: list[Path] | None = None,
) -> dict[str, Any]:
    """Execute the supplied argv once; write a new immutable attempt directory."""
    root, cwd = root.resolve(), cwd.resolve()
    if (
        not isinstance(command, list)
        or not command
        or not command[0]
        or any(not isinstance(a, str) or "\0" in a for a in command)
        or type(expected_exit) is not int
        or not cwd.is_dir()
    ):
        raise VerificationError("COMMAND_OR_CWD_INVALID")
    root, cwd, output = _validate_execution_context(
        root=root,
        cwd=cwd,
        output=output,
        sources=sources,
        declared_checkout=declared_checkout,
        required_inputs=required_inputs,
    )
    candidate = output.resolve()
    before = _snapshot(root, sources)
    secrets = _secrets(
        redact_env,
        command,
        [str(root), str(cwd), *sources, str(output), str(candidate)],
    )
    output.mkdir(parents=True, exist_ok=False)
    log = output / "command.log"
    code = _capture(command, cwd, log, secrets)
    if on_exit is not None:
        on_exit(code)
    with log.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    receipt = {
        "format": FORMAT,
        "attempt_id": uuid.uuid4().hex,
        "command": command,
        "cwd": str(cwd),
        "source_root": str(root),
        "source_before": before,
        "source_after": _snapshot(root, sources, allow_missing=True),
        "exit": code,
        "expected_exit": expected_exit,
        "log": {"path": "command.log", "sha256": digest},
    }
    payload = (json.dumps(receipt, indent=2) + "\n").encode()
    pin = hashlib.sha256(payload).hexdigest()
    with (output / "receipt.json").open("xb") as stream:
        stream.write(payload)
    return verify_check(
        output / "receipt.json", root=root, sources=sources, expected_sha256=pin
    )


def _load(path: Path, expected_sha256: str | None) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = dict(pairs)
        if len(result) != len(pairs):
            raise VerificationError("DUPLICATE_JSON_KEY")
        return result

    if (
        not isinstance(expected_sha256, str)
        or re.fullmatch("[0-9a-f]{64}", expected_sha256) is None
    ):
        raise VerificationError("RECEIPT_PIN_REQUIRED")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise VerificationError("RECEIPT_INVALID")
    value = json.loads(
        read_exact_chunk(path, 0, path.stat().st_size, expected_sha256),
        object_pairs_hook=unique,
    )
    if not isinstance(value, dict):
        raise VerificationError("RECEIPT_INVALID")
    return value


def verify_check(
    receipt: Path, *, root: Path, sources: list[str], expected_sha256: str | None = None
) -> dict[str, Any]:
    """Recompute integrity against an independent source selection; never execute."""
    record: dict[str, Any] = {}
    errors = []
    try:
        record = _load(receipt, expected_sha256)
        current = _snapshot(root.resolve(), sources)
        if record.get("format") != FORMAT:
            errors.append("FORMAT_INVALID")
        attempt = record.get("attempt_id")
        if (
            not isinstance(attempt, str)
            or re.fullmatch("[0-9a-f]{32}", attempt) is None
        ):
            errors.append("ATTEMPT_INVALID")
        command = record.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not command[0]
            or any(not isinstance(a, str) or "\0" in a for a in command)
        ):
            errors.append("COMMAND_INVALID")
        cwd = record.get("cwd")
        if not isinstance(cwd, str) or not cwd or not Path(cwd).is_absolute():
            errors.append("CWD_INVALID")
        if record.get("source_root") != str(root.resolve()):
            errors.append("SOURCE_ROOT_MISMATCH")
        if (
            type(record.get("exit")) is not int
            or type(record.get("expected_exit")) is not int
        ):
            errors.append("EXIT_INVALID")
        if (
            record.get("source_before") != record.get("source_after")
            or record.get("source_before") != current
        ):
            errors.append("SOURCE_MISMATCH")
        log = record.get("log")
        if (
            not isinstance(log, dict)
            or log.get("path") != "command.log"
            or not isinstance(log.get("sha256"), str)
            or re.fullmatch("[0-9a-f]{64}", log["sha256"]) is None
        ):
            errors.append("LOG_DESCRIPTOR_INVALID")
        else:
            path = receipt.parent / "command.log"
            if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
                raise VerificationError("LOG_INVALID")
            read_exact_chunk(path, 0, 0, log["sha256"])
    except (OSError, ValueError, TypeError, UnicodeError):
        errors.append("EVIDENCE_UNAVAILABLE_OR_INVALID")
    return {**_outcome(record, errors), "receipt_sha256": expected_sha256}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("run", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, required=True)
        command.add_argument("--source", action="append", required=True)
        if name == "run":
            command.add_argument("--cwd", type=Path)
            command.add_argument("--declared-checkout", type=Path)
            command.add_argument("--required-input", type=Path, action="append", default=[])
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--expect-exit", type=int, default=0)
            command.add_argument("--redact-env", action="append", default=[])
            command.add_argument("command", nargs=argparse.REMAINDER)
        else:
            command.add_argument("--receipt", type=Path, required=True)
            command.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "run":
            command = args.command[1:] if args.command[:1] == ["--"] else args.command
            result = run_check(
                command,
                root=args.root,
                cwd=args.cwd or args.root,
                sources=args.source,
                output=args.output,
                expected_exit=args.expect_exit,
                redact_env=args.redact_env,
                declared_checkout=args.declared_checkout,
                required_inputs=args.required_input,
            )
            path = args.output / "receipt.json"
        else:
            path = args.receipt
            result = verify_check(
                path, root=args.root, sources=args.source, expected_sha256=args.sha256
            )
    except (OSError, ValueError) as exc:
        error_code = (
            str(exc)
            if isinstance(exc, VerificationError)
            else "PREPARATION_OR_OUTPUT_FAILED"
        )
        print(
            json.dumps(
                {"integrity": "FAIL", "behavior": "UNKNOWN", "errors": [error_code]}
            )
        )
        return 2
    print(
        json.dumps(
            {
                **{
                    k: result.get(k)
                    for k in (
                        "attempt_id",
                        "integrity",
                        "behavior",
                        "expectation",
                        "exit",
                        "errors",
                        "receipt_sha256",
                    )
                },
                "receipt": str(path),
            }
        )
    )
    if args.action == "verify":
        return 0 if result["integrity"] == "PASS" else 2
    process_exit = result.get("exit")
    if type(process_exit) is int and process_exit != 0:
        return process_exit if process_exit > 0 else 128 - process_exit
    return 0 if result["integrity"] == result["expectation"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
