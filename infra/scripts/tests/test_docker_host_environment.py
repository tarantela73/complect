"""Public host-environment contract; no daemon or external credentials."""

import os
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path

from infra.scripts.docker_host_environment import prepare_host_environment


class DockerHostEnvironmentTests(unittest.TestCase):
    def test_default_isolates_buildx_and_preserves_connection_and_auth_inputs(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            config = base / "home/.docker"
            config.mkdir(parents=True)
            (config / "config.json").write_text(
                '{"currentContext":"chosen","auths":{}}'
            )
            original = {
                "HOME": str(base / "home"),
                "DOCKER_HOST": "unix:///chosen.sock",
                "DOCKER_CONTEXT": "chosen",
                "DOCKER_TLS_VERIFY": "1",
                "DOCKER_CERT_PATH": "/chosen/certs",
            }
            lease = prepare_host_environment(base / "service", original)
            self.assertEqual(
                original.keys(), lease.environment.keys() - {"BUILDX_CONFIG"}
            )
            for key, value in original.items():
                self.assertEqual(lease.environment[key], value)
            self.assertNotIn("DOCKER_CONFIG", lease.environment)
            buildx = Path(lease.environment["BUILDX_CONFIG"])
            self.assertTrue(buildx.is_dir())
            self.assertTrue(buildx.is_relative_to(base / "service"))
            self.assertEqual(buildx.stat().st_mode & 0o777, 0o700)
            self.assertEqual(
                (config / "config.json").read_text(),
                '{"currentContext":"chosen","auths":{}}',
            )
            self.assertEqual(lease.cleanup(), [])
            self.assertFalse(buildx.exists())
            self.assertEqual(lease.cleanup(), [])
            self.assertTrue(config.exists())

    def test_explicit_configs_are_probed_and_preserved_without_fallback(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            docker = base / "docker"
            buildx = base / "buildx"
            docker.mkdir()
            buildx.mkdir()
            values = {"DOCKER_CONFIG": str(docker), "BUILDX_CONFIG": str(buildx)}
            lease = prepare_host_environment(base / "owned", values)
            self.assertEqual(lease.environment, values)
            self.assertEqual(lease.cleanup(), [])
            self.assertTrue(docker.exists())
            self.assertTrue(buildx.exists())
            for key in ("DOCKER_CONFIG", "BUILDX_CONFIG"):
                with self.subTest(key=key):
                    invalid = {**values, key: "/sys"}
                    with self.assertRaisesRegex(ValueError, key):
                        prepare_host_environment(base / ("failed-" + key), invalid)
                    self.assertFalse((base / ("failed-" + key)).exists())

    def test_custom_builder_is_not_silently_replaced_by_an_empty_config(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            original = base / "home/.docker/buildx"
            original.mkdir(parents=True)
            (original / "current").write_text('{"Name":"remote-builder"}')
            values = {"HOME": str(base / "home")}
            with self.assertRaisesRegex(ValueError, "BUILDX_CONFIG"):
                prepare_host_environment(base / "service", values)
            self.assertFalse((base / "service").exists())
            lease = prepare_host_environment(
                base / "explicit", {**values, "BUILDX_CONFIG": str(original)}
            )
            self.assertEqual(lease.environment["BUILDX_CONFIG"], str(original))
            self.assertEqual(lease.cleanup(), [])
            self.assertTrue((original / "current").is_file())

    def test_cleanup_refuses_replacement_and_preserves_explicit_directories(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            lease = prepare_host_environment(
                base / "service", {"HOME": str(base / "home")}
            )
            old = base / "old"
            lease.base.rename(old)
            lease.base.mkdir(mode=0o700)
            (lease.base / "foreign").write_text("preserve")
            self.assertTrue(lease.cleanup())
            self.assertEqual((lease.base / "foreign").read_text(), "preserve")
            (lease.base / "foreign").unlink()
            lease.base.rmdir()
            old.rename(lease.base)
            self.assertEqual(lease.cleanup(), [])

    def test_cleanup_failure_is_explicit_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            lease = prepare_host_environment(
                base / "service", {"HOME": str(base / "home")}
            )
            with patch(
                "shutil.rmtree", side_effect=OSError("synthetic removal failure")
            ):
                self.assertEqual(
                    lease.cleanup(), ["host environment cleanup unconfirmed"]
                )
            self.assertTrue(lease.base.exists())
            self.assertEqual(lease.cleanup(), [])

    def test_activation_restores_environment_even_when_work_raises(self):
        with tempfile.TemporaryDirectory() as raw:
            before = dict(os.environ)
            lease = prepare_host_environment(Path(raw) / "service", {"HOME": raw})
            with self.assertRaisesRegex(RuntimeError, "primary failure"):
                with lease.activated():
                    self.assertEqual(
                        os.environ["BUILDX_CONFIG"], lease.environment["BUILDX_CONFIG"]
                    )
                    raise RuntimeError("primary failure")
            self.assertEqual(dict(os.environ), before)
            self.assertEqual(lease.cleanup(), [])

    def test_failed_default_write_probe_cleans_its_owned_directory(self):
        with tempfile.TemporaryDirectory() as raw:
            service = Path(raw) / "service"
            with patch("tempfile.TemporaryFile", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(ValueError, "BUILDX_CONFIG"):
                    prepare_host_environment(service, {"HOME": raw})
            self.assertFalse(service.exists())

    def test_failed_preparation_retains_cleanup_identity_and_separate_error(self):
        from infra.scripts.docker_host_environment import cleanup_host_environment

        with tempfile.TemporaryDirectory() as raw:
            service = Path(raw) / "service"
            with (
                patch("tempfile.TemporaryFile", side_effect=OSError("disk full")),
                patch("shutil.rmtree", side_effect=OSError("cannot remove")),
            ):
                with self.assertRaises(ValueError) as caught:
                    prepare_host_environment(service, {"HOME": raw})
            self.assertTrue(getattr(caught.exception, "cleanup_errors", []))
            record = caught.exception.record
            self.assertEqual(cleanup_host_environment(service, record), [])


if __name__ == "__main__":
    unittest.main()
