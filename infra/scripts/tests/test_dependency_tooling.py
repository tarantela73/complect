"""Regression checks for lock metadata coverage and host version semantics."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]


def load_module(name, relative):
    specification = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


LOCK = load_module("dependency_lock", "backend/requirements/lock.py")
TOOLCHAIN = load_module("toolchain_check", "infra/scripts/check_toolchain.py")


class LockMetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "requirements"
        shutil.copytree(ROOT / "backend/requirements", self.directory)
        directory_patch = patch.object(LOCK, "DIRECTORY", self.directory)
        directory_patch.start()
        self.addCleanup(directory_patch.stop)

    def metadata(self):
        return json.loads((self.directory / LOCK.METADATA).read_text())

    def write_metadata(self, metadata):
        (self.directory / LOCK.METADATA).write_text(json.dumps(metadata))

    def test_valid_snapshot_is_accepted(self):
        LOCK.verify_inputs()

    def test_changed_input_is_rejected(self):
        with (self.directory / "base.txt").open("a") as stream:
            stream.write("\nhttpx>=99\n")
        with self.assertRaisesRegex(ValueError, "Stale dependency lock"):
            LOCK.verify_inputs()

    def test_changed_lock_is_rejected(self):
        with (self.directory / "dev.lock").open("a") as stream:
            stream.write("\n# altered\n")
        with self.assertRaisesRegex(ValueError, "Stale dependency lock"):
            LOCK.verify_inputs()

    def test_empty_fingerprints_cannot_bypass_checks(self):
        metadata = self.metadata()
        metadata["sha256"] = {}
        self.write_metadata(metadata)
        with self.assertRaisesRegex(ValueError, "every declared input and lock"):
            LOCK.verify_inputs()

    def test_omitted_fingerprint_cannot_bypass_checks(self):
        metadata = self.metadata()
        del metadata["sha256"]["base.txt"]
        self.write_metadata(metadata)
        with self.assertRaisesRegex(ValueError, "every declared input and lock"):
            LOCK.verify_inputs()

    def test_metadata_cannot_select_extra_paths(self):
        metadata = self.metadata()
        metadata["sha256"]["../unrelated"] = "0" * 64
        self.write_metadata(metadata)
        with self.assertRaisesRegex(ValueError, "no extra paths"):
            LOCK.verify_inputs()

    def test_invalid_hash_format_is_rejected(self):
        metadata = self.metadata()
        metadata["sha256"]["base.txt"] = None
        self.write_metadata(metadata)
        with self.assertRaisesRegex(ValueError, "Invalid SHA-256"):
            LOCK.verify_inputs()

    def test_rehashed_new_input_cannot_bypass_offline_install_validation(self):
        source = self.directory / "dev.txt"
        source.write_text(source.read_text() + "\nnot-in-the-reviewed-lock==1.0\n")
        metadata = self.metadata()
        metadata["sha256"]["dev.txt"] = hashlib.sha256(source.read_bytes()).hexdigest()
        self.write_metadata(metadata)
        with self.assertRaisesRegex(ValueError, "Missing locked requirement"):
            LOCK.verify_inputs()

    def test_installed_transitive_dependencies_cannot_be_omitted_from_lock(self):
        target = self.directory / "dev.lock"
        content, count = re.subn(r"^certifi==[^\n]*(?:\n[ \t][^\n]*)*\n?", "", target.read_text(), flags=re.M)
        self.assertEqual(count, 1)
        target.write_text(content)
        metadata = self.metadata()
        metadata["sha256"]["dev.lock"] = hashlib.sha256(target.read_bytes()).hexdigest()
        self.write_metadata(metadata)
        with self.assertRaisesRegex(ValueError, "Missing locked dependency"):
            LOCK.verify_installed()

    def test_unsupported_schema_is_rejected(self):
        metadata = self.metadata()
        metadata["schema_version"] = 99
        self.write_metadata(metadata)
        with self.assertRaisesRegex(ValueError, "Invalid lock metadata schema"):
            LOCK.verify_inputs()


class HostVersionPolicyTests(unittest.TestCase):
    def test_docker_patch_difference_is_warning(self):
        diagnostic = TOOLCHAIN.host_version_diagnostic("docker", "29.7.3", {"tested_version": "29.7.2"})
        self.assertTrue(diagnostic.startswith("WARN:"))

    def test_compose_minor_difference_is_warning(self):
        diagnostic = TOOLCHAIN.host_version_diagnostic("docker_compose", "v5.6.0", {"tested_version": "5.5.0"})
        self.assertTrue(diagnostic.startswith("WARN:"))

    def test_tested_version_is_accepted(self):
        diagnostic = TOOLCHAIN.host_version_diagnostic("docker_compose", "v5.5.0", {"tested_version": "5.5.0"})
        self.assertTrue(diagnostic.startswith("PASS:"))

    def test_missing_required_observation_fails(self):
        with self.assertRaisesRegex(ValueError, "Missing observed version"):
            TOOLCHAIN.host_version_diagnostic("docker", "", {"tested_version": "29.7.2"})


if __name__ == "__main__":
    unittest.main()
