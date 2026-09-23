"""Explicit Docker/PostGIS rehearsal for cleanup foundation migration recovery.

Run only against a task-owned Compose PostgreSQL service with network_mode=none
and tmpfs PGDATA. The runner creates distinct databases; it never resets a DB.
Its output directory must be mounted at /evidence in the migrate service.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path


def command(args, *, expected=0):
    result = subprocess.run(args, text=True, capture_output=True, timeout=180)
    if result.returncode != expected:
        raise AssertionError(
            f"Command status {result.returncode}, expected {expected}: {args}\n" + result.stdout + result.stderr
        )
    return result.stdout


class Rehearsal:
    def __init__(self, args):
        self.args = args
        self.root = Path(args.evidence).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.run_id = uuid.uuid4().hex[:12]
        self.directory = self.root / f"rehearsal-{self.run_id}"
        self.directory.mkdir()
        self.inside = f"/evidence/{self.directory.name}"
        self.compose = ["docker", "compose", "-f", str(Path(args.compose).resolve())]
        self.calls = []
        self.results = []
        spec = importlib.util.spec_from_file_location(
            "foundation_host_verifier",
            Path(__file__).resolve().parents[1] / "cleanup_foundation_recovery.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.verify_host = module.verify_disposable_server
        self.attestation = self.verify_host(self.compose, args.owner, self.root)
        inspected = json.loads(command(["docker", "inspect", self.attestation["container_id"]]))[0]
        (self.directory / "docker-ownership.json").write_text(json.dumps(inspected, sort_keys=True, indent=2))
        self.snapshot_count = 0
        self.postgres_image = inspected["Image"]
        self.marker_source = next(
            m["Source"] for m in inspected["Mounts"] if m["Destination"] == "/run/complect-foundation-task/owner.json"
        )

    def sql(self, database, sql):
        assert re.fullmatch(r"[a-z0-9_]+", database)
        return command(
            self.compose
            + [
                "exec",
                "-T",
                "postgres_primary",
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "foundation",
                "-d",
                database,
                "-Atc",
                sql,
            ]
        ).strip()

    def run(self, database, arguments, *, expected=0, label, environment=None, volumes=None):
        assert self.verify_host(self.compose, self.args.owner, self.root) == self.attestation
        overrides = []
        for key, value in (environment or {}).items():
            overrides += ["-e", f"{key}={value}"]
        for volume in volumes or []:
            overrides += ["-v", volume]
        argv = self.compose + [
            "run",
            "--rm",
            "--no-deps",
            "-e",
            f"POSTGRES_DB={database}",
            "-e",
            f"DEV_STATE_DIR={self.inside}/state-{database}",
            *overrides,
            "migrate",
            *arguments,
        ]
        result = subprocess.run(argv, text=True, capture_output=True, timeout=180)
        log = self.directory / f"{len(self.calls):03d}-{label}.log"
        log.write_text(result.stdout + result.stderr + f"\nEXIT_STATUS={result.returncode}\n")
        self.calls.append(
            {
                "argv": argv,
                "expected": expected,
                "actual": result.returncode,
                "log": log.name,
            }
        )
        (self.directory / "commands.json").write_text(json.dumps(self.calls, indent=2))
        assert result.returncode == expected, f"{label}: see {log}"
        return result.stdout

    def new_database(self, label):
        database = f"foundation_{self.run_id}_{label}"
        self.sql("postgres", f'CREATE DATABASE "{database}"')
        self.sql(database, "CREATE EXTENSION postgis; CREATE EXTENSION pgcrypto")
        return database

    def snapshot(self, database):
        dump = command(
            self.compose
            + [
                "exec",
                "-T",
                "postgres_primary",
                "pg_dump",
                "-U",
                "foundation",
                "-d",
                database,
                "--schema-only",
            ]
        )
        schema = "\n".join(line for line in dump.splitlines() if not line.startswith(("\\restrict", "\\unrestrict")))
        self.snapshot_count += 1
        (self.directory / f"snapshot-{self.snapshot_count:03d}-{database}.sql").write_text(schema)
        exists = self.sql(database, "SELECT to_regclass('public.django_migrations') IS NOT NULL")
        ledger = (
            self.sql(
                database,
                "SELECT coalesce(jsonb_agg(x ORDER BY app,name),'[]') "
                "FROM (SELECT app,name,applied FROM django_migrations) x",
            )
            if exists == "t"
            else "[]"
        )
        comment = self.sql(
            database,
            "SELECT coalesce(shobj_description(oid,'pg_database'),'') "
            "FROM pg_database WHERE datname=current_database()",
        )
        receipt = self.directory / f"state-{database}" / "schema.json"
        receipt_bytes = receipt.read_text() if receipt.exists() else "<absent>"
        return hashlib.sha256((schema + ledger + receipt_bytes + comment).encode()).hexdigest()

    def prepare(self, database):
        token = f"{self.run_id}:{database}"
        att = {**self.attestation, "task_token": token, "database_name": database}
        (self.directory / f"{database}-ownership.json").write_text(json.dumps(att, sort_keys=True))
        self.run(
            database,
            [
                "/infra/scripts/cleanup_foundation_recovery_core.py",
                "prepare",
                "--reference",
                f"{self.inside}/reference.json",
                "--network-attestation",
                f"{self.inside}/{database}-ownership.json",
                "--task-token",
                token,
                "--output",
                f"{self.inside}/{database}-plan.json",
            ],
            label=f"{database}-prepare",
        )
        return [
            "recover",
            "--reference",
            f"{self.inside}/reference.json",
            "--plan",
            f"{self.inside}/{database}-plan.json",
            "--task-token",
            token,
            "--state-dir",
            f"{self.inside}/state-{database}",
        ]

    def failed_bootstrap(self, label, mode):
        database = self.new_database(label)
        recovery = self.prepare(database)
        self.run(
            database,
            ["/app/apps/users/tests/cleanup_foundation_rehearsal_driver.py", mode],
            label=f"{label}-partial-apply",
        )
        before = self.snapshot(database)
        output = self.run(
            database,
            ["-m", "config.dev_database", "apply"],
            expected=1,
            label=f"{label}-normal-retry-rejected",
        )
        assert "Incomplete migration batch" in output
        assert self.snapshot(database) == before
        return database, recovery

    def recovered(self, database, recovery):
        self.run(
            database,
            ["/infra/scripts/cleanup_foundation_recovery_core.py", *recovery],
            label=f"{database}-recover",
        )
        self.run(database, ["-m", "config.dev_database", "ready"], label=f"{database}-ready")
        before = self.snapshot(database)
        self.run(
            database,
            ["/infra/scripts/cleanup_foundation_recovery_core.py", *recovery],
            label=f"{database}-noop",
        )
        assert self.snapshot(database) == before

    def reject_actual_non_tmpfs(self):
        name = f"foundation-ineligible-{self.run_id}"
        owner = f"foundation-ineligible-{self.run_id}"
        # PGDATA deliberately points to the disposable container layer, while
        # the usual PGDATA mount is tmpfs. A boolean attestation must not pass.
        container = command(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--network",
                "none",
                "--tmpfs",
                "/var/lib/postgresql/data",
                "--label",
                f"task.owner={owner}",
                "-v",
                f"{self.marker_source}:/run/complect-foundation-task/owner.json:ro",
                "-e",
                "PGDATA=/tmp/non_tmpfs_data",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "-e",
                "POSTGRES_USER=foundation",
                "-e",
                "POSTGRES_DB=foundation_ineligible",
                self.postgres_image,
            ]
        ).strip()
        prefix = [
            "docker",
            "exec",
            container,
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "foundation",
            "-h",
            "127.0.0.1",
            "-d",
            "foundation_ineligible",
            "-Atc",
        ]
        try:
            for _ in range(60):
                status = subprocess.run(prefix + ["SELECT 1"], capture_output=True, timeout=5)
                if status.returncode == 0:
                    break
                time.sleep(0.25)
            else:
                raise AssertionError("ineligible disposable PostgreSQL failed to initialize")
            info = json.loads(command(["docker", "inspect", container]))[0]
            assert info["Config"]["Labels"]["task.owner"] == owner
            assert info["HostConfig"]["NetworkMode"] == "none"
            assert all(m["Type"] != "volume" for m in info["Mounts"])
            (self.directory / "ineligible-server-inspect.json").write_text(json.dumps(info, indent=2))
            observed_directory = command(prefix + ["SELECT current_setting('data_directory')"]).strip()
            assert observed_directory == "/tmp/non_tmpfs_data"
            before = command(
                prefix
                + [
                    "SELECT jsonb_build_object('ledger',to_regclass('public.django_migrations'),"
                    "'comment',shobj_description(oid,'pg_database')) FROM pg_database "
                    "WHERE datname=current_database()"
                ]
            )
            token = "fabricated-attestation"
            att = {
                **self.attestation,
                "container_id": container,
                "server_hostname": info["Config"]["Hostname"],
                "system_identifier": command(prefix + ["SELECT system_identifier FROM pg_control_system()"]).strip(),
                "database_name": "foundation_ineligible",
                "task_token": token,
            }
            (self.directory / "fabricated-attestation.json").write_text(json.dumps(att))
            hostfile = self.directory / "ineligible-hosts"
            hostfile.write_text("127.0.0.1 localhost postgres_primary\n")
            configuration = json.loads(command(self.compose + ["config", "--format", "json"]))
            image = configuration["services"]["migrate"]["image"]
            backend = Path(__file__).resolve().parents[3] / "backend"
            argv = [
                "docker",
                "run",
                "--rm",
                "--network",
                f"container:{container}",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "-v",
                f"{backend}:/app:ro",
                "-v",
                f"{backend.parent / 'infra'}:/infra:ro",
                "-v",
                f"{self.root}:/evidence:ro",
                "-v",
                f"{hostfile}:/etc/hosts:ro",
                "-w",
                "/app",
                "-e",
                "DJANGO_SETTINGS_MODULE=config.settings.local",
                "-e",
                "DEV_SCHEMA_MANAGEMENT=1",
                "-e",
                "POSTGRES_REPLICA_ENABLED=false",
                "-e",
                "POSTGRES_USER=foundation",
                "-e",
                "POSTGRES_DB=foundation_ineligible",
                "-e",
                "PYTHONDONTWRITEBYTECODE=1",
                "-e",
                f"DEV_STATE_DIR={self.inside}/ineligible-state",
                "--entrypoint",
                "python",
                image,
                "-B",
                "/infra/scripts/cleanup_foundation_recovery_core.py",
                "prepare",
                "--reference",
                f"{self.inside}/reference.json",
                "--network-attestation",
                f"{self.inside}/fabricated-attestation.json",
                "--task-token",
                token,
                "--output",
                f"{self.inside}/ineligible-plan.json",
            ]
            result = subprocess.run(argv, text=True, capture_output=True, timeout=30)
            (self.directory / "direct-helper-actual-non-tmpfs.log").write_text(
                result.stdout + result.stderr + f"\nEXIT_STATUS={result.returncode}\n"
            )
            assert result.returncode == 1 and "not the dedicated disposable tmpfs" in result.stdout
            after = command(
                prefix
                + [
                    "SELECT jsonb_build_object('ledger',to_regclass('public.django_migrations'),"
                    "'comment',shobj_description(oid,'pg_database')) FROM pg_database "
                    "WHERE datname=current_database()"
                ]
            )
            assert before == after
            assert not (self.directory / "ineligible-plan.json").exists()
            (self.directory / "ineligible-preservation.json").write_text(
                json.dumps(
                    {
                        "before": before,
                        "after": after,
                        "actual_data_directory": observed_directory,
                    }
                )
            )
            self.results.append(
                {
                    "case": "direct_helper_fabricated_attestation_non_tmpfs_refused",
                    "result": "PASS",
                }
            )
        finally:
            info = json.loads(command(["docker", "inspect", container]))[0]
            assert info["Config"]["Labels"].get("task.owner") == owner
            command(["docker", "rm", "-f", container])

    def main(self):
        # Apply-level B1 negatives use external policy overlays; candidate bytes stay immutable.
        fresh = self.new_database("preflight")
        baseline_policy = json.loads(
            (Path(__file__).resolve().parents[3] / "backend/config/dev_migration_policy.json").read_text()
        )
        for label in ("missing", "wrong_hash"):
            policy = json.loads(json.dumps(baseline_policy))
            if label == "missing":
                policy["approved"].pop("users.0003b1_cleanup_admission_indexes")
            else:
                policy["approved"]["users.0003b1_cleanup_admission_indexes"] = "0" * 64
            path = self.directory / f"policy-{label}.json"
            path.write_text(json.dumps(policy))
            before = self.snapshot(fresh)
            output = self.run(
                fresh,
                ["-m", "config.dev_database", "apply"],
                expected=1,
                label=f"preflight-{label}-no-ddl",
                volumes=[f"{path}:/app/config/dev_migration_policy.json:ro"],
            )
            assert "Unreviewed migration" in output
            assert self.snapshot(fresh) == before
            self.results.append({"case": f"preflight_{label}_no_ddl", "result": "PASS"})
        reference = self.new_database("reference")
        self.run(reference, ["-m", "config.dev_database", "apply"], label="reference-apply")
        self.run(reference, ["-m", "config.dev_database", "ready"], label="reference-ready")
        self.run(
            reference,
            [
                "/infra/scripts/cleanup_foundation_recovery_core.py",
                "reference",
                "--output",
                f"{self.inside}/reference.json",
            ],
            label="reference-export",
        )
        self.reject_actual_non_tmpfs()
        wrong_marker_db = self.new_database("wrong_marker")
        token = f"{self.run_id}:{wrong_marker_db}"
        bad_attestation = {
            **self.attestation,
            "task_token": token,
            "database_name": wrong_marker_db,
            "cluster_nonce": "0" * 32,
        }
        (self.directory / "wrong-marker.json").write_text(json.dumps(bad_attestation))
        before = self.snapshot(wrong_marker_db)
        output = self.run(
            wrong_marker_db,
            [
                "/infra/scripts/cleanup_foundation_recovery_core.py",
                "prepare",
                "--reference",
                f"{self.inside}/reference.json",
                "--network-attestation",
                f"{self.inside}/wrong-marker.json",
                "--task-token",
                token,
                "--output",
                f"{self.inside}/wrong-marker-plan.json",
            ],
            expected=1,
            label="direct-helper-live-marker-mismatch",
        )
        assert "live server ownership mismatch" in output
        assert self.snapshot(wrong_marker_db) == before
        self.results.append({"case": "direct_helper_live_marker_mismatch_refused", "result": "PASS"})
        for mode in ("missing", "invalid", "valid", "multiple", "timeout", "recorded"):
            database, recovery = self.failed_bootstrap(mode, mode)
            self.recovered(database, recovery)
            self.results.append({"case": mode, "result": "PASS"})
            print(f"PASS: {mode}", flush=True)
        database, recovery = self.failed_bootstrap("unexpected", "missing")
        self.sql(
            database,
            "CREATE INDEX uniq_hold_request_idempotency ON public.users_identity_hold_requests (id)",
        )
        before = self.snapshot(database)
        self.run(
            database,
            ["/infra/scripts/cleanup_foundation_recovery_core.py", *recovery],
            expected=1,
            label="unexpected-reject",
        )
        assert self.snapshot(database) == before
        self.results.append({"case": "unexpected_same_name_preserved", "result": "PASS"})
        database, recovery = self.failed_bootstrap("repeat_failure", "valid")
        self.run(
            database,
            [
                "/app/apps/users/tests/cleanup_foundation_rehearsal_driver.py",
                "timeout",
                *recovery,
            ],
            label="recovery-second-lock-timeout",
        )
        self.run(
            database,
            ["-m", "config.dev_database", "ready"],
            expected=1,
            label="no-false-ready",
        )
        self.recovered(database, recovery)
        self.results.append({"case": "repeated_failure_then_recovery", "result": "PASS"})
        database, recovery = self.failed_bootstrap("quiescence", "valid")
        before = self.snapshot(database)
        for label, environment in (
            ("missing_stage_flag", {"DEV_SCHEMA_MANAGEMENT": "0"}),
            ("wrong_host", {"DATABASE_HOST": "localhost"}),
        ):
            output = self.run(
                database,
                ["/infra/scripts/cleanup_foundation_recovery_core.py", *recovery],
                expected=1,
                label=label,
                environment=environment,
            )
            assert "explicit local Compose migration stage" in output
            assert self.snapshot(database) == before
            self.results.append({"case": f"direct_helper_{label}_refused", "result": "PASS"})
        wrong_token = list(recovery)
        wrong_token[wrong_token.index("--task-token") + 1] = "wrong-token"
        self.run(
            database,
            ["/infra/scripts/cleanup_foundation_recovery_core.py", *wrong_token],
            expected=1,
            label="wrong-ownership-token",
        )
        assert self.snapshot(database) == before
        self.results.append({"case": "wrong_ownership_token_refused", "result": "PASS"})
        wrong_reference = self.directory / "modified-reference.json"
        content = json.loads((self.directory / "reference.json").read_text())
        content["inputs"]["policy_sha256"] = "0" * 64
        wrong_reference.write_text(json.dumps(content))
        changed = list(recovery)
        changed[changed.index("--reference") + 1] = f"{self.inside}/modified-reference.json"
        self.run(
            database,
            ["/infra/scripts/cleanup_foundation_recovery_core.py", *changed],
            expected=1,
            label="changed-reference-refused",
        )
        assert self.snapshot(database) == before
        self.results.append({"case": "changed_trusted_reference_refused", "result": "PASS"})
        for label, lock_sql in (
            ("mutex", "SELECT pg_advisory_lock(18946016917341526)"),
            ("quiescence", "SELECT 1"),
        ):
            proc = subprocess.Popen(
                self.compose
                + [
                    "exec",
                    "-T",
                    "postgres_primary",
                    "psql",
                    "-X",
                    "-U",
                    "foundation",
                    "-d",
                    database,
                    "-c",
                    f"{lock_sql}; SELECT pg_sleep(20)",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                for _ in range(50):
                    count = self.sql(
                        database,
                        "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                        "AND pid<>pg_backend_pid() AND query LIKE '%pg_sleep(20)%'",
                    )
                    if count == "1":
                        break
                    time.sleep(0.05)
                else:
                    raise AssertionError("blocker session did not appear")
                before = self.snapshot(database)
                self.run(
                    database,
                    ["/infra/scripts/cleanup_foundation_recovery_core.py", *recovery],
                    expected=1,
                    label=f"{label}-rejected",
                )
                assert self.snapshot(database) == before
            finally:
                self.sql(
                    database,
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=current_database() "
                    "AND pid<>pg_backend_pid() AND query LIKE '%pg_sleep(20)%'",
                )
                proc.wait(timeout=10)
            self.results.append({"case": f"{label}_refusal_preserves_state", "result": "PASS"})
        self.recovered(database, recovery)
        (self.directory / "results.json").write_text(json.dumps(self.results, indent=2))
        print(json.dumps({"results": self.results, "evidence": str(self.directory)}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--owner", required=True)
    Rehearsal(parser.parse_args()).main()
