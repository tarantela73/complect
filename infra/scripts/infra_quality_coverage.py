"""Hash-bound per-file lint/type coverage, separate from the legacy quality receipt."""
from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
VERSION = 1


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or str(path) != value or any(c in value for c in "\n\r\t\x00"):
        raise ValueError("unsafe coverage path")
    return value


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *arguments], capture_output=True, check=True).stdout


def changed_files(base: str, *, root: Path = ROOT) -> list[dict[str, str]]:
    fields = git(root, "diff", "--name-status", "--no-renames", "-z", base, "--").decode().split("\0")
    if fields[-1] != "":
        raise ValueError("incomplete change inventory")
    fields.pop()
    if len(fields) % 2:
        raise ValueError("incomplete change inventory")
    entries = {safe_path(fields[i + 1]): fields[i] for i in range(0, len(fields), 2)}
    for path in git(root, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0"):
        if path:
            entries[safe_path(path)] = "??"
    return [{"path": path, "status": entries[path]} for path in sorted(entries)]


def included(path: str) -> bool:
    return path.startswith("infra/") and (Path(path).suffix in {".py", ".ini", ".sh", ".json", ".mjs", ".toml"}
                                           or Path(path).name.startswith("Dockerfile"))


def current_inventory(root: Path) -> dict[str, dict[str, str]]:
    result = {}
    for path in sorted((root / "infra").rglob("*")):
        relative = path.relative_to(root).as_posix()
        if not included(relative):
            continue
        if path.is_symlink():
            raise ValueError("symlink in coverage source inventory")
        if path.is_file():
            result[relative] = {"sha256": digest(path.read_bytes()), "mode": "100755" if path.stat().st_mode & 0o111 else "100644"}
    return result


def prepare_input(*, base: str, output: Path, root: Path = ROOT) -> int:
    resolved = git(root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").decode().strip()
    changes = changed_files(resolved, root=root)
    current = current_inventory(root)
    previous = {}
    sources = {}
    for row in git(root, "ls-tree", "-rz", "--full-tree", resolved, "--", "infra").split(b"\0"):
        if not row:
            continue
        metadata, raw_path = row.split(b"\t", 1)
        path = safe_path(raw_path.decode())
        if not included(path):
            continue
        mode, kind, blob = metadata.decode().split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("unsupported baseline source kind")
        data = git(root, "cat-file", "blob", blob)
        previous[path] = {"sha256": digest(data), "mode": mode}
        sources[path] = base64.b64encode(data).decode()
    for entry in changes:
        current_path = root / entry["path"]
        if current_path.is_symlink():
            raise ValueError("symlink in change inventory")
        if current_path.is_file():
            entry["current_sha256"] = digest(current_path.read_bytes())
    payload = {"version": VERSION, "comparison_base": resolved, "changes": changes,
               "current_inventory": current, "baseline_inventory": previous, "baseline_sources": sources}
    payload["source_identity"] = digest(canonical(payload))
    output.mkdir(parents=True, exist_ok=True)
    (output / "input.json").write_bytes(canonical(payload) + b"\n")
    return 0


def validate(root: Path, payload: Any) -> None:
    if not isinstance(payload, dict) or payload.get("version") != VERSION:
        raise ValueError("unsupported coverage input")
    body = {key: value for key, value in payload.items() if key != "source_identity"}
    if payload.get("source_identity") != digest(canonical(body)):
        raise ValueError("coverage input identity mismatch")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", payload.get("comparison_base", "")):
        raise ValueError("invalid comparison base")
    if payload.get("current_inventory") != current_inventory(root):
        raise ValueError("current source inventory incomplete or stale")
    baseline = payload.get("baseline_inventory")
    sources = payload.get("baseline_sources")
    if not isinstance(baseline, dict) or not isinstance(sources, dict) or set(baseline) != set(sources):
        raise ValueError("baseline source inventory incomplete")
    for path, item in baseline.items():
        safe_path(path)
        if not included(path) or not isinstance(item, dict) or digest(base64.b64decode(sources[path], validate=True)) != item.get("sha256"):
            raise ValueError("baseline source hash mismatch")
    changes = payload.get("changes")
    if not isinstance(changes, list):
        raise ValueError("missing change inventory")
    seen = set()
    for entry in changes:
        if not isinstance(entry, dict):
            raise ValueError("invalid change entry")
        path = safe_path(entry.get("path", ""))
        if path in seen or entry.get("status") not in {"A", "M", "D", "T", "??"}:
            raise ValueError("duplicate path or unknown change status")
        seen.add(path)
        current = root / path
        if not current.resolve().is_relative_to(root.resolve()):
            raise ValueError("coverage path escapes source root")
        if current.is_symlink():
            raise ValueError("symlink change is unsupported")
        if entry["status"] == "D":
            if current.exists():
                raise ValueError("deleted path still present")
        elif not current.is_file() or entry.get("current_sha256") != digest(current.read_bytes()):
            raise ValueError("current source hash mismatch")
    expected = {path for path in set(baseline) | set(payload["current_inventory"])
                if path.endswith(".py") and baseline.get(path) != payload["current_inventory"].get(path)}
    actual = {entry["path"] for entry in changes if entry["path"].startswith("infra/") and entry["path"].endswith(".py")}
    if expected != actual:
        raise ValueError("changed Python inventory incomplete or unknown")


def check_file(root: Path, path: str, *, shadows: list[str], source: bytes | None = None) -> dict[str, Any]:
    # Shadow/base comparisons must not reuse size/mtime-only cached diagnostics.
    commands = {
        "ruff": ["ruff", "check", "--no-cache", "--output-format", "json", "--stdin-filename", path, "-"],
        "mypy": ["mypy", "--config-file", "infra/mypy.ini", "--ignore-missing-imports", "--explicit-package-bases",
                 "--cache-dir", "/tmp/infra-coverage-mypy", "--no-incremental", "--no-error-summary", "--show-error-codes", *shadows, path],
    }
    checks = {}
    for tool, command in commands.items():
        binary = shutil.which(tool)
        if binary is None:
            raise RuntimeError("required offline tool is missing: " + tool)
        command[0] = binary
        completed = subprocess.run(command, cwd=root, input=(source if source is not None else (root / path).read_bytes()) if tool == "ruff" else None,
                                   capture_output=True, check=False)
        stdout, stderr = completed.stdout.decode(errors="replace"), completed.stderr.decode(errors="replace")
        if completed.returncode not in {0, 1}:
            raise RuntimeError(tool + " failed to execute: " + stderr[:1000])
        if tool == "ruff":
            diagnostics = json.loads(stdout)
            if not isinstance(diagnostics, list):
                raise ValueError("invalid ruff diagnostics")
            for diagnostic in diagnostics:
                diagnostic.pop("filename", None)
        else:
            diagnostics = sorted(line for line in stdout.splitlines() if line)
        # stderr is retained and also compared; internal failures never become baseline.
        checks[tool] = {"command": command, "exit": completed.returncode, "stdout": stdout,
                        "stderr": stderr, "diagnostics": diagnostics}
    return checks


def baseline_locations(previous: bytes, current: bytes) -> dict[int, int]:
    """Map only byte-identical source lines; changed lines never inherit an error."""
    old_lines, new_lines = previous.splitlines(keepends=True), current.splitlines(keepends=True)
    mapping = {}
    for block in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            mapping[block.b + offset + 1] = block.a + offset + 1
        if block.size and block.a + block.size == len(old_lines) and block.b + block.size == len(new_lines):
            mapping[len(new_lines) + 1] = len(old_lines) + 1
    return mapping


def mapped_diagnostics(tool: str, diagnostics: list[Any], path: str, mapping: dict[int, int]) -> list[Any] | None:
    """Retain diagnostic text/code/columns and remap proven unchanged locations."""
    def remap(value: Any) -> Any:
        if isinstance(value, list):
            return [remap(item) for item in value]
        if isinstance(value, dict):
            return {key: mapping[item] if key in {"row", "noqa_row"} and isinstance(item, int) else remap(item)
                    for key, item in value.items()}
        return value

    try:
        if tool == "ruff":
            return remap(diagnostics)
        result = []
        for line in diagnostics:
            match = re.match(r"^(.+?):([0-9]+)(:[0-9]+)?: (.*)$", line)
            if match and match[1] == path:
                result.append(f"{path}:{mapping[int(match[2])]}{match[3] or ''}: {match[4]}")
            elif match:
                # A diagnostic outside this checked file has no proven source mapping.
                return None
            else:
                result.append(line)
        return sorted(result)
    except KeyError:
        return None


def run(*, base: str | None, report: Path, coverage_input: Path | None = None, root: Path = ROOT) -> int:
    if coverage_input is None:
        if base is None:
            raise ValueError("--base or --coverage-input is required")
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            prepare_input(base=base, output=package, root=root)
            return run(base=None, report=report, coverage_input=package / "input.json", root=root)
    payload = json.loads(coverage_input.read_bytes())
    validate(root, payload)
    files: dict[str, Any] = {}
    unknown = []
    with tempfile.TemporaryDirectory() as temporary:
        baseline_root = Path(temporary)
        shadows = []
        for path, source in payload["baseline_sources"].items():
            destination = baseline_root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(base64.b64decode(source, validate=True))
            if path.endswith(".py"):
                shadows.extend(["--shadow-file", path, str(destination)])
        for entry in payload["changes"]:
            path = entry["path"]
            if not path.startswith("infra/"):
                continue
            if not path.endswith(".py"):
                if path.startswith("infra/scripts/"):
                    unknown.append(path)
                continue
            result: dict[str, Any] = {"change": entry["status"], "source_sha256": entry.get("current_sha256"),
                                      "baseline_sha256": payload["baseline_inventory"].get(path, {}).get("sha256")}
            if entry["status"] == "D":
                if path not in payload["baseline_inventory"]:
                    raise ValueError("deleted source absent from base")
                result.update(status="REMOVED", checks={})
            else:
                checks = check_file(root, path, shadows=[])
                result["checks"] = checks
                result["status"] = "PASS" if all(check["exit"] == 0 for check in checks.values()) else "FAIL"
                if result["status"] == "FAIL" and path in payload["baseline_sources"]:
                    previous = check_file(root, path, shadows=shadows, source=base64.b64decode(payload["baseline_sources"][path], validate=True))
                    result["baseline_checks"] = previous
                    mapping = baseline_locations(base64.b64decode(payload["baseline_sources"][path], validate=True),
                                                 (root / path).read_bytes())
                    comparison = {tool: mapped_diagnostics(tool, checks[tool]["diagnostics"], path, mapping) for tool in checks}
                    result["baseline_mapped_diagnostics"] = comparison
                    matched = all(checks[tool]["exit"] == previous[tool]["exit"] and comparison[tool] is not None
                                  and comparison[tool] == previous[tool]["diagnostics"]
                                  and checks[tool]["stderr"] == previous[tool]["stderr"] for tool in checks)
                    result["baseline"] = "MATCHED" if matched else "CHANGED_DIAGNOSTICS"
                    if matched:
                        result["status"] = "FAIL_BASELINE"
            files[path] = result
    blocked = bool(unknown) or any(item["status"] == "FAIL" for item in files.values())
    receipt = {"version": VERSION, "status": "BLOCKED" if blocked else "PASS", "comparison_base": payload["comparison_base"],
               "source_identity": payload["source_identity"], "changed_files": [entry["path"] for entry in payload["changes"]],
               "unknown_paths": unknown, "files": files}
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes(canonical(receipt) + b"\n")
    print(json.dumps({"status": receipt["status"], "report": str(report), "files": len(files)}))
    return 1 if blocked else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--base")
    parser.add_argument("--prepare-input", type=Path)
    parser.add_argument("--coverage-input", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.prepare_input is not None:
            if args.base is None:
                raise ValueError("--base is required with --prepare-input")
            return prepare_input(base=args.base, output=args.prepare_input, root=args.root)
        if args.report is None:
            raise ValueError("--report is required")
        return run(base=args.base, report=args.report, coverage_input=args.coverage_input, root=args.root)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, KeyError, TypeError) as error:
        diagnostic = {"version": VERSION, "status": "BLOCKED_TOOLING", "error": str(error)}
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_bytes(canonical(diagnostic) + b"\n")
        print(json.dumps(diagnostic))
        return 2


if __name__ == "__main__":
    sys.exit(main())
