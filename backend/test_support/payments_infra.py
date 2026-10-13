"""Separate Docker pytest and unittest evidence for stand tooling."""

from __future__ import annotations

import argparse
import json
import unittest
from pathlib import Path

import pytest
from infra.scripts.infra_test_selection import payments_unittest_names, pytest_paths


class Evidence:
    def __init__(self):
        self.nodes = []
        self.reports = []

    def pytest_collection_finish(self, session):
        self.nodes = [item.nodeid for item in session.items]

    def pytest_runtest_logreport(self, report):
        self.reports.append({"nodeid": report.nodeid, "when": report.when, "outcome": report.outcome})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("pytest", "unittest"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--scope", choices=("local", "gate"), default="gate")
    args = parser.parse_args()
    if args.mode == "pytest":
        evidence = Evidence()
        code = int(
            pytest.main(
                [
                    "-q",
                    "-p",
                    "no:django",
                    "-o",
                    "cache_dir=/tmp/infra-cache",
                    "--rootdir=/workspace",
            *pytest_paths(root="/workspace"),
                ],
                plugins=[evidence],
            )
        )
        result = {"collected": evidence.nodes, "reports": evidence.reports, "exit": code}
    else:
        if args.scope == "local":
            suite = unittest.defaultTestLoader.discover(
                "/workspace/infra/scripts/tests", pattern="test*.py", top_level_dir="/workspace"
            )
        else:
            suite = unittest.defaultTestLoader.loadTestsFromNames([*payments_unittest_names()])
        summary = unittest.TextTestRunner(verbosity=2).run(suite)
        result = {
            "collected": summary.testsRun,
            "passed": summary.testsRun - len(summary.errors) - len(summary.failures) - len(summary.skipped),
            "skipped": len(summary.skipped),
            "errors": len(summary.errors),
            "failures": len(summary.failures),
        }
        code = int(
            not summary.wasSuccessful()
            or not summary.testsRun
            or (args.scope == "gate" and bool(summary.skipped))
        )
    args.manifest.write_text(json.dumps(result, indent=2) + "\n")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
