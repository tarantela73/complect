"""Совместимый CLI-адаптер валидатора Payments gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from infra.scripts.payments_gate_evidence import (
        MANDATORY_PHASES,
        PHASE_STATUSES,
        TEST_PHASES,
        validate_report,
    )
except ModuleNotFoundError:  # direct execution from infra/scripts
    from payments_gate_evidence import (  # type: ignore[no-redef]
        MANDATORY_PHASES,
        PHASE_STATUSES,
        TEST_PHASES,
        validate_report,
    )

__all__ = [
    "MANDATORY_PHASES",
    "PHASE_STATUSES",
    "TEST_PHASES",
    "validate_report",
    "main",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read report: {exc}", file=sys.stderr)
        return 1
    errors = validate_report(report)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
