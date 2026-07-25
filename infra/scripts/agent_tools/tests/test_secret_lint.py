from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from infra.scripts.agent_tools.common import CommandResult
from infra.scripts.agent_tools.secret_lint import (
    is_candidate_path,
    scan_text,
    tracked_candidate_paths,
)


class SecretScannerTests(unittest.TestCase):
    def test_reports_rule_without_secret_value(self) -> None:
        secret = "sm_" + "A" * 32

        findings = scan_text(Path("settings.json"), f'{{"apiKey":"{secret}"}}')

        self.assertEqual([(finding.line, finding.rule) for finding in findings], [(1, "supermemory-token")])
        self.assertNotIn(secret, "\n".join(str(finding) for finding in findings))

    def test_allows_environment_references_and_placeholders(self) -> None:
        text = """
API_KEY=${SUPERMEMORY_API_KEY}
SECRET_KEY=REQUIRED_FROM_VAULT_HIGH_ENTROPY_STRING
EXPECTED_GATEWAY_TOKEN=$EXPECTED_GATEWAY_TOKEN
Authorization: Bearer <JWT>
Authorization: Bearer `${accessToken}`
Authorization: Bearer {runtime_token}
"""

        self.assertEqual(scan_text(Path(".env.example"), text), [])

    def test_ignores_unsupported_binary_and_local_env(self) -> None:
        self.assertFalse(is_candidate_path(Path(".env")))
        self.assertFalse(is_candidate_path(Path(".env.local")))
        self.assertFalse(is_candidate_path(Path("image.png")))
        self.assertTrue(is_candidate_path(Path(".env.example")))

    def test_reports_all_supported_rule_names(self) -> None:
        text = "\n".join(
            (
                "ctx7sk-" + "B" * 20,
                "Authorization: Bearer opaque-production-credential",
                "-----BEGIN PRIVATE KEY-----",
            )
        )

        findings = scan_text(Path("settings.md"), text)

        self.assertEqual(
            [(finding.line, finding.rule) for finding in findings],
            [(1, "context7-token"), (2, "generic-bearer"), (3, "private-key")],
        )


class CandidateEnumerationTests(unittest.TestCase):
    def test_enumerates_only_tracked_candidate_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def runner(command: list[str], cwd: Path | None) -> CommandResult:
                self.assertEqual(command, ["git", "ls-files", "-z"])
                self.assertEqual(cwd, root)
                return CommandResult(
                    0,
                    "AGENTS.md\0.codex/config.toml\0.env\0frontend/public/logo.png\0",
                    "",
                )

            self.assertEqual(
                tracked_candidate_paths(root, runner),
                [root / "AGENTS.md", root / ".codex/config.toml"],
            )

    def test_enumeration_rejects_git_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def runner(command: list[str], cwd: Path | None) -> CommandResult:
                return CommandResult(128, "", "redacted failure")

            with self.assertRaisesRegex(RuntimeError, "git ls-files failed"):
                tracked_candidate_paths(root, runner)


if __name__ == "__main__":
    unittest.main()
