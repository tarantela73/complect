from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
KONG_CONFIG = PROJECT_ROOT / "infra/api_gateway/kong.yml"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from infra.scripts.docker_network import (  # noqa: E402
    NetworkError,
    candidates,
    inventory,
    overlapping,
    validate_subnet,
)


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=check, timeout=60)


def disposable_network_command(name: str, subnet: str) -> list[str]:
    return ["docker", "network", "create", "--internal", "--subnet", subnet, name]


def create_disposable_network(
    name: str,
    identity: str,
    *,
    override: str | None = None,
    runner=run,
) -> str:
    """Create one owned network, retrying only a race that made the subnet overlap."""
    occupied = inventory(runner)
    sequence = [
        validate_subnet(override, kind="test")
    ] if override is not None else candidates(identity, kind="test")
    for subnet in sequence:
        if overlapping(subnet, occupied):
            if override is not None:
                raise NetworkError(f"subnet overlaps an existing Docker network: {subnet}")
            continue
        result = runner(disposable_network_command(name, subnet), check=False)
        if result.returncode == 0:
            return subnet
        refreshed = inventory(runner)
        overlap_error = "overlap" in result.stderr.lower()
        if not overlap_error or not overlapping(subnet, refreshed):
            if override is not None and overlap_error:
                raise NetworkError(
                    f"explicit KONG_TEST_SUBNET overlaps an existing Docker network: {subnet}"
                )
            raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
        occupied = refreshed
    if override is not None:
        raise NetworkError(f"explicit KONG_TEST_SUBNET overlaps an existing Docker network: {sequence[0]}")
    raise NetworkError("disposable test subnet pool is exhausted")


def cleanup_disposable_resources(runner, *, network: str, backend: str, gateway: str, created: bool) -> None:
    if not created:
        return
    runner(["docker", "rm", "-f", gateway, backend], check=False)
    runner(["docker", "network", "rm", network], check=False)


