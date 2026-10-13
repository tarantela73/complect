"""Public CLI acceptance tests for workstation setup."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "infra/scripts/dev_environment.py"


class ConfigureTests(unittest.TestCase):
    def test_isolated_e2e_configuration_is_explicit_and_cannot_reuse_dev_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.dev.example").write_text(
                "COMPOSE_PROJECT_NAME=__PROJECT__\nDEV_UID=__UID__\nDEV_GID=__GID__\n"
            )
            command = [sys.executable, "-B", str(CLI), "configure", "--root", str(root)]
            isolated = subprocess.run(
                [*command, "--isolated-e2e"], text=True, capture_output=True
            )
            self.assertEqual(isolated.returncode, 0, isolated.stderr)
            self.assertIn("DEV_ISOLATED_E2E=true", (root / ".env.dev").read_text())
            before = (root / ".env.dev").read_bytes()
            repeated = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual((root / ".env.dev").read_bytes(), before)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.dev.example").write_text(
                "COMPOSE_PROJECT_NAME=__PROJECT__\nDEV_UID=__UID__\nDEV_GID=__GID__\n"
            )
            command = [sys.executable, "-B", str(CLI), "configure", "--root", str(root)]
            subprocess.run(command, check=True, capture_output=True)
            before = (root / ".env.dev").read_bytes()
            refused = subprocess.run(
                [*command, "--isolated-e2e"], text=True, capture_output=True
            )
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual((root / ".env.dev").read_bytes(), before)

    def test_isolated_e2e_refuses_orphaned_credentials_without_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_secret = root / ".dev/secrets/postgres_password"
            old_secret.parent.mkdir(parents=True)
            old_secret.write_bytes(b"existing-private-state")
            result = subprocess.run(
                [sys.executable, "-B", str(CLI), "configure", "--isolated-e2e", "--root", str(root)],
                text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing reuse", result.stderr)
            self.assertEqual(old_secret.read_bytes(), b"existing-private-state")
            self.assertFalse((root / ".env.dev").exists())

    def test_configure_preserves_secrets_and_never_prints_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.dev.example").write_text(
                "COMPOSE_PROJECT_NAME=__PROJECT__\nDEV_UID=__UID__\nDEV_GID=__GID__\n"
            )
            command = [sys.executable, "-B", str(CLI), "configure", "--root", str(root)]
            first = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            names = {
                "django_secret_key",
                "postgres_password",
                "replication_password",
                "minio_password",
                "gateway_token",
            }
            secrets = {
                name: (root / ".dev/secrets" / name).read_bytes() for name in names
            }
            self.assertTrue(all(len(value.strip()) >= 64 for value in secrets.values()))
            second = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            for name, value in secrets.items():
                self.assertEqual((root / ".dev/secrets" / name).read_bytes(), value)
                self.assertNotIn(
                    value.decode().strip(),
                    first.stdout + first.stderr + second.stdout + second.stderr,
                )
                self.assertEqual(
                    (root / ".dev/secrets" / name).stat().st_mode & 0o777, 0o600
                )
            self.assertEqual((root / ".dev/secrets").stat().st_mode & 0o777, 0o700)
            self.assertEqual((root / ".dev/external.env").read_text(), "")
            self.assertNotIn("__PROJECT__", (root / ".env.dev").read_text())


class DoctorTests(unittest.TestCase):
    def test_isolated_ports_do_not_touch_occupied_canonical_listener(self):
        import socket
        from infra.scripts import dev_environment

        config = {"services": {name: {"ports": []} for name in dev_environment.PORTS}}
        with socket.socket() as foreign_listener:
            foreign_listener.bind(("127.0.0.1", 15432))
            foreign_listener.listen()
            dev_environment.validate_ports(
                config, {}, require_published=False, isolated_e2e=True
            )
            with socket.create_connection(("127.0.0.1", 15432)):
                connection, _ = foreign_listener.accept()
                connection.close()
            config["services"]["postgres_primary"]["ports"] = [
                {"published": "15432", "host_ip": "127.0.0.1"}
            ]
            with self.assertRaisesRegex(dev_environment.DevError, "must not publish"):
                dev_environment.validate_ports(
                    config, {}, require_published=False, isolated_e2e=True
                )

    def test_foreign_historical_artifacts_do_not_block_doctor_ownership_check(self):
        from infra.scripts import dev_environment

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            historical = root / ".dev/test-artifacts/iam-rehearsal"
            historical.mkdir(parents=True)
            (historical / "evidence.json").write_text("{}")
            dev_environment.validate_e2e_artifact_ownership(root, owner_lookup=lambda _: 65534)

    def test_foreign_active_e2e_artifact_fails_ownership_check(self):
        from infra.scripts import dev_environment

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / ".dev/test-artifacts/e2e"
            active.mkdir(parents=True)
            (active / "results.json").write_text("{}")
            with self.assertRaisesRegex(dev_environment.DevError, "Active E2E test artifact"):
                dev_environment.validate_e2e_artifact_ownership(root, owner_lookup=lambda _: 65534)

    def test_empty_checkout_fails_without_creating_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sentinel").write_bytes(b"existing-checkout-state")
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            result = subprocess.run(
                [sys.executable, "-B", str(CLI), "doctor", "--root", str(root)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("FAIL", result.stdout + result.stderr)
            self.assertIn("make bootstrap", result.stdout + result.stderr)
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)


class RuntimeSecretTests(unittest.TestCase):
    def test_runtime_distribution_never_rotates_an_existing_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "runtime"
            source.mkdir(mode=0o700)
            values = {
                name: (name + "x" * 80).encode()
                for name in (
                    "django_secret_key",
                    "postgres_password",
                    "replication_password",
                    "minio_password",
                    "gateway_token",
                )
            }
            for name, value in values.items():
                (source / name).write_bytes(value)
                (source / name).chmod(0o600)
            command = [
                sys.executable,
                "-B",
                str(ROOT / "infra/scripts/runtime_secrets.py"),
                "--source",
                str(source),
                "--target",
                str(target),
                "--postgres-uid",
                str(os.getuid()),
                "--postgres-gid",
                str(os.getgid()),
                "--kong-uid",
                str(os.getuid()),
                "--kong-gid",
                str(os.getgid()),
                "--minio-uid",
                str(os.getuid()),
                "--minio-gid",
                str(os.getgid()),
            ]
            first = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            for name, value in values.items():
                self.assertEqual((target / name).read_bytes(), value)
                self.assertEqual((target / name).stat().st_mode & 0o777, 0o400)
                self.assertNotIn(value.decode(), first.stdout + first.stderr)
            second = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            (source / "postgres_password").write_bytes(b"changed" * 20)
            refused = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(refused.returncode, 1)
            self.assertIn("refusing to rotate", refused.stdout + refused.stderr)
            self.assertEqual(
                (target / "postgres_password").read_bytes(), values["postgres_password"]
            )


class SecretOwnerTests(unittest.TestCase):
    def test_app_and_postgres_readers_have_separate_owned_secret_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir(mode=0o700)
            for name in (
                "django_secret_key",
                "postgres_password",
                "replication_password",
                "minio_password",
                "gateway_token",
            ):
                (source / name).write_text(name + "x" * 80)
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "infra/scripts/runtime_secrets.py"),
                    "--source",
                    str(source),
                    "--target",
                    str(root / "runtime"),
                    "--postgres-uid",
                    "999",
                    "--postgres-gid",
                    "999",
                    "--kong-uid",
                    "1000",
                    "--kong-gid",
                    "1000",
                    "--minio-uid",
                    "0",
                    "--minio-gid",
                    "0",
                    "--app-uid",
                    "2000",
                    "--app-gid",
                    "2000",
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / "runtime/postgres_password").stat().st_uid, 999)
            self.assertEqual(
                (root / "runtime/app_postgres_password").stat().st_uid, 2000
            )
            self.assertEqual((root / "runtime/django_secret_key").stat().st_uid, 2000)
            self.assertEqual(
                (root / "runtime/app_postgres_password").read_bytes(),
                (source / "postgres_password").read_bytes(),
            )
            self.assertEqual((source / "postgres_password").stat().st_uid, os.getuid())


class BootstrapPrerequisiteTests(unittest.TestCase):
    def test_missing_host_tool_fails_before_creating_local_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = dict(os.environ, PATH=str(root))
            result = subprocess.run(
                [sys.executable, "-B", str(CLI), "bootstrap", "--root", str(root)],
                env=environment,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn(
                "Missing required host tool: docker", result.stdout + result.stderr
            )
            self.assertEqual(list(root.iterdir()), [])


class CheckoutBoundaryTests(unittest.TestCase):
    def test_sync_refuses_a_private_state_symlink_before_touching_another_checkout(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "checkout"
            other = base / "other"
            root.mkdir()
            other.mkdir()
            (root / ".env.dev.example").write_text(
                "COMPOSE_PROJECT_NAME=__PROJECT__\nDEV_UID=__UID__\nDEV_GID=__GID__\n"
            )
            subprocess.run(
                [sys.executable, "-B", str(CLI), "configure", "--root", str(root)],
                check=True,
                capture_output=True,
            )
            (root / ".dev").rename(other / "state")
            (root / ".dev").symlink_to(other / "state", target_is_directory=True)
            before = {
                str(p.relative_to(other)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in other.rglob("*")
                if p.is_file()
            }
            bin_path = base / "bin"
            bin_path.mkdir()
            for name in ("git", "bash", "make", "jq"):
                (bin_path / name).write_text("#!/bin/sh\nexit 0\n")
                (bin_path / name).chmod(0o755)
            (bin_path / "codegraph").write_text("#!/bin/sh\necho 1.6.0\n")
            (bin_path / "codegraph").chmod(0o755)
            (bin_path / "docker").write_text(
                '#!/bin/sh\ncase "$1" in context) echo unix:///fixture.sock;; info) echo linux;; *) exit 89;; esac\n'
            )
            (bin_path / "docker").chmod(0o755)
            result = subprocess.run(
                [sys.executable, "-B", str(CLI), "sync", "--root", str(root)],
                env=dict(os.environ, PATH=str(bin_path)),
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 1)
            after = {
                str(p.relative_to(other)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in other.rglob("*")
                if p.is_file()
            }
            self.assertEqual(after, before)
            self.assertIn("Unsafe private directory", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()


class DockerContextTests(unittest.TestCase):
    def test_selected_remote_context_overrides_local_host_and_is_rejected_before_info(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / "bin"
            tools.mkdir()
            for name in ("git", "bash", "make", "jq"):
                (tools / name).write_text("#!/bin/sh\nexit 0\n")
                (tools / name).chmod(0o755)
            (tools / "codegraph").write_text("#!/bin/sh\necho 1.6.0\n")
            (tools / "codegraph").chmod(0o755)
            (tools / "docker").write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TRACE_FILE"\n'
                'case "$1" in context) echo ssh://remote-host;; info) echo linux;; *) exit 89;; esac\n'
            )
            (tools / "docker").chmod(0o755)
            trace = root / "trace"
            result = subprocess.run(
                [sys.executable, "-B", str(CLI), "bootstrap", "--root", str(root)],
                env=dict(
                    os.environ,
                    PATH=str(tools),
                    DOCKER_HOST="unix:///fixture.sock",
                    DOCKER_CONTEXT="remote-context",
                    TRACE_FILE=str(trace),
                ),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("remote deployment contexts are not allowed", result.stderr)
            self.assertIn("context inspect remote-context", trace.read_text())
            self.assertNotIn("info", trace.read_text())
            self.assertFalse((root / ".dev").exists())


class OneShotTimeoutTests(unittest.TestCase):
    def test_timeout_stops_only_the_label_verified_one_shot_container(self):
        import importlib.util
        import json
        from unittest.mock import patch

        spec = importlib.util.spec_from_file_location(
            "dev_environment_timeout_test", CLI
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env.dev.example").write_text(
                "COMPOSE_PROJECT_NAME=__PROJECT__\nDEV_UID=__UID__\nDEV_GID=__GID__\n"
            )
            module.configure(root)
            commands = module.Commands(root)
            called = []
            labels = {
                "com.docker.compose.project": commands.project,
                "complect.checkout_id": commands.values["DEV_CHECKOUT_ID"],
            }

            def execute(argv, **kwargs):
                called.append(argv)
                if "compose" in argv and "run" in argv:
                    raise subprocess.TimeoutExpired(argv, 1)
                if argv[1] == "inspect":
                    body = [
                        {
                            "Id": "fixture-owned-container-id",
                            "Config": {"Labels": labels},
                        }
                    ]
                    return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")
                return subprocess.CompletedProcess(argv, 0, "", "")

            with patch.object(module.subprocess, "run", side_effect=execute):
                with self.assertRaises(module.DevError):
                    commands.compose(
                        "run",
                        "--rm",
                        "--no-deps",
                        "frontend_deps",
                        write=True,
                        timeout=1,
                    )
            self.assertTrue(
                any(
                    c[:2] == ["docker", "stop"]
                    and c[-1] == "fixture-owned-container-id"
                    for c in called
                )
            )
            self.assertFalse(any("prune" in c for c in called))

            for key in tuple(labels):
                original = labels[key]
                labels[key] = "another-checkout"
                called.clear()
                with patch.object(module.subprocess, "run", side_effect=execute):
                    with self.assertRaises(module.DevError):
                        commands.compose(
                            "run",
                            "--rm",
                            "--no-deps",
                            "frontend_deps",
                            write=True,
                            timeout=1,
                        )
                self.assertFalse(
                    any(c[:2] in (["docker", "stop"], ["docker", "rm"]) for c in called)
                )
                labels[key] = original


class ExplicitNetworkContractTests(unittest.TestCase):
    def test_canonical_ipv4_subnet_and_reserved_zones(self):
        from infra.scripts.docker_network import NetworkError, validate_subnet

        self.assertEqual(validate_subnet("198.18.16.0/24"), "198.18.16.0/24")
        self.assertEqual(validate_subnet("192.0.2.0/24"), "192.0.2.0/24")
        for value in ("", "bad", "2001:db8::/24", "198.18.16.0/25",
                      "198.18.16.1/24", "198.18.0.0/24", "198.18.128.0/24",
                      "198.19.42.0/24"):
            with self.subTest(value=value), self.assertRaises(NetworkError):
                validate_subnet(value)

    def test_deterministic_cyclic_pool_and_inventory_overlap(self):
        from infra.scripts.docker_network import candidates, overlapping

        sequence = candidates("abc", kind="dev")
        self.assertEqual(sequence[0], "198.18.29.0/24")
        self.assertEqual(sequence[3], "198.18.16.0/24")
        self.assertEqual(len(set(sequence)), 16)
        networks = [{"Name": "other", "IPAM": {"Config": [{"Subnet": "198.18.28.0/23"}]}}]
        self.assertTrue(overlapping(sequence[0], networks))
        self.assertFalse(overlapping(sequence[1], networks))


class CheckoutNetworkTests(unittest.TestCase):
    def test_down_removes_only_project_orphans(self):
        self.commands.compose("down", write=True)
        calls = [c for c in self.calls if "compose" in c]
        self.assertEqual(len(calls), 1)
        for profile in ("tools", "real-files", "observability"):
            self.assertIn(profile, calls[0])
        self.assertEqual(calls[0][-1], "--remove-orphans")

    def test_main_routes_down_through_project_cleanup_wrapper(self):
        from unittest.mock import patch

        with patch.object(self.dev, "prerequisites"), \
             patch.object(self.dev, "source_digest", return_value="test"), \
             patch.object(self.dev.Commands, "own_resources"), \
             patch.object(self.dev.Commands, "configuration", return_value={}), \
             patch.object(self.dev.Commands, "compose", return_value=subprocess.CompletedProcess([], 0, "", "")) as compose, \
             patch.object(sys, "argv", [str(CLI), "compose", "--root", str(self.root), "down"]):
            self.assertEqual(self.dev.main(), 0)
        compose.assert_called_once_with("down", write=True, timeout=3600)

    def test_make_up_runs_shared_schema_stage_before_application(self):
        from unittest.mock import patch

        events = []
        with patch.object(self.dev, "prerequisites"), \
             patch.object(self.dev, "source_digest", return_value="test"), \
             patch.object(self.dev.Commands, "own_resources"), \
             patch.object(self.dev.Commands, "configuration", return_value={}), \
             patch.object(self.dev, "ensure_mount_directories"), \
             patch.object(self.dev, "build_backend"), \
             patch.object(self.dev, "runtime_owners"), \
             patch.object(self.dev, "prepare_database_startup", side_effect=lambda _: events.append("schema")), \
             patch.object(self.dev.Commands, "compose", side_effect=lambda *a, **k: (events.append("application") or subprocess.CompletedProcess([], 0, "", ""))), \
             patch.object(sys, "argv", [str(CLI), "compose", "--root", str(self.root), "up", "--build", "-d"]):
            self.assertEqual(self.dev.main(), 0)
        self.assertEqual(events, ["schema", "application"])

    def test_dev_auth_origins_include_loopback_browser(self):
        compose = (ROOT / "docker-compose.dev.yml").read_text()
        self.assertIn("CORS_ALLOWED_ORIGINS: http://localhost:8080,http://127.0.0.1:8080", compose)
        self.assertIn("CSRF_TRUSTED_ORIGINS: http://localhost:8080,http://127.0.0.1:8080", compose)
        self.assertIn("AUTH_TRUSTED_ORIGINS: http://localhost:8080,http://127.0.0.1:8080", compose)

    def setUp(self):
        import json
        from unittest.mock import patch
        from infra.scripts import dev_environment as dev

        self.dev = dev
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / ".env.dev.example").write_bytes((ROOT / ".env.dev.example").read_bytes())
        dev.configure(self.root)
        self.commands = dev.Commands(self.root)
        self.networks = []
        self.calls = []
        self.environments = []
        self.race = False
        self.failure = False

        def execute(argv, **kwargs):
            self.calls.append(argv)
            env = dict(kwargs.get("env", {}))
            self.environments.append(env)
            body, code = "", 0
            if argv[:4] == ["docker", "network", "ls", "-q"]:
                body = " ".join(n["Name"] for n in self.networks)
            elif argv[:3] == ["docker", "network", "inspect"]:
                body = json.dumps([n for n in self.networks if n["Name"] in argv[3:]])
            elif "compose" in argv:
                subnet = env.get("COMPLECT_DOCKER_SUBNET")
                if "config" in argv:
                    body = json.dumps({"networks": {"erp_network": {"ipam": {"config": [{"subnet": subnet}]}}}})
                elif any(command in argv for command in ("up", "run", "create", "scale")):
                    if self.race:
                        self.race = False
                        self.networks.append(self.network("racer", subnet))
                        return subprocess.CompletedProcess(argv, 1, "", "Pool overlaps with other one on this address space")
                    if self.failure:
                        return subprocess.CompletedProcess(argv, 1, "", "permission denied")
                    if not any(n["Name"] == self.commands.project + "_erp_network" for n in self.networks):
                        self.networks.append(self.network(self.commands.project + "_erp_network", subnet, own=True))
            return subprocess.CompletedProcess(argv, code, body, "")

        self.mock = patch.object(dev.subprocess, "run", side_effect=execute)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def network(self, name, subnet, own=False):
        return {"Name": name, "IPAM": {"Config": [{"Subnet": subnet}]}, "Labels": {
            "com.docker.compose.project": self.commands.project if own else "other",
            "com.docker.compose.network": "erp_network",
            "complect.checkout_id": self.commands.values["DEV_CHECKOUT_ID"] if own else "other",
        }}

    def test_config_is_explicit_ephemeral_and_skips_occupied_candidate(self):
        from infra.scripts.docker_network import candidates
        sequence = candidates(str(self.root), kind="dev")
        self.networks.append(self.network("occupied", sequence[0]))
        before = (self.root / ".env.dev").read_bytes()
        config = self.commands.configuration()
        self.assertEqual(config["networks"]["erp_network"]["ipam"]["config"][0]["subnet"], sequence[1])
        self.assertEqual((self.root / ".env.dev").read_bytes(), before)

    def test_success_persists_auto_provenance_and_restart_is_stable(self):
        path = self.root / ".env.dev"
        before = path.read_bytes()
        self.commands.compose("up", "-d", write=True)
        subnet = self.environments[-1]["COMPLECT_DOCKER_SUBNET"]
        self.assertIn(f"COMPLECT_DOCKER_SUBNET={subnet}\n", path.read_text())
        self.assertIn(f"# COMPLECT_AUTO_SUBNET={subnet}\n", path.read_text())
        self.assertTrue(path.read_bytes().startswith(before))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        restarted = self.dev.Commands(self.root)
        config = restarted.configuration()
        self.assertEqual(config["networks"]["erp_network"]["ipam"]["config"][0]["subnet"], subnet)

    def test_exhaustion_fails_before_compose(self):
        from infra.scripts.docker_network import candidates
        self.networks.extend(self.network(f"other-{i}", subnet)
                             for i, subnet in enumerate(candidates(str(self.root))))
        with self.assertRaisesRegex(self.dev.DevError, "exhausted"):
            self.commands.compose("up", write=True)
        self.assertFalse(any("compose" in call for call in self.calls))

    def test_old_own_network_requires_nondestructive_migration(self):
        self.networks.append(self.network(self.commands.project + "_erp_network", "172.30.0.0/24", own=True))
        before = (self.root / ".env.dev").read_bytes()
        with self.assertRaisesRegex(self.dev.DevError, "make down.*make up"):
            self.commands.compose("up", write=True)
        self.assertEqual((self.root / ".env.dev").read_bytes(), before)
        self.assertFalse(any("compose" in call or "rm" in call for call in self.calls))
        self.commands.compose("down", write=True)
        calls = [c for c in self.calls if "compose" in c]
        self.assertEqual(len(calls), 1)
        self.assertIn("down", calls[0])
        self.assertIn("--remove-orphans", calls[0])
        self.assertNotIn("-v", calls[0])

    def test_user_override_is_not_rewritten_and_collision_fails(self):
        path = self.root / ".env.dev"
        with path.open("a") as out:
            out.write("COMPLECT_DOCKER_SUBNET=192.0.2.0/24\n")
        before = path.read_bytes()
        self.commands = self.dev.Commands(self.root)
        self.commands.compose("up", write=True)
        self.assertEqual(path.read_bytes(), before)
        self.networks.append(self.network("other", "192.0.2.0/24"))
        with self.assertRaisesRegex(self.dev.DevError, "overlaps"):
            self.commands.compose("up", write=True)
        self.assertEqual(path.read_bytes(), before)

    def test_initial_race_retries_only_uncommitted_auto_candidate(self):
        from infra.scripts.docker_network import candidates
        self.race = True
        self.commands.compose("up", write=True)
        created = [env["COMPLECT_DOCKER_SUBNET"] for call, env in zip(self.calls, self.environments)
                   if "compose" in call and "up" in call]
        self.assertEqual(created, candidates(str(self.root))[:2])
        self.assertIn(f"COMPLECT_DOCKER_SUBNET={created[1]}\n", (self.root / ".env.dev").read_text())

    def test_unrelated_failure_does_not_retry_or_persist(self):
        self.failure = True
        before = (self.root / ".env.dev").read_bytes()
        with self.assertRaises(self.dev.DevError):
            self.commands.compose("up", write=True)
        self.assertEqual((self.root / ".env.dev").read_bytes(), before)
        self.assertEqual(sum("compose" in c for c in self.calls), 1)

    def test_cli_creation_routes_through_network_helper(self):
        from unittest.mock import patch
        with patch.object(self.dev, "prerequisites"), patch.object(self.dev, "source_digest", return_value="test"), \
             patch.object(self.dev.Commands, "own_resources"), \
             patch.object(sys, "argv", [str(CLI), "compose", "--root", str(self.root), "up", "-d"]), \
             patch.object(self.dev.subprocess, "call", return_value=95):
            self.assertEqual(self.dev.main(), 0)
        self.assertIn("COMPLECT_DOCKER_SUBNET=", (self.root / ".env.dev").read_text().replace("# COMPLECT_DOCKER_SUBNET=", "# example="))

    def test_two_checkouts_same_initial_slot_have_distinct_stable_networks(self):
        from infra.scripts.docker_network import candidates
        first = candidates(str(self.root))[0]
        second = next(self.root / str(i) for i in range(200)
                      if candidates(str(self.root / str(i)))[0] == first)
        self.commands.compose("up", write=True)
        second.mkdir()
        (second / ".env.dev.example").write_bytes((ROOT / ".env.dev.example").read_bytes())
        self.dev.configure(second)
        first_commands = self.commands
        self.commands = self.dev.Commands(second)
        self.assertNotEqual(self.commands.project, first_commands.project)
        self.commands.compose("up", write=True)
        self.assertNotEqual(self.commands.environment["COMPLECT_DOCKER_SUBNET"], first)
        for root, subnet in ((self.root, first), (second, self.commands.environment["COMPLECT_DOCKER_SUBNET"])):
            self.assertEqual(self.dev.Commands(root).configuration()["networks"]["erp_network"]["ipam"]["config"][0]["subnet"], subnet)

    def test_persisted_auto_conflict_is_not_reselected(self):
        self.commands.compose("up", write=True)
        subnet = self.commands.environment["COMPLECT_DOCKER_SUBNET"]
        before = (self.root / ".env.dev").read_bytes()
        self.networks.clear()
        self.networks.append(self.network("new-owner", subnet))
        with self.assertRaisesRegex(self.dev.DevError, "overlaps"):
            self.dev.Commands(self.root).compose("up", write=True)
        self.assertEqual((self.root / ".env.dev").read_bytes(), before)

    def test_explicit_initial_race_never_retries(self):
        with (self.root / ".env.dev").open("a") as out:
            out.write("COMPLECT_DOCKER_SUBNET=192.0.2.0/24\n")
        self.race = True
        with self.assertRaises(self.dev.DevError):
            self.commands.compose("up", write=True)
        self.assertEqual(sum("compose" in c for c in self.calls), 1)

    def test_unsafe_environment_persistence_is_rejected(self):
        (self.root / ".env.dev").chmod(0o644)
        with self.assertRaisesRegex(self.dev.DevError, "permissions"):
            self.commands.compose("up", write=True)

    def test_unknown_network_owner_is_never_reused(self):
        self.networks.append(self.network(self.commands.project + "_erp_network", "198.18.16.0/24"))
        with self.assertRaisesRegex(self.dev.DevError, "another or unmanaged"):
            self.commands.compose("up", write=True)
        self.assertFalse(any("compose" in c for c in self.calls))

    def test_raw_compose_requires_nonempty_subnet(self):
        compose = (ROOT / "docker-compose.dev.yml").read_text()
        self.assertIn('${COMPLECT_DOCKER_SUBNET:?', compose)

    def test_user_edit_during_create_is_not_overwritten(self):
        original = self.commands._compose_command
        def editing(*args, **kwargs):
            result = original(*args, **kwargs)
            with (self.root / ".env.dev").open("a") as stream:
                stream.write("# user setting changed concurrently\n")
            return result
        from unittest.mock import patch
        with patch.object(self.commands, "_compose_command", side_effect=editing):
            with self.assertRaisesRegex(self.dev.DevError, "changed during"):
                self.commands.compose("up", write=True)
        self.assertTrue((self.root / ".env.dev").read_text().endswith("# user setting changed concurrently\n"))

    def test_readonly_config_does_not_persist_crash_recovered_network(self):
        from infra.scripts.docker_network import candidates
        subnet = candidates(str(self.root))[0]
        self.networks.append(self.network(self.commands.project + "_erp_network", subnet, own=True))
        before = (self.root / ".env.dev").read_bytes()
        self.dev.Commands(self.root, read_only=True).configuration()
        self.assertEqual((self.root / ".env.dev").read_bytes(), before)
        self.assertFalse((self.root / ".dev/network.lock").exists())
        self.commands.compose("up", write=True)
        self.assertIn(f"# COMPLECT_AUTO_SUBNET={subnet}", (self.root / ".env.dev").read_text())

    def test_scale_creation_is_persisted_through_cli(self):
        from unittest.mock import patch
        with patch.object(self.dev, "prerequisites"), patch.object(self.dev, "source_digest", return_value="test"), \
             patch.object(self.dev.Commands, "own_resources"), \
             patch.object(sys, "argv", [str(CLI), "compose", "--root", str(self.root), "scale", "probe=1"]), \
             patch.object(self.dev.subprocess, "call", return_value=95):
            self.assertEqual(self.dev.main(), 0)
        configured = self.dev.parse_env(self.root / ".env.dev", self.dev.PUBLIC_KEYS)
        self.assertIn("COMPLECT_DOCKER_SUBNET", configured)


class VpnDiagnosticTests(unittest.TestCase):
    def test_split_default_routes_produce_readonly_actionable_warning(self):
        import io
        from contextlib import redirect_stderr
        from unittest.mock import patch
        from infra.scripts import dev_environment as dev
        output = io.StringIO()
        result = subprocess.CompletedProcess([], 0, "0.0.0.0/1 dev tun0\n128.0.0.0/1 dev tun0\n", "")
        with patch.object(dev.shutil, "which", return_value="/usr/bin/ip"), \
             patch.object(dev.subprocess, "run", return_value=result) as run, redirect_stderr(output):
            dev.vpn_route_diagnostic()
        self.assertIn("bip", output.getvalue())
        self.assertIn("workstations.md", output.getvalue())
        self.assertEqual(run.call_args.args[0], ["ip", "-4", "route", "show"])
