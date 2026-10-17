import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from infra.scripts.publication_preflight import preflight, stand_source_identity
from infra.scripts.agent_harness import working_tree_snapshot


class PublicationPreflightTests(unittest.TestCase):
    def _run(self, function):
        with tempfile.TemporaryDirectory() as directory:
            function(Path(directory))

    def test_matching_head_and_base(self):
        self._run(test_preflight_accepts_matching_head_and_base)

    def test_unknown_base(self):
        self._run(test_preflight_rejects_unknown_base)

    def test_public_cli_read_only(self):
        self._run(test_public_cli_is_read_only)

    def test_new_pr_requires_target(self):
        self._run(test_new_pr_requires_target_and_never_uses_local_stale_base)

    def test_existing_pr_boundary(self):
        self._run(test_existing_pr_reads_live_base_and_reports_remote_head)

    def test_remote_advanced(self):
        self._run(test_remote_branch_advanced_blocks_stale_selection_base)

    def test_same_tree_precommit(self):
        self._run(test_same_tree_new_commit_is_allowed_by_full_snapshot)

    def test_changed_snapshot(self):
        self._run(test_changed_head_after_snapshot_blocks_precommit_proof)

    def test_pr_base_drift(self):
        self._run(test_existing_pr_base_drift_blocks_against_live_remote)

    def test_cli_uses_current_target_ref_instead_of_historical_pr_base(self):
        import os
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            head, remote, requirements, receipt = complete_setup(root)
            requirements_path = root / "requirements.json"
            requirements_path.write_text(json.dumps(requirements))
            response = root / "ref-response.json"
            response.write_text(json.dumps({"object": {"sha": head, "type": "commit"}}))
            binary = root / "bin"
            binary.mkdir()
            gh = binary / "gh"
            metadata = {"baseRefName": "main", "baseRefOid": "0" * 40,
                        "headRefOid": head, "url": str(remote) + "/pull/17"}
            metadata_path = root / "pr-metadata.json"
            metadata_path.write_text(json.dumps(metadata))
            gh.write_text(
                "#!" + sys.executable + "\nimport json,sys\nfrom pathlib import Path\n"
                + "if sys.argv[1] == 'pr': print(Path(" + repr(str(metadata_path)) + ").read_text())\n"
                + "elif sys.argv[1] == 'api':\n data=Path(" + repr(str(response)) + ").read_text()\n if json.loads(data).get('api_error'): raise SystemExit(1)\n print(data)\n"
                + "else: raise SystemExit(3)\n"
            )
            gh.chmod(0o755)
            command = [sys.executable, "-B", "-m", "infra.scripts.publication_preflight",
                       "--repository", str(root), "--remote", "origin", "--head", head,
                       "--pr", "17", "--requirements", str(requirements_path),
                       "--receipt", str(receipt)]
            environment = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]}
            accepted = subprocess.run(command, capture_output=True, text=True, env=environment)
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            self.assertEqual(json.loads(accepted.stdout)["base"], head)
            metadata["baseRefOid"] = head
            metadata_path.write_text(json.dumps(metadata))
            for invalid in ({"object": {"sha": "0" * 40, "type": "commit"}}, {},
                            {"object": {"sha": head, "type": "tag"}}, {"api_error": True}):
                response.write_text(json.dumps(invalid))
                blocked = subprocess.run(command, capture_output=True, text=True, env=environment)
                self.assertNotEqual(blocked.returncode, 0)
                self.assertEqual(json.loads(blocked.stdout)["status"], "BLOCKED_STAND")

    def test_ls_remote_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def timeout(*_args, **_kwargs):
                raise subprocess.TimeoutExpired("git ls-remote", 30)
            with patch("infra.scripts.publication_preflight.subprocess.run", timeout):
                result = preflight(root, remote="origin", head="missing", target="main", requirements={}, receipts=[])
            self.assertEqual(result["status"], "BLOCKED_STAND")

    def test_pr_head_behind(self):
        self._run(test_existing_pr_head_behind_local_is_blocked)

    def test_pr_unknown_head(self):
        self._run(test_existing_pr_unknown_head_is_blocked)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def complete_setup(tmp_path: Path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    for relative, content in (("backend/a.py", "backend"), ("infra/scripts/z.py", "scripts"), ("infra/postgres/x.sql", "postgres")):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "one"], check=True)
    head = git(tmp_path, "rev-parse", "HEAD")
    tree = git(tmp_path, "rev-parse", "HEAD^{tree}")
    snapshot_path = tmp_path.parent / "snapshot.json"
    snapshot_path.write_text(json.dumps(working_tree_snapshot(tmp_path)))
    remote_path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", str(remote_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "push", "-q", "origin", f"{head}:refs/heads/main"], check=True)
    producer_hash = stand_source_identity(tmp_path, head)
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"version": 1, "status": "PASS", "sources_unchanged": True, "comparison_base": head, "source_identity": {"sha256": producer_hash}}))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"collected": 1, "passed": 1, "skipped": 0, "errors": 0, "failures": 0}))
    receipt = tmp_path / "result.json"
    receipt.write_text(json.dumps({"mode": "pytest", "run_id": "run-1", "image": "image-1", "status": "PASS", "exit": 0, "cleanup": "PASS", "manifest": "manifest.json"}))
    requirements = {"version": 1, "repository": str(remote_path), "source": {"source_commit": head, "source_tree": tree, "producer_sha256": producer_hash, "snapshot": {"path": str(snapshot_path), "sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest()}}, "checks": [{"id": "infra", "producer": "infrastructure", "artifacts": {"selection": {"path": str(selection), "sha256": hashlib.sha256(selection.read_bytes()).hexdigest()}}, "execution": {"kind": "docker", "mode": "pytest", "image": "image-1"}}]}
    return head, remote_path, requirements, receipt


def test_preflight_accepts_matching_head_and_base(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "tracked").write_text("one")
    for relative, content in (("backend/a.py", "backend"), ("infra/scripts/z.py", "scripts"), ("infra/postgres/x.sql", "postgres")):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "one"], check=True)
    head = git(tmp_path, "rev-parse", "HEAD")
    tree = git(tmp_path, "rev-parse", "HEAD^{tree}")
    snapshot_path = tmp_path.parent / "snapshot.json"
    snapshot_path.write_text(json.dumps(working_tree_snapshot(tmp_path)))
    remote_path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", str(remote_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "push", "-q", "origin", f"{head}:refs/heads/main"], check=True)
    receipt = tmp_path / "result.json"
    selection = tmp_path / "selection.json"
    producer_hash = stand_source_identity(tmp_path, head)
    selection.write_text(json.dumps({"version": 1, "status": "PASS", "sources_unchanged": True, "comparison_base": head, "source_identity": {"sha256": producer_hash}}))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"collected": 1, "passed": 1, "skipped": 0, "errors": 0, "failures": 0}))
    receipt.write_text(json.dumps({"mode": "pytest", "run_id": "run-1", "image": "image-1", "status": "PASS", "exit": 0, "cleanup": "PASS", "manifest": "manifest.json"}))
    requirements = {"version": 1, "repository": str(remote_path), "source": {"source_commit": head, "source_tree": tree, "producer_sha256": producer_hash, "snapshot": {"path": str(snapshot_path), "sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest()}}, "checks": [{"id": "infra", "producer": "infrastructure", "artifacts": {"selection": {"path": str(selection), "sha256": hashlib.sha256(selection.read_bytes()).hexdigest()}}, "execution": {"kind": "docker", "mode": "pytest", "image": "image-1"}}]}
    result = preflight(tmp_path, remote="origin", head=head, target="main", requirements=requirements, receipts=[receipt])
    assert result["status"] == "PASS"
    assert result["head"] == head


def test_preflight_rejects_unknown_base(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    result = preflight(tmp_path, remote="origin", head="deadbeef", target="missing", requirements={"version": 1, "checks": [{"id": "infra", "producer": "infrastructure"}], "source_commit": "deadbeef"}, receipts=[])
    assert result["status"] == "BLOCKED_STAND"
    assert result["errors"]


def test_public_cli_is_read_only(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    completed = subprocess.run([sys.executable, "-m", "infra.scripts.publication_preflight", "--repository", str(tmp_path), "--head", "missing", "--target", "main"], capture_output=True, text=True)
    assert completed.returncode != 0
    assert json.loads(completed.stdout)["status"] == "BLOCKED_STAND"


def test_new_pr_requires_target_and_never_uses_local_stale_base(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    result = preflight(tmp_path, remote="origin", head="missing", requirements={"version": 1, "checks": [], "source_commit": "x"}, receipts=[])
    assert result["status"] == "BLOCKED_STAND"
    assert any("GIT_INPUT" in error or "target" in error.lower() for error in result["errors"])


def test_existing_pr_reads_live_base_and_reports_remote_head(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "tracked").write_text("one")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "one"], check=True)
    head = git(tmp_path, "rev-parse", "HEAD")
    remote_path = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", str(remote_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "push", "-q", "origin", f"{head}:refs/heads/main"], check=True)
    result = preflight(tmp_path, remote="origin", head=head, pr="17", requirements=None, receipts=None,
                       gh_resolver=lambda _root, _pr: {"baseRefName": "main", "baseRefOid": head, "headRefOid": head})
    assert result["status"] == "BLOCKED_STAND"
    assert result["pr_head"] == head
    assert "REQUIREMENTS_REQUIRED" in result["errors"]


def test_remote_branch_advanced_blocks_stale_selection_base(tmp_path):
    head, remote, requirements, receipt = complete_setup(tmp_path)
    (tmp_path / "tracked").write_text("advanced")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "advance"], check=True)
    advanced = git(tmp_path, "rev-parse", "HEAD")
    subprocess.run(["git", "-C", str(tmp_path), "push", "-q", "origin", f"{advanced}:refs/heads/main"], check=True)
    result = preflight(tmp_path, remote="origin", head=head, target="main", requirements=requirements, receipts=[receipt])
    assert result["status"] == "BLOCKED_STAND"
    assert "COMPARISON_BASE_MISSING_OR_MISMATCH" in result["errors"]


def test_same_tree_new_commit_is_allowed_by_full_snapshot(tmp_path):
    old, remote, requirements, receipt = complete_setup(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "--allow-empty", "-qm", "same-tree"], check=True)
    new = git(tmp_path, "rev-parse", "HEAD")
    requirements["source"]["source_commit"] = old
    result = preflight(tmp_path, remote="origin", head=new, target="main", requirements=requirements, receipts=[receipt])
    assert result["status"] == "PASS"


def test_changed_head_after_snapshot_blocks_precommit_proof(tmp_path):
    old, remote, requirements, receipt = complete_setup(tmp_path)
    (tmp_path / "backend/a.py").write_text("changed")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "changed"], check=True)
    result = preflight(tmp_path, remote="origin", head=git(tmp_path, "rev-parse", "HEAD"), target="main", requirements=requirements, receipts=[receipt])
    assert result["status"] == "BLOCKED_STAND"
    assert "SOURCE_SNAPSHOT_MISMATCH" in result["errors"]