class FakeDocker:
    def __init__(self, networks: list[dict] | None = None, *, create_errors: list[str] | None = None):
        self.networks = list(networks or [])
        self.create_errors = list(create_errors or [])
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[:4] == ["docker", "network", "ls", "-q"]:
            ids = " ".join(network["Id"] for network in self.networks)
            return subprocess.CompletedProcess(command, 0, stdout=ids, stderr="")
        if command[:3] == ["docker", "network", "inspect"]:
            wanted = set(command[3:])
            payload = [network for network in self.networks if network["Id"] in wanted]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
        if command[:3] == ["docker", "network", "create"]:
            subnet = command[command.index("--subnet") + 1]
            if self.create_errors:
                error = self.create_errors.pop(0)
                if "overlap" in error:
                    self.networks.append({"Id": f"race-{len(self.networks)}", "Name": "racer", "IPAM": {"Config": [{"Subnet": subnet}]}})
                return subprocess.CompletedProcess(command, 1, stdout="", stderr=error)
            self.networks.append({"Id": f"created-{len(self.networks)}", "Name": command[-1], "IPAM": {"Config": [{"Subnet": subnet}]}})
            return subprocess.CompletedProcess(command, 0, stdout="created", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


class GatewayRuntimeTests(unittest.TestCase):
    def test_generated_disposable_candidates_are_canonical_ipv4_24(self) -> None:
        values = candidates("gateway-runtime-run", kind="test")
        self.assertEqual(len(values), 256)
        self.assertEqual(len(set(values)), 256)
        self.assertTrue(
            all(value.startswith("198.19.") and value.endswith(".0/24") for value in values)
        )

    def test_candidate_order_starts_at_sha256_slot_and_is_stable(self) -> None:
        identity = "abc"
        expected_slot = 173
        values = candidates(identity, kind="test")
        self.assertEqual(values[0], f"198.19.{expected_slot}.0/24")
        self.assertEqual(values, candidates(identity, kind="test"))

    def test_parallel_same_initial_slot_uses_next_candidate(self) -> None:
        docker = FakeDocker()
        first = create_disposable_network("owned-a", "same-checkout", runner=docker)
        second = create_disposable_network("owned-b", "same-checkout", runner=docker)
        self.assertNotEqual(first, second)
        creates = [command for command in docker.commands if command[:3] == ["docker", "network", "create"]]
        self.assertTrue(all("--subnet" in command for command in creates))

    def test_subnet_validation_rejects_malformed_ipv6_wrong_prefix_and_noncanonical(self) -> None:
        for value in ("not-a-subnet", "2001:db8::/64", "198.19.1.0/25", "198.19.1.7/24"):
            with self.subTest(value=value):
                with self.assertRaises(NetworkError):
                    validate_subnet(value, kind="test")

    def test_subnet_validation_rejects_reserved_pool_overlap(self) -> None:
        for value in ("198.18.0.0/24", "198.18.16.0/24", "198.18.128.0/24"):
            with self.subTest(value=value):
                with self.assertRaises(NetworkError):
                    validate_subnet(value, kind="test")

    def test_explicit_override_is_canonical_and_is_never_replaced(self) -> None:
        override = "172.30.44.0/24"
        docker = FakeDocker(create_errors=["Pool overlaps with other one on this address space"])
        with self.assertRaisesRegex(NetworkError, "explicit KONG_TEST_SUBNET overlaps"):
            create_disposable_network("owned", "run", override=override, runner=docker)
        self.assertEqual(len([command for command in docker.commands if command[:3] == ["docker", "network", "create"]]), 1)

    def test_collision_uses_next_candidate_and_exhaustion_is_explicit(self) -> None:
        sequence = candidates("run", kind="test")
        all_occupied = [
            {
                "Id": f"occupied-{index}",
                "Name": f"occupied-{index}",
                "IPAM": {"Config": [{"Subnet": subnet}]},
            }
            for index, subnet in enumerate(sequence)
        ]
        docker = FakeDocker(all_occupied)
        with self.assertRaisesRegex(NetworkError, "pool is exhausted"):
            create_disposable_network("owned", "run", runner=docker)
        self.assertFalse(any(command[:3] == ["docker", "network", "create"] for command in docker.commands))

    def test_race_overlap_retries_next_candidate(self) -> None:
        docker = FakeDocker(create_errors=["Pool overlaps with other one on this address space"])
        subnet = create_disposable_network("owned", "run", runner=docker)
        self.assertEqual(subnet, candidates("run", kind="test")[1])

    def test_unrelated_create_error_does_not_retry_even_if_inventory_changed(self) -> None:
        docker = FakeDocker(create_errors=["permission denied"])
        with self.assertRaises(subprocess.CalledProcessError):
            create_disposable_network("owned", "run", runner=docker)
        self.assertEqual(len([command for command in docker.commands if command[:3] == ["docker", "network", "create"]]), 1)

    def test_cleanup_deletes_only_owned_resources_after_success(self) -> None:
        docker = FakeDocker()
        cleanup_disposable_resources(docker, network="owned-network", backend="owned-backend", gateway="owned-gateway", created=False)
        self.assertEqual(docker.commands, [])
        cleanup_disposable_resources(docker, network="owned-network", backend="owned-backend", gateway="owned-gateway", created=True)
        self.assertEqual(
            docker.commands,
            [
                ["docker", "rm", "-f", "owned-gateway", "owned-backend"],
                ["docker", "network", "rm", "owned-network"],
            ],
        )

    def test_inventory_and_overlap_ignore_only_the_named_own_network(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '[{"Name":"own","IPAM":{"Config":[{"Subnet":"198.19.4.0/24"}]}},'
                    '{"Name":"other","IPAM":{"Config":[{"Subnet":"198.19.5.0/24"}]}}]'
                ),
                stderr="",
            )

        networks = inventory(fake_run)
        self.assertTrue(overlapping("198.19.4.0/24", networks, exclude_name="own") is False)
        self.assertTrue(overlapping("198.19.5.0/24", networks, exclude_name="own"))
        self.assertTrue(calls)

    def test_gateway_sanitizes_identity_headers_without_decoding_jwt(self) -> None:
        config = KONG_CONFIG.read_text(encoding="utf-8")
        self.assertIn('kong.service.request.clear_header("X-User-ID")', config)
        self.assertIn('kong.service.request.clear_header("X-Tenant-ID")', config)
        self.assertIn('kong.service.request.clear_header("X-CTX-User-ID")', config)
        self.assertIn('kong.service.request.clear_header("X-CTX-Tenant-ID")', config)
        self.assertNotIn("ngx.decode_base64", config)
        self.assertNotIn('kong.service.request.set_header("X-User-ID"', config)
        self.assertNotIn('kong.service.request.set_header("X-Tenant-ID"', config)
        self.assertLess(
            config.index('kong.service.request.clear_header("X-User-ID")'),
            config.index('kong.service.request.set_header("X-Gateway-Token"'),
        )

    @unittest.skipUnless(
        os.environ.get("KONG_TEST_BACKEND_IMAGE"), "set KONG_TEST_BACKEND_IMAGE to a built backend image"
    )
    def test_isolated_kong_and_backend_auth_boundary(self) -> None:
        """Real Kong + JWTAuth; fixed users Protocol double, not a database test.

        Runs entirely on an internal Docker network, without published ports,
        inherited application credentials, persistent volumes or image pulls.
        """
        suffix = uuid.uuid4().hex
        network = f"complect_kong_test_{suffix}"
        backend = f"complect_kong_backend_{suffix}"
        gateway = f"complect_kong_gateway_{suffix}"
        fixtures = Path(__file__).resolve().parent
        network_created = False
        try:
            subnet = create_disposable_network(
                network,
                network,
                override=os.environ.get("KONG_TEST_SUBNET"),
            )
            print(f"KONG_EXPLICIT_SUBNET={subnet}")
            network_created = True
            run(
                [
                    "docker",
                    "run",
                    "--pull=never",
                    "--rm",
                    "-d",
                    "--name",
                    backend,
                    "--network",
                    network,
                    "--network-alias",
                    "backend",
                    "--mount",
                    f"type=bind,src={PROJECT_ROOT / 'backend'},dst=/app,readonly",
                    "--mount",
                    f"type=bind,src={fixtures},dst=/probe,readonly",
                    "--workdir",
                    "/app",
                    "-e",
                    "PYTHONPATH=/app:/probe",
                    "-e",
                    "PYTHONDONTWRITEBYTECODE=1",
                    "--entrypoint",
                    "granian",
                    os.environ["KONG_TEST_BACKEND_IMAGE"],
                    "--interface",
                    "asgi",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8010",
                    "gateway_boundary_probe:application",
                ]
            )
            run(
                [
                    "docker",
                    "run",
                    "--pull=never",
                    "--rm",
                    "-d",
                    "--name",
                    gateway,
                    "--network",
                    network,
                    "--network-alias",
                    "gateway",
                    "-e",
                    "KONG_DATABASE=off",
                    "-e",
                    "KONG_DECLARATIVE_CONFIG=/etc/kong/kong.yml",
                    "-e",
                    "KONG_PROXY_LISTEN=0.0.0.0:8000",
                    "-e",
                    "KONG_ADMIN_LISTEN=off",
                    "-e",
                    "EXPECTED_GATEWAY_TOKEN=verification-only-gateway",
                    "--mount",
                    f"type=bind,src={KONG_CONFIG},dst=/etc/kong/kong.yml,readonly",
                    "kong@sha256:a2b2ec9964bba34065943e8be1d56791a4a06b7b0c10222f05564b372ca87840",
                ]
            )
            result = run(["docker", "exec", backend, "python", "/probe/gateway_boundary_client.py"])
            self.assertIn("KONG_RUNTIME_CASES=6 PASS", result.stdout)
            print(result.stdout.strip())
        finally:
            cleanup_disposable_resources(
                run,
                network=network,
                backend=backend,
                gateway=gateway,
                created=network_created,
            )


if __name__ == "__main__":
    unittest.main()
