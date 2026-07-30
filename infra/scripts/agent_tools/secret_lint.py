from __future__ import annotations

import argparse
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .common import CommandResult, CommandRunner

RULES = (
    ("supermemory-token", re.compile(r"\bsm_[A-Za-z0-9_-]{20,}\b")),
    ("context7-token", re.compile(r"\bctx7sk-[A-Za-z0-9_-]{16,}\b")),
    (
        "generic-bearer",
        re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9][A-Za-z0-9._~-]{15,}"),
    ),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)
CANDIDATE_SUFFIXES = {".md", ".toml", ".json", ".yaml", ".yml", ".sh", ".env", ".example"}
SPECIAL_NAMES = {"Dockerfile", "Makefile", "docker-compose.dev.yml"}
CREDENTIAL_CONFIG_SUFFIXES = {".toml", ".json", ".yaml", ".yml", ".env", ".example"}
CREDENTIAL_FIELD_PATTERN = re.compile(
    r"""(?ix)
    ["']?
    (?P<field>[a-z0-9_.-]*(?:api[_-]?key|token|secret|password)[a-z0-9_.-]*)
    ["']?
    \s*[:=]\s*
    ["']?(?P<value>[^\s,"'}\#]+)
    """
)
CREDENTIAL_PLACEHOLDERS = (
    "REQUIRED",
    "CHANGEME",
    "CHANGE_ME",
    "PLACEHOLDER",
    "EXAMPLE",
    "DUMMY",
)


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.rule}"


def is_candidate_path(path: Path) -> bool:
    name = path.name
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return False
    return name in SPECIAL_NAMES or path.suffix.lower() in CANDIDATE_SUFFIXES


def scan_text(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    scan_credential_fields = path.suffix.lower() in CREDENTIAL_CONFIG_SUFFIXES
    for line_number, line in enumerate(text.splitlines(), start=1):
        matched_specific_rule = False
        for rule, pattern in RULES:
            if pattern.search(line):
                findings.append(Finding(path=path, line=line_number, rule=rule))
                matched_specific_rule = True
        credential_match = CREDENTIAL_FIELD_PATTERN.search(line) if scan_credential_fields else None
        if credential_match and not matched_specific_rule:
            field = credential_match.group("field")
            value = credential_match.group("value")
            is_reference = value.startswith(("$", "<", "{", "`", "__"))
            is_placeholder = value.upper().startswith(CREDENTIAL_PLACEHOLDERS)
            is_secret_file_reference = field.lower().endswith("_file") and value.startswith("/run/secrets/")
            if len(value) >= 16 and not is_reference and not is_placeholder and not is_secret_file_reference:
                findings.append(
                    Finding(path=path, line=line_number, rule="credential-field")
                )
    return findings


def tracked_candidate_paths(root: Path, runner: CommandRunner) -> list[Path]:
    result = runner(["git", "ls-files", "-z"], root)
    if result.returncode != 0:
        raise RuntimeError("git ls-files failed")
    return [root / value for value in result.stdout.split("\0") if value and is_candidate_path(Path(value))]


def run_secret_lint(paths: Sequence[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        findings.extend(scan_text(path, path.read_text(encoding="utf-8")))
    return findings


def _subprocess_runner(command: Sequence[str], cwd: Path | None) -> CommandResult:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    return CommandResult(result.returncode, result.stdout, result.stderr)


def _git_root() -> Path:
    result = _subprocess_runner(["git", "rev-parse", "--show-toplevel"], Path.cwd())
    if result.returncode != 0:
        raise RuntimeError("cannot determine Git root")
    return Path(result.stdout.strip())


def _explicit_candidate_paths(root: Path, values: Sequence[str]) -> list[Path]:
    candidates: list[Path] = []
    for value in values:
        path = root / value
        if path.is_dir():
            candidates.extend(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file() and is_candidate_path(candidate)
            )
        elif path.is_file() and is_candidate_path(path):
            candidates.append(path)
    return sorted(set(candidates))


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan tracked agent configuration for credential-shaped values.")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    try:
        root = _git_root()
        paths = (
            _explicit_candidate_paths(root, args.paths)
            if args.paths
            else tracked_candidate_paths(root, _subprocess_runner)
        )
        findings = run_secret_lint(paths)
    except (OSError, RuntimeError, UnicodeError) as error:
        parser.exit(2, f"error: {error}\n")
    if findings:
        for finding in findings:
            print(finding)
        return 1
    print(f"PASS agent-secret-check files={len(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
