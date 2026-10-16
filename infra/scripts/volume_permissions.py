"""Initialize only empty, project-owned runtime volume roots; never chown the checkout."""

import os
import sys
import argparse
from pathlib import Path

DEFAULT_VOLUMES = ("state", "backend", "frontend-modules", "frontend-next", "e2e-modules")


def parse_arguments(arguments: list[str]) -> tuple[int, int, tuple[str, ...]]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("uid", type=int)
    parser.add_argument("gid", type=int)
    parser.add_argument("--volumes", nargs="+", choices=DEFAULT_VOLUMES, default=DEFAULT_VOLUMES)
    parsed = parser.parse_args(arguments)
    return parsed.uid, parsed.gid, tuple(parsed.volumes)


def enforce_volume_ownership(uid: int, gid: int, names: tuple[str, ...], *, root: Path = Path("/owned")) -> None:
    for name in names:
        path = root / name
        info = path.lstat()
        if path.is_symlink() or not path.is_dir():
            raise SystemExit(f"FAIL: invalid runtime volume root: {name}")
        if (info.st_uid, info.st_gid) != (uid, gid):
            if any(path.iterdir()):
                raise SystemExit(f"FAIL: nonempty runtime volume has another owner: {name}; manual review required")
            os.chown(path, uid, gid)
            path.chmod(0o700)


def main(arguments: list[str] | None = None) -> int:
    uid, gid, names = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    enforce_volume_ownership(uid, gid, names)
    print("PASS: project runtime volume ownership verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
