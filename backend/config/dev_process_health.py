"""Read-only process liveness probe: does not publish Celery control messages."""

import os
import sys
from pathlib import Path

role = sys.argv[1]
for path in Path("/proc").glob("[0-9]*/cmdline"):
    if path.parent.name == str(os.getpid()):
        continue
    try:
        command = path.read_bytes().replace(b"\x00", b" ").decode(errors="replace")
    except OSError:
        continue
    if "celery" in command and f" {role}" in command and "dev_process_health" not in command and "sh -" not in command:
        raise SystemExit(0)
raise SystemExit(1)
