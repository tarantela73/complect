import ast
from dataclasses import dataclass
from pathlib import Path

from apps.users.security.credential_entrypoints import load_credential_entrypoint_manifest


@dataclass(frozen=True, slots=True)
class CredentialEntrypointViolation:
    callable_path: str
    reason: str


SENSITIVE_NAMES = {
    "check_password",
    "decode",
    "make_password",
    "_make_access_token",
    "_make_refresh_token",
    "mfa_secret",
    "mfa_enabled",
    "is_active",
}

INTERNAL_CREDENTIAL_HELPERS = {
    "apps.users.services.identity_foundation.projected_identity_foundation",
    "apps.users.services.merge_convergence_service.CanonicalTotpVerifier.verify",
}


def _callable_path(path: Path, source_root: Path, class_name: str | None, function_name: str) -> str:
    module = ".".join(path.relative_to(source_root).with_suffix("").parts)
    return ".".join(part for part in (module, class_name, function_name) if part)


def scan_credential_entrypoints(
    *,
    source_root: Path,
    paths: tuple[Path, ...] | None = None,
) -> list[CredentialEntrypointViolation]:
    manifest = load_credential_entrypoint_manifest()
    registered = {
        entry["callable"]
        for entry in manifest["entries"]
        if entry["status"] == "present" and entry["callable"]
    }
    required = {
        "apps.users.services.auth_service.login",
        "apps.common.api.auth.JWTAuth.authenticate",
        "apps.users.services.auth_service.refresh_access_token",
        "apps.users.services.auth_service.logout",
        "apps.users.services.mfa_service.setup_totp",
        "apps.users.services.mfa_service.confirm_totp_setup",
        "apps.users.services.mfa_service.verify_mfa_and_issue_tokens",
        "apps.users.services.user_admin_service.enable_user",
        "apps.users.services.user_admin_service.disable_user",
    }
    violations = [
        CredentialEntrypointViolation(callable_path, "REQUIRED_RELEASE_N_ENTRYPOINT_MISSING")
        for callable_path in sorted(required - registered)
    ]
    scan_paths = paths or tuple(
        sorted((source_root / "apps/users/services").glob("*.py"))
        + [source_root / "apps/common/api/auth.py"],
    )
    for path in scan_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for class_node in [node for node in tree.body if isinstance(node, ast.ClassDef)]:
            violations.extend(_scan_functions(path, source_root, class_node.name, class_node.body, registered))
        violations.extend(_scan_functions(path, source_root, None, tree.body, registered))
    return violations


def _scan_functions(path, source_root, class_name, nodes, registered):
    violations = []
    for node in nodes:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
            continue
        names = {
            child.id
            for child in ast.walk(node)
            if isinstance(child, ast.Name)
        } | {
            child.attr
            for child in ast.walk(node)
            if isinstance(child, ast.Attribute)
        }
        if names & SENSITIVE_NAMES:
            callable_path = _callable_path(path, source_root, class_name, node.name)
            if callable_path not in registered and callable_path not in INTERNAL_CREDENTIAL_HELPERS:
                violations.append(CredentialEntrypointViolation(callable_path, "UNREGISTERED_CREDENTIAL_ENTRYPOINT"))
    return violations
