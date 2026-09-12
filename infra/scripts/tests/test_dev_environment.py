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
