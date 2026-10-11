"""Doctor must compare immutable runnable identities without modifying Docker."""
import copy
import json
from pathlib import Path
import shlex
import subprocess
import unittest
from unittest.mock import Mock

from infra.scripts import dev_environment as dev


INDEX = 'sha256:' + '1' * 64
MANIFEST = 'sha256:' + '2' * 64
CONFIG = 'sha256:' + '3' * 64
OTHER = 'sha256:' + '4' * 64
MANIFEST_TYPE = 'application/vnd.oci.image.manifest.v1+json'


class ImageIdentityTests(unittest.TestCase):
    def setUp(self):
        self.image = {
            'Id': INDEX,
            'Descriptor': {'digest': INDEX, 'mediaType': 'application/vnd.oci.image.index.v1+json'},
        }
        self.container = {
            'Image': CONFIG,
            'Platform': 'linux',
            'ImageManifestDescriptor': {
                'digest': MANIFEST,
                'mediaType': MANIFEST_TYPE,
                'platform': {'os': 'linux', 'architecture': 'amd64'},
            },
        }
        self.selected = {
            'Id': MANIFEST,
            'Os': 'linux',
            'Architecture': 'amd64',
            'Descriptor': {'digest': MANIFEST, 'mediaType': MANIFEST_TYPE},
        }
        self.commands = Mock()

    def verify(self):
        self.commands.run.return_value = subprocess.CompletedProcess(
            [], 0, json.dumps([self.selected]), ''
        )
        dev.verify_image_identity(self.commands, 'checkout_backend:dev', self.container, self.image)

    def test_index_and_config_ids_may_differ_when_runnable_manifest_matches(self):
        self.verify()
        self.commands.run.assert_called_once_with([
            'docker', 'image', 'inspect', '--platform', 'linux/amd64', INDEX,
        ])

    def test_different_manifest_is_rejected_even_with_matching_image_ids(self):
        self.container['Image'] = INDEX
        self.container['ImageManifestDescriptor']['digest'] = OTHER
        with self.assertRaisesRegex(dev.DevError, 'different platform manifest'):
            self.verify()

    def test_manifest_reference_is_accepted(self):
        self.image = copy.deepcopy(self.selected)
        self.verify()

    def test_classic_store_requires_exact_config_identity(self):
        self.image = {'Id': CONFIG, 'GraphDriver': {'Name': 'overlay2'}}
        self.container = {'Image': CONFIG}
        self.verify()
        self.commands.run.assert_not_called()
        self.container['Image'] = OTHER
        with self.assertRaises(dev.DevError):
            self.verify()

    def test_incomplete_metadata_never_passes(self):
        for mutation in (
            lambda: self.image['Descriptor'].pop('digest'),
            lambda: self.image.update(Id=OTHER),
            lambda: self.container.pop('ImageManifestDescriptor'),
            lambda: self.container['ImageManifestDescriptor'].pop('platform'),
            lambda: self.container['ImageManifestDescriptor']['platform'].pop('architecture'),
            lambda: self.selected.pop('Descriptor'),
            lambda: self.selected.update(Id=OTHER),
            lambda: self.selected.update(Architecture='arm64'),
        ):
            self.setUp()
            mutation()
            with self.subTest(image=self.image, container=self.container, selected=self.selected):
                with self.assertRaises(dev.DevError):
                    self.verify()

    def test_missing_classic_id_does_not_pass(self):
        self.image = {}
        self.container = {}
        with self.assertRaises(dev.DevError):
            self.verify()

    def test_missing_both_descriptors_is_not_assumed_to_be_a_classic_store(self):
        self.image = {'Id': INDEX}
        self.container = {'Image': INDEX}
        with self.assertRaisesRegex(dev.DevError, 'store identity is unresolved'):
            self.verify()

    def test_unresolvable_platform_and_invalid_json_do_not_pass(self):
        for error in (dev.DevError('platform unavailable'), ValueError('invalid metadata')):
            self.commands.run.side_effect = error
            with self.subTest(error=error):
                with self.assertRaises((dev.DevError, ValueError)):
                    self.verify()

    def test_doctor_commands_refuse_corrective_operations(self):
        commands = dev.Commands.__new__(dev.Commands)
        commands.read_only = True
        with self.assertRaisesRegex(dev.DevError, 'corrective operation'):
            commands.run(['docker', 'run', 'checkout_backend:dev'], write=True)


class MakeRootSelectionTests(unittest.TestCase):
    def test_make_targets_select_one_checkout_for_up_doctor_sdk_and_e2e(self):
        root = Path(__file__).resolve().parents[3]
        disposable = '/tmp/disposable checkout'
        result = subprocess.run([
            'make', '--no-print-directory', '--dry-run', 'up', 'doctor', 'test-e2e',
            f'DEV_ROOT={disposable}',
        ], cwd=root, text=True, capture_output=True, check=True)
        commands = [shlex.split(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(commands), 4)
        for command in commands:
            self.assertIn('--root', command)
            self.assertEqual(command[command.index('--root') + 1], disposable)
        self.assertIn('compose', commands[0])
        self.assertIn('sdk', commands[2])
        self.assertIn('e2e', commands[3])


if __name__ == '__main__':
    unittest.main()
