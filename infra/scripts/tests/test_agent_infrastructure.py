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
            "--profile",
            "tools",
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
    def test_minio_server_uses_reviewed_upstream_release_and_index(self):
        self.assertEqual(
            compose_config()["services"]["minio"]["image"],
            "quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z"
            "@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e",
        )

    def test_minio_client_uses_reviewed_upstream_release_and_index(self):
        self.assertEqual(
            compose_config()["services"]["s3_bootstrap"]["image"],
            "quay.io/minio/mc:RELEASE.2025-08-13T08-35-41Z"
            "@sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727",
        )

    def test_minio_runtime_contract_is_preserved(self):
        services = compose_config()["services"]
        server = services["minio"]
        self.assertEqual(
            [(p["host_ip"], str(p["published"]), p["target"]) for p in server["ports"]],
            [("127.0.0.1", "9000", 9000), ("127.0.0.1", "9001", 9001)],
        )
        self.assertEqual(server["command"], ["server", "/data", "--console-address", ":9001"])
        self.assertEqual(server["healthcheck"], {
            "test": ["CMD", "curl", "-fsS", "--connect-timeout", "1", "--max-time", "2",
                     "http://localhost:9000/minio/health/live"],
            "interval": "3s", "timeout": "3s", "retries": 30,
        })
        client = services["s3_bootstrap"]
        self.assertEqual(client["entrypoint"], ["/bin/sh", "-ec"])
        self.assertEqual(client["command"], [
            'mc alias set local http://minio:9000 complect_root_user '
            '"$$(cat /run/complect-secrets/minio_password)" >/dev/null; '
            'mc mb --ignore-existing local/complect-quarantine; '
            'mc mb --ignore-existing local/complect-production'
        ])

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
