from __future__ import annotations

import importlib
from typing import Protocol


class StorageBackend(Protocol):
    """[ADR-0005] Cross-domain storage contract. Import from here, not from apps.files."""

    def generate_upload_url(
        self,
        *,
        object_key: str,
        content_type: str,
        size_bytes: int,
        expires_in: int,
    ) -> str: ...

    def generate_download_url(
        self,
        *,
        object_key: str,
        expires_in: int,
    ) -> str: ...

    def object_exists(self, *, object_key: str) -> bool: ...


def get_storage_backend() -> StorageBackend:
    """
    Returns storage backend configured via FILES_STORAGE_BACKEND setting.
    Uses importlib so apps/common has no static dependency on apps/files.
    """
    from django.conf import settings

    backend_type = getattr(settings, "FILES_STORAGE_BACKEND", "STUB")
    mod = importlib.import_module("apps.files.services.storage_backend")
    if backend_type == "S3":
        return mod.S3StorageBackend()
    return mod.StubStorageBackend()
