from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.dev.yml"


def compose_config(*, missing_required_variable: str | None = None) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["SECRET_KEY"] = "verification-only-secret"
    environment["EXPECTED_GATEWAY_TOKEN"] = "verification-only-gateway"
    if missing_required_variable is not None:
        environment.pop(missing_required_variable, None)
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--format", "json"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


class AgentInfrastructureTests(unittest.TestCase):
    def test_compose_requires_backend_credentials(self) -> None:
        for variable in ("SECRET_KEY", "EXPECTED_GATEWAY_TOKEN"):
            with self.subTest(variable=variable):
                result = compose_config(missing_required_variable=variable)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(variable, result.stderr)

    def test_supermemory_uses_a_dedicated_network(self) -> None:
        result = compose_config()
        self.assertEqual(result.returncode, 0, result.stderr)
        configuration = json.loads(result.stdout)

        supermemory_networks = set(configuration["services"]["supermemory"]["networks"])
        self.assertEqual(supermemory_networks, {"supermemory_network"})
        for service_name, service in configuration["services"].items():
            if service_name != "supermemory":
                self.assertNotIn("supermemory_network", service.get("networks", {}))

    def test_replication_password_is_one_docker_secret(self) -> None:
        result = compose_config()
        self.assertEqual(result.returncode, 0, result.stderr)
        configuration = json.loads(result.stdout)

        self.assertIn("replication_password", configuration["secrets"])
        for service_name in ("postgres_primary", "postgres_replica"):
            secret_sources = {
                secret["source"]
                for secret in configuration["services"][service_name]["secrets"]
            }
            self.assertIn("replication_password", secret_sources)
        self.assertNotIn(
            "PGPASSWORD",
            configuration["services"]["postgres_replica"].get("environment", {}),
        )

    def test_gateway_token_is_loaded_from_the_environment_vault(self) -> None:
        result = compose_config()
        self.assertEqual(result.returncode, 0, result.stderr)
        configuration = json.loads(result.stdout)

        gateway_environment = configuration["services"]["api_gateway"]["environment"]
        self.assertEqual(
            gateway_environment["EXPECTED_GATEWAY_TOKEN"],
            "verification-only-gateway",
        )

        declarative_config = (PROJECT_ROOT / "infra/api_gateway/kong.yml").read_text()
        self.assertIn(
            "X-Gateway-Token:{vault://env/EXPECTED_GATEWAY_TOKEN}",
            declarative_config,
        )
        self.assertNotIn("complect_internal_secure_mesh_token_2026", declarative_config)


if __name__ == "__main__":
    unittest.main()
