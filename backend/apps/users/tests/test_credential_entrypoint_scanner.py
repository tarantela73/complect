from pathlib import Path

from django.conf import settings

from apps.users.security.credential_entrypoint_scanner import scan_credential_entrypoints
from apps.users.security.credential_entrypoints import load_credential_entrypoint_manifest


def test_release_n_credential_entrypoint_scanner_is_clean(settings):
    assert scan_credential_entrypoints(source_root=Path(settings.BASE_DIR)) == []


def test_scanner_rejects_new_unregistered_sensitive_entrypoint(tmp_path):
    root = tmp_path
    path = root / "apps/users/services/new_password_path.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "def verify_password(user, raw):\n    return check_password(raw, user.password)\n",
        encoding="utf-8",
    )
    violations = scan_credential_entrypoints(source_root=root, paths=(path,))
    assert [(row.callable_path, row.reason) for row in violations] == [
        ("apps.users.services.new_password_path.verify_password", "UNREGISTERED_CREDENTIAL_ENTRYPOINT"),
    ]


def test_release_n_retention_workers_are_imported_and_scheduled():
    expected = {
        "apps.users.tasks.identity_merge_maintenance": "identity-merge-expiry",
        "apps.users.tasks.identity_merge_requeue": "identity-merge-requeue",
        "apps.users.tasks.identity_merge_redis_cleanup": "identity-merge-redis-cleanup",
    }
    for module, schedule_name in expected.items():
        assert module in settings.CELERY_IMPORTS
        assert settings.CELERY_BEAT_SCHEDULE[schedule_name]["options"]["queue"] == "retention"


def test_future_security_entrypoints_remain_absent_at_release_n():
    entries = {
        entry["name"]: entry["status"]
        for entry in load_credential_entrypoint_manifest()["entries"]
    }
    assert entries["password_change"] == "not_present_at_release_n"
    assert entries["admin_password_reset"] == "not_present_at_release_n"
    assert entries["global_lock_unlock"] == "not_present_at_release_n"
    assert entries["logout_all"] == "not_present_at_release_n"
    assert entries["recovery"] == "not_present_at_release_n"
