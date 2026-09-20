from __future__ import annotations

import json
import ipaddress
import os
import shlex
import subprocess
import tempfile
import shutil
import unittest
from pathlib import Path

import yaml

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
        # Rendering only: no Docker socket/network creation or shared checkout allocation.
        env=dict(os.environ, COMPLECT_DOCKER_SUBNET="198.18.16.0/24"),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


class AgentInfrastructureTests(unittest.TestCase):
    def test_iam_ci_prepares_schema_before_backend_boot(self):
        workflow = yaml.safe_load(
            (PROJECT_ROOT / ".github/workflows/iam-ci.yml").read_text()
        )
        steps = workflow["jobs"]["gate"]["steps"]
        commands = []
        for index, step in enumerate(steps):
            for line in step.get("run", "").replace("\\\n", " ").splitlines():
                tokens = shlex.split(line, comments=True) if line.strip().startswith("docker compose ") and "--entrypoint" not in line else []
                if tokens[:4] == ["docker", "compose", "-f", "docker-compose.dev.yml"]:
                    commands.append((index, tokens[4:]))
        applies = [(i, args) for i, args in commands
                   if args == ["run", "--rm", "--no-deps", "migrate"]]
        self.assertEqual(len(applies), 1, "Exactly one canonical migration apply is required before backend")
        apply_index = applies[0][0]
        ups = [(i, args) for i, args in commands if args[0] == "up"]
        self.assertEqual(len(ups), 2)
        infra_index, infra = ups[0]
        backend_index, backend = ups[1]
        self.assertNotIn("backend", infra)
        for service in ("postgres_primary", "postgres_replica", "redis", "minio", "clamav"):
            self.assertIn(service, infra)
        self.assertIn("--wait", infra)
        self.assertIn("backend", backend)
        ownership = [(i, args) for i, args in commands
                     if args == ["run", "--rm", "--no-deps", "volume_init"]]
        self.assertEqual(len(ownership), 1,
                         "Fresh root-owned dev_state prevents migrate from writing its receipt")
        self.assertLess(infra_index, ownership[0][0])
        self.assertLess(ownership[0][0], apply_index)
        self.assertLess(infra_index, apply_index)
        self.assertLess(apply_index, backend_index)
        metadata_index = next(i for i, step in enumerate(steps)
                              if step["name"] == "Validate runtime secret metadata")
        self.assertLess(metadata_index, infra_index)
        ready_index = next(i for i, step in enumerate(steps)
                           if step["name"] == "Wait for schema and replica readiness")
        self.assertLess(apply_index, ready_index)
        self.assertLess(ready_index, backend_index)
        ready = steps[ready_index]["run"]
        self.assertIn("config.dev_database ready", ready)
        self.assertIn("--no-deps migrate", ready)
        self.assertIn("timeout", shlex.split(ready))
        self.assertIn("180s", shlex.split(ready))
        self.assertIn("set -euo pipefail", ready)
        self.assertGreater(steps[apply_index]["timeout-minutes"], 0)
        for step in steps:
            self.assertNotIn("manage.py migrate", step.get("run", ""))
            self.assertNotIn("--fake", step.get("run", ""))

    def test_iam_ci_ready_retries_and_fails_closed_on_timeout(self):
        workflow = yaml.safe_load(
            (PROJECT_ROOT / ".github/workflows/iam-ci.yml").read_text()
        )
        step = next(step for step in workflow["jobs"]["gate"]["steps"]
                    if step["name"] == "Wait for schema and replica readiness")
        # Exercise the actual workflow shell with a controlled readiness command.
        # Shorten only the wall-clock deadline; retain real timeout exit semantics.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docker = root / "docker"
            docker.write_text("""#!/bin/sh
printf '%s\\n' "$*" >> "$PROBE_LOG"
count=$(wc -l < "$PROBE_LOG")
[ "$count" -ge "$READY_AFTER" ]
""")
            docker.chmod(0o755)
            (root / "timeout").write_text(
                "#!/bin/sh\nshift 3\nexec " + shutil.which("timeout") + " 0.3s \"$@\"\n"
            )
            (root / "timeout").chmod(0o755)
            (root / "sleep").write_text("#!/bin/sh\nexec /bin/sleep 0.01\n")
            (root / "sleep").chmod(0o755)
            for ready_after, expected in ((3, 0), (10000, 124)):
                with self.subTest(ready_after=ready_after):
                    log = root / "probes"
                    log.unlink(missing_ok=True)
                    result = subprocess.run(
                        ["bash", "-c", step["run"]], capture_output=True, text=True,
                        timeout=5, env=dict(os.environ, PATH=f"{root}:{os.environ['PATH']}",
                                            PROBE_LOG=str(log), READY_AFTER=str(ready_after)),
                    )
                    self.assertEqual(result.returncode, expected, result.stderr)
                    probes = log.read_text().splitlines()
                    self.assertGreaterEqual(len(probes), 2)
                    for probe in probes:
                        self.assertEqual(shlex.split(probe), [
                            "compose", "-f", "docker-compose.dev.yml", "run", "--rm",
                            "--no-deps", "migrate", "python", "-B", "-m",
                            "config.dev_database", "ready",
                        ])

    def test_iam_ci_materializes_canonical_runtime_secrets_before_boot(self):
        workflow = yaml.safe_load(
            (PROJECT_ROOT / ".github/workflows/iam-ci.yml").read_text()
        )
        steps = workflow["jobs"]["gate"]["steps"]
        names = [step["name"] for step in steps]

        generate_index = names.index("Generate disposable canonical secrets")
        owners_index = names.index("Resolve runtime secret owners")
        materialize_index = names.index("Materialize runtime secrets")
        metadata_index = names.index("Validate runtime secret metadata")
        boot_index = names.index("Build backend and boot infrastructure")
        self.assertLess(generate_index, owners_index)
        self.assertLess(owners_index, materialize_index)
        self.assertLess(materialize_index, metadata_index)
        self.assertLess(metadata_index, boot_index)

        generate = steps[generate_index]["run"]
        canonical_names = (
            "django_secret_key",
            "postgres_password",
            "replication_password",
            "minio_password",
            "gateway_token",
        )
        self.assertIn('install -d -m 700 ".dev/secrets"', generate)
        for name in canonical_names:
            self.assertIn(name, generate)
        self.assertNotIn("infra/secrets", generate)
        self.assertNotIn("dev_db_password.txt", generate)
        self.assertNotIn("dev_s3_password.txt", generate)
        self.assertNotIn("dev_replication_password.txt", generate)
        self.assertNotIn("GITHUB_ENV", generate)
        self.assertNotIn("add-mask", generate)

        owners = steps[owners_index]["run"]
        for service in ("postgres_primary", "api_gateway", "minio"):
            self.assertIn(service, owners)
        self.assertNotIn("postgis/postgis:16-3.4", owners)
        self.assertNotIn("quay.io/minio/minio", owners)
        for name in (
            "DEV_POSTGRES_UID",
            "DEV_POSTGRES_GID",
            "DEV_MINIO_UID",
            "DEV_MINIO_GID",
            "DEV_KONG_UID",
            "DEV_KONG_GID",
            "DEV_UID",
            "DEV_GID",
        ):
            self.assertIn(name, owners)
        self.assertIn("docker run --rm --network none --entrypoint id", owners)

        materialize = steps[materialize_index]["run"]
        self.assertIn(
            "docker compose -f docker-compose.dev.yml run --rm --no-deps secrets_init",
            materialize,
        )
        self.assertNotIn("runtime_secrets.py", materialize)
        self.assertNotIn("sudo chown", materialize)

        metadata = steps[metadata_index]["run"]
        self.assertIn("docker compose -f docker-compose.dev.yml run --rm --no-deps", metadata)
        self.assertIn("secrets_init", metadata)
        self.assertIn("stat -c", metadata)
        self.assertIn("0400", metadata)
        self.assertNotIn("cat /run/complect-secrets", metadata)
        self.assertNotIn("runtime_secrets.py", metadata)
        for name in (
            "postgres_password",
            "replication_password",
            "minio_password",
            "django_secret_key",
            "app_postgres_password",
            "app_gateway_token",
            "gateway_token",
        ):
            self.assertIn(name, metadata)

        cleanup = next(step["run"] for step in steps if step["name"] == "Remove disposable resources")
        self.assertIn("rm -rf -- .dev/secrets", cleanup)
        self.assertNotIn("infra/secrets", cleanup)

    def test_iam_ci_minio_probe_uses_rendered_compose_image(self):
        workflow = yaml.safe_load(
            (PROJECT_ROOT / ".github/workflows/iam-ci.yml").read_text()
        )
        step = next(
            step
            for step in workflow["jobs"]["gate"]["steps"]
            if step["name"] == "Resolve runtime secret owners"
        )
        command = step["run"]

        self.assertNotIn("minio/minio:latest", command)
        self.assertNotIn("quay.io/minio/minio", command)
        self.assertIn(
            '("MINIO_IMAGE", "minio")', command,
        )
        self.assertIn('services = json.load(sys.stdin)["services"]', command)
        self.assertIn('image = services[service].get("image")', command)
        self.assertIn('docker run --rm --network none --entrypoint id "$MINIO_IMAGE" -u', command)
        self.assertIn('docker run --rm --network none --entrypoint id "$MINIO_IMAGE" -g', command)

    def test_iam_ci_compose_steps_inherit_explicit_disposable_subnet(self):
        workflow = yaml.safe_load(
            (PROJECT_ROOT / ".github/workflows/iam-ci.yml").read_text()
        )
        checked_steps = []
        for job_name, job in workflow["jobs"].items():
            for step in job["steps"]:
                tokens = shlex.split(step.get("run", ""), comments=True)
                if not any(tokens[i:i + 2] == ["docker", "compose"]
                           for i in range(len(tokens) - 1)):
                    continue
                if "docker-compose.dev.yml" not in tokens:
                    continue
                checked_steps.append(step["name"])
                with self.subTest(job=job_name, step=step["name"]):
                    # A fixed disposable subnet is safe only on a fresh hosted runner.
                    self.assertEqual(job["runs-on"], "ubuntu-24.04")
                    effective_env = {
                        **workflow.get("env", {}),
                        **job.get("env", {}),
                        **step.get("env", {}),
                    }
                    subnet = effective_env.get("COMPLECT_DOCKER_SUBNET")
                    self.assertTrue(subnet, "Compose requires an explicit CI subnet")
                    network = ipaddress.ip_network(subnet, strict=True)
                    self.assertEqual(network.version, 4)
                    self.assertEqual(network.prefixlen, 24)
                    self.assertTrue(network.subnet_of(ipaddress.ip_network("198.19.0.0/16")))
                    # Ignore developer Compose settings; substitute only the Actions project ID.
                    environment = {key: value for key, value in os.environ.items()
                                   if not key.startswith("COMPOSE_")
                                   and key != "COMPLECT_DOCKER_SUBNET"}
                    environment.update(effective_env)
                    environment["COMPOSE_PROJECT_NAME"] = "iam-ci-subnet-regression"
                    result = subprocess.run(
                        ["docker", "compose", "-f", "docker-compose.dev.yml",
                         "config", "--format", "json"],
                        cwd=PROJECT_ROOT, env=environment, capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        json.loads(result.stdout)["networks"]["erp_network"]["ipam"]["config"],
                        [{"subnet": subnet}],
                    )
        self.assertTrue(checked_steps, "No IAM CI Compose steps were checked")

    def test_rendered_network_has_explicit_subnet(self):
        self.assertEqual(
            compose_config()["networks"]["erp_network"]["ipam"]["config"][0]["subnet"],
            "198.18.16.0/24",
        )

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
