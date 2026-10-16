"""Run the offline static checks required by the infrastructure gate."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKFLOW = ROOT / ".github/workflows/infra-tests.yml"
# Keep this list explicit: the infra tree contains legacy probes with their own
# environments, while these modules are the local gate's implementation seam.
DEFAULT_SCOPE = (
    "infra/scripts/run_infra_quality.py",
    "infra/scripts/run_infra_tests.py",
    "infra/scripts/run_legacy_regressions.py",
    "infra/scripts/infra_test_selection.py",
    "infra/scripts/legacy_phase_contract.py",
    "infra/scripts/docker_ownership.py",
    "infra/scripts/evidence_bundle.py",
    "infra/scripts/prepare_legacy_stand.py",
    "infra/scripts/verify_legacy_preflight_refusals.py",
    "infra/scripts/qualification_resources.py",
    "infra/scripts/qualification_processes.py",
    "infra/scripts/run_legacy_qualification.py",
)
RUFF_SCOPE = DEFAULT_SCOPE + (
    "infra/scripts/tests/test_evidence_bundle.py",
    "infra/scripts/tests/test_infra_quality.py",
    "infra/scripts/tests/test_infra_test_runner.py",
    "infra/scripts/tests/test_infra_ci_workflow.py",
    "infra/scripts/tests/test_prepare_legacy_stand.py",
    "infra/scripts/tests/test_legacy_phase_contract.py",
    "infra/scripts/tests/test_legacy_preflight.py",
    "infra/scripts/tests/test_legacy_preflight_contract.py",
    "infra/scripts/tests/test_legacy_preflight_refusals.py",
    "infra/scripts/tests/test_legacy_qualification.py",
    "infra/scripts/tests/test_legacy_cleanup_configuration.py",
    "infra/scripts/tests/test_legacy_regression_runtime_paths.py",
    "infra/scripts/tests/test_qualification_resources.py",
    "infra/scripts/tests/test_qualification_processes.py",
)


def _tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"required offline tool is missing: {name}")
    return path


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def run(*, workflow: Path = DEFAULT_WORKFLOW, scope: tuple[str, ...] = DEFAULT_SCOPE) -> int:
    """Run each check and emit one machine-readable receipt on stdout."""
    commands = [
        [
            _tool("ruff"),
            "check",
            "--no-cache",
            *RUFF_SCOPE,
        ],
        [
            _tool("mypy"),
            "--config-file",
            "infra/mypy.ini",
            "--ignore-missing-imports",
            "--explicit-package-bases",
            "--cache-dir",
            "/tmp/infra-mypy-cache",
            *scope,
        ],
        [_tool("actionlint"), str(workflow.relative_to(ROOT))],
    ]
    checks: list[dict[str, object]] = []
    for command in commands:
        completed = _run(command, cwd=ROOT)
        checks.append(
            {
                "name": Path(command[0]).name,
                "command": command,
                "exit": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "status": "PASS" if completed.returncode == 0 else "FAIL",
            }
        )
        if completed.returncode:
            print(json.dumps({"status": "FAIL", "checks": checks}, indent=2))
            return completed.returncode
    print(json.dumps({"status": "PASS", "checks": checks}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    args = parser.parse_args(argv)
    try:
        return run(workflow=args.workflow)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED_TOOLING", "error": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
