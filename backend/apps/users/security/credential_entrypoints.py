import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import TypedDict, cast

MANIFEST_PATH = Path(__file__).with_name("credential_entrypoint_manifest.json")


class CredentialEntrypointManifestEntry(TypedDict):
    name: str
    callable: str | None
    status: str


class CredentialEntrypointManifest(TypedDict):
    schema_version: int
    entries: list[CredentialEntrypointManifestEntry]


@lru_cache(maxsize=1)
def load_credential_entrypoint_manifest() -> CredentialEntrypointManifest:
    return cast(CredentialEntrypointManifest, json.loads(MANIFEST_PATH.read_text(encoding="utf-8")))


def credential_entrypoint_manifest_digest() -> str:
    manifest = load_credential_entrypoint_manifest()
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def require_registered_credential_entrypoint(callable_path: str) -> None:
    entries = load_credential_entrypoint_manifest()["entries"]
    if not any(entry["callable"] == callable_path and entry["status"] == "present" for entry in entries):
        raise RuntimeError("UNREGISTERED_IAM_CREDENTIAL_ENTRYPOINT")
