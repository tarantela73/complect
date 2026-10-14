from __future__ import annotations

from infra.scripts.legacy_phase_contract import (
    ORIGINAL_NODE_IDS,
    PHASES,
    validate_legacy_phases,
)

def _valid_manifest() -> dict[str, object]:
    return {
        "collected": list(ORIGINAL_NODE_IDS),
        "reports": [
            {"nodeid": node, "when": phase, "outcome": "passed"}
            for node in ORIGINAL_NODE_IDS
            for phase in PHASES
        ],
    }


def test_accepts_exact_399_passed_phases() -> None:
    assert validate_legacy_phases(_valid_manifest()) == []


def test_reports_missing_phase_with_node_and_phase() -> None:
    manifest = _valid_manifest()
    manifest["reports"] = manifest["reports"][:-1]
    errors = validate_legacy_phases(manifest)
    assert f"missing phase: {ORIGINAL_NODE_IDS[-1]}/teardown" in errors


def test_reports_skipped_duplicate_extra_and_malformed() -> None:
    manifest = _valid_manifest()
    reports = list(manifest["reports"])
    reports[0] = {"nodeid": ORIGINAL_NODE_IDS[0], "when": "setup", "outcome": "skipped"}
    reports.append(reports[1])
    reports.append({"nodeid": "extra::node", "when": "call", "outcome": "passed"})
    reports.append({"nodeid": ORIGINAL_NODE_IDS[1], "when": "bogus", "outcome": "passed"})
    reports.append({"nodeid": ORIGINAL_NODE_IDS[2], "when": "call"})
    manifest["reports"] = reports
    errors = validate_legacy_phases(manifest)
    assert any("skipped phase:" in error for error in errors)
    assert any("duplicate phase:" in error for error in errors)
    assert any("extra node ID in report:" in error for error in errors)
    assert any("extra phase record:" in error for error in errors)
    assert any("malformed outcome:" in error for error in errors)


def test_reports_failed_and_unhashable_report_fields() -> None:
    manifest = _valid_manifest()
    reports = list(manifest["reports"])
    reports[0] = {"nodeid": ["unhashable"], "when": "setup", "outcome": "passed"}
    reports[1] = {"nodeid": ORIGINAL_NODE_IDS[1], "when": ["call"], "outcome": "passed"}
    reports[2] = {"nodeid": ORIGINAL_NODE_IDS[2], "when": "call", "outcome": "failed"}
    manifest["reports"] = reports
    errors = validate_legacy_phases(manifest)
    assert any("malformed report[0] node ID" in error for error in errors)
    assert any("extra phase record:" in error for error in errors)
    assert any("failed phase:" in error for error in errors)


def test_reports_missing_and_extra_collected_ids() -> None:
    manifest = _valid_manifest()
    collected = list(manifest["collected"])
    removed = collected.pop()
    collected.append("extra::node")
    manifest["collected"] = collected
    errors = validate_legacy_phases(manifest)
    assert f"missing node ID: {removed}" in errors
    assert "extra node ID: extra::node" in errors
    assert any("immutable 133-node contract" in error for error in errors)
