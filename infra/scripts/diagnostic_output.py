"""Bounded human views; complete cleaned artifacts and native results stay separate."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, NoReturn


@dataclass(frozen=True)
class View:
    display: str
    artifact: Path
    sha256: str
    truncated: bool


def _redact(text: str, secrets: Sequence[str]) -> str:
    if isinstance(secrets, str) or any(not isinstance(value, str) or not value for value in secrets):
        raise ValueError("INVALID_REDACTION_VALUE")
    for value in sorted(set(secrets), key=len, reverse=True):
        if not isinstance(value, str) or not value:
            raise ValueError("INVALID_REDACTION_VALUE")
        for form in {value, json.dumps(value, ensure_ascii=False)[1:-1], json.dumps(value)[1:-1]}:
            text = text.replace(form, "[REDACTED]")
    return text


def _render(text: str, artifact: Path, digest: str, header: dict[str, Any], *, tail: bool, max_lines: int, max_bytes: int) -> tuple[str, bool]:
    if not 3 <= max_lines <= 20 or not 128 <= max_bytes <= 4096:
        raise ValueError("INVALID_DISPLAY_BUDGET")
    metadata = {**header, "artifact": str(artifact), "sha256": digest, "truncated": False}
    prefix = json.dumps(metadata, ensure_ascii=False) + "\n"
    lines = text.splitlines()
    body = "\n".join(lines)
    if len((prefix + body + "\n").encode()) <= max_bytes and len(lines) + 1 <= max_lines:
        return prefix + body + ("\n" if body else ""), False
    metadata["truncated"] = True
    prefix = json.dumps(metadata, ensure_ascii=False) + "\n"
    marker = "\n[TRUNCATED]\n"
    room = max_bytes - len((prefix + marker).encode())
    if room < 0:
        raise ValueError("METADATA_EXCEEDS_DISPLAY_BUDGET")
    selected = lines[-(max_lines - 2):] if tail else lines[:max_lines - 2]
    data = "\n".join(selected).encode()
    clipped = (data[-room:] if tail and room else data[:room]).decode("utf-8", errors="ignore")
    display = prefix + clipped + marker
    if len(display.encode()) > max_bytes or len(display.splitlines()) > max_lines:
        raise ValueError("DISPLAY_BUDGET_EXCEEDED")
    return display, True


def present(text: str, artifact: Path, *, detail: str | None = None, header: dict[str, Any] | None = None,
            secrets: Sequence[str] = (), tail: bool = False, max_lines: int = 20, max_bytes: int = 4096) -> View:
    """Save a new full cleaned artifact, then return its bounded human presentation."""
    artifact = artifact.absolute()
    if any(p.is_symlink() for p in (artifact, *artifact.parents)) or artifact.exists():
        raise ValueError("NEW_REGULAR_ARTIFACT_REQUIRED")
    cleaned = _redact(text, secrets)
    selected = cleaned if detail is None else _redact(detail, secrets)
    digest = hashlib.sha256(cleaned.encode()).hexdigest()
    metadata = json.dumps({**(header or {}), "artifact": str(artifact), "sha256": digest}, ensure_ascii=False)
    if _redact(metadata, secrets) != metadata:
        raise ValueError("SECRET_IN_DISPLAY_METADATA")
    display, truncated = _render(selected, artifact, digest, header or {}, tail=tail, max_lines=max_lines, max_bytes=max_bytes)
    with os.fdopen(os.open(artifact, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(cleaned.encode())
    return View(display, artifact, digest, truncated)


def error_view(text: str, artifact: Path, *, secrets: Sequence[str] = (), max_lines: int = 20, max_bytes: int = 4096) -> View:
    return present(text, artifact, header={"status": "ERROR_VIEW"}, secrets=secrets, tail=True, max_lines=max_lines, max_bytes=max_bytes)


def _scrub(value: Any, secrets: Sequence[str]) -> Any:
    if isinstance(value, str):
        return _redact(value, secrets)
    if isinstance(value, dict):
        if any(not isinstance(k, str) or _redact(k, secrets) != k for k in value):
            raise ValueError("SECRET_OR_INVALID_NATIVE_KEY")
        return {k: _scrub(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v, secrets) for v in value]
    return value


def summary_view(result: dict[str, Any], artifact: Path, *, attempt: str, stage: str, exit_code: int,
                 secrets: Sequence[str] = ()) -> View:
    """Opt-in human view; never mutate a native result or its existing receipt."""
    if type(exit_code) is not int:
        raise ValueError("INTEGER_EXIT_REQUIRED")
    cleaned = _scrub(result, secrets)
    full = json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n"
    error = cleaned.get("primary_error") or cleaned.get("errors") or cleaned.get("cleanup_errors") or cleaned.get("reason")
    detail = json.dumps(error, ensure_ascii=False, indent=2) if error is not None else ""
    header = {"attempt": attempt, "stage": stage, "status": result.get("status", "UNKNOWN"), "exit": exit_code}
    for key in ("diagnostic_counts", "diagnostic_artifacts"):
        if key in cleaned:
            header[key] = cleaned[key]
    digest = hashlib.sha256(full.encode()).hexdigest()
    metadata = json.dumps({**header, "artifact": str(artifact.absolute()), "sha256": digest}, ensure_ascii=False)
    if _redact(metadata, secrets) != metadata:
        raise ValueError("SECRET_IN_DISPLAY_METADATA")
    return present(full, artifact, detail=detail, header=header, tail=True)


def artifact_destination(artifact: Path, *, source_root: Path | None = None) -> Path:
    artifact = artifact.absolute()
    if ".." in artifact.parts:
        raise ValueError("PARENT_TRAVERSAL_IN_ARTIFACT")
    if any(p.is_symlink() for p in (artifact, *artifact.parents)) or artifact.exists() or not artifact.parent.is_dir():
        raise ValueError("NEW_REGULAR_ARTIFACT_REQUIRED")
    if source_root is not None:
        relative = artifact.relative_to(source_root.resolve()) if artifact.is_relative_to(source_root.resolve()) else None
        if relative is not None and not str(relative).startswith(".dev/test-artifacts/"):
            raise ValueError("ARTIFACT_INSIDE_SOURCES")
    return artifact


def status_view(root: Path, artifact: Path, *, scopes: Sequence[str] = (), secrets: Sequence[str] = ()) -> tuple[View, int]:
    import subprocess

    root = root.resolve()
    artifact_destination(artifact, source_root=root)
    environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    selected_scopes = [str(Path(scope)).rstrip("/") for scope in scopes]
    if any(Path(s).is_absolute() or ".." in Path(s).parts for s in selected_scopes):
        raise ValueError("INVALID_STATUS_SCOPE")
    checked = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, env=environment, check=False)
    if checked.returncode:
        text = (checked.stdout + checked.stderr).decode("utf-8")
        return present(text, artifact, header={"status": "FAIL", "exit": checked.returncode}, tail=True, secrets=secrets), checked.returncode
    if checked.stdout != (str(root) + "\n").encode():
        raise ValueError("EXACT_REPOSITORY_ROOT_REQUIRED")
    result = subprocess.run(["git", "-C", str(root), "-c", "core.quotepath=false", "status", "--porcelain=v1", "-z", "--untracked-files=all"], capture_output=True, env=environment, check=False)
    text = result.stdout.decode("utf-8")
    if result.returncode:
        text += result.stderr.decode("utf-8")
        return present(text, artifact, header={"status": "FAIL", "exit": result.returncode}, tail=True, secrets=secrets), result.returncode
    if text and not text.endswith("\0"):
        raise ValueError("INCOMPLETE_GIT_STATUS")
    fields = text.split("\0")[:-1]
    counts: dict[str, int] = {}
    chosen = []
    entries = 0
    index = 0
    while index < len(fields):
        row = fields[index]
        index += 1
        if len(row) < 4 or row[2] != " " or any(c not in " MADRCUT?!" for c in row[:2]):
            raise ValueError("INVALID_GIT_STATUS")
        code, name = row[:2], row[3:]
        original = None
        if "R" in code or "C" in code:
            if index == len(fields) or not fields[index]:
                raise ValueError("INCOMPLETE_GIT_RENAME")
            original = fields[index]
            index += 1
        entries += 1
        counts[code] = counts.get(code, 0) + 1
        paths = [name] + ([original] if original is not None else [])
        if any(scope == "." or p == scope or p.startswith(scope + "/") for p in paths for scope in selected_scopes):
            label = json.dumps(name, ensure_ascii=False)
            if original is not None:
                label = json.dumps(original, ensure_ascii=False) + " -> " + label
            chosen.append(code + " " + label)
    view = present(text, artifact, detail="\n".join(chosen), header={"status": "PASS", "exit": 0, "entries": entries, "counts": counts, "selected": len(chosen)}, secrets=secrets)
    return view, 0


def read_section(path: Path, heading: str) -> str:
    """Select one unambiguous Markdown ATX section, ignoring fenced code."""
    import re

    lines = _text_file(path).splitlines(keepends=True)
    headings = []
    fence = None
    for index, line in enumerate(lines):
        value = line.rstrip("\r\n")
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", value)
        if fence is not None:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
            continue
        if marker:
            fence = (marker[1][0], len(marker[1]))
            continue
        match = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+)$", value.lstrip("\ufeff") if index == 0 else value)
        if match:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", match[2]).strip()
            headings.append((index, len(match[1]), title))
    selected = [(index, level) for index, level, title in headings if title == heading]
    if len(selected) != 1:
        raise ValueError("SECTION_MISSING_OR_AMBIGUOUS")
    start, level = selected[0]
    end = next((index for index, depth, _title in headings if index > start and depth <= level), len(lines))
    return "".join(lines[start:end])


class DiagnosticBatch:
    """Collect independent reads before applying one total response budget."""
    def __init__(self) -> None:
        self.parts: list[tuple[str, str]] = []

    def add(self, label: str, text: str) -> None:
        if not isinstance(label, str) or not isinstance(text, str):
            raise ValueError("TEXT_REQUIRED")
        self.parts.append((label, text))

    def write(self, artifact: Path, *, secrets: Sequence[str] = (), max_lines: int = 20, max_bytes: int = 4096) -> View:
        text = "\n".join("[section " + json.dumps(label, ensure_ascii=False) + "]\n" + value for label, value in self.parts)
        return present(text, artifact, header={"status": "PASS", "sections": len(self.parts)}, secrets=secrets, max_lines=max_lines, max_bytes=max_bytes)


def documents_view(sections: Sequence[tuple[Path, str]], artifact: Path, *, secrets: Sequence[str] = (), max_lines: int = 20, max_bytes: int = 4096) -> View:
    batch = DiagnosticBatch()
    for path, heading in sections:
        batch.add(str(path) + "#" + heading, read_section(path, heading))
    return batch.write(artifact, secrets=secrets, max_lines=max_lines, max_bytes=max_bytes)


def _text_file(path: Path) -> str:
    path = path.absolute()
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError("REGULAR_INPUT_REQUIRED")
    return path.read_bytes().decode("utf-8")


def _json(text: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=unique)


def _pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise ValueError("INVALID_JSON_POINTER")
    for raw in pointer[1:].split("/"):
        import re
        if re.search(r"~(?![01])", raw):
            raise ValueError("INVALID_JSON_POINTER")
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and token.isascii() and token.isdigit() and str(int(token)) == token and int(token) < len(value):
            value = value[int(token)]
        else:
            raise ValueError("JSON_POINTER_NOT_FOUND")
    return value


def secret_values(names: Sequence[str]) -> list[str]:
    result = []
    for name in names:
        value = os.environ.get(name)
        if not value:
            raise ValueError("REDACTION_ENV_UNAVAILABLE")
        result.append(value)
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse

    class Parser(argparse.ArgumentParser):
        def error(self, message: str) -> NoReturn:
            raise ValueError("INVALID_ARGUMENTS")

    parser = Parser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for operation in ("error", "documents", "status"):
        command = commands.add_parser(operation)
        command.add_argument("--artifact", type=Path, required=True)
        command.add_argument("--source-root", type=Path)
        command.add_argument("--redact-env", action="append", default=[])
        if operation == "error":
            command.add_argument("--input", type=Path, required=True)
            command.add_argument("--json-pointer")
        elif operation == "documents":
            command.add_argument("--section", nargs=2, action="append", required=True, metavar=("FILE", "HEADING"))
        else:
            command.add_argument("--root", type=Path, required=True)
            command.add_argument("--scope", action="append", default=[])
    try:
        args = parser.parse_args(argv)
        artifact_destination(args.artifact, source_root=args.source_root)
        secrets = secret_values(args.redact_env)
        code = 0
        if args.operation == "status":
            view, code = status_view(args.root, args.artifact, scopes=args.scope, secrets=secrets)
        elif args.operation == "documents":
            view = documents_view([(Path(path), heading) for path, heading in args.section], args.artifact, secrets=secrets)
        else:
            text = _text_file(args.input)
            if args.json_pointer is None:
                view = error_view(text, args.artifact, secrets=secrets)
            else:
                native = _scrub(_json(text), secrets)
                detail = json.dumps(_pointer(native, args.json_pointer), ensure_ascii=False, indent=2)
                full = json.dumps(native, ensure_ascii=False, indent=2) + "\n"
                metadata = json.dumps({"artifact": str(args.artifact.absolute()), "sha256": hashlib.sha256(full.encode()).hexdigest(), "status": "ERROR_VIEW"}, ensure_ascii=False)
                if _redact(metadata, secrets) != metadata:
                    raise ValueError("SECRET_IN_DISPLAY_METADATA")
                view = present(full, args.artifact, detail=detail, header={"status": "ERROR_VIEW"}, tail=True)
        print(view.display, end="")
        return code
    except (OSError, ValueError, UnicodeError):
        print(json.dumps({"status": "FAIL", "error": "DIAGNOSTIC_INPUT_OR_OUTPUT_INVALID"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
