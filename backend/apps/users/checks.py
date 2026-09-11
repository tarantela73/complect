from django.conf import settings
from django.core.checks import Error, Tags, register

from apps.users.security.credential_entrypoint_scanner import scan_credential_entrypoints
from apps.users.security.deferred_merge_execution_policy import DeferredMergeExecutionPolicy


@register(Tags.security, deploy=True)
def deferred_merge_coverage_check(app_configs, **kwargs):
    if not getattr(settings, "IAM_DEFERRED_MERGE_EXECUTION_ENABLED", False):
        return []
    evaluation = DeferredMergeExecutionPolicy.evaluate()
    if evaluation.effective:
        return []
    return [
        Error(
            f"Deferred IAM merge execution was requested but attestation is invalid: {evaluation.reason}",
            id="users.E024",
        ),
    ]


@register(Tags.security, deploy=True)
def credential_entrypoint_coverage_check(app_configs, **kwargs):
    violations = scan_credential_entrypoints(source_root=settings.BASE_DIR)
    return [
        Error(
            f"Credential entrypoint coverage failed: {violation.callable_path} ({violation.reason})",
            id="users.E025",
        )
        for violation in violations
    ]
