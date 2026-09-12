"""Checkout-local development lifecycle; no implicit host installation or Git writes."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRET_NAMES = (
    "django_secret_key",
    "postgres_password",
    "replication_password",
    "minio_password",
    "gateway_token",
)


class DevError(Exception):
    """An actionable failure that is safe to print without secret values."""


def private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise DevError(f"Unsafe private directory permissions: {path.name}")


def create_private_file(path: Path, content: bytes) -> None:
    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
    except FileExistsError:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise DevError(f"Unsafe local file permissions: {path.name}")
        return
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)


def configure(root: Path) -> None:
    private_directory(root / ".dev")
    private_directory(root / ".dev/secrets")
    project = "complect_" + hashlib.sha256(str(root).encode()).hexdigest()[:12]
    template = (root / ".env.dev.example").read_text()
    configuration = template.replace("__PROJECT__", project).replace(
        "__UID__", str(os.getuid())
    )
    configuration = configuration.replace("__GID__", str(os.getgid()))
    create_private_file(root / ".env.dev", configuration.encode())
    for name in SECRET_NAMES:
        path = root / ".dev/secrets" / name
        create_private_file(path, (secrets.token_hex(32) + "\n").encode())
        if path.stat().st_size < 64:
            raise DevError(
                f"Local secret is empty or too short: {name}; refusing to overwrite"
            )
    create_private_file(root / ".dev/external.env", b"")
    create_private_file(root / ".dev/e2e.env", b"")
    print("PASS: local development configuration ready; existing secrets preserved")


PUBLIC_KEYS = {
    "COMPOSE_PROJECT_NAME",
    "DEV_UID",
    "DEV_GID",
    "DEV_FRONTEND_ORIGIN",
    "DEV_API_ORIGIN",
    "IAM_DEFERRED_MERGE_EXECUTION_ENABLED",
}
CORE = (
    "postgres_primary",
    "postgres_replica",
    "redis",
    "minio",
    "backend",
    "api_gateway",
    "frontend",
    "celery_worker",
    "celery_beat",
)
INFRA = ("postgres_primary", "postgres_replica", "redis", "minio")
APPS = ("backend", "api_gateway", "frontend", "celery_worker", "celery_beat")
PORTS = {
    "api_gateway": {8000},
    "frontend": {8080},
    "postgres_primary": {15432},
    "postgres_replica": {5433},
    "minio": {9000, 9001},
}


def parse_env(path: Path, allowed: set[str]) -> dict[str, str]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise DevError(f"Missing or unsafe environment file: {path.name}")
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator or name not in allowed or name in result:
            raise DevError(
                f"Unsupported or duplicate environment key in {path.name}; use the documented contract"
            )
        result[name] = value
    return result


def local_configuration(root: Path) -> dict[str, str]:
    for relative in (".dev", ".dev/secrets"):
        path = root / relative
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise DevError(
                f"Unsafe private directory: {relative}; refusing cross-checkout state access"
            )
    values = parse_env(root / ".env.dev", PUBLIC_KEYS)
    project = values.get("COMPOSE_PROJECT_NAME", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,50}", project):
        raise DevError("Invalid COMPOSE_PROJECT_NAME; run configure for a new checkout")
    if values.get("DEV_UID") != str(os.getuid()) or values.get("DEV_GID") != str(
        os.getgid()
    ):
        raise DevError(
            "Dev UID/GID do not match this developer; do not reuse another user's local config"
        )
    if (
        values.get("DEV_FRONTEND_ORIGIN", "http://localhost:8080")
        != "http://localhost:8080"
        or values.get("DEV_API_ORIGIN", "http://localhost:8000")
        != "http://localhost:8000"
    ):
        raise DevError(
            "Development origins must follow the canonical localhost:8080/8000 contract"
        )
    if values.get("IAM_DEFERRED_MERGE_EXECUTION_ENABLED", "false") != "false":
        raise DevError("IAM_DEFERRED_MERGE_EXECUTION_ENABLED must remain false")
    for name in SECRET_NAMES:
        path = root / ".dev/secrets" / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_mode & 0o077
            or path.stat().st_uid != os.getuid()
            or path.stat().st_size < 32
        ):
            raise DevError(f"Missing or unsafe local secret file: {name}")
    for name, keys in (
        ("external.env", {"DADATA_API_KEY"}),
        ("e2e.env", {"E2E_USERNAME", "E2E_PASSWORD"}),
    ):
        path = root / ".dev" / name
        if path.exists():
            if path.stat().st_mode & 0o077:
                raise DevError(f"Credential file must be private: {name}")
            parse_env(path, keys)
    values["DEV_CHECKOUT_ID"] = hashlib.sha256(str(root).encode()).hexdigest()
    return values


def prerequisites() -> None:
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise DevError(
            "This reviewed development target is Linux amd64 (native or WSL2)"
        )
    if sys.version_info < (3, 10):
        raise DevError(
            "Python 3.10+ is required for host orchestration; install python3 through your OS package manager"
        )
    for tool in ("docker", "git", "bash", "make", "jq"):
        if shutil.which(tool) is None:
            raise DevError(
                f"Missing required host tool: {tool}. Install the documented Fedora/WSL prerequisite; no install attempted"
            )
    if shutil.which("codegraph") is None:
        raise DevError(
            "Missing CodeGraph 1.6.0; install the documented standalone tool, then retry"
        )
    version = subprocess.run(
        ["codegraph", "--version"], capture_output=True, text=True, timeout=15
    )
    if version.returncode or version.stdout.strip() != "1.6.0":
        raise DevError("CodeGraph must match the reviewed 1.6.0 toolchain")
    selected_context = os.getenv("DOCKER_CONTEXT")
    endpoint = os.getenv("DOCKER_HOST")
    if selected_context or not endpoint:
        context_arguments = [selected_context] if selected_context else []
        result = subprocess.run(
            [
                "docker",
                "context",
                "inspect",
                *context_arguments,
                "--format",
                "{{.Endpoints.docker.Host}}",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode:
            raise DevError(
                "Selected Docker context cannot be inspected; no lifecycle operation attempted"
            )
        endpoint = result.stdout.strip()
    if not endpoint.startswith("unix://"):
        raise DevError(
            "Use a local Unix-socket Docker context; remote deployment contexts are not allowed"
        )
    result = subprocess.run(
        ["docker", "info", "--format", "{{.OSType}}"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode or result.stdout.strip() != "linux":
        raise DevError(
            "Docker Linux daemon is unavailable; start Docker Engine/Desktop and verify user socket access"
        )


class Commands:
    def __init__(self, root: Path, *, read_only: bool = False):
        self.root = root
        self.read_only = read_only
        self.values = local_configuration(root)
        self.project = self.values["COMPOSE_PROJECT_NAME"]
        self.environment = dict(os.environ)
        for name in (
            "COMPOSE_FILE",
            "COMPOSE_PROFILES",
            "COMPOSE_PROJECT_NAME",
            "COMPOSE_ENV_FILES",
        ):
            self.environment.pop(name, None)
        self.environment.update(
            self.values,
            GIT_OPTIONAL_LOCKS="0",
            COMPOSE_ANSI="never",
            BUILDKIT_PROGRESS="plain",
        )
        self.prefix = [
            "docker",
            "compose",
            "--project-directory",
            str(root),
            "--env-file",
            str(root / ".env.dev"),
            "-p",
            self.project,
            "-f",
            str(root / "docker-compose.dev.yml"),
        ]

    def redact(self, text: str) -> str:
        for path in (self.root / ".dev/secrets").glob("*"):
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 4096:
                value = path.read_text().strip()
                if value:
                    text = text.replace(value, "<REDACTED>")
        for name, keys in (
            ("external.env", {"DADATA_API_KEY"}),
            ("e2e.env", {"E2E_USERNAME", "E2E_PASSWORD"}),
        ):
            path = self.root / ".dev" / name
            if path.exists():
                for value in parse_env(path, keys).values():
                    if value:
                        text = text.replace(value, "<REDACTED>")
        return text

    def run(
        self,
        arguments: list[str],
        *,
        write: bool = False,
        timeout: int = 60,
        check: bool = True,
    ):
        forbidden = {
            "up",
            "down",
            "build",
            "pull",
            "push",
            "run",
            "restart",
            "stop",
            "start",
            "create",
            "rm",
            "prune",
            "migrate",
            "apply",
            "seed",
            "chmod",
            "chown",
            "install",
            "ci",
            "compile",
            "add",
            "checkout",
            "reset",
            "init",
        }
        if self.read_only and (
            write or any(argument in forbidden for argument in arguments)
        ):
            raise DevError("Doctor refused a corrective operation")
        try:
            result = subprocess.run(
                arguments,
                cwd=self.root,
                env=self.environment,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise DevError(
                "Bounded development command timed out; inspect service health"
            ) from error
        if check and result.returncode:
            detail = self.redact(result.stdout + "\n" + result.stderr)[-4000:]
            raise DevError(
                f"Development command failed (exit {result.returncode}): {detail}"
            )
        return result

    def compose(self, *arguments: str, **kwargs):
        if not arguments or arguments[0] != "run" or self.read_only:
            return self.run([*self.prefix, *arguments], **kwargs)
        name = f"{self.project}-oneshot-{secrets.token_hex(8)}"
        try:
            return self.run(
                [*self.prefix, "run", "--name", name, *arguments[1:]], **kwargs
            )
        except DevError:
            # Killing the Compose client does not stop its detached daemon work.
            # Inspect the exact generated name, then address only the verified ID.
            inspected = self.run(["docker", "inspect", name], check=False, timeout=10)
            if inspected.returncode == 0:
                container = json.loads(inspected.stdout)[0]
                labels = container["Config"].get("Labels") or {}
                if (
                    labels.get("com.docker.compose.project") == self.project
                    and labels.get("complect.checkout_id")
                    == self.values["DEV_CHECKOUT_ID"]
                ):
                    identifier = container["Id"]
                    self.run(
                        ["docker", "stop", "--time", "5", identifier],
                        write=True,
                        check=False,
                        timeout=15,
                    )
                    self.run(
                        ["docker", "rm", "-f", identifier],
                        write=True,
                        check=False,
                        timeout=15,
                    )
            raise

    def configuration(self):
        return json.loads(self.compose("config", "--format", "json").stdout)

    def own_resources(self, config):
        ids = self.run(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={self.project}",
            ]
        ).stdout.split()
        if ids:
            containers = json.loads(self.run(["docker", "inspect", *ids]).stdout)
            for container in containers:
                labels = container["Config"].get("Labels") or {}
                if labels.get("complect.checkout_id") != self.values["DEV_CHECKOUT_ID"]:
                    raise DevError(
                        "Project contains containers owned by another checkout; refusing to touch them"
                    )
        for kind, field in (("volume", "volumes"), ("network", "networks")):
            for specification in config.get(field, {}).values():
                name = specification.get("name")
                if not name:
                    continue
                result = self.run(["docker", kind, "inspect", name], check=False)
                if result.returncode == 0:
                    labels = json.loads(result.stdout)[0].get("Labels") or {}
                    if (
                        labels.get("complect.checkout_id")
                        != self.values["DEV_CHECKOUT_ID"]
                    ):
                        raise DevError(
                            f"Existing {kind} belongs to another or unmanaged checkout: {name}"
                        )

    def containers(self):
        ids = self.compose("ps", "-aq").stdout.split()
        if not ids:
            return {}
        result = json.loads(self.run(["docker", "inspect", *ids]).stdout)
        return {
            item["Config"]["Labels"]["com.docker.compose.service"]: item
            for item in result
        }


def source_digest(commands: Commands) -> str:
    names = commands.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            "backend",
        ]
    ).stdout.split("\0")
    digest = hashlib.sha256()
    for name in sorted(set(filter(None, names))):
        path = commands.root / name
        if path.is_file():
            digest.update(name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def lock_fingerprints(root: Path):
    directory = root / "backend/requirements"
    metadata = json.loads((directory / "lock-metadata.json").read_text())
    expected = {"base.txt", "dev.txt", "base.lock", "dev.lock", "lock.py"}
    if set(metadata.get("sha256", {})) != expected:
        raise DevError(
            "Invalid dependency lock metadata; run the reviewed lock workflow"
        )
    for name in expected:
        if (
            hashlib.sha256((directory / name).read_bytes()).hexdigest()
            != metadata["sha256"][name]
        ):
            raise DevError(
                "Dependency inputs/locks are stale; sync never regenerates them"
            )


def validate_ports(config, containers, *, require_published: bool):
    for service, expected in PORTS.items():
        configured = config["services"][service].get("ports", [])
        actual = {int(port["published"]) for port in configured}
        if actual != expected or any(
            port.get("host_ip") != "127.0.0.1" for port in configured
        ):
            raise DevError(f"Canonical loopback port contract changed: {service}")
        container = containers.get(service)
        if container and container["State"]["Running"]:
            published = container["NetworkSettings"].get("Ports") or {}
            bindings = [
                entry for values in published.values() for entry in values or []
            ]
            if {int(entry["HostPort"]) for entry in bindings} != expected or any(
                entry["HostIp"] != "127.0.0.1" for entry in bindings
            ):
                raise DevError(
                    f"Running service has noncanonical port bindings: {service}"
                )
        else:
            for port in expected:
                with socket.socket() as probe:
                    try:
                        probe.bind(("127.0.0.1", port))
                    except OSError as error:
                        raise DevError(
                            f"Canonical port {port} is occupied by another service; no fallback port chosen"
                        ) from error
            if require_published:
                raise DevError(f"Expected published service is not running: {service}")


def ensure_mount_directories(root: Path):
    for relative in (
        ".dev/sdk",
        ".dev/test-artifacts",
        "frontend/node_modules",
        "frontend/.next",
        "frontend/e2e/node_modules",
        "frontend/src/shared/api/generated",
    ):
        path = root / relative
        if path.is_symlink():
            raise DevError(f"Generated mount path must not be a symlink: {relative}")
        path.mkdir(parents=True, exist_ok=True)


def build_backend(commands: Commands):
    lock_fingerprints(commands.root)
    digest = source_digest(commands)
    commands.environment["DEV_SOURCE_DIGEST"] = digest
    image = f"{commands.project}_backend:dev"
    result = commands.run(["docker", "image", "inspect", image], check=False)
    if result.returncode == 0:
        labels = json.loads(result.stdout)[0]["Config"].get("Labels") or {}
        if labels.get("complect.source_digest") == digest:
            print("PASS: backend image inputs unchanged; no rebuild")
            return
    print("BUILD: backend from reviewed locks (no resolver)", flush=True)
    commands.run(
        [
            "docker",
            "build",
            "-f",
            "backend/Dockerfile.dev",
            "--build-arg",
            f"DEV_SOURCE_DIGEST={digest}",
            "-t",
            image,
            "backend",
        ],
        write=True,
        timeout=1200,
    )
    print("PASS: locked backend image built; no dependency resolver invoked")


def runtime_owners(commands: Commands, config):
    images = {
        name: config["services"][service]["image"]
        for name, service in (
            ("POSTGRES", "postgres_primary"),
            ("KONG", "api_gateway"),
            ("MINIO", "minio"),
        )
    }
    cache = commands.root / ".dev/owners.json"
    if cache.is_file():
        data = json.loads(cache.read_text())
        if data.get("images") == images:
            commands.environment.update(data["owners"])
            return
    owners = {}
    for name, image in images.items():
        for flag, suffix in (("-u", "UID"), ("-g", "GID")):
            arguments = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                "id",
                image,
                flag,
            ]
            if name == "POSTGRES":
                arguments.append("postgres")
            value = commands.run(arguments, write=True, timeout=180).stdout.strip()
            if not value.isdigit():
                raise DevError("Could not determine a system image UID/GID")
            owners[f"DEV_{name}_{suffix}"] = value
    cache.write_text(
        json.dumps({"images": images, "owners": owners}, sort_keys=True) + "\n"
    )
    commands.environment.update(owners)


def database_status(commands: Commands, *, running: bool = False):
    if running:
        result = commands.compose(
            "exec",
            "-T",
            "backend",
            "python",
            "-B",
            "-m",
            "config.dev_database",
            "status",
        )
    else:
        result = commands.compose(
            "run",
            "--rm",
            "--no-deps",
            "migrate",
            "python",
            "-B",
            "-m",
            "config.dev_database",
            "plan",
            write=True,
        )
    data = json.loads(result.stdout.strip())
    if "error" in data:
        raise DevError(data["error"])
    return data


def apply_migrations(commands: Commands):
    status = database_status(commands)
    print(
        "MIGRATION PLAN: " + (", ".join(status["pending"]) or "no pending migrations")
    )
    if status["pending"]:
        commands.compose(
            "stop", "celery_worker", "celery_beat", "backend", write=True, timeout=60
        )
        result = commands.compose(
            "run", "--rm", "--no-deps", "migrate", write=True, timeout=900
        )
        print(commands.redact(result.stdout).strip())
    deadline = time.monotonic() + 90
    while True:
        status = database_status(commands)
        if status.get("replica_recovery") and status.get("replica_current"):
            break
        if time.monotonic() >= deadline:
            raise DevError(
                "Replica did not reach the current schema within the bounded deadline"
            )
        time.sleep(2)
    print("PASS: primary schema and replica migration history are ready")


def generate_sdk(commands: Commands):
    result = commands.compose(
        "run",
        "--rm",
        "--no-deps",
        "migrate",
        "python",
        "-B",
        "-m",
        "config.dev_database",
        "schema",
        write=True,
    )
    schema = json.loads(result.stdout)
    path = commands.root / ".dev/sdk/openapi.json"
    path.write_text(json.dumps(schema, sort_keys=True, separators=(",", ":")) + "\n")
    commands.compose("run", "--rm", "--no-deps", "sdk", write=True, timeout=120)
    generated = commands.root / "frontend/src/shared/api/generated/schema.d.ts"
    if generated.stat().st_uid != os.getuid():
        raise DevError("Generated SDK is not owned by the developer")
    receipt = {
        "source": source_digest(commands),
        "schema_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "sdk_sha256": hashlib.sha256(generated.read_bytes()).hexdigest(),
    }
    (commands.root / ".dev/sdk/receipt.json").write_text(
        json.dumps(receipt, sort_keys=True) + "\n"
    )
    print("PASS: generated SDK is current and developer-owned")


def _lifecycle(root: Path, *, initial: bool):
    prerequisites()
    if initial:
        configure(root)
    elif not (root / ".env.dev").is_file():
        raise DevError("This checkout is not bootstrapped; run make bootstrap first")
    commands = Commands(root)
    with os.fdopen(
        os.open(
            root / ".dev/lifecycle.lock",
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
            0o600,
        ),
        "a",
    ) as mutex:
        try:
            fcntl.flock(mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DevError(
                "Another lifecycle operation is running for this checkout"
            ) from error
        commands.run([sys.executable, "-B", "infra/scripts/check_toolchain.py"])
        lock_fingerprints(root)
        config = commands.configuration()
        commands.own_resources(config)
        validate_ports(config, commands.containers(), require_published=False)
        ensure_mount_directories(root)
        build_backend(commands)
        runtime_owners(commands, config)
        print(
            "PREPARE: private runtime credentials and scoped volume ownership",
            flush=True,
        )
        commands.compose(
            "run", "--rm", "--no-deps", "secrets_init", write=True, timeout=120
        )
        commands.compose(
            "run", "--rm", "--no-deps", "volume_init", write=True, timeout=120
        )
        print("START: isolated project infrastructure", flush=True)
        commands.compose(
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "240",
            *INFRA,
            write=True,
            timeout=900,
        )
        commands.compose(
            "run", "--rm", "--no-deps", "s3_bootstrap", write=True, timeout=120
        )
        apply_migrations(commands)
        # npm ci replaces modules atomically from the caller's perspective only
        # while no Next process is using the shared dependency volume.
        commands.compose("stop", "frontend", write=True, timeout=60)
        commands.compose(
            "run", "--rm", "--no-deps", "frontend_deps", write=True, timeout=600
        )
        generate_sdk(commands)
        print("START: application after explicit schema and SDK stages", flush=True)
        commands.compose(
            "up",
            "-d",
            "--no-build",
            "--wait",
            "--wait-timeout",
            "360",
            *APPS,
            write=True,
            timeout=900,
        )
        doctor(root)


def bootstrap(root: Path) -> None:
    _lifecycle(root, initial=True)


def sync(root: Path) -> None:
    _lifecycle(root, initial=False)


def _http(url: str, *, origin: bool = False):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    headers = {"Origin": "http://localhost:8080"} if origin else {}
    with opener.open(
        urllib.request.Request(url, headers=headers), timeout=6
    ) as response:
        if response.status != 200:
            raise DevError("Expected development URL is not healthy")
        if (
            origin
            and response.headers.get("Access-Control-Allow-Origin")
            != "http://localhost:8080"
        ):
            raise DevError("Gateway CORS does not allow the canonical developer origin")


def doctor(root: Path) -> None:
    if not (root / ".env.dev").is_file():
        raise DevError(
            "Local dev config is missing; review prerequisites and run make bootstrap"
        )
    prerequisites()
    commands = Commands(root, read_only=True)
    commands.run([sys.executable, "-B", "infra/scripts/check_toolchain.py"])
    lock_fingerprints(root)
    config = commands.configuration()
    commands.own_resources(config)
    containers = commands.containers()
    validate_ports(config, containers, require_published=True)
    for service in CORE:
        state = containers.get(service, {}).get("State", {})
        if (
            not state.get("Running")
            or state.get("Health", {}).get("Status") != "healthy"
        ):
            raise DevError(f"Service is missing, stopped or unhealthy: {service}")
        print(f"PASS: {service} is running and healthy")
    for service, specification in config["services"].items():
        if service not in containers:
            continue
        for mount in specification.get("volumes", []):
            if mount.get("type") == "bind":
                path = Path(mount["source"])
                if not path.exists():
                    raise DevError(f"Missing bind source for {service}")
                if mount.get("bind", {}).get("selinux") != "z":
                    raise DevError(f"Shared SELinux label missing for {service}")
    for service, path in (
        ("backend", "app_postgres_password"),
        ("backend", "django_secret_key"),
        ("backend", "app_gateway_token"),
        ("api_gateway", "gateway_token"),
        ("minio", "minio_password"),
    ):
        commands.compose(
            "exec", "-T", service, "test", "-r", "/run/complect-secrets/" + path
        )
    commands.compose(
        "exec",
        "-T",
        "--user",
        "postgres",
        "postgres_primary",
        "test",
        "-r",
        "/run/complect-secrets/postgres_password",
    )
    database = database_status(commands, running=True)
    if (
        database["pending"]
        or not database["replica_recovery"]
        or not database["replica_current"]
    ):
        raise DevError("Database/replica migration state is not current")
    if not {"postgis", "pgcrypto", "pg_trgm"} <= set(database["extensions"]):
        raise DevError(
            "Required PostgreSQL extensions are missing; doctor will not create them"
        )
    if database["superuser"] or database["bypassrls"]:
        print(
            "WARN: development database role bypasses RLS; this is not production-role/RLS evidence"
        )
    commands.compose("exec", "-T", "redis", "redis-cli", "ping")
    commands.compose(
        "exec",
        "-T",
        "backend",
        "python",
        "-B",
        "requirements/lock.py",
        "verify-installed",
    )
    references = list(dict.fromkeys(config["services"][name]["image"] for name in CORE))
    image_data = json.loads(
        commands.run(["docker", "image", "inspect", *references]).stdout
    )
    expected_images = dict(zip(references, image_data, strict=True))
    for name in CORE:
        if (
            containers[name]["Image"]
            != expected_images[config["services"][name]["image"]]["Id"]
        ):
            raise DevError(
                f"Service is not using the current pinned/built image: {name}"
            )
    expected_source = source_digest(commands)
    if (
        expected_images[config["services"]["backend"]["image"]]["Config"]
        .get("Labels", {})
        .get("complect.source_digest")
        != expected_source
    ):
        raise DevError("Backend image inputs are stale; run make sync")
    frontend = commands.compose(
        "exec", "-T", "frontend", "node", "-p", "process.version"
    ).stdout.strip()
    npm = commands.compose("exec", "-T", "frontend", "npm", "--version").stdout.strip()
    manifest = json.loads((root / "toolchain.json").read_text())
    if (
        frontend != "v" + manifest["node"]["version"]
        or npm != manifest["npm"]["version"]
    ):
        raise DevError("Frontend runtime differs from pinned Node/npm")
    digest = hashlib.sha256()
    digest.update((root / "frontend/package-lock.json").read_bytes())
    digest.update((root / "frontend/package.json").read_bytes())
    digest.update(frontend.encode())
    digest.update(npm.encode())
    marker = commands.compose(
        "exec", "-T", "frontend", "cat", "/app/node_modules/.complect-install"
    ).stdout.strip()
    if marker != digest.hexdigest():
        raise DevError("Frontend dependency inputs are stale; run make sync")
    commands.compose(
        "exec",
        "-T",
        "backend",
        "curl",
        "-fsS",
        "--connect-timeout",
        "1",
        "--max-time",
        "2",
        "http://localhost:8010/health/",
    )
    _http("http://localhost:8080/login")
    _http("http://localhost:8000/public/ping/", origin=True)
    receipt_path = root / ".dev/sdk/receipt.json"
    generated = root / "frontend/src/shared/api/generated/schema.d.ts"
    if not receipt_path.is_file() or not generated.is_file():
        raise DevError("Generated SDK is missing; run make generate-sdk")
    receipt = json.loads(receipt_path.read_text())
    if (
        receipt["source"] != source_digest(commands)
        or receipt["sdk_sha256"] != hashlib.sha256(generated.read_bytes()).hexdigest()
    ):
        raise DevError("Generated SDK is stale; run make generate-sdk")
    if generated.stat().st_uid != os.getuid():
        raise DevError("Generated SDK ownership differs from developer")
    for path in (root / ".dev/test-artifacts").rglob("*"):
        if path.stat().st_uid != os.getuid():
            raise DevError("Test artifact ownership differs from developer")
    tracked = commands.run(["git", "ls-files", "-z"]).stdout.split("\0")
    forbidden = (".dev/", ".codegraph/", ".venv/", "node_modules/")
    for name in filter(None, tracked):
        if (
            name.startswith(forbidden)
            or "/node_modules/" in name
            or name in {".env", ".env.dev"}
        ):
            raise DevError("Generated state or local credentials are tracked by Git")
    commands.run(["git", "diff", "--check"])
    for line in commands.run(
        ["git", "ls-files", "--stage", "--", "*.sh"]
    ).stdout.splitlines():
        metadata, name = line.split("\t", 1)
        path = root / name
        if path.exists() and (
            bool(path.stat().st_mode & 0o111) != metadata.startswith("100755")
        ):
            raise DevError("Shell executable-bit policy differs from Git")
    if os.access(root / "infra/postgres/init/01_replication.sh", os.X_OK):
        raise DevError(
            "The sourced PostgreSQL replication initializer must not be executable"
        )
    if not (root / ".gitattributes").is_file():
        raise DevError("Repository LF policy is missing")
    for name in filter(None, tracked):
        path = root / name
        if (
            path.is_file()
            and path.suffix in {".sh", ".py", ".yml", ".toml"}
            and b"\r\n" in path.read_bytes()
        ):
            raise DevError("CRLF found in executable/source configuration")
    release = (
        Path("/etc/os-release").read_text() if Path("/etc/os-release").is_file() else ""
    )
    if "ID=fedora" in release or 'ID="fedora"' in release:
        if (
            shutil.which("getenforce") is None
            or commands.run(["getenforce"]).stdout.strip() != "Enforcing"
        ):
            raise DevError(
                "Fedora target requires SELinux Enforcing; no automatic correction attempted"
            )
        for service, specification in config["services"].items():
            if service in containers:
                for mount in specification.get("volumes", []):
                    if mount.get("type") == "bind":
                        context = commands.run(["ls", "-Zd", mount["source"]]).stdout
                        if "container_file_t" not in context:
                            raise DevError(
                                f"Bind source has an unexpected SELinux context: {service}"
                            )
        print("PASS: SELinux enforcing and container bind contexts observed")
    else:
        print("SKIP: native Fedora SELinux proof requires the Phase 3 laptop check")
    print("PASS: CodeGraph executable matches 1.6.0")
    if not (root / ".codegraph/codegraph.db").is_file():
        print(
            "SKIP: local CodeGraph index absent; run codegraph init before agent code analysis"
        )
    skill_manifest = root / "infra/dev/skills.json"
    if not skill_manifest.is_file():
        raise DevError("Required skill manifest is missing")
    for name in json.loads(skill_manifest.read_text())["required"]:
        if not (Path.home() / ".agents/skills" / name / "SKILL.md").is_file():
            print(f"WARN: agent skill availability not verified on this host: {name}")
    print("PASS: doctor completed read-only diagnostics")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["configure", "bootstrap", "sync", "doctor", "sdk", "e2e", "compose"],
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    arguments, remaining = parser.parse_known_args()
    root = arguments.root.resolve()
    try:
        if arguments.command in {"configure", "bootstrap", "sync", "doctor"}:
            if remaining:
                raise DevError("Unexpected lifecycle arguments")
            {
                "configure": configure,
                "bootstrap": bootstrap,
                "sync": sync,
                "doctor": doctor,
            }[arguments.command](root)
        else:
            prerequisites()
            commands = Commands(root)
            commands.own_resources(commands.configuration())
            if arguments.command == "sdk":
                ensure_mount_directories(root)
                generate_sdk(commands)
            elif arguments.command == "e2e":
                doctor(root)
                commands.compose(
                    "run", "--rm", "--no-deps", "e2e_deps", write=True, timeout=600
                )
                result = commands.compose(
                    "run", "--rm", "--no-deps", "e2e", write=True, timeout=600
                )
                print(commands.redact(result.stdout))
            else:
                if not remaining:
                    raise DevError("A Compose command is required")
                commands.environment["DEV_SOURCE_DIGEST"] = source_digest(commands)
                return subprocess.call(
                    [*commands.prefix, *remaining], cwd=root, env=commands.environment
                )
    except (DevError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
