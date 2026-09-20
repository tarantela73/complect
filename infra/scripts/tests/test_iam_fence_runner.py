from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNNER = PROJECT_ROOT / "infra/scripts/run_iam_fence_tests.sh"
WORKFLOW = PROJECT_ROOT / ".github/workflows/iam-ci.yml"


class IamFenceRunnerContractTests(unittest.TestCase):
    def _run_runner(
        self,
        *,
        ci: bool,
        project: str = "iam-ci-123-1",
        label_project: str = "iam-ci-123-1",
        label_service: str = "backend",
        network: str = "iam-ci-123-1_erp_network",
        subnet: str = "198.19.0.0/24",
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "commands.log"
            docker = root / "docker"
            docker.write_text(
                """#!/bin/sh
printf 'docker %s\n' "$*" >> "$FAKE_LOG"
case "$1" in
  compose)
    for argument in "$@"; do
      if [ "$argument" = ps ]; then
        printf 'backend-id\n'
        exit 0
      fi
    done
    ;;
  inspect)
    case "$*" in
      *Config.Labels*) printf '%s:%s\n' "$FAKE_LABEL_PROJECT" "$FAKE_LABEL_SERVICE" ;;
      *NetworkSettings.Networks*) printf '%s\n' "$FAKE_NETWORK" ;;
    esac
    ;;
  network)
    printf '%s:erp_network\n' "$FAKE_LABEL_PROJECT"
    ;;
  run)
    printf 'isolated-redis-id\n'
    ;;
esac
"""
            )
            docker.chmod(0o755)
            python = root / "python3"
            python.write_text(
                """#!/bin/sh
printf 'python3 %s\n' "$*" >> "$FAKE_LOG"
printf 'backend-id\n'
"""
            )
            python.chmod(0o755)
            environment = dict(os.environ)
            environment.update(
                {
                    "PATH": f"{root}:{environment['PATH']}",
                    "FAKE_LOG": str(log),
                    "FAKE_LABEL_PROJECT": label_project,
                    "FAKE_LABEL_SERVICE": label_service,
                    "FAKE_NETWORK": network,
                    "COMPOSE_PROJECT_NAME": project,
                    "COMPLECT_DOCKER_SUBNET": subnet,
                }
            )
            if ci:
                environment.update({"CI": "true", "GITHUB_ACTIONS": "true"})
            else:
                environment.pop("CI", None)
                environment.pop("GITHUB_ACTIONS", None)
            result = subprocess.run(
                ["bash", str(RUNNER), "apps/users/tests/test_identity_audit_fence.py"],
                cwd=PROJECT_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=10,
            )
            return result, log.read_text().splitlines() if log.exists() else []

    def test_default_invocation_remains_workstation_managed(self):
        result, commands = self._run_runner(ci=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "python3 -B infra/scripts/dev_environment.py compose ps -q backend",
            commands,
        )
        self.assertFalse(any("docker compose" in command for command in commands))

    def test_ci_invocation_uses_only_the_existing_iam_compose_project(self):
        result, commands = self._run_runner(ci=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(command.startswith("python3 ") for command in commands))
        self.assertIn(
            "docker compose -f docker-compose.dev.yml ps -q backend",
            commands,
        )
        self.assertTrue(
            any(
                "run --rm -d --name complect-iam-test-" in command
                and "--network iam-ci-123-1_erp_network" in command
                for command in commands
            )
        )
        self.assertTrue(any(command.startswith("docker rm -f ") for command in commands))

    def test_ci_invocation_rejects_a_backend_from_another_compose_project(self):
        result, commands = self._run_runner(ci=True, label_project="foreign-project")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not belong to the IAM CI Compose project", result.stderr)
        self.assertFalse(any(" run --rm -d " in command for command in commands))

    def test_ci_invocation_requires_the_hosted_project_and_subnet_contract(self):
        result, commands = self._run_runner(ci=True, project="developer-project")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid IAM CI Compose project", result.stderr)
        self.assertEqual(commands, [])

        result, commands = self._run_runner(ci=True, subnet="198.18.16.0/24")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid IAM CI disposable subnet", result.stderr)
        self.assertEqual(commands, [])

    def test_runner_preserves_disposable_database_and_redis_isolation(self):
        source = RUNNER.read_text()

        for required in (
            "test_redis_name=\"complect-iam-test-${test_run_id}\"",
            "REDIS_DEFAULT_URL",
            "database = \"test_iam_fence_\" + uuid.uuid4().hex",
            "SET statement_timeout = '60s'",
            "SET lock_timeout = '10s'",
            "--reuse-db",
            'DROP DATABASE \"{database}\" WITH (FORCE)',
            "docker rm -f",
        ):
            self.assertIn(required, source)

    def test_iam_ci_uses_the_same_bounded_runner_contract_for_both_suites(self):
        workflow = yaml.safe_load(WORKFLOW.read_text())
        steps = workflow["jobs"]["gate"]["steps"]
        critical = next(step for step in steps if step["name"] == "IAM fence critical tests")
        full = next(step for step in steps if step["name"] == "Full backend regression")

        self.assertEqual(
            critical["run"],
            "bash infra/scripts/run_iam_fence_tests.sh apps/users/tests/test_identity_audit_fence.py",
        )
        self.assertEqual(full["run"], "bash infra/scripts/run_iam_fence_tests.sh apps --maxfail=10")
        self.assertRegex(workflow["jobs"]["gate"]["env"]["COMPOSE_PROJECT_NAME"], r"iam-ci-")
        self.assertEqual(workflow["jobs"]["gate"]["env"]["COMPLECT_DOCKER_SUBNET"], "198.19.0.0/24")
        self.assertNotIn("@colbymchenry/codegraph", WORKFLOW.read_text())


if __name__ == "__main__":
    unittest.main()
