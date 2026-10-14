"""Disposable Payments stand and complete local gate with owned cleanup."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if __package__:
    from .docker_ownership import OwnershipMismatch, OwnershipProof
    from .payments_gate_evidence import EvidenceError, PaymentsReport, check_manifest
else:
    from docker_ownership import OwnershipMismatch, OwnershipProof
    from payments_gate_evidence import EvidenceError, PaymentsReport, check_manifest


class StandError(RuntimeError):
    pass


class CheckFailure(StandError):
    pass


class Stand:
    def __init__(self, args):
        self.args = args
        self.run_id = "payments-" + uuid.uuid4().hex
        self.owned = []
        self.ownership = OwnershipProof(self._inspect_resources)
        self.private = None
        self.passwords = []
        self.evidence = PaymentsReport(self.run_id, args.scope)
        self._finished = False
        self._replica_facts = None
        self._replica_status = "BLOCKED_STAND"
        self._replica_path = None

    @property
    def report(self):
        return self.evidence.snapshot()

    def _sync_observed(self):
        self.evidence.record(metadata={"owned": self.owned})
        if self._replica_facts is not None and "primary_error" not in self.report:
            self.evidence.record(
                "physical_replica",
                status=self._replica_status,
                replica_evidence=self._replica_facts,
                **({"manifest_path": self._replica_path} if self._replica_path else {}),
            )

    def save(self):
        if not self._finished:
            self._sync_observed()
        (self.args.output / "result.json").write_text(
            json.dumps(self.report, indent=2) + "\n"
        )

    def docker(self, *args, check=True, timeout=120):
        result = subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=timeout
        )
        if check and result.returncode:
            # Never print argv, environment or server logs containing credentials.
            raise StandError("docker operation failed: " + args[0])
        return result

    def inspect(self, kind, identifier):
        return json.loads(self.docker(kind, "inspect", identifier).stdout)[0]

    def _inspect_resources(self, kind, identifiers, *, optional=False):
        resources = [self.inspect(kind, identifier) for identifier in identifiers]
        if kind == "container":
            for resource in resources:
                # Docker may enumerate its mount map in a different order per inspect.
                # Compare every mount field exactly, with only enumeration order canonicalized.
                if isinstance(resource.get("Mounts"), list):
                    resource["Mounts"] = sorted(
                        resource["Mounts"], key=lambda mount: json.dumps(mount, sort_keys=True)
                    )
        return resources

    def register(self, kind, identifier):
        try:
            found = self.ownership.owned(kind, [identifier], {
                "complect.payments_run": self.run_id,
            })[0]
        except OwnershipMismatch as error:
            raise StandError("created resource ownership mismatch") from error
        labels = (
            found.get("Labels") if kind == "network" else found["Config"].get("Labels")
        )
        record = {
            "kind": kind,
            "id": found["Id"],
            "name": found["Name"],
            "labels": labels,
        }
        if kind == "container":
            record.update(image=found["Image"], mounts=found["Mounts"])
        self.owned.append(record)
        self.save()
        return found["Id"]

    def create(self, suffix, image, options, command):
        result = self.docker(
            "create",
            "--pull",
            "never",
            "--name",
            self.run_id + "-" + suffix,
            "--label",
            "complect.payments_run=" + self.run_id,
            *options,
            self.report["images"][image],
            *command,
        )
        return self.register("container", result.stdout.strip())

    def preflight(self):
        context = self.docker("context", "show").stdout.strip()
        endpoint = json.loads(self.docker("context", "inspect", context).stdout)[0][
            "Endpoints"
        ]["docker"]["Host"]
        if os.environ.get("DOCKER_HOST", endpoint) != endpoint:
            raise StandError("Docker endpoint override differs from selected context")
        if (
            not endpoint.startswith("unix://")
            or os.environ.get("DOCKER_CONTEXT", context) != context
        ):
            raise StandError("local Unix Docker endpoint required")
        self.evidence.record(metadata={"docker_endpoint": endpoint})
        self.docker("info", "--format", "{{.ID}}")
        for image in (
            self.args.backend_image,
            self.args.postgres_image,
            self.args.redis_image,
        ):
            images = self.report.get("images", {})
            images[image] = json.loads(self.docker("image", "inspect", image).stdout)[
                0
            ]["Id"]
            self.evidence.record(metadata={"images": images})
        subnet = ipaddress.ip_network(self.args.subnet)
        if (
            subnet.version != 4
            or subnet.prefixlen != 24
            or not subnet.subnet_of(ipaddress.ip_network("198.19.0.0/16"))
        ):
            raise StandError("disposable subnet must be /24 within 198.19.0.0/16")
        identifiers = self.docker("network", "ls", "-q").stdout.split()
        for identifier in identifiers:
            found = self.inspect("network", identifier)
            for config in found.get("IPAM", {}).get("Config", []) or []:
                if config.get("Subnet") and subnet.overlaps(
                    ipaddress.ip_network(config["Subnet"])
                ):
                    raise StandError("disposable subnet overlaps existing network")
        self.evidence.record(
            metadata={
                "source_identity": self.source_identity(),
                "uid_gid": [os.getuid(), os.getgid()],
                "subnet": str(subnet),
            }
        )

    def source_identity(self):
        digest = hashlib.sha256()
        for base in (ROOT / "backend", ROOT / "infra/scripts", ROOT / "infra/postgres"):
            for path in sorted(base.rglob("*")):
                if (
                    path.is_file()
                    and path.suffix in (".py", ".json", ".sql", ".sh")
                    and "__pycache__" not in path.parts
                ):
                    digest.update(str(path.relative_to(ROOT)).encode())
                    digest.update(path.read_bytes())
        return {"sha256": digest.hexdigest()}

    def provision(self):
        self.private = Path(tempfile.mkdtemp(prefix=self.run_id + "-"))
        self.control = self.private / "control"
        self.control.mkdir(mode=0o700)
        self.state = self.private / "state"
        self.state.mkdir(mode=0o700)
        secret_dir = self.private / "secrets"
        secret_dir.mkdir(mode=0o700)
        owner, runtime = secrets.token_hex(32), secrets.token_hex(32)
        self.passwords = [owner, runtime]
        for name, value in (("owner", owner), ("runtime", runtime)):
            path = secret_dir / name
            path.write_text(value)
            path.chmod(0o644)
        common = {
            "DJANGO_SETTINGS_MODULE": "config.settings.local",
            "ENVIRONMENT": "local",
            "POSTGRES_DB": "payments_gate",
            "DATABASE_HOST": "postgres_primary",
            "DATABASE_REPLICA_HOST": "postgres_primary",
            "POSTGRES_REPLICA_ENABLED": "false",
            "IAM_IDENTITY_AUTH_MODE": "ENFORCE",
            "DEV_SCHEMA_MANAGEMENT": "1",
            "DEV_STATE_DIR": "/state",
            "HOME": "/tmp",
            "PYTHONDONTWRITEBYTECODE": "1",
            "DJANGO_SECRET_KEY": secrets.token_hex(32),
            "EXPECTED_GATEWAY_TOKEN": secrets.token_hex(32),
            "AWS_EC2_METADATA_DISABLED": "true",
            "EMAIL_BACKEND": "django.core.mail.backends.dummy.EmailBackend",
            "PYTHONPATH": "/workspace:/app",
            "PAYMENTS_CLI_TEST_TMPDIR": "/control",
        }
        for index, key in enumerate(
            (
                "REDIS_DEFAULT_URL",
                "REDIS_CACHE_URL",
                "REDIS_CELERY_URL",
                "REDIS_STREAMS_URL",
                "REDIS_IDEMPOTENCY_URL",
                "CHANNEL_LAYER_URL",
                "CELERY_RESULT_BACKEND",
                "REDIS_AUDIT_URL",
            )
        ):
            common[key] = f"redis://redis:6379/{index}"
        self.passwords.extend(
            [common["DJANGO_SECRET_KEY"], common["EXPECTED_GATEWAY_TOKEN"]]
        )
        for name, user in (
            ("owner", "complect_migrator"),
            ("runtime", "complect_app_local"),
        ):
            values = dict(common, POSTGRES_USER=user, DATABASE_PASSWORD_FILE="/secret")
            if name == "runtime":
                values.update(
                    DEV_SCHEMA_MANAGEMENT="0",
                    PYTEST_LIFECYCLE_SOCKET="/control/lifecycle.sock",
                    PYTEST_LIFECYCLE_RUN_ID=self.run_id,
                )
            path = self.private / (name + ".env")
            path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
            path.chmod(0o600)
        self.network = self.register(
            "network",
            self.docker(
                "network",
                "create",
                "--internal",
                "--subnet",
                self.args.subnet,
                "--label",
                "complect.payments_run=" + self.run_id,
                self.run_id,
            ).stdout.strip(),
        )
        pg = self.create(
            "postgres",
            self.args.postgres_image,
            [
                "--network",
                self.network,
                "--network-alias",
                "postgres_primary",
                "--tmpfs",
                "/var/lib/postgresql/data:rw",
                "-e",
                "POSTGRES_USER=complect_migrator",
                "-e",
                "POSTGRES_DB=payments_gate",
                "-e",
                "POSTGRES_PASSWORD_FILE=/run/owner",
                "-v",
                f"{secret_dir / 'owner'}:/run/owner:ro,Z",
                "-v",
                f"{secret_dir / 'runtime'}:/run/complect-secrets/bootstrap_app_postgres_password:ro,Z",
                "-v",
                f"{ROOT / 'infra/postgres/init/03_dev_extensions.sql'}:/docker-entrypoint-initdb.d/03_extensions.sql:ro,z",
                "-v",
                f"{ROOT / 'infra/postgres/init/04_runtime_role.sh'}:/docker-entrypoint-initdb.d/04_runtime_role.sh:ro,z",
            ],
            [],
        )
        self.pg = pg
        self.docker("start", pg)
        for _ in range(120):
            ready = self.docker(
                "exec",
                pg,
                "pg_isready",
                "-h",
                "127.0.0.1",
                "-U",
                "complect_migrator",
                "-d",
                "payments_gate",
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise StandError("PostgreSQL readiness timeout")
        redis = self.create(
            "redis",
            self.args.redis_image,
            [
                "--network",
                self.network,
                "--network-alias",
                "redis",
                "--tmpfs",
                "/data:rw",
            ],
            [],
        )
        self.docker("start", redis)

    def backend(self, suffix, role, command, detached=False):
        options = [
            "--network",
            self.network,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw",
            "--env-file",
            str(self.private / (role + ".env")),
            "-v",
            f"{ROOT / 'backend'}:/app:ro,z",
            "-v",
            f"{ROOT / 'infra/scripts'}:/workspace/infra/scripts:ro,z",
            "-v",
            f"{ROOT / 'backend'}:/workspace/backend:ro,z",
            "-v",
            f"{ROOT / '.env.dev.example'}:/workspace/.env.dev.example:ro,z",
            "-w",
            "/app",
            "-v",
            f"{self.control}:/control:z",
            "-v",
            f"{self.private / 'secrets' / role}:/secret:ro,z",
            "-v",
            f"{self.state}:/state:z",
            "--entrypoint",
            "python",
        ]
        if role == "runtime":
            # runtime must never see the owner credential or writable migration receipt.
            options[options.index(f"{self.state}:/state:z")] = (
                f"{self.state}:/state:ro,z"
            )
        identifier = self.create(
            suffix, self.args.backend_image, options, ["-B", *command]
        )
        result = self.docker(
            "start", *([] if detached else ["-a"]), identifier, check=False, timeout=900
        )
        if detached:
            if result.returncode:
                raise StandError("lifecycle server failed to start")
            return identifier
        log = result.stdout + result.stderr
        for value in self.passwords:
            log = log.replace(value, "<REDACTED>")
        (self.args.output / (suffix + ".log")).write_text(log)
        code = self.inspect("container", identifier)["State"]["ExitCode"]
        if result.returncode or code:
            for line in reversed(log.splitlines()):
                try:
                    diagnostic = json.loads(line)
                except ValueError:
                    continue
                if isinstance(diagnostic, dict) and "error" in diagnostic:
                    self.evidence.record(metadata={"phase_diagnostic": diagnostic})
                    break
            manifest_path = self.control / (
                "manifest.json"
                if suffix == "http-control"
                else suffix + "-manifest.json"
            )
            details = ""
            if manifest_path.exists():
                diagnostics = json.loads(manifest_path.read_text()).get(
                    "diagnostics", []
                )
                if diagnostics:
                    self.evidence.record(metadata={"phase_diagnostic": diagnostics[0]})
                    details = "; " + " -> ".join(
                        item["type"] for item in diagnostics[0].get("chain", [])
                    )
            raise StandError(suffix + " failed" + details + "; see redacted phase log")
        return identifier

    def cleanup(self):
        errors = []
        for record in reversed(self.owned):
            try:
                try:
                    self.ownership.unchanged(record)
                except OwnershipMismatch as error:
                    message = (
                        "cleanup image/mount mismatch" if error.reason == "fingerprint"
                        else "cleanup identity mismatch"
                    )
                    raise StandError(message) from error
                if record["kind"] == "container":
                    self.docker("container", "rm", "-f", record["id"])
                else:
                    self.docker("network", "rm", record["id"])
                record["removed"] = True
            except (StandError, OSError, subprocess.SubprocessError, ValueError) as exc:
                errors.append({"id": record["id"], "error": str(exc)})
        if self.private:
            for name in (
                path.name
                for path in self.control.iterdir()
                if path.suffix in (".json", ".jsonl")
            ):
                if (self.control / name).exists():
                    try:
                        shutil.copyfile(self.control / name, self.args.output / name)
                    except OSError as exc:
                        errors.append({"file": name, "error": type(exc).__name__})
            if not errors:
                shutil.rmtree(self.private)
            else:
                # Keep only diagnostics, never retain temporary credentials after failure.
                shutil.rmtree(self.private / "secrets", ignore_errors=True)
                for name in ("owner.env", "runtime.env"):
                    (self.private / name).unlink(missing_ok=True)
        if self.args.fault == "cleanup":
            errors.append({"error": "injected cleanup failure"})
        self.evidence.record("cleanup", errors=errors)
        return not errors

    def pytest_phase(self, phase, selection):
        nodes = self.control / (phase + "-nodes.json")
        manifest_path = self.control / (phase + "-manifest.json")
        nodes.write_text(json.dumps(selection))
        try:
            self.backend(
                phase,
                "runtime",
                [
                    "-m",
                    "test_support.regression_runner",
                    "--nodes",
                    "/control/" + nodes.name,
                    "--manifest",
                    "/control/" + manifest_path.name,
                ],
            )
        except StandError:
            if manifest_path.exists():
                failed = json.loads(manifest_path.read_text())
                if any(
                    r["when"] == "call" and r["outcome"] == "failed"
                    for r in failed.get("reports", [])
                ):
                    raise CheckFailure(
                        phase + " behavior failed; see manifest"
                    ) from None
            raise
        finally:
            if manifest_path.exists():
                self.evidence.record(
                    phase,
                    status="PASS",
                    selection=selection,
                    manifest_path=manifest_path.name,
                    manifest=json.loads(manifest_path.read_text()),
                    auth_rbac="fixtures",
                )
        self.check_manifest(self.report["manifest"][phase])

    @staticmethod
    def check_manifest(manifest):
        try:
            check_manifest(manifest)
        except EvidenceError as exc:
            raise StandError(str(exc)) from None

    def infra_phase(self, phase, mode):
        self.backend(
            phase,
            "runtime",
            [
                "-m",
                "test_support.payments_infra",
                mode,
                "--manifest",
                "/control/" + phase + "-manifest.json",
            ],
        )
        path = self.control / (phase + "-manifest.json")
        manifest = json.loads(path.read_text())
        self.evidence.record(
            phase,
            status="PASS",
            manifest_path=path.name,
            **({"manifest": manifest} if mode == "pytest" else {"tests": manifest}),
        )
        if mode == "pytest":
            self.check_manifest(manifest)

    def pg_query(self, container, sql):
        return self.docker(
            "exec",
            container,
            "psql",
            "-U",
            "complect_migrator",
            "-d",
            "payments_gate",
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ).stdout.strip()

    def replica_sync(self, replica, timeout=30):
        target = self.pg_query(self.pg, "SELECT pg_current_wal_lsn()")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            replay = self.pg_query(replica, "SELECT pg_last_wal_replay_lsn()")
            if (
                replay
                and self.pg_query(
                    self.pg,
                    "SELECT pg_wal_lsn_diff('" + replay + "', '" + target + "') >= 0",
                )
                == "t"
            ):
                return {"primary_lsn": target, "replica_lsn": replay}
            time.sleep(0.1)
        raise StandError("replica synchronization timeout")

    def replica_action(self, cycle, action):
        path = f"replica-{cycle}-{action}.json"
        command = [
            "-m",
            "test_support.payments_replica_http",
            action,
            "--output",
            "/control/" + path,
        ]
        if action != "prepare":
            command.extend(["--ids", f"/control/replica-{cycle}-prepare.json"])
        self.backend(f"replica-{cycle}-{action}", "runtime", command)
        return json.loads((self.control / path).read_text())

    def physical_replica(self):
        # Only this run's immutable primary ID is used; credentials remain owner-side.
        self.docker(
            "exec",
            self.pg,
            "sh",
            "-c",
            "printf '%s\\n' 'host replication complect_migrator "
            + self.args.subnet
            + " scram-sha-256' >> /var/lib/postgresql/data/pg_hba.conf",
        )
        self.pg_query(self.pg, "SELECT pg_reload_conf()")
        replica = self.create(
            "replica",
            self.args.postgres_image,
            [
                "--network",
                self.network,
                "--network-alias",
                "postgres_replica",
                "--tmpfs",
                "/var/lib/postgresql/data:rw",
                "-v",
                f"{self.private / 'secrets' / 'owner'}:/run/owner:ro,z",
                "--entrypoint",
                "/bin/bash",
            ],
            [
                "-ceu",
                'export PGPASSWORD="$(cat /run/owner)"; pg_basebackup -h postgres_primary -U complect_migrator -D /var/lib/postgresql/data -R -X stream -c fast; unset PGPASSWORD; exec docker-entrypoint.sh postgres -c hot_standby=on',
            ],
        )
        self.docker("start", replica)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            ready = self.docker(
                "exec",
                replica,
                "pg_isready",
                "-U",
                "complect_migrator",
                "-d",
                "payments_gate",
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise StandError("physical replica readiness timeout")
        if self.pg_query(replica, "SELECT pg_is_in_recovery()") != "t":
            raise StandError("physical replica must be in recovery")
        primary_cluster = self.pg_query(
            self.pg, "SELECT system_identifier FROM pg_control_system()"
        )
        if (
            self.pg_query(replica, "SELECT system_identifier FROM pg_control_system()")
            != primary_cluster
        ):
            raise StandError("replica belongs to a different primary cluster")
        runtime_env = self.private / "runtime.env"
        runtime_env.write_text(
            runtime_env.read_text()
            .replace(
                "DATABASE_REPLICA_HOST=postgres_primary",
                "DATABASE_REPLICA_HOST=postgres_replica",
            )
            .replace("POSTGRES_REPLICA_ENABLED=false", "POSTGRES_REPLICA_ENABLED=true")
        )
        evidence = {
            "runs": [],
            "replay_resumed": False,
            "synchronized": False,
            "primary_id": self.pg,
            "replica_id": replica,
            "cluster": primary_cluster,
        }
        self._replica_facts = evidence
        self.evidence.record(
            "physical_replica", status="BLOCKED_STAND", replica_evidence=evidence
        )
        for cycle in (1, 2):
            prepared = self.replica_action(cycle, "prepare")
            sync_before = self.replica_sync(replica)
            entry = {
                "run_id": f"{self.run_id}-{cycle}",
                "line_id": prepared["line_id"],
                "before": prepared["primary_before"],
                "synchronized_before": sync_before,
            }
            evidence["runs"].append(entry)
            error = None
            try:
                self.pg_query(replica, "SELECT pg_wal_replay_pause()")
                deadline = time.monotonic() + 10
                while self.pg_query(replica, "SELECT pg_is_wal_replay_paused()") != "t":
                    if time.monotonic() >= deadline:
                        raise StandError("replica pause timeout")
                    time.sleep(0.1)
                self.replica_action(cycle, "lag-write")
                primary_lsn = self.pg_query(self.pg, "SELECT pg_current_wal_lsn()")
                replay_lsn = self.pg_query(replica, "SELECT pg_last_wal_replay_lsn()")
                lag_bytes = int(
                    self.pg_query(
                        self.pg,
                        "SELECT pg_wal_lsn_diff('"
                        + primary_lsn
                        + "', '"
                        + replay_lsn
                        + "')",
                    )
                )
                if lag_bytes <= 0:
                    raise StandError("physical replica lag not established")
                entry["lag"] = {
                    "primary_lsn": primary_lsn,
                    "replica_lsn": replay_lsn,
                    "lag_bytes": lag_bytes,
                    "replay_paused": True,
                    "in_recovery": True,
                }
                entry["observed"] = self.replica_action(cycle, "observe")
                if self.args.fault == "replica_commands":
                    raise StandError(
                        "injected failure after replica lag before HTTP commands"
                    )
                result = self.replica_action(cycle, "commands")
                entry["commands"] = result["commands"]
                entry["after"] = result["commands"][-1]["state_after"]
            except (
                StandError,
                OSError,
                subprocess.SubprocessError,
                ValueError,
                KeyError,
            ) as exc:
                error = exc
                raise
            finally:
                try:
                    self.pg_query(replica, "SELECT pg_wal_replay_resume()")
                    if (
                        self.pg_query(replica, "SELECT pg_is_wal_replay_paused()")
                        != "f"
                    ):
                        raise StandError("replica replay did not resume")
                    entry["replay_resumed"] = True
                    entry["synchronized_after"] = self.replica_sync(replica)
                    entry["synchronized"] = True
                    evidence["replay_resumed"] = all(
                        r.get("replay_resumed") is True for r in evidence["runs"]
                    )
                    evidence["synchronized"] = all(
                        r.get("synchronized") is True for r in evidence["runs"]
                    )
                except (
                    StandError,
                    OSError,
                    subprocess.SubprocessError,
                    ValueError,
                ) as resume_error:
                    entry["recovery_error"] = str(resume_error)
                    if error is None:
                        raise
            entry["recovered"] = self.replica_action(cycle, "recovered")
            self.replica_sync(replica)
        evidence.update(replay_resumed=True, synchronized=True)
        path = self.control / "physical-replica.json"
        path.write_text(json.dumps(evidence, indent=2) + "\n")
        self._replica_status = "PASS"
        self._replica_path = path.name
        self._sync_observed()

    def run(self):
        phase = "preflight"
        self.save()
        try:
            self.preflight()
            if self.args.fault == "prepare":
                raise StandError("injected preparation failure")
            self.provision()
            self.backend(
                "control-access",
                "runtime",
                [
                    "-c",
                    "from pathlib import Path; p=Path('/control/access'); p.write_text('ok'); p.unlink(); assert Path('/secret').is_file()",
                ],
            )
            self.evidence.record(phase, status="PASS")
            phase = "migrations"
            self.backend("migrations", "owner", ["-m", "config.dev_database", "apply"])
            self.evidence.record(phase, status="PASS")
            phase = "identity"
            self.backend(
                "identity",
                "owner",
                [
                    "-m",
                    "test_support.payments_stand",
                    "prepare",
                    "--run-id",
                    self.run_id,
                ],
            )
            self.backend(
                "runtime-identity",
                "runtime",
                ["-m", "test_support.payments_stand", "runtime"],
            )
            self.evidence.record(
                metadata={
                    "target_identity": json.loads(
                        (self.control / "target.json").read_text()
                    ),
                    "runtime_identity": json.loads(
                        (self.control / "runtime.json").read_text()
                    ),
                }
            )
            self.evidence.record(phase, status="PASS")
            phase = "http_control"
            if self.args.fault == "foreign_target":
                target = dict(self.report["target_identity"], cluster="foreign-cluster")
                (self.control / "target.json").write_text(json.dumps(target))
            lifecycle = self.backend(
                "lifecycle",
                "owner",
                [
                    "-m",
                    "test_support.lifecycle_database",
                    "--identity",
                    "/control/target.json",
                    "--socket",
                    "/control/lifecycle.sock",
                    "--evidence",
                    "/control/lifecycle.jsonl",
                ],
                detached=True,
            )
            for _ in range(100):
                if (self.control / "lifecycle.sock").exists():
                    break
                state = self.inspect("container", lifecycle)["State"]
                if not state["Running"]:
                    log = (
                        self.docker("logs", lifecycle, check=False).stdout
                        + self.docker("logs", lifecycle, check=False).stderr
                    )
                    for value in self.passwords:
                        log = log.replace(value, "<REDACTED>")
                    (self.args.output / "lifecycle-startup.log").write_text(log)
                    reason = next(
                        (
                            line
                            for line in reversed(log.splitlines())
                            if "TargetMismatchError:" in line
                            or line.startswith("RuntimeError:")
                        ),
                        "lifecycle server exited",
                    )
                    raise StandError(reason)
                time.sleep(0.1)
            else:
                raise StandError("lifecycle socket readiness timeout")
            if self.args.fault == "socket":
                runtime_env = self.private / "runtime.env"
                runtime_env.write_text(
                    runtime_env.read_text().replace(
                        "/control/lifecycle.sock", "/control/unavailable.sock"
                    )
                )
            (self.control / "nodes.json").write_text(
                json.dumps(
                    [
                        "apps/payments/tests/test_manual_match_api.py::test_http_attach_and_detach_controls_return_200"
                    ]
                )
            )
            self.backend(
                "http-control",
                "runtime",
                [
                    "-m",
                    "test_support.regression_runner",
                    "--nodes",
                    "/control/nodes.json",
                    "--manifest",
                    "/control/manifest.json",
                ],
            )
            manifest = json.loads((self.control / "manifest.json").read_text())
            try:
                check_manifest(manifest)
                if len(manifest["collected"]) != 1:
                    raise EvidenceError("incomplete HTTP control evidence")
            except EvidenceError:
                raise StandError("incomplete HTTP control evidence") from None
            if self.source_identity() != self.report["source_identity"]:
                raise StandError("source changed during run")
            self.evidence.record(
                phase,
                status="PASS",
                auth_rbac="fixtures",
                manifest=manifest,
                manifest_path="manifest.json",
            )
            if self.args.scope == "full":
                for phase, selection in (
                    ("payments", ["apps/payments/tests/"]),
                    ("http", ["apps/payments/tests/test_manual_match_api.py"]),
                ):
                    self.pytest_phase(phase, selection)
                phase = "physical_replica"
                self.physical_replica()
                phase = "lifecycle_pytest"
                self.infra_phase(phase, "pytest")
                phase = "lifecycle_unittest"
                self.infra_phase(phase, "unittest")
            if self.source_identity() != self.report["source_identity"]:
                raise StandError("source changed during run")
            self.evidence.record(metadata={"stand_status": "PASS"})
        except (
            StandError,
            OSError,
            subprocess.SubprocessError,
            ValueError,
            KeyError,
        ) as exc:
            self._sync_observed()
            self.evidence.record(
                phase,
                primary_error={
                    "phase": phase,
                    "type": type(exc).__name__,
                    "reason": str(exc),
                },
            )
        finally:
            self.cleanup()
            self._sync_observed()
            result = self.evidence.finish()
            self._finished = True
            self.save()
        print(
            json.dumps(
                {
                    "status": self.report["status"],
                    "report": str(self.args.output / "result.json"),
                }
            )
        )
        return result.exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("stand", "full"), default="stand")
    parser.add_argument(
        "--fault",
        choices=("prepare", "foreign_target", "socket", "cleanup", "replica_commands"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".dev/test-artifacts" / ("payments-stand-" + uuid.uuid4().hex),
    )
    parser.add_argument(
        "--backend-image",
        default=os.environ.get("PAYMENTS_BACKEND_IMAGE", "complect_backend:dev"),
    )
    parser.add_argument("--postgres-image", default="postgis/postgis:16-3.4")
    parser.add_argument("--redis-image", default="redis:7.2-alpine")
    parser.add_argument("--subnet", default="198.19.249.0/24")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    return Stand(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
