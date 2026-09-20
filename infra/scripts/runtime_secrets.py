"""Materialize checkout-owned secrets into a private Docker volume, without rotation."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

from dev_environment import DevError


def materialize(source: Path, target: Path, owners: dict[str, tuple[int, int]]) -> None:
    if source.is_symlink() or not source.is_dir():
        raise DevError("Secret source must be a real private directory")
    target.mkdir(mode=0o711, parents=True, exist_ok=True)
    if target.is_symlink():
        raise DevError("Runtime secret directory must not be a symlink")
    if not any(target.iterdir()):
        target.chmod(0o711)
    elif target.stat().st_mode & 0o777 != 0o711:
        raise DevError("Unsafe runtime secret directory mode")
    for name in owners:
        source_name = name.removeprefix("app_")
        info = (source / source_name).lstat()
        if not stat.S_ISREG(info.st_mode):
            raise DevError(f"Secret source is not a regular file: {source_name}")
        with os.fdopen(os.open(source / source_name, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            value = stream.read(4097)
        if not 32 <= len(value.strip()) <= 4096:
            raise DevError(f"Invalid local secret size: {name}")
        path = target / name
        uid, gid = owners[name]
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        except FileExistsError:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_mode & 0o777) != (uid, gid, 0o400):
                raise DevError(f"Runtime secret permissions differ: {name}; manual review required")
            if path.read_bytes() != value:
                raise DevError(f"Runtime secret differs: {name}; refusing to rotate existing credentials")
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(value)
                os.fchown(stream.fileno(), uid, gid)
    print("PASS: runtime secret permissions verified; existing values preserved")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/source-secrets"))
    parser.add_argument("--target", type=Path, default=Path("/run/complect-secrets"))
    for name in ("postgres", "kong", "minio"):
        parser.add_argument(f"--{name}-uid", type=int, required=True)
        parser.add_argument(f"--{name}-gid", type=int, required=True)
    parser.add_argument("--app-uid", type=int, default=os.getuid())
    parser.add_argument("--app-gid", type=int, default=os.getgid())
    arguments = parser.parse_args()
    owners = {
        "django_secret_key": (arguments.app_uid, arguments.app_gid),
        "app_postgres_password": (arguments.app_uid, arguments.app_gid),
        "app_gateway_token": (arguments.app_uid, arguments.app_gid),
        "postgres_password": (arguments.postgres_uid, arguments.postgres_gid),
        "replication_password": (arguments.postgres_uid, arguments.postgres_gid),
        "gateway_token": (arguments.kong_uid, arguments.kong_gid),
        "minio_password": (arguments.minio_uid, arguments.minio_gid),
    }
    try:
        materialize(arguments.source, arguments.target, owners)
    except (DevError, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
