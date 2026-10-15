"""Validate the fixed legacy pytest lifecycle phase contract."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
PHASES = ("setup", "call", "teardown")
ORIGINAL_NODE_IDS_SHA256 = "56626883e2be0b0573b16630042739ac6b5d72375c7385294ee29693c3c5be02"
ORIGINAL_NODE_IDS = (
    "apps/contragents/tests/test_crypto_vault.py::TestPiiCryptoResilience::test_pgp_sym_encrypt_uses_salt_and_generates_unique_ciphertexts",
    "apps/users/tests/test_user_admin_service.py::TestCreateUser::test_creates_user_with_correct_tenant",
    "apps/users/tests/test_user_admin_service.py::TestCreateUser::test_returns_working_temp_password",
    "apps/users/tests/test_user_admin_service.py::TestCreateUser::test_assigns_requested_roles",
    "apps/users/tests/test_user_admin_service.py::TestCreateUser::test_duplicate_email_raises",
    "apps/users/tests/test_user_admin_service.py::TestCreateUser::test_unknown_role_raises",
    "apps/users/tests/test_user_admin_service.py::TestDisableUser::test_disables_user",
    "apps/users/tests/test_user_admin_service.py::TestDisableUser::test_self_protection_raises",
    "apps/users/tests/test_user_admin_service.py::TestDisableUser::test_last_admin_raises",
    "apps/users/tests/test_user_admin_service.py::TestEnableUser::test_enables_user",
    "apps/users/tests/test_user_admin_service.py::TestAssignRoles::test_replaces_all_roles",
    "apps/users/tests/test_user_admin_service.py::TestAssignRoles::test_last_admin_lock_raises",
    "apps/users/tests/test_user_admin_service.py::TestAssignRoles::test_self_admin_removal_raises",
    "apps/users/tests/test_user_admin_service.py::TestAssignRoles::test_invalidates_policy_cache",
    "apps/users/tests/test_admin_api.py::TestListUsers::test_admin_can_list_users",
    "apps/users/tests/test_admin_api.py::TestListUsers::test_viewer_can_list_users",
    "apps/users/tests/test_admin_api.py::TestListUsers::test_unauthenticated_gets_401_or_403",
    "apps/users/tests/test_admin_api.py::TestCreateUser::test_admin_creates_user",
    "apps/users/tests/test_admin_api.py::TestCreateUser::test_manager_cannot_create_user",
    "apps/users/tests/test_admin_api.py::TestCreateUser::test_duplicate_email_returns_422",
    "apps/users/tests/test_admin_api.py::TestDisableEnableUser::test_admin_disables_user",
    "apps/users/tests/test_admin_api.py::TestDisableEnableUser::test_admin_enables_user",
    "apps/users/tests/test_admin_api.py::TestDisableEnableUser::test_manager_cannot_disable",
    "apps/users/tests/test_admin_api.py::TestAssignRoles::test_admin_assigns_roles",
    "apps/users/tests/test_admin_api.py::TestAssignRoles::test_manager_cannot_assign_roles",
    "apps/users/tests/test_admin_api.py::TestListRoles::test_admin_can_list_roles",
    "apps/users/tests/test_admin_api.py::TestListRoles::test_viewer_can_list_roles",
    "apps/users/tests/test_local_files_provisioning.py::test_local_profile_has_exact_permissions_and_tenant_actor",
    "apps/users/tests/test_local_files_provisioning.py::test_repeat_preserves_password_and_creates_no_duplicates",
    "apps/users/tests/test_local_files_provisioning.py::test_foreign_role_name_conflict_does_not_create_identity",
    "apps/users/tests/test_local_files_provisioning.py::test_database_mismatch_refuses_before_any_write",
    "apps/users/tests/test_local_files_provisioning.py::test_nonlocal_refuses_before_any_write",
    "apps/users/tests/test_local_files_provisioning.py::test_failed_identity_creation_rolls_back_role_and_registry",
    "apps/users/tests/test_local_files_provisioning.py::test_membership_is_invisible_in_another_tenant",
    "apps/users/tests/test_local_files_provisioning.py::test_operator_cannot_be_fabricated_through_user_service",
    "apps/users/tests/test_local_files_provisioning.py::test_private_command_preserves_settings_and_credentials_on_repeat",
    "apps/users/tests/test_local_files_provisioning.py::test_command_rejects_nonprivate_file_before_database_write",
    "apps/users/tests/test_local_files_provisioning.py::test_repeat_rejects_missing_binding_without_repair",
    "apps/notifications/tests/test_notifications.py::TestNotificationsConsumerDeferredCallbacks::test_fanout_callbacks_keep_each_recipient",
    "apps/contragents/tests/test_registration_tenancy.py::TestRegistrationTenancyIsolation::test_cross_tenant_pii_registration_raises_permission_denied",
    "apps/contragents/tests/test_registration_tenancy.py::TestRegistrationTenancyIsolation::test_same_tenant_pii_registration_succeeds",
    "apps/contragents/tests/test_registration_tenancy.py::TestRegistrationTenancyIsolation::test_nonexistent_contragent_raises_permission_denied",
    "apps/users/tests/test_create_test_users_command.py::test_authorized_call_provisions_all_fixed_users",
    "apps/users/tests/test_create_test_users_command.py::test_public_create_user_without_operator_context_is_rejected",
    "apps/users/tests/test_create_test_users_command.py::test_nonlocal_environment_is_rejected_without_side_effects",
    "apps/users/tests/test_create_test_users_command.py::test_real_local_environment_guard_accepts_disposable_restricted_database",
    "apps/users/tests/test_create_test_users_command.py::test_repeat_preserves_existing_identity_and_roles",
    "apps/users/tests/test_create_test_users_command.py::test_username_email_conflict_is_rejected_before_any_write",
    "apps/users/tests/test_create_test_users_command.py::test_existing_identity_in_other_tenant_is_not_replayed_into_public_site",
    "apps/users/tests/test_create_test_users_command.py::test_late_service_failure_rolls_back_the_whole_batch",
    "apps/users/tests/test_create_test_users_command.py::test_unsupported_force_option_is_rejected",
    "apps/contragents/tests/test_person_profile_audit_regression.py::test_audit_append_uses_plain_select_tail_and_persists_exact_row",
    "apps/contragents/tests/test_person_profile_audit_regression.py::test_durable_pii_audit_rejects_ambient_atomic_before_append",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_audit_is_committed_before_ciphertext_and_decrypt",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_audit_failure_never_selects_or_decrypts_pii",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_decrypt_failure_cannot_rollback_committed_audit",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_forged_pii_metadata_is_not_authority",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_foreign_tenant_profile_cannot_be_returned",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_ciphertext_hex_and_legacy_writer_remain_compatible",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_corrupt_or_wrong_key_never_returns_plaintext",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_textual_ciphertext_is_not_encoded_twice",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_request_ambient_transaction_is_rejected_before_pii",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_null_and_empty_keep_the_existing_empty_rule[None]",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_null_and_empty_keep_the_existing_empty_rule[0]",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_null_and_empty_keep_the_existing_empty_rule[1]",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_null_and_empty_keep_the_existing_empty_rule[\\\\x]",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_null_and_empty_keep_the_existing_empty_rule[b'']",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_concurrent_genesis_appends_have_one_verifiable_chain",
    "apps/contragents/tests/test_person_profile_read_policy.py::test_runtime_group_cannot_mutate_or_truncate_chain",
    "apps/contragents/tests/test_person_profile_existing_contracts.py::test_existing_append_contract_produces_a_verifiable_persisted_hash",
    "apps/contragents/tests/test_person_profile_existing_contracts.py::test_existing_bytes_writer_textfield_reader_postgresql_roundtrip",
    "apps/contracts/tests/test_legacy_document_mapping.py::test_real_auth_export_and_file_proof_record_distinct_ids_idempotently",
    "apps/contracts/tests/test_legacy_document_mapping.py::test_missing_verified_context_never_writes",
    "apps/contracts/tests/test_legacy_document_mapping.py::test_off_mode_never_authorizes_mapping",
    "apps/contracts/tests/test_legacy_document_mapping.py::test_source_target_mismatch_never_creates_mapping[object_key-unproven-key]",
    "apps/contracts/tests/test_legacy_document_mapping.py::test_source_target_mismatch_never_creates_mapping[checksum_sha256-0000000000000000000000000000000000000000000000000000000000000000]",
    "apps/contracts/tests/test_legacy_document_mapping.py::test_source_target_mismatch_never_creates_mapping[size_bytes-1]",
    "apps/contracts/tests/test_legacy_document_mapping.py::test_database_rejects_incomplete_resolved_mapping",
    "apps/common/tests/test_legacy_task_recovery.py::test_recovery_sql_is_tenant_scoped_and_repeat_preserves_original_envelope",
    "apps/common/tests/test_legacy_task_recovery.py::test_unresolved_and_completed_records_cannot_receive_scan_checkpoint",
    "apps/files/tests/test_legacy_document_scan.py::test_real_clean_scan_promotes_confirmed_bytes_and_emits_available",
    "apps/files/tests/test_legacy_document_scan.py::test_real_infected_scan_retains_quarantine_and_never_promotes",
    "apps/files/tests/test_legacy_document_scan.py::test_checksum_mismatch_never_becomes_available",
    "apps/files/tests/test_legacy_document_scan.py::test_resume_after_confirmed_copy_and_delete_requires_clean_checkpoint",
    "apps/common/tests/test_legacy_compatibility_worker.py::test_real_worker_parks_unresolved_then_replays_verified_mapping_and_deduplicates",
    "apps/common/tests/test_legacy_compatibility_worker.py::test_double_durability_failure_stops_worker_and_requeues_original_envelope",
    "apps/common/tests/test_legacy_compatibility_worker.py::test_controlled_reconciliation_requires_real_worker_quiescence",
    "apps/common/tests/test_sync_redis_metrics.py::test_budget_reads_real_redis_list",
    "apps/common/tests/test_sync_redis_metrics.py::test_queue_depth_reads_real_redis_integer",
    "apps/common/tests/test_sync_redis_metrics.py::test_websocket_count_reads_real_redis_string",
    "apps/common/tests/test_sync_redis_metrics.py::test_dlq_inspection_reads_real_redis_stream",
    "apps/common/tests/test_sync_redis_metrics.py::test_replica_health_routes_only_current_context_to_primary",
    "apps/integration/tests/test_pkk_client.py::test_search_cadastre_returns_result[False]",
    "apps/integration/tests/test_pkk_client.py::test_search_cadastre_returns_result[True]",
    "apps/contragents/tests/test_pii_governance.py::TestPiiSecretsGovernance::test_pii_master_key_is_not_stored_in_django_settings",
    "apps/files/tests/test_clamav_scan_engine.py::TestClamAVResponseParsing::test_ok_response_returns_clean",
    "apps/files/tests/test_clamav_scan_engine.py::TestClamAVResponseParsing::test_found_response_returns_infected",
    "apps/files/tests/test_clamav_scan_engine.py::TestClamAVResponseParsing::test_unknown_response_returns_error",
    "apps/files/tests/test_clamav_scan_engine.py::TestClamAVResponseParsing::test_empty_response_returns_error",
    "apps/files/tests/test_clamav_scan_engine.py::TestClamAVErrorPaths::test_timeout_returns_error",
    "apps/files/tests/test_clamav_scan_engine.py::TestClamAVErrorPaths::test_connection_refused_returns_error",
    "apps/files/tests/test_clamav_scan_engine.py::TestClamAVErrorPaths::test_recv_timeout_returns_error",
    "apps/files/tests/test_clamav_scan_engine.py::TestClamAVErrorPaths::test_broken_pipe_returns_error",
    "apps/files/tests/test_clamav_scan_engine.py::TestClamAVConfiguration::test_socket_path_taken_from_settings",
    "apps/files/tests/test_clamav_scan_engine.py::TestClamAVConfiguration::test_explicit_socket_path_overrides_settings",
    "apps/files/tests/test_clamav_scan_engine.py::TestClamAVConfiguration::test_timeout_applied_from_settings",
    "apps/files/tests/test_clamav_scan_engine.py::TestClamAVConfiguration::test_object_key_sent_to_daemon",
    "apps/files/tests/test_clamav_scan_engine.py::TestGetScanEngineFactory::test_stub_setting_returns_stub_engine",
    "apps/files/tests/test_clamav_scan_engine.py::TestGetScanEngineFactory::test_clamav_setting_returns_clamav_engine",
    "apps/files/tests/test_clamav_scan_engine.py::TestGetScanEngineFactory::test_clamav_engine_has_correct_socket_path",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestCircuitBreakerStateTransitions::test_initially_closed",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestCircuitBreakerStateTransitions::test_opens_after_threshold_failures",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestCircuitBreakerStateTransitions::test_stays_closed_below_threshold",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestCircuitBreakerStateTransitions::test_success_resets_failure_counter",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestCircuitBreakerStateTransitions::test_success_closes_open_circuit",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestCircuitBreakerStateTransitions::test_failure_while_open_does_not_re_arm_ttl",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestCircuitBreakerStateTransitions::test_failure_counter_increments_correctly",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestCircuitBreakerStateTransitions::test_open_key_set_when_circuit_opens",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestCircuitBreakerStateTransitions::test_failure_counter_cleared_when_circuit_opens",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestGetCircuitBreakerFactory::test_factory_reads_settings",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestGetCircuitBreakerFactory::test_factory_returns_circuit_breaker_instance",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestClamAVScanEngineCircuitBreaker::test_circuit_opens_after_threshold_socket_errors",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestClamAVScanEngineCircuitBreaker::test_scan_fast_fails_when_circuit_open",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestClamAVScanEngineCircuitBreaker::test_circuit_closes_after_successful_probe",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestClamAVScanEngineCircuitBreaker::test_infected_result_closes_open_circuit",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestClamAVScanEngineCircuitBreaker::test_circuit_stays_open_after_failed_probe",
    "apps/files/tests/test_clamav_circuit_breaker.py::TestClamAVScanEngineCircuitBreaker::test_circuit_reopens_immediately_on_failed_probe_at_threshold_1",
    "apps/common/tests/test_legacy_clamav_result.py::test_clean_clamd_reply_is_clean",
    "apps/common/tests/test_legacy_clamav_result.py::test_infected_clamd_reply_cannot_be_clean",
    "apps/common/tests/test_legacy_clamav_result.py::test_missing_or_unknown_clamd_reply_requires_retry[None]",
    "apps/common/tests/test_legacy_clamav_result.py::test_missing_or_unknown_clamd_reply_requires_retry[reply1]",
    "apps/common/tests/test_legacy_clamav_result.py::test_missing_or_unknown_clamd_reply_requires_retry[reply2]",
    "apps/common/tests/test_legacy_task_recovery.py::test_durable_transport_parking_is_idempotent_and_preserves_unknown_form",
)


def _diagnostic(message: str) -> str:
    return message


def validate_legacy_phases(manifest: Mapping[str, object]) -> list[str]:
    """Return deterministic diagnostics for an exact 133 x 3 lifecycle manifest."""
    errors: list[str] = []
    if not isinstance(manifest, Mapping):
        return [_diagnostic("manifest malformed: expected mapping")]

    collected = manifest.get("collected")
    if not isinstance(collected, list) or any(not isinstance(node, str) or not node for node in collected):
        errors.append("collected malformed: expected non-empty string node ID list")
        collected_nodes: list[str] = []
    else:
        collected_nodes = collected

    seen_collected: set[str] = set()
    for node in collected_nodes:
        if node in seen_collected:
            errors.append(f"duplicate node ID: {node}")
        seen_collected.add(node)

    expected_nodes = set(ORIGINAL_NODE_IDS)
    actual_nodes = set(collected_nodes)
    for node in sorted(expected_nodes - actual_nodes):
        errors.append(f"missing node ID: {node}")
    for node in sorted(actual_nodes - expected_nodes):
        errors.append(f"extra node ID: {node}")

    if (
        len(collected_nodes) != len(ORIGINAL_NODE_IDS)
        or len(seen_collected) != len(ORIGINAL_NODE_IDS)
        or actual_nodes != expected_nodes
        or hashlib.sha256(json.dumps(collected_nodes, separators=(",", ":")).encode()).hexdigest()
        != ORIGINAL_NODE_IDS_SHA256
    ):
        errors.append("collected malformed: does not match immutable 133-node contract")

    reports = manifest.get("reports")
    if not isinstance(reports, list):
        errors.append("reports malformed: expected list")
        return errors

    observed: dict[tuple[str, str], int] = {}
    for index, item in enumerate(reports):
        if not isinstance(item, Mapping):
            errors.append(f"malformed report[{index}]: expected mapping")
            continue
        report_node = item.get("nodeid")
        phase = item.get("when")
        outcome = item.get("outcome")
        if not isinstance(report_node, str) or not report_node:
            errors.append(f"malformed report[{index}] node ID")
            continue
        if not isinstance(phase, str) or phase not in PHASES:
            errors.append(f"extra phase record: {report_node}/{phase!r}")
            continue
        key = (report_node, phase)
        observed[key] = observed.get(key, 0) + 1
        if report_node not in expected_nodes:
            errors.append(f"extra node ID in report: {report_node}/{phase}")
        if observed[key] > 1:
            errors.append(f"duplicate phase: {report_node}/{phase}")
        if outcome != "passed":
            if outcome == "skipped":
                errors.append(f"skipped phase: {report_node}/{phase}")
            elif outcome is None or not isinstance(outcome, str):
                errors.append(f"malformed outcome: {report_node}/{phase}")
            else:
                errors.append(f"failed phase: {report_node}/{phase} outcome={outcome!r}")

    for node in ORIGINAL_NODE_IDS:
        for phase in PHASES:
            key = (node, phase)
            if key not in observed:
                errors.append(f"missing phase: {node}/{phase}")
    return errors
