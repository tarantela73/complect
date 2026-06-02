import os
from pathlib import Path


def get_docker_secret(
    env_name: str,
    secret_file_env: str = "DATABASE_PASSWORD_FILE",
    default: str = "",
) -> str:
    """Read a secret from a Docker Secret file, falling back to an env var.

    Priority:
    1. File at path given by `secret_file_env` env var (Docker Secret)
    2. Env var `env_name`
    3. `default`
    """
    secret_file = os.getenv(secret_file_env)
    if secret_file:
        p = Path(secret_file)
        if p.is_file():
            return p.read_text().strip()
    return os.getenv(env_name, default)