def test_existing_pr_base_drift_blocks_against_live_remote(tmp_path):
    old, remote, requirements, receipt = complete_setup(tmp_path)
    result = preflight(tmp_path, remote="origin", head=old, pr="17", requirements=requirements, receipts=[receipt], gh_resolver=lambda _root, _pr: {"baseRefName": "main", "baseRefOid": "0" * 40, "headRefOid": "0" * 40})
    assert result["status"] == "BLOCKED_STAND"
    assert "PR_BASE_MISMATCH" in result["errors"]


def test_ls_remote_timeout_returns_blocked_result(tmp_path, monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("git ls-remote", 30)
    monkeypatch.setattr("infra.scripts.publication_preflight.subprocess.run", timeout)
    result = preflight(tmp_path, remote="origin", head="missing", target="main", requirements={}, receipts=[])
    assert result["status"] == "BLOCKED_STAND"
    assert any("GIT_INPUT_INVALID" in error for error in result["errors"])


def test_existing_pr_head_behind_local_is_blocked(tmp_path):
    old, remote, requirements, receipt = complete_setup(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "--allow-empty", "-qm", "future"], check=True)
    future = git(tmp_path, "rev-parse", "HEAD")
    result = preflight(tmp_path, remote="origin", head=old, pr="17", requirements=requirements, receipts=[receipt], gh_resolver=lambda _root, _pr: {"baseRefName": "main", "baseRefOid": old, "headRefOid": future})
    assert result["status"] == "BLOCKED_STAND"
    assert "PR_HEAD_NOT_ANCESTOR" in result["errors"]


def test_existing_pr_unknown_head_is_blocked(tmp_path):
    old, remote, requirements, receipt = complete_setup(tmp_path)
    result = preflight(tmp_path, remote="origin", head=old, pr="17", requirements=requirements, receipts=[receipt], gh_resolver=lambda _root, _pr: {"baseRefName": "main", "baseRefOid": old, "headRefOid": "f" * 40})
    assert result["status"] == "BLOCKED_STAND"
    assert "PR_HEAD_NOT_ANCESTOR" in result["errors"]
