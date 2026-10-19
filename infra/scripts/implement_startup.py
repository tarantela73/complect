"""Run one implementation command with mandatory bounded startup diagnostics."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import NoReturn

from infra.scripts.diagnostic_output import (
    DiagnosticBatch,
    artifact_destination,
    present,
    read_section,
    secret_values,
    status_view,
)


def _parser() -> argparse.ArgumentParser:
    class Parser(argparse.ArgumentParser):
        def error(self, message: str) -> NoReturn:
            raise ValueError("INVALID_ARGUMENTS")

    parser = Parser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scope", action="append", default=[])
    parser.add_argument("--redact-env", action="append", default=[])
    parser.add_argument("--section", nargs=2, action="append", default=[], metavar=("PATH", "HEADING"))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def _reject_symlink_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise ValueError("SYMLINK_PATH_FORBIDDEN")
    return absolute.resolve()


def run(argv: list[str] | None = None) -> int:
    primary_exit: int | None = None
    try:
        args = _parser().parse_args(argv)
        if not args.command or args.command[0] != "--":
            raise ValueError("COMMAND_REQUIRED")
        root = _reject_symlink_path(args.root)
        output = _reject_symlink_path(args.output_dir)
        if not output.is_dir():
            raise ValueError("OUTPUT_DIRECTORY_REQUIRED")
        artifact_destination(output / "status.bin", source_root=root)
        artifact_destination(output / "command.json", source_root=root)
        artifact_destination(output / "summary.txt", source_root=root)
        if args.section:
            artifact_destination(output / "documents.txt", source_root=root)
        secrets = secret_values(args.redact_env)
        command = args.command[1:]
        if any(secret in str(value) for secret in secrets for value in (*command, str(args.root), str(args.output_dir))):
            raise ValueError("SECRET_IN_COMMAND_OR_PATH")

        status, status_code = status_view(root, output / "status.bin", scopes=args.scope, secrets=secrets)
        if status_code:
            return 2

        batch = DiagnosticBatch()
        batch.add("status", status.display)
        if args.section:
            selected = []
            for relative, heading in args.section:
                path = Path(relative)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("INVALID_DOCUMENT_PATH")
                selected.append((str(path) + "#" + heading, read_section(root / path, heading)))
            documents_text = "\n".join("[section " + json.dumps(label, ensure_ascii=False) + "]\n" + text for label, text in selected)
            documents_view = present(documents_text, output / "documents.txt", secrets=secrets, tail=True)
            batch.add("documents", documents_view.display)

        completed = subprocess.run(
            args.command[1:], cwd=root, env=os.environ.copy(), capture_output=True, check=False,
        )
        primary_exit = completed.returncode
        native = {
            "status": "PASS" if primary_exit == 0 else "FAIL",
            "primary_exit": primary_exit,
            "stdout": completed.stdout.decode("utf-8", errors="replace"),
            "stderr": completed.stderr.decode("utf-8", errors="replace"),
        }
        command_view = present(
            json.dumps(native, ensure_ascii=False, indent=2) + "\n",
            output / "command.json",
            header={"status": native["status"], "primary_exit": primary_exit},
            secrets=secrets,
            tail=True,
        )

        batch.add("command", command_view.display)
        summary = batch.write(output / "summary.txt", secrets=secrets)
        print(summary.display, end="")
        assert primary_exit is not None
        return 128 + abs(primary_exit) if primary_exit < 0 else primary_exit
    except (OSError, ValueError, UnicodeError):
        # A failed diagnostic step cannot turn a successful primary command into PASS.
        # When the primary exit is known, callers retain it; setup failures have no primary exit.
        print(json.dumps({"status": "FAIL", "error": "STARTUP_DIAGNOSTICS_FAILED"}))
        failed_exit = primary_exit
        if failed_exit is not None and failed_exit != 0:
            return 128 + abs(failed_exit) if failed_exit < 0 else failed_exit
        return 2


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
