import json
import hashlib
import tempfile
import unittest
import signal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from infra.scripts import prepare_legacy_stand


class PrepareLegacyStandTests(unittest.TestCase):
    def test_stand_commands_selects_port_free_checkout_template(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            checkout = parent / "checkout"
            checkout.mkdir()
            template = checkout / "infra/legacy-stand.compose.yml"
            template.parent.mkdir()
            template.write_text("services:\n  backend: {}\n")
            override = parent / "stand.compose.json"
            override.write_text("{\"services\": {}}\n")
            registered = {
                "compose_template": {
                    "path": str(template),
                    "sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
                },
                "compose_override": {
                    "path": str(override),
                    "sha256": hashlib.sha256(override.read_bytes()).hexdigest(),
                },
            }
            commands = SimpleNamespace(
                prefix=[
                    "docker", "compose", "--project-directory", str(checkout),
                    "-f", str(checkout / "docker-compose.dev.yml"),
                    "-f", str(checkout / "docker-compose.e2e.yml"),
                ]
            )
            with patch.object(prepare_legacy_stand, "Commands", return_value=commands):
                result = prepare_legacy_stand.stand_commands(checkout, registered)
            self.assertIs(result, commands)
            self.assertIn(str(template), commands.prefix)
            self.assertIn(str(override), commands.prefix)
            self.assertNotIn(str(checkout / "docker-compose.dev.yml"), commands.prefix)
            self.assertNotIn(str(checkout / "docker-compose.e2e.yml"), commands.prefix)

    def test_legacy_template_has_no_host_ports_or_fixed_names(self):
        template = Path(__file__).parents[2] / "legacy-stand.compose.yml"
        content = template.read_text()
        self.assertNotIn("container_name:", content)
        self.assertNotIn("ports:", content)
        for argument in ("--postgres-uid", "--postgres-gid", "--kong-uid", "--kong-gid", "--minio-uid", "--minio-gid", "--app-uid", "--app-gid"):
            self.assertIn(argument, content)
        for service in ("secrets_init:", "volume_init:", "postgres_primary:", "postgres_replica:", "redis:", "minio:", "s3_bootstrap:", "migrate:", "backend:", "clamav:", "celery_worker:"):
            self.assertIn(service, content)
        migrate = content.split("  migrate:\n", 1)[1].split("  backend:\n", 1)[0]
        self.assertIn('user: "${DEV_UID:-1000}:${DEV_POSTGRES_GID:-999}"', migrate)
        self.assertIn("./backend:/app:ro,z", migrate)
        self.assertIn("runtime_secrets:/run/complect-secrets:ro", migrate)
        self.assertIn("dev_state:/var/lib/complect-dev", migrate)

    def test_invalid_snapshot_refuses_before_docker_or_directory_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            snapshot = parent / "snapshot.json"
            snapshot.write_text('{}')
            destination = parent / "stand"
            with patch.object(prepare_legacy_stand, "_docker_endpoint_is_local") as docker:
                with self.assertRaises(ValueError):
                    prepare_legacy_stand.prepare(parent, snapshot, destination)
            docker.assert_not_called()
            self.assertFalse(destination.exists())

    def _cleanup_receipt(self, parent, *, changed=False, removal_error=False):
        labels = {"com.docker.compose.project": "owned", "complect.checkout_id": "checkout-id"}
        resource = {"Id": "owned-id", "Image": "image-id", "Mounts": [], "Config": {"Labels": labels}}
        receipt = {"root": str(parent / "checkout"), "project": "owned", "checkout_id": "checkout-id",
                   "inventory_complete": True, "inventory_status": "complete",
                   "resources": [{"kind": "container", "id": "owned-id", "labels": labels,
                                  "fingerprint": {"Id": "owned-id", "Image": "image-id", "Mounts": []}}]}
        path = parent / "ownership.json"
        path.write_text(json.dumps(receipt))
        if changed:
            resource["Image"] = "replaced-image"
        commands = Mock(project="owned", values={"DEV_CHECKOUT_ID": "checkout-id"})

        def run(command, **kwargs):
            if command[:2] == ["docker", "ps"]:
                return SimpleNamespace(stdout="owned-id\n")
            if command[:2] == ["docker", "inspect"]:
                return SimpleNamespace(stdout=json.dumps([resource]))
            if removal_error:
                raise RuntimeError("removal failed")
            return SimpleNamespace(stdout="")

        commands.run.side_effect = run
        return path, commands

    def test_changed_identity_is_not_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path, commands = self._cleanup_receipt(Path(temporary), changed=True)
            with patch.object(prepare_legacy_stand, "_docker_endpoint_is_local"), patch.object(
                prepare_legacy_stand, "stand_commands", return_value=commands,
            ):
                result = prepare_legacy_stand.cleanup(path)
            self.assertEqual(result["cleanup"], "BLOCKED_STAND")
            self.assertFalse(any(call.kwargs.get("write") for call in commands.run.call_args_list))

    def test_removal_failure_blocks_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            path, commands = self._cleanup_receipt(Path(temporary), removal_error=True)
            with patch.object(prepare_legacy_stand, "_docker_endpoint_is_local"), patch.object(
                prepare_legacy_stand, "stand_commands", return_value=commands,
            ):
                result = prepare_legacy_stand.cleanup(path)
            self.assertEqual(result["cleanup"], "BLOCKED_STAND")
            self.assertEqual(result["cleanup_errors"][0]["resource"], "container:owned-id")

    def test_successful_cleanup_is_repeatable_without_redeleting(self):
        with tempfile.TemporaryDirectory() as temporary:
            path, commands = self._cleanup_receipt(Path(temporary))
            with patch.object(prepare_legacy_stand, "_docker_endpoint_is_local"), patch.object(
                prepare_legacy_stand, "stand_commands", return_value=commands,
            ):
                result = prepare_legacy_stand.cleanup(path)
                again = prepare_legacy_stand.cleanup(path)
            self.assertEqual(result["cleanup"], "PASS")
            self.assertEqual(again["cleanup"], "PASS")
            self.assertEqual(sum(call.args[0][:2] == ["docker", "rm"] for call in commands.run.call_args_list), 1)

    def test_cleanup_repeated_after_image_already_removed_is_safe(self):
        with tempfile.TemporaryDirectory() as temporary:
            path, commands = self._cleanup_receipt(Path(temporary))
            receipt = json.loads(path.read_text())
            receipt["image"] = {"reference": "owned_backend:dev", "id": "image-id"}
            path.write_text(json.dumps(receipt))
            with patch.object(prepare_legacy_stand, "_docker_endpoint_is_local"), patch.object(
                prepare_legacy_stand, "stand_commands", return_value=commands,
            ):
                first = prepare_legacy_stand.cleanup(path)
                second = prepare_legacy_stand.cleanup(path)
            self.assertEqual(first["cleanup"], "PASS")
            self.assertEqual(second["cleanup"], "PASS")
            self.assertTrue(json.loads(path.read_text())["image_removed"])

    def test_cleanup_blocks_when_image_absence_probe_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            path, commands = self._cleanup_receipt(Path(temporary))
            receipt = json.loads(path.read_text())
            receipt["image"] = {"reference": "owned_backend:dev", "id": "image-id"}
            path.write_text(json.dumps(receipt))

            def run(arguments, **kwargs):
                if arguments[:3] == ["docker", "image", "ls"]:
                    raise RuntimeError("image inventory unavailable")
                return SimpleNamespace(stdout="owned-id\n" if arguments[:2] == ["docker", "ps"] else json.dumps([{
                    "Id": "owned-id", "Image": "image-id", "Mounts": [], "Config": {"Labels": {
                        "com.docker.compose.project": "owned", "complect.checkout_id": "checkout-id"
                    }}
                }]))

            commands.run.side_effect = run
            with patch.object(prepare_legacy_stand, "_docker_endpoint_is_local"), patch.object(
                prepare_legacy_stand, "stand_commands", return_value=commands,
            ):
                result = prepare_legacy_stand.cleanup(path)
            self.assertEqual(result["cleanup"], "BLOCKED_STAND")
            self.assertEqual(result["cleanup_errors"][-1]["resource"], "image")

    def test_foreign_ownership_is_not_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path, commands = self._cleanup_receipt(Path(temporary))
            receipt = json.loads(path.read_text())
            # The saved fingerprint remains exact, but live labels belong elsewhere.
            actual = json.loads(commands.run(["docker", "inspect", "owned-id"]).stdout)[0]
            actual["Config"]["Labels"]["complect.checkout_id"] = "foreign-checkout"
            commands.run.reset_mock()
            commands.run.side_effect = lambda args, **kwargs: SimpleNamespace(stdout="owned-id\n" if args[:2] == ["docker", "ps"] else json.dumps([actual]))
            with patch.object(prepare_legacy_stand, "_docker_endpoint_is_local"), patch.object(
                prepare_legacy_stand, "stand_commands", return_value=commands,
            ):
                result = prepare_legacy_stand.cleanup(path)
            self.assertEqual(result["cleanup"], "BLOCKED_STAND")
            self.assertFalse(any(call.kwargs.get("write") for call in commands.run.call_args_list))
            self.assertEqual(json.loads(path.read_text())["resources"], receipt["resources"])


    def test_unknown_inventory_blocks_cleanup_even_when_empty(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            path = parent / "ownership.json"
            path.write_text(json.dumps({
                "root": str(parent / "checkout"), "project": "owned", "checkout_id": "checkout-id",
                "resources": [], "inventory_complete": False,
            }))
            commands = Mock(project="owned", values={"DEV_CHECKOUT_ID": "checkout-id"})
            with patch.object(prepare_legacy_stand, "_docker_endpoint_is_local"), patch.object(
                prepare_legacy_stand, "stand_commands", return_value=commands
            ):
                result = prepare_legacy_stand.cleanup(path)
            self.assertEqual(result["cleanup"], "BLOCKED_STAND")
            self.assertIn({"error": "inventory_incomplete"}, result["cleanup_errors"])

    def test_recovery_rejects_changed_recorded_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            checkout = parent / "checkout"
            checkout.mkdir()
            override = parent / "stand.compose.json"
            override.write_text("{}")
            digest = hashlib.sha256(override.read_bytes()).hexdigest()
            labels = {"com.docker.compose.project": "owned", "complect.checkout_id": "checkout-id"}
            original = {"Id": "owned-id", "Image": "image-id", "Mounts": [], "Config": {"Labels": labels}}
            changed = dict(original, Image="foreign-image")
            receipt = {"root": str(checkout), "project": "owned", "checkout_id": "checkout-id",
                       "compose_override": {"path": str(override), "sha256": digest},
                       "resources": [{"kind": "container", "id": "owned-id", "labels": labels,
                                      "fingerprint": prepare_legacy_stand._fingerprint("container", original)}],
                       "inventory_complete": False}
            path = parent / "ownership.json"
            path.write_text(json.dumps(receipt))
            commands = Mock(project="owned", values={"DEV_CHECKOUT_ID": "checkout-id"})
            commands.run.return_value = SimpleNamespace(stdout=json.dumps([changed]))
            with patch.object(prepare_legacy_stand, "_docker_endpoint_is_local"), patch.object(
                prepare_legacy_stand, "stand_commands", return_value=commands
            ):
                with self.assertRaises(ValueError):
                    prepare_legacy_stand.recover_inventory(path)

    def test_recovery_clears_error_and_confirms_already_removed_resource(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            checkout = parent / "checkout"
            checkout.mkdir()
            path = parent / "ownership.json"
            receipt = {
                "root": str(checkout), "project": "owned", "checkout_id": "checkout-id",
                "status": "BLOCKED_STAND", "interruption": {"reason": "SIGTERM"},
                "resources": [{"kind": "container", "id": "gone", "fingerprint": {}, "labels": {}}],
                "removed": ["container:gone"], "inventory_error": "RuntimeError",
                "cleanup_errors": [{"error": "RuntimeError"}], "inventory_complete": False,
            }
            path.write_text(json.dumps(receipt))
            commands = Mock(project="owned", values={"DEV_CHECKOUT_ID": "checkout-id"})
            commands.run.return_value = SimpleNamespace(returncode=0, stdout="")
            with patch.object(prepare_legacy_stand, "_docker_endpoint_is_local"), patch.object(prepare_legacy_stand, "stand_commands", return_value=commands):
                result = prepare_legacy_stand.recover_inventory(path)
                self.assertTrue(result["inventory_complete"])
                self.assertNotIn("inventory_error", result)
                self.assertNotIn("cleanup_errors", result)
                self.assertEqual(result["status"], "BLOCKED_STAND")
                self.assertEqual(result["interruption"]["reason"], "SIGTERM")
                self.assertEqual(prepare_legacy_stand.cleanup(path)["cleanup"], "PASS")

    def test_recovery_rejects_inconclusive_image_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            checkout = parent / "checkout"
            checkout.mkdir()
            path = parent / "ownership.json"
            receipt = {"root": str(checkout), "project": "owned", "checkout_id": "checkout-id", "resources": [], "inventory_complete": False}
            path.write_text(json.dumps(receipt))
            commands = Mock(project="owned", values={"DEV_CHECKOUT_ID": "checkout-id"})

            def run(arguments, **kwargs):
                if arguments[:3] == ["docker", "image", "ls"]:
                    raise RuntimeError("Docker inventory unavailable")
                if arguments[:3] == ["docker", "image", "inspect"]:
                    return SimpleNamespace(returncode=1, stdout="", stderr="Docker inventory unavailable")
                return SimpleNamespace(returncode=0, stdout="")

            commands.run.side_effect = run
            with patch.object(prepare_legacy_stand, "_docker_endpoint_is_local"), patch.object(prepare_legacy_stand, "stand_commands", return_value=commands):
                with self.assertRaises(RuntimeError):
                    prepare_legacy_stand.recover_inventory(path)
            self.assertFalse(json.loads(path.read_text())["inventory_complete"])

    def test_recovery_rejects_removed_id_that_is_still_present_with_foreign_labels(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            path, commands = self._cleanup_receipt(parent)
            receipt = json.loads(path.read_text())
            receipt["removed"] = ["container:owned-id"]
            receipt["inventory_complete"] = False
            path.write_text(json.dumps(receipt))
            actual = {"Id": "owned-id", "Image": "image-id", "Mounts": [], "Config": {"Labels": {"com.docker.compose.project": "foreign"}}}

            def run(arguments, **kwargs):
                if arguments[:2] == ["docker", "ps"]:
                    return SimpleNamespace(stdout="" if any("label=" in part for part in arguments) else "owned-id\n")
                if arguments[:2] == ["docker", "inspect"]:
                    return SimpleNamespace(stdout=json.dumps([actual]))
                return SimpleNamespace(stdout="")

            commands.run.side_effect = run
            with patch.object(prepare_legacy_stand, "_docker_endpoint_is_local"), patch.object(prepare_legacy_stand, "stand_commands", return_value=commands):
                with self.assertRaises(ValueError):
                    prepare_legacy_stand.recover_inventory(path)
            self.assertFalse(any(call.kwargs.get("write") for call in commands.run.call_args_list))

    def test_interruption_after_preparation_keeps_blocked_receipt_and_restores_signal(self):
        for reason in ("SIGTERM", "KeyboardInterrupt"):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                directory = root / ".dev/test-artifacts/stand"
                snapshot = root / "snapshot.json"
                template = root / "infra/legacy-stand.compose.yml"
                template.parent.mkdir()
                template.write_text("services:\n  backend: {}\n")
                snapshot.write_text(json.dumps({
                    "format": "complect-working-tree-snapshot-v1",
                    "files": ["infra/legacy-stand.compose.yml"],
                }))
                manifest = root / "manifest.json"
                # Use the canonical public manifest rather than a synthesized node list.
                manifest.write_bytes(prepare_legacy_stand.MANIFEST.read_bytes())
                labels = {"com.docker.compose.project": "owned", "complect.checkout_id": "checkout-id"}
                container = {"Id": "owned-id", "Image": "image", "Mounts": [], "Config": {"Labels": labels}, "NetworkSettings": {"Networks": {"owned_network": {}}}, "Name": "/owned-redis"}
                commands = Mock(project="owned", values={"DEV_CHECKOUT_ID": "checkout-id"})
                commands.containers.return_value = {"postgres_primary": container, "redis": container}
                commands.redact.side_effect = lambda value: value
                interrupted = False

                def run(arguments, **kwargs):
                    nonlocal interrupted
                    if arguments[:2] == ["docker", "exec"]:
                        return SimpleNamespace(stdout="16384|cluster|complect_migrator|complect_migrator")
                    if arguments[:2] == ["docker", "ps"] and not interrupted:
                        interrupted = True
                        if reason == "SIGTERM":
                            signal.raise_signal(signal.SIGTERM)
                        raise KeyboardInterrupt()
                    return SimpleNamespace(stdout="", returncode=0)

                commands.run.side_effect = run
                previous = signal.getsignal(signal.SIGTERM)
                with patch.object(prepare_legacy_stand, "ROOT", root), patch.object(prepare_legacy_stand.agent_harness, "verify_working_tree_snapshot", return_value=[]), patch.object(prepare_legacy_stand, "verify_prepared_checkout", return_value=[]), patch.object(prepare_legacy_stand, "_docker_endpoint_is_local"), patch.object(prepare_legacy_stand, "configure"), patch.object(prepare_legacy_stand, "Commands", return_value=commands), patch.object(prepare_legacy_stand, "stand_commands", return_value=commands), patch.object(prepare_legacy_stand, "build_backend"), patch.object(prepare_legacy_stand, "prepare_database_startup"), patch.object(prepare_legacy_stand.subprocess, "run"):
                    with self.assertRaises(prepare_legacy_stand.StandInterrupted if reason == "SIGTERM" else KeyboardInterrupt):
                        prepare_legacy_stand.prepare(root, snapshot, directory, manifest=manifest)
                receipt = json.loads((directory / "ownership.json").read_text())
                self.assertEqual(receipt["status"], "BLOCKED_STAND")
                self.assertEqual(receipt["interruption"]["reason"], reason)
                self.assertEqual(signal.getsignal(signal.SIGTERM), previous)


if __name__ == "__main__":
    unittest.main()
