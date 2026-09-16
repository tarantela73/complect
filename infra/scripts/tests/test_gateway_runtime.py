from __future__ import annotations

import os
import subprocess
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
KONG_CONFIG = PROJECT_ROOT / "infra/api_gateway/kong.yml"


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=check)


class GatewayRuntimeTests(unittest.TestCase):
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
        suffix = uuid.uuid4().hex[:12]
        network = f"complect_kong_test_{suffix}"
        backend = f"complect_kong_backend_{suffix}"
        gateway = f"complect_kong_gateway_{suffix}"
        fixtures = Path(__file__).resolve().parent
        try:
            network_command = ["docker", "network", "create", "--internal"]
            if subnet := os.environ.get("KONG_TEST_SUBNET"):
                network_command.extend(["--subnet", subnet])
            run([*network_command, network])
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
            run(["docker", "rm", "-f", gateway, backend], check=False)
            run(["docker", "network", "rm", network], check=False)


if __name__ == "__main__":
    unittest.main()
