"""Ownership proof uses current Docker observations through its public seam."""

import unittest
from copy import deepcopy

from infra.scripts.docker_ownership import OwnershipMismatch, OwnershipProof


class OwnershipProofTests(unittest.TestCase):
    def test_each_proof_observes_current_owner_and_refuses_foreign_resource(self):
        observations = iter(
            [
                [{"Id": "owned", "Config": {"Labels": {"owner": "checkout"}}}],
                [{"Id": "owned", "Config": {"Labels": {"owner": "foreign"}}}],
            ]
        )
        calls = []

        def inspect(kind, identifiers, *, optional=False):
            calls.append((kind, identifiers, optional))
            return next(observations)

        proof = OwnershipProof(inspect)
        self.assertEqual(proof.owned("container", ["owned"], {"owner": "checkout"})[0]["Id"], "owned")
        with self.assertRaises(OwnershipMismatch) as caught:
            proof.owned("container", ["owned"], {"owner": "checkout"})
        self.assertEqual(caught.exception.reason, "ownership")
        self.assertEqual(calls, [("container", ["owned"], False)] * 2)

    def test_recorded_identity_and_fingerprint_are_rechecked_before_use(self):
        record = {
            "kind": "container", "id": "owned",
            "labels": {"owner": "payments"}, "image": "original-image",
            "mounts": [{"Type": "bind", "Source": "/owned", "Destination": "/data"}],
        }
        original = {
            "Id": "owned", "Config": {"Labels": {"owner": "payments"}},
            "Image": "original-image", "Mounts": deepcopy(record["mounts"]),
        }
        for field, value, reason in (
            ("Id", "replacement", "identity"),
            ("Config", {"Labels": {"owner": "payments", "extra": "changed"}}, "identity"),
            ("Image", "replacement-image", "fingerprint"),
            ("Mounts", [], "fingerprint"),
        ):
            with self.subTest(field=field):
                changed = deepcopy(original)
                changed[field] = value
                observations = iter([[deepcopy(original)], [changed]])
                proof = OwnershipProof(lambda *args, **kwargs: next(observations))
                self.assertEqual(proof.unchanged(record), original)
                with self.assertRaises(OwnershipMismatch) as caught:
                    proof.unchanged(record)
                self.assertEqual(caught.exception.reason, reason)

    def test_batch_refuses_a_foreign_resource_after_an_owned_one(self):
        calls = []

        def inspect(kind, identifiers, *, optional=False):
            calls.append((kind, list(identifiers), optional))
            return [
                {"Id": "ours", "Config": {"Labels": {"owner": "checkout"}}},
                {"Id": "foreign", "Config": {"Labels": {"owner": "other"}}},
            ]

        with self.assertRaises(OwnershipMismatch):
            OwnershipProof(inspect).owned("container", ["ours", "foreign"], {"owner": "checkout"})
        self.assertEqual(calls, [("container", ["ours", "foreign"], False)])

    def test_named_resources_allow_extra_labels_and_preserve_optional_absence(self):
        for kind in ("network", "volume"):
            with self.subTest(kind=kind):
                observations = iter([[{"Labels": {"owner": "checkout", "extra": "compose"}}], []])
                calls = []

                def inspect(observed_kind, identifiers, *, optional=False):
                    calls.append((observed_kind, list(identifiers), optional))
                    return next(observations)

                proof = OwnershipProof(inspect)
                self.assertTrue(proof.owned(kind, ["named"], {"owner": "checkout"}, optional=True))
                self.assertEqual(proof.owned(kind, ["named"], {"owner": "checkout"}, optional=True), [])
                self.assertEqual(calls, [(kind, ["named"], True)] * 2)

    def test_network_recheck_uses_exact_labels_without_container_fingerprint(self):
        record = {"kind": "network", "id": "network-id", "labels": {"owner": "payments"}}
        observations = iter([
            [{"Id": "network-id", "Labels": {"owner": "payments"}}],
            [{"Id": "network-id", "Labels": {"owner": "payments", "extra": "changed"}}],
        ])
        proof = OwnershipProof(lambda *args, **kwargs: next(observations))
        self.assertEqual(proof.unchanged(record)["Id"], "network-id")
        with self.assertRaises(OwnershipMismatch) as caught:
            proof.unchanged(record)
        self.assertEqual(caught.exception.reason, "identity")

    def test_reader_failure_is_preserved_for_the_lifecycle_owner(self):
        error = OSError("socket closed")

        def inspect(*args, **kwargs):
            raise error

        with self.assertRaises(OSError) as caught:
            OwnershipProof(inspect).owned("container", ["owned"], {"owner": "checkout"})
        self.assertIs(caught.exception, error)

    def test_recheck_preserves_raw_missing_and_null_labels(self):
        for kind in ("container", "network"):
            for raw_labels in ({}, {"Labels": None}):
                with self.subTest(kind=kind, labels=raw_labels):
                    record = {"kind": kind, "id": "owned", "labels": None}
                    resource = {"Id": "owned", **raw_labels}
                    if kind == "container":
                        record.update(image="image", mounts=[])
                        resource.update(Config=raw_labels, Image="image", Mounts=[])
                    proof = OwnershipProof(lambda *args, **kwargs: [resource])
                    self.assertEqual(proof.unchanged(record), resource)


if __name__ == "__main__":
    unittest.main()
