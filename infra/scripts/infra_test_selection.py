"""Authoritative selections for the local infrastructure test gates.

Keep paths relative to the repository.  Payments' Docker adapter turns them
into container paths, while the local runner uses the same values directly.
"""

from __future__ import annotations


PYTEST_FILES: tuple[str, ...] = (
    "infra/scripts/tests/test_infra_context.py",
    "infra/scripts/tests/test_pytest_lifecycle.py",
    "infra/scripts/tests/test_lifecycle_database.py",
    "infra/scripts/tests/test_payments_replica.py",
    "infra/scripts/tests/test_payments_cleanup.py",
    "infra/scripts/tests/test_lifecycle_session.py",
    "infra/scripts/tests/test_resource_ownership_compatibility.py",
    "infra/scripts/tests/test_legacy_phase_contract.py",
    "infra/scripts/tests/test_legacy_qualification.py",
    "infra/scripts/tests/test_legacy_cleanup_configuration.py",
    "infra/scripts/tests/test_legacy_regression_runtime_paths.py",
    "infra/scripts/tests/test_qualification_resources.py",
    "infra/scripts/tests/test_qualification_processes.py",
)

PAYMENTS_UNITTEST_NAMES: tuple[str, ...] = (
    "infra.scripts.tests.test_dev_image_identity.ImageIdentityTests",
    "infra.scripts.tests.test_contract_test_support_boundary",
    "infra.scripts.tests.test_payments_gate_report",
    "infra.scripts.tests.test_payments_evidence",
    "infra.scripts.tests.test_payments_full_gate",
    "infra.scripts.tests.test_docker_ownership.OwnershipProofTests",
)


def pytest_paths(*, root: str = "") -> list[str]:
    """Return the shared pytest selection, optionally rooted at ``root``."""

    return [f"{root.rstrip('/')}/{path}" if root else path for path in PYTEST_FILES]


def payments_unittest_names() -> list[str]:
    """Return the unittest classes included in the Payments infra gate."""

    return list(PAYMENTS_UNITTEST_NAMES)


def required_gates(changed_paths: list[str]) -> list[str]:
    """Select existing host gates; unknown infra tests fail toward more coverage."""
    from pathlib import PurePosixPath

    gates = ["agent-infra-test"]
    needs_payments = False
    for path in changed_paths:
        parsed = PurePosixPath(path)
        if not path or parsed.is_absolute() or ".." in parsed.parts:
            raise ValueError("unsafe changed path")
        if (path.startswith("infra/scripts/") or path.startswith("backend/test_support/")
                or path in {"Makefile", "infra/Dockerfile.tests", "backend/Dockerfile.dev"}):
            needs_payments = True
    return gates + (["test-payments-gate"] if needs_payments else [])
