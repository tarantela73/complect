from apps.users.security.credential_entrypoints import (
    credential_entrypoint_manifest_digest,
    load_credential_entrypoint_manifest,
    require_registered_credential_entrypoint,
)
from apps.users.security.deferred_merge_execution_policy import DeferredMergeExecutionPolicy


def test_release_n_credential_entrypoint_manifest_is_closed():
    manifest = load_credential_entrypoint_manifest()
    assert manifest["schema_version"] == 1
    names = {entry["name"] for entry in manifest["entries"]}
    assert len(names) == len(manifest["entries"])
    assert len(credential_entrypoint_manifest_digest()) == 64
    require_registered_credential_entrypoint("apps.users.services.auth_service.login")


def test_execution_is_disabled_without_attestation(settings):
    settings.IAM_DEFERRED_MERGE_EXECUTION_ENABLED = True
    settings.IAM_DEFERRED_MERGE_COVERAGE_DIGEST = ""
    assert DeferredMergeExecutionPolicy.evaluate().effective is False


def test_valid_build_bound_attestation_enables_execution(valid_runtime_merge_coverage_attestation):
    evaluation = DeferredMergeExecutionPolicy.evaluate()
    assert evaluation.effective is True
    assert evaluation.reason == "ENABLED"
