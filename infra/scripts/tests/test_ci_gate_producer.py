import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from infra.scripts import ci_gate_producer as producer
from infra.scripts.ci_audit_evidence import verify_ci_audit


def fixture(root):
    (root / "input.txt").write_text("stable\n")
    for args in (
        ("init", "-q"),
        ("config", "user.email", "test@example.invalid"),
        ("config", "user.name", "Test"),
        ("add", "input.txt"),
        ("commit", "-qm", "initial"),
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()


class Response:
    def __init__(self, data):
        self.data = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self.data


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.head = fixture(self.root)
        self.event = Path(self.tmp.name) / "event.json"
        self.event.write_text(json.dumps({"before": "f" * 40}))
        self.env = {
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_RUN_ID": "12",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_JOB": "gate",
            "GITHUB_SHA": self.head,
            "GITHUB_EVENT_PATH": str(self.event),
            "GH_TOKEN": "secret",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def api(self, jobs=None, **extra):
        jobs = (
            jobs
            if jobs is not None
            else [
                {
                    "id": 99,
                    "name": "gate",
                    "run_id": 12,
                    "run_attempt": 1,
                    "head_sha": self.head,
                }
            ]
        )
        return lambda request, timeout=20: Response(
            {
                "total_count": len(jobs),
                "run_id": 12,
                "head_sha": self.head,
                "jobs": jobs,
                **extra,
            }
        )

    def produce_gate(self, command, artifacts=None):
        with (
            patch.dict(os.environ, self.env, clear=False),
            patch.object(producer.urllib.request, "urlopen", self.api()),
        ):
            return producer.produce(
                command,
                root=self.root,
                output=Path(self.tmp.name) / "out",
                artifacts=artifacts or {},
            )

    def test_success_bundle(self):
        result, code = self.produce_gate([sys.executable, "-c", "print('ok')"])
        self.assertEqual((result["status"], code), ("PASS", 0))

    def test_nonzero_missing_artifact_preserves_exit(self):
        result, code = self.produce_gate(
            [sys.executable, "-c", "raise SystemExit(7)"], {"native": "missing.log"}
        )
        self.assertEqual((result["status"], code), ("BLOCKED_EVIDENCE", 7))

    def test_zero_missing_artifact_returns_two(self):
        result, code = self.produce_gate(
            [sys.executable, "-c", "pass"], {"native": "missing.log"}
        )
        self.assertEqual((result["status"], code), ("BLOCKED_EVIDENCE", 2))

    def test_source_modification_is_blocked(self):
        result, code = self.produce_gate(
            [sys.executable, "-c", "open('input.txt','w').write('changed')"]
        )
        self.assertEqual(result["status"], "BLOCKED_EVIDENCE")
        self.assertEqual(code, 2)

    def test_output_is_immutable(self):
        self.produce_gate([sys.executable, "-c", "pass"])
        with (
            patch.dict(os.environ, self.env, clear=False),
            patch.object(producer.urllib.request, "urlopen", self.api()),
        ):
            with self.assertRaises(producer.ProducerError):
                producer.produce(
                    ["true"],
                    root=self.root,
                    output=Path(self.tmp.name) / "out",
                    artifacts={},
                )

    def test_api_ambiguity_and_mismatch(self):
        for jobs, error in [
            (
                [
                    {
                        "id": i,
                        "name": "gate",
                        "run_id": 12,
                        "run_attempt": 1,
                        "head_sha": self.head,
                    }
                    for i in (1, 2)
                ],
                "CI_JOB_ID_AMBIGUOUS",
            ),
            (
                [{"id": 1, "name": "gate", "head_sha": "e" * 40}],
                "CI_API_JOB_IDENTITY_MISMATCH",
            ),
        ]:
            with (
                patch.dict(os.environ, self.env, clear=False),
                patch.object(producer.urllib.request, "urlopen", self.api(jobs)),
            ):
                with self.assertRaisesRegex(producer.ProducerError, error):
                    producer.produce(
                        ["true"],
                        root=self.root,
                        output=Path(self.tmp.name) / error,
                        artifacts={},
                    )

    def test_token_is_redacted(self):
        self.produce_gate(
            [sys.executable, "-c", "import os; print(os.getenv('GH_TOKEN'))"]
        )
        self.assertNotIn(
            "secret", (Path(self.tmp.name) / "out/run/command.log").read_text()
        )

    def test_actual_validator_accepts_pr_head_distinct_from_merge_checkout(self):
        pr_head = "a" * 40
        self.event.write_text(
            json.dumps(
                {"pull_request": {"base": {"sha": "b" * 40}, "head": {"sha": pr_head}}}
            )
        )
        job = {
            "id": 99,
            "name": "gate",
            "run_id": 12,
            "run_attempt": 1,
            "head_sha": pr_head,
        }
        with (
            patch.dict(os.environ, self.env, clear=False),
            patch.object(producer.urllib.request, "urlopen", self.api([job])),
        ):
            result, code = producer.produce(
                [sys.executable, "-c", "pass"],
                root=self.root,
                output=Path(self.tmp.name) / "out",
                artifacts={},
            )
        self.assertEqual((result["status"], code), ("PASS", 0))
        output = Path(self.tmp.name) / "out"
        log = output / "checkout.log"
        log.write_text(f"[command]/usr/bin/git log -1 --format=%H\n{self.head}\n")
        import hashlib

        log_desc = {
            "path": "checkout.log",
            "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
            "checkout_line": 2,
        }
        identity = result["identity"]
        requirements = {
            "format": "complect-ci-audit-requirements-v1",
            "jobs": [{"name": "gate", **identity, "artifacts": ["receipt"]}],
        }
        report = {
            "format": "complect-ci-audit-report-v1",
            "jobs": [
                {
                    "name": "gate",
                    **identity,
                    "status": "completed",
                    "conclusion": "success",
                    "verdict": "PASS",
                    "exit_code": 0,
                    "log": log_desc,
                    "source_proof": result["proof"],
                    "artifacts": {"receipt": result["artifacts"]["receipt"]},
                }
            ],
        }
        self.assertEqual(
            verify_ci_audit(requirements, report, output)["status"], "PASS"
        )

    def test_api_requires_complete_identity_and_full_pagination(self):
        valid = {
            "id": 99,
            "name": "gate",
            "run_id": 12,
            "run_attempt": 1,
            "head_sha": self.head,
        }
        scenarios = [
            (self.api([{k: v for k, v in valid.items() if k != field}]), field)
            for field in ("run_id", "run_attempt", "head_sha")
        ]
        scenarios += [(self.api([valid], total_count=2), "incomplete")]
        for api, label in scenarios:
            with (
                self.subTest(label=label),
                patch.dict(os.environ, self.env),
                patch.object(producer.urllib.request, "urlopen", api),
            ):
                with self.assertRaises(producer.ProducerError):
                    producer.produce(
                        [sys.executable, "-c", "pass"],
                        root=self.root,
                        output=Path(self.tmp.name) / label,
                        artifacts={},
                    )

    def test_write_failure_after_gate_preserves_cli_failure_and_blocks_success(self):
        original = Path.write_text
        for child_exit in (0, 7):
            output = Path(self.tmp.name) / ("write-fail-" + str(child_exit))

            def disk_fault(path, *args, **kwargs):
                if path == output / "source-proof.json":
                    raise OSError("synthetic disk failure")
                return original(path, *args, **kwargs)

            with (
                self.subTest(child_exit=child_exit),
                patch.dict(os.environ, self.env),
                patch.object(producer.urllib.request, "urlopen", self.api()),
                patch.object(Path, "write_text", disk_fault),
                contextlib.redirect_stdout(io.StringIO()) as text,
            ):
                code = producer.main(
                    [
                        "--root",
                        str(self.root),
                        "--output",
                        str(output),
                        "--",
                        sys.executable,
                        "-c",
                        "raise SystemExit(" + str(child_exit) + ")",
                    ]
                )
            self.assertEqual(code, child_exit or 2)
            self.assertEqual(json.loads(text.getvalue())["status"], "BLOCKED_EVIDENCE")

    def test_source_inventory_and_head_changes_are_blocked(self):
        scenarios = [
            "import os; os.unlink('input.txt')",
            "import subprocess; open('new.txt','w').write('new'); subprocess.run(['git','add','new.txt'],check=True)",
            "import subprocess; subprocess.run(['git','commit','--allow-empty','-qm','new head'],check=True)",
        ]
        for index, script in enumerate(scenarios):
            with self.subTest(script=script):
                subprocess.run(
                    ["git", "-C", str(self.root), "reset", "--hard", self.head],
                    check=True,
                    capture_output=True,
                )
                with (
                    patch.dict(os.environ, self.env),
                    patch.object(producer.urllib.request, "urlopen", self.api()),
                ):
                    result, code = producer.produce(
                        [sys.executable, "-c", script],
                        root=self.root,
                        output=Path(self.tmp.name) / ("drift-" + str(index)),
                        artifacts={},
                    )
                self.assertEqual((result["status"], code), ("BLOCKED_EVIDENCE", 2))

    def test_reserved_and_duplicate_artifacts_rejected_before_child(self):
        marker = self.root / "marker"
        for index, options in enumerate(
            (
                ["--artifact", "receipt=other.json"],
                ["--artifact", "native=a", "--artifact", "native=b"],
            )
        ):
            with (
                patch.dict(os.environ, self.env),
                patch.object(producer.urllib.request, "urlopen", self.api()),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = producer.main(
                    [
                        "--root",
                        str(self.root),
                        "--output",
                        str(Path(self.tmp.name) / ("reserved-" + str(index))),
                        *options,
                        "--",
                        sys.executable,
                        "-c",
                        "open('marker','w').write('bad')",
                    ]
                )
            self.assertEqual(code, 2)
            self.assertFalse(marker.exists())

    def test_symlink_artifact_cannot_receive_pass(self):
        outside = Path(self.tmp.name) / "outside.json"
        outside.write_text("{}")
        output = Path(self.tmp.name) / "symlink-output"
        script = (
            "from pathlib import Path; Path("
            + repr(str(output / "native.json"))
            + ").symlink_to("
            + repr(str(outside))
            + ")"
        )
        with (
            patch.dict(os.environ, self.env),
            patch.object(producer.urllib.request, "urlopen", self.api()),
        ):
            result, code = producer.produce(
                [sys.executable, "-c", script],
                root=self.root,
                output=output,
                artifacts={"native": "native.json"},
            )
        self.assertEqual((result["status"], code), ("BLOCKED_EVIDENCE", 2))

    def test_runner_receipt_write_failure_preserves_observed_child_exit(self):
        original = Path.open
        for child_exit in (0, 7):
            output = Path(self.tmp.name) / ("runner-write-fail-" + str(child_exit))

            def disk_fault(path, *args, **kwargs):
                if path == output / "run/receipt.json" and args and args[0] == "xb":
                    raise OSError("synthetic receipt disk failure")
                return original(path, *args, **kwargs)

            with (
                self.subTest(child_exit=child_exit),
                patch.dict(os.environ, self.env),
                patch.object(producer.urllib.request, "urlopen", self.api()),
                patch.object(Path, "open", disk_fault),
                contextlib.redirect_stdout(io.StringIO()) as text,
            ):
                code = producer.main(
                    [
                        "--root",
                        str(self.root),
                        "--output",
                        str(output),
                        "--",
                        sys.executable,
                        "-c",
                        "raise SystemExit(" + str(child_exit) + ")",
                    ]
                )
            self.assertEqual(code, child_exit or 2)
            self.assertEqual(json.loads(text.getvalue())["status"], "BLOCKED_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
