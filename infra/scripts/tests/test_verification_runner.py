"""Public behavior of command execution and read-only receipt verification."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infra.scripts.verification_runner import run_check, verify_check


def evidence(root: Path, name: str) -> Path:
    return root / ".dev/test-artifacts" / name


def receipt_pin(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class VerificationRunnerTests(unittest.TestCase):
    def test_verify_requires_an_independent_pin_and_never_replays_valid_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").write_text("original")
            counter = root / "counter"
            code = "from pathlib import Path; p=Path('counter'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1')"
            result = run_check(
                [sys.executable, "-c", code],
                root=root,
                cwd=root,
                sources=["source"],
                output=evidence(root, "attempt"),
            )
            path = evidence(root, "attempt") / "receipt.json"
            self.assertEqual(result["receipt_sha256"], receipt_pin(path))
            self.assertEqual(
                verify_check(path, root=root, sources=["source"])["integrity"], "FAIL"
            )
            self.assertEqual(
                verify_check(
                    path, root=root, sources=["source"], expected_sha256="0" * 64
                )["integrity"],
                "FAIL",
            )
            checked = verify_check(
                path,
                root=root,
                sources=["source"],
                expected_sha256=result["receipt_sha256"],
            )
            self.assertEqual(
                (checked["integrity"], checked["behavior"]), ("PASS", "PASS")
            )
            self.assertEqual(counter.read_text(), "1")

    def test_known_secrets_in_recorded_paths_are_rejected_before_execution(self):
        for field in ("root", "cwd", "source", "output"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                parent = Path(directory)
                secret = "sensitive55"
                root = parent / (secret if field == "root" else "checkout")
                root.mkdir()
                source = secret if field == "source" else "source"
                (root / source).write_text("original")
                cwd = root / secret if field == "cwd" else root
                cwd.mkdir(exist_ok=True)
                output = parent / (
                    secret + "-attempt" if field == "output" else "attempt"
                )
                with (
                    patch.dict(os.environ, {"ISSUE55_SECRET": secret}),
                    self.assertRaises(ValueError),
                ):
                    run_check(
                        [
                            sys.executable,
                            "-c",
                            "from pathlib import Path; Path('marker').touch()",
                        ],
                        root=root,
                        cwd=cwd,
                        sources=[source],
                        output=output,
                        redact_env=["ISSUE55_SECRET"],
                    )
                self.assertFalse((cwd / "marker").exists())
                self.assertFalse(output.exists())

    def test_output_inside_nonexcluded_source_area_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").write_text("original")
            with self.assertRaises(ValueError):
                run_check(
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; Path('marker').touch()",
                    ],
                    root=root,
                    cwd=root,
                    sources=["source"],
                    output=root / ".evidence/attempt",
                )
            self.assertFalse((root / "marker").exists())

    def test_independent_receipt_pin_rejects_changed_execution_facts(self):
        for field, value in (
            ("command", ["different-command"]),
            ("cwd", "/etc"),
            ("expected_exit", 7),
            ("exit", 9),
            ("attempt_id", "0" * 32),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "source").write_text("original")
                run_check(
                    [sys.executable, "-c", "print('ok')"],
                    root=root,
                    cwd=root,
                    sources=["source"],
                    output=evidence(root, "attempt"),
                )
                path = evidence(root, "attempt") / "receipt.json"
                pin = hashlib.sha256(path.read_bytes()).hexdigest()
                record = json.loads(path.read_text())
                record[field] = value
                path.write_text(json.dumps(record))
                self.assertEqual(
                    verify_check(
                        path, root=root, sources=["source"], expected_sha256=pin
                    )["integrity"],
                    "FAIL",
                )

    def test_registered_secret_in_argv_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").write_text("original")
            marker = root / "must-not-run"
            with (
                patch.dict(os.environ, {"ISSUE55_SECRET": "synthetic-secret"}),
                self.assertRaises(ValueError),
            ):
                run_check(
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; Path('must-not-run').touch()",
                        "synthetic-secret",
                    ],
                    root=root,
                    cwd=root,
                    sources=["source"],
                    output=evidence(root, "attempt"),
                    redact_env=["ISSUE55_SECRET"],
                )
            self.assertFalse(marker.exists())
            self.assertFalse((evidence(root, "attempt")).exists())

    def test_start_failure_and_signal_preserve_distinct_exit_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").write_text("original")
            not_run = run_check(
                [str(root / "no-such-program")],
                root=root,
                cwd=root,
                sources=["source"],
                output=evidence(root, "not-run"),
            )
            self.assertIsNone(not_run["exit"])
            self.assertEqual(
                (not_run["integrity"], not_run["behavior"]), ("FAIL", "NOT_RUN")
            )
            signal = run_check(
                [
                    sys.executable,
                    "-c",
                    "import os,signal; os.kill(os.getpid(),signal.SIGTERM)",
                ],
                root=root,
                cwd=root,
                sources=["source"],
                output=evidence(root, "signal"),
                expected_exit=-15,
            )
            self.assertEqual(signal["exit"], -15)
            self.assertEqual(
                (signal["integrity"], signal["behavior"], signal["expectation"]),
                ("PASS", "FAIL", "PASS"),
            )

    def test_removed_source_preserves_the_command_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").write_text("original")
            result = run_check(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('source').unlink(); raise SystemExit(9)",
                ],
                root=root,
                cwd=root,
                sources=["source"],
                output=evidence(root, "attempt"),
                expected_exit=9,
            )
            self.assertEqual(result["exit"], 9, result)
            self.assertEqual(result["integrity"], "FAIL")
            self.assertEqual(result["behavior"], "FAIL")

    def test_retries_are_new_attempts_and_old_evidence_is_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").write_text("original")
            command = [sys.executable, "-c", "print('ok')"]
            first = run_check(
                command,
                root=root,
                cwd=root,
                sources=["source"],
                output=evidence(root, "one"),
            )
            saved = {p.name: p.read_bytes() for p in (evidence(root, "one")).iterdir()}
            with self.assertRaises(FileExistsError):
                run_check(
                    [sys.executable, "-c", "raise SystemExit(3)"],
                    root=root,
                    cwd=root,
                    sources=["source"],
                    output=evidence(root, "one"),
                )
            second = run_check(
                command,
                root=root,
                cwd=root,
                sources=["source"],
                output=evidence(root, "two"),
            )
            self.assertNotEqual(first["attempt_id"], second["attempt_id"])
            verify_check(
                evidence(root, "one") / "receipt.json",
                root=root,
                sources=["source"],
                expected_sha256=receipt_pin(evidence(root, "one") / "receipt.json"),
            )
            self.assertEqual(
                saved,
                {p.name: p.read_bytes() for p in (evidence(root, "one")).iterdir()},
            )

    def test_source_selection_is_independent_and_unsafe_sources_never_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").write_text("original")
            (root / "extra").write_text("other")
            (root / "alias").symlink_to(root / "source")
            marker = root / "must-not-run"
            for i, sources in enumerate(
                (
                    [],
                    ["../escape"],
                    [str(root / "source")],
                    ["alias"],
                    ["source", "./source"],
                )
            ):
                with self.subTest(sources=sources), self.assertRaises(ValueError):
                    run_check(
                        [
                            sys.executable,
                            "-c",
                            "from pathlib import Path; Path('must-not-run').touch()",
                        ],
                        root=root,
                        cwd=root,
                        sources=sources,
                        output=evidence(root, str(i)),
                    )
                self.assertFalse(marker.exists())
            run_check(
                [sys.executable, "-c", "print('ok')"],
                root=root,
                cwd=root,
                sources=["source"],
                output=evidence(root, "valid"),
            )
            result = verify_check(
                evidence(root, "valid") / "receipt.json",
                root=root,
                sources=["source", "extra"],
                expected_sha256=receipt_pin(evidence(root, "valid") / "receipt.json"),
            )
            self.assertEqual(result["integrity"], "FAIL")

    def test_cli_keeps_raw_failure_and_verify_only_checks_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").write_text("original")
            output = evidence(root, "attempt")
            command = [
                sys.executable,
                "-m",
                "infra.scripts.verification_runner",
                "run",
                "--root",
                str(root),
                "--cwd",
                str(root),
                "--source",
                "source",
                "--output",
                str(output),
                "--expect-exit",
                "7",
                "--",
                sys.executable,
                "-c",
                "print('detail'*1000); raise SystemExit(7)",
            ]
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(
                completed.returncode, 7, completed.stdout + completed.stderr
            )
            summary = json.loads(completed.stdout)
            self.assertEqual(
                (summary["integrity"], summary["behavior"], summary["expectation"]),
                ("PASS", "FAIL", "PASS"),
            )
            self.assertLess(len(completed.stdout), 4096)
            self.assertEqual((output / "command.log").read_text().count("detail"), 1000)
            marker = root / "must-not-run"
            path = output / "receipt.json"
            receipt = json.loads(path.read_text())
            receipt["command"] = [
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(marker)!r}).touch()",
            ]
            path.write_text(json.dumps(receipt))
            verified = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "infra.scripts.verification_runner",
                    "verify",
                    "--root",
                    str(root),
                    "--source",
                    "source",
                    "--receipt",
                    str(path),
                    "--sha256",
                    summary["receipt_sha256"],
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(verified.returncode, 2, verified.stdout + verified.stderr)
            self.assertEqual(json.loads(verified.stdout)["integrity"], "FAIL")
            self.assertFalse(marker.exists())

    def test_registered_secrets_are_redacted_from_full_combined_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").write_text("original")
            output = evidence(root, "attempt")
            secret = "ключ-issue55-secret"
            code = "import os; os.write(1,b'x'*65533); s=os.environ['ISSUE55_SECRET'].encode(); os.write(1,s[:4]); os.write(1,s[4:]); os.write(2,s); print('x'*9000)"
            with patch.dict(os.environ, {"ISSUE55_SECRET": secret}):
                result = run_check(
                    [sys.executable, "-c", code],
                    root=root,
                    cwd=root,
                    sources=["source"],
                    output=output,
                    redact_env=["ISSUE55_SECRET"],
                )
            log = (output / "command.log").read_bytes()
            self.assertNotIn(secret.encode(), log)
            self.assertEqual(log.count(b"[REDACTED]"), 2)
            self.assertIn(b"x" * 9000, log)
            self.assertNotIn(secret, (output / "receipt.json").read_text())
            self.assertEqual(result["log"]["sha256"], hashlib.sha256(log).hexdigest())
            self.assertEqual(result["integrity"], "PASS")

    def test_missing_or_changed_evidence_never_verifies(self):
        for missing in (
            "command",
            "cwd",
            "exit",
            "expected_exit",
            "log",
            "source_before",
            "source_after",
            "attempt_id",
            "format",
        ):
            with (
                self.subTest(missing=missing),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                (root / "source").write_text("original")
                output = evidence(root, "attempt")
                run_check(
                    [sys.executable, "-c", "print('ok')"],
                    root=root,
                    cwd=root,
                    sources=["source"],
                    output=output,
                )
                path = output / "receipt.json"
                receipt = json.loads(path.read_text())
                receipt.pop(missing)
                path.write_text(json.dumps(receipt))
                self.assertEqual(
                    verify_check(
                        path,
                        root=root,
                        sources=["source"],
                        expected_sha256=receipt_pin(path),
                    )["integrity"],
                    "FAIL",
                )
        for changed in ("log", "delete_log", "source"):
            with (
                self.subTest(changed=changed),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                (root / "source").write_text("original")
                output = evidence(root, "attempt")
                run_check(
                    [sys.executable, "-c", "print('ok')"],
                    root=root,
                    cwd=root,
                    sources=["source"],
                    output=output,
                )
                target = (
                    root / "source" if changed == "source" else output / "command.log"
                )
                if changed == "delete_log":
                    target.unlink()
                else:
                    target.write_text("changed")
                self.assertEqual(
                    verify_check(
                        output / "receipt.json",
                        root=root,
                        sources=["source"],
                        expected_sha256=receipt_pin(output / "receipt.json"),
                    )["integrity"],
                    "FAIL",
                )

    def test_source_drift_is_recorded_with_original_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").write_text("original")
            result = run_check(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('source').write_text('changed')",
                ],
                root=root,
                cwd=root,
                sources=["source"],
                output=evidence(root, "attempt"),
            )
            self.assertEqual(result["exit"], 0)
            self.assertEqual(result["integrity"], "FAIL", result)
            self.assertNotEqual(result["source_before"], result["source_after"])

    def test_real_command_cwd_output_and_sources_are_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tool.py").write_text("original\n")
            cwd = root / "work"
            cwd.mkdir()
            output = evidence(root, "attempt")
            command = [
                sys.executable,
                "-c",
                "from pathlib import Path; print(Path.cwd()); print('ok')",
            ]
            result = run_check(
                command, root=root, cwd=cwd, sources=["tool.py"], output=output
            )
            self.assertEqual(result["exit"], 0)
            self.assertEqual(result["command"], command)
            self.assertEqual(result["cwd"], str(cwd))
            self.assertEqual(result["source_before"], result["source_after"])
            self.assertEqual(
                result["source_before"]["tool.py"]["sha256"],
                hashlib.sha256(b"original\n").hexdigest(),
            )
            self.assertEqual((output / "command.log").read_text(), str(cwd) + "\nok\n")
            verified = verify_check(
                output / "receipt.json",
                root=root,
                sources=["tool.py"],
                expected_sha256=receipt_pin(output / "receipt.json"),
            )
            self.assertEqual(verified["integrity"], "PASS", verified)
            self.assertEqual(verified["behavior"], "PASS")


if __name__ == "__main__":
    unittest.main()
