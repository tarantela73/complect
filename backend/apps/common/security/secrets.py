import os
from pathlib import Path


class MaskedSecret(str):
    """str subclass that hides the value in repr — safe to log inside dicts."""

    def __repr__(self) -> str:
        return "'[MASKED]'"


def get_secret(
    env_name: str,
    secret_file_env: str | None = None,
    default: str | None = None,
) -> MaskedSecret:
    """Read a secret from a Docker Secret file, an env var, or a default.

    Priority:
    1. File at the path given by ``secret_file_env`` env var (Docker Secret)
    2. Env var ``env_name``
    3. ``default`` (only if explicitly provided)

    Raises RuntimeError when the secret is required (no default) but absent.
    In prod.py call without ``default`` so a missing secret hard-fails at startup.
    """
    if secret_file_env:
        secret_file = os.getenv(secret_file_env)
        if secret_file:
            p = Path(secret_file)
            if p.is_file():
                return MaskedSecret(p.read_text().strip())

    value = os.getenv(env_name)
    if value:
        return MaskedSecret(value)

    if default is not None:
        return MaskedSecret(default)

    raise RuntimeError(
        f"Required secret '{env_name}' is missing: set the env var or provide "
        f"a Docker Secret file via '{secret_file_env}'."
    )
