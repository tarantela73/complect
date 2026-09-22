"""Foundation must remain disconnected until separately approved integration slices."""

import ast
from pathlib import Path

SYMBOLS = {
    "IdentityCleanupAdmission",
    "IdentityCleanupAdmissionSubject",
    "IdentityCleanupLifecycleEvent",
    "IdentityHoldRequest",
    "IdentityHoldRequestSubject",
    "IdentityHoldLifecycleEvent",
    "IdentityCleanupAdmissionRepository",
    "IdentityHoldRequestRepository",
    "CleanupTerminalReceipt",
    "CleanupAdmissionOwner",
    "cleanup_catalog_phase",
}
OWNERS = {
    "apps/users/models.py",
    "apps/users/models_cleanup_protocol.py",
    "apps/users/repositories/identity_cleanup_admission_repository.py",
    "apps/users/repositories/identity_hold_request_repository.py",
    "apps/users/security/identity_cleanup_admission.py",
}


def test_no_existing_runtime_entrypoint_references_foundation_writers_or_models():
    root = Path(__file__).resolve().parents[3]
    unexpected = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if "tests" in relative.parts or "migrations" in relative.parts or relative.as_posix() in OWNERS:
            continue
        tree = ast.parse(path.read_text(), filename=str(relative))
        for node in ast.walk(tree):
            values = []
            if isinstance(node, ast.Name):
                values.append(node.id)
            elif isinstance(node, ast.Attribute):
                values.append(node.attr)
            elif isinstance(node, ast.alias):
                values.append(node.name)
            elif isinstance(node, ast.ImportFrom):
                values.append(node.module or "")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                values.append(node.value)
            if any(
                value in SYMBOLS
                or "identity_cleanup_admission" in value
                or "identity_hold_request" in value
                or "models_cleanup_protocol" in value
                or "users_identity_cleanup_" in value
                or "users_identity_hold_requests" in value
                for value in values
            ):
                unexpected.append((str(relative), node.lineno))
    assert unexpected == [], f"Foundation runtime integration is outside this slice: {unexpected}"
