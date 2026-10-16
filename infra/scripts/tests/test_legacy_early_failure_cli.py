"""Host-driven early Docker inventory failure and cleanup-only recovery."""

from __future__ import annotations

import os
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from infra.scripts.tests.test_legacy_recovery_cli import run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--image", default=os.environ.get("RECOVERY_TEST_IMAGE", "complect-infra-deps:20261016"))
    args = parser.parse_args()
    root = Path(args.output).resolve()
    matrix = {}
    for mode in ("success", "blocked", "tamper"):
        os.environ["COMPLECT_EARLY_INVENTORY_FAILURE"] = "1"
        os.environ["COMPLECT_EARLY_MODE"] = mode
        result = run(root / mode, args.image)
        matrix[mode] = result
    result = {"status": "PASS", "matrix": matrix}
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "result.json", "w", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
