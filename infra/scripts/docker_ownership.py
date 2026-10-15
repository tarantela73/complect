"""Fresh Docker ownership proofs; callers own effects and user diagnostics.

The reader adapter preserves each lifecycle's inspect batching and missing-resource
policy. A proof never caches observations, removes resources or writes evidence.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any


class OwnershipMismatch(Exception):
    """A failed proof, mapped to existing diagnostics by the lifecycle owner."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class OwnershipProof:
    def __init__(self, inspect: Callable[..., list[dict[str, Any]]]):
        self._inspect = inspect

    @staticmethod
    def _labels(kind: str, resource: Mapping[str, Any]):
        if kind == "container":
            return resource["Config"].get("Labels")
        return resource.get("Labels")

    def owned(
        self,
        kind: str,
        identifiers: Sequence[str],
        labels: Mapping[str, str],
        *,
        optional: bool = False,
    ) -> list[dict[str, Any]]:
        resources = self._inspect(kind, identifiers, optional=optional)
        for resource in resources:
            observed = self._labels(kind, resource) or {}
            if any(observed.get(key) != value for key, value in labels.items()):
                raise OwnershipMismatch("ownership")
        return resources

    def unchanged(self, record: Mapping[str, Any]) -> dict[str, Any]:
        kind = record["kind"]
        resource = self._inspect(kind, [record["id"]], optional=False)[0]
        if resource["Id"] != record["id"] or self._labels(kind, resource) != record["labels"]:
            raise OwnershipMismatch("identity")
        if kind == "container" and (
            resource["Image"] != record["image"] or resource["Mounts"] != record["mounts"]
        ):
            raise OwnershipMismatch("fingerprint")
        return resource
