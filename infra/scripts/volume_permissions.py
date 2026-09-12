"""Initialize only empty, project-owned runtime volume roots; never chown the checkout."""

import os
import sys
from pathlib import Path

uid, gid = (int(value) for value in sys.argv[1:])
for name in ("state", "backend", "frontend-modules", "frontend-next", "e2e-modules"):
    path = Path("/owned") / name
    info = path.lstat()
    if path.is_symlink() or not path.is_dir():
        raise SystemExit(f"FAIL: invalid runtime volume root: {name}")
    if (info.st_uid, info.st_gid) != (uid, gid):
        if any(path.iterdir()):
            raise SystemExit(f"FAIL: nonempty runtime volume has another owner: {name}; manual review required")
        os.chown(path, uid, gid)
        path.chmod(0o700)
print("PASS: project runtime volume ownership verified")
