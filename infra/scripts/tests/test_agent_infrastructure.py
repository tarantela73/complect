from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def compose_config() -> dict:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(PROJECT_ROOT / "docker-compose.dev.yml"),
            "config",
            "--format",
            "json",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


class AgentInfrastructureTests(unittest.TestCase):
    def test_credentials_are_files_and_shared_runtime_volume_is_read_only(self):
        services = compose_config()["services"]
        for name in ("backend", "celery_worker", "celery_beat"):
            service = services[name]
            env = service["environment"]
            self.assertNotIn("SECRET_KEY", env)
            self.assertNotIn("EXPECTED_GATEWAY_TOKEN", env)
            self.assertEqual(
                env["DATABASE_PASSWORD_FILE"],
                "/run/complect-secrets/app_postgres_password",
            )
            self.assertEqual(
                env["DJANGO_SECRET_KEY_FILE"], "/run/complect-secrets/django_secret_key"
            )
            self.assertEqual(
                env["EXPECTED_GATEWAY_TOKEN_FILE"],
                "/run/complect-secrets/app_gateway_token",
            )
        for name in (
            "backend",
            "celery_worker",
            "celery_beat",
            "postgres_primary",
            "postgres_replica",
            "minio",
            "api_gateway",
        ):
            mount = next(
                v
                for v in services[name]["volumes"]
                if v["target"] == "/run/complect-secrets"
            )
            self.assertTrue(mount["read_only"], name)

    def test_startup_does_not_apply_migrations_or_reconcile_replication_authority(self):
        services = compose_config()["services"]
        for name in ("backend", "celery_worker", "celery_beat"):
            command = " ".join(services[name]["command"])
            self.assertIn("config.dev_database ready", command)
            self.assertNotIn("manage.py migrate", command)
            self.assertNotIn("config.dev_database apply", command)
        self.assertNotIn("postgres_replication_auth", services)
        replica = (PROJECT_ROOT / "infra/postgres/start_replica.sh").read_text()
        self.assertNotIn("rm -rf", replica)
        self.assertNotIn("ALTER ROLE", replica)
        self.assertIn("PG_VERSION", replica)

    def test_gateway_token_is_loaded_by_the_pre_function(self):
        gateway = compose_config()["services"]["api_gateway"]
        self.assertNotIn("EXPECTED_GATEWAY_TOKEN", gateway["environment"])
        declarative = (PROJECT_ROOT / "infra/api_gateway/kong.yml").read_text()
        self.assertIn(
            'kong.vault.get("{vault://env/EXPECTED_GATEWAY_TOKEN}")', declarative
        )
        self.assertIn(
            'kong.service.request.set_header("X-Gateway-Token", gateway_token)',
            declarative,
        )
        self.assertNotIn("name: request-transformer", declarative)
        self.assertNotIn("complect_internal_secure_mesh_token_2026", declarative)

    def test_bind_mounts_are_shared_labelled_and_system_users_are_preserved(self):
        services = compose_config()["services"]
        for name, service in services.items():
            for mount in service.get("volumes", []):
                if mount["type"] == "bind":
                    self.assertEqual(
                        mount.get("bind", {}).get("selinux"),
                        "z",
                        (name, mount["target"]),
                    )
        for name in ("postgres_primary", "postgres_replica", "redis", "minio"):
            self.assertNotIn("user", services[name])
            self.assertFalse(services[name].get("privileged", False))


if __name__ == "__main__":
    unittest.main()
