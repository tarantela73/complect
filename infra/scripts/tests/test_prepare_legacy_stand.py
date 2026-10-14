import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from infra.scripts import prepare_legacy_stand


class PrepareLegacyStandTests(unittest.TestCase):
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
                   "resources": [{"kind": "container", "id": "owned-id", "labels": labels,
                                  "fingerprint": {"Id": "owned-id", "Image": "image-id", "Mounts": []}}]}
        path = parent / "ownership.json"
        path.write_text(json.dumps(receipt))
        if changed:
            resource["Image"] = "replaced-image"
        commands = Mock(project="owned", values={"DEV_CHECKOUT_ID": "checkout-id"})

        def run(command, **kwargs):
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
            self.assertEqual(commands.run.call_count, 1)

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
            self.assertEqual(commands.run.call_count, 2)

    def test_foreign_ownership_is_not_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path, commands = self._cleanup_receipt(Path(temporary))
            receipt = json.loads(path.read_text())
            # The saved fingerprint remains exact, but live labels belong elsewhere.
            actual = json.loads(commands.run(["docker", "inspect", "owned-id"]).stdout)[0]
            actual["Config"]["Labels"]["complect.checkout_id"] = "foreign-checkout"
            commands.run.reset_mock()
            commands.run.side_effect = None
            commands.run.return_value = SimpleNamespace(stdout=json.dumps([actual]))
            with patch.object(prepare_legacy_stand, "_docker_endpoint_is_local"), patch.object(
                prepare_legacy_stand, "stand_commands", return_value=commands,
            ):
                result = prepare_legacy_stand.cleanup(path)
            self.assertEqual(result["cleanup"], "BLOCKED_STAND")
            self.assertEqual(commands.run.call_count, 1)
            self.assertEqual(json.loads(path.read_text())["resources"], receipt["resources"])


if __name__ == "__main__":
    unittest.main()
