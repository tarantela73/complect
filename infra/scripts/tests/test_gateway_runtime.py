from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
KONG_CONFIG = PROJECT_ROOT / "infra/api_gateway/kong.yml"


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=check)


class GatewayRuntimeTests(unittest.TestCase):
    def test_kong_resolves_the_gateway_token_before_forwarding(self) -> None:
        suffix = uuid.uuid4().hex[:12]
        network = f"complect_kong_test_{suffix}"
        echo_container = f"complect_kong_echo_{suffix}"
        kong_container = f"complect_kong_gateway_{suffix}"

        with tempfile.TemporaryDirectory() as directory:
            echo_script = Path(directory) / "echo.py"
            echo_script.write_text(
                """
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        matches = self.headers.get("X-Gateway-Token") == "verification-only-gateway"
        self.send_response(200 if matches else 403)
        self.end_headers()

    def log_message(self, format, *args):
        return


HTTPServer(("0.0.0.0", 8010), Handler).serve_forever()
""".lstrip(),
                encoding="utf-8",
            )

            try:
                run(["docker", "network", "create", network])
                run(
                    [
                        "docker",
                        "run",
                        "-d",
                        "--name",
                        echo_container,
                        "--network",
                        network,
                        "--network-alias",
                        "backend",
                        "-v",
                        f"{echo_script}:/app/echo.py:ro",
                        "python:3.12-slim",
                        "python",
                        "/app/echo.py",
                    ]
                )
                run(
                    [
                        "docker",
                        "run",
                        "-d",
                        "--name",
                        kong_container,
                        "--network",
                        network,
                        "-p",
                        "127.0.0.1::8000",
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
                        "-v",
                        f"{KONG_CONFIG}:/etc/kong/kong.yml:ro",
                        "kong:latest",
                    ]
                )
                published = run(["docker", "port", kong_container, "8000/tcp"]).stdout
                host_port = next(
                    value.rsplit(":", maxsplit=1)[1]
                    for value in published.splitlines()
                    if value.startswith("127.0.0.1:")
                )
                url = f"http://127.0.0.1:{host_port}/"

                deadline = time.monotonic() + 30
                last_error: Exception | None = None
                while time.monotonic() < deadline:
                    try:
                        with urllib.request.urlopen(url, timeout=3) as response:
                            self.assertEqual(response.status, 200)
                            return
                    except (OSError, urllib.error.URLError) as error:
                        last_error = error
                        time.sleep(0.5)
                self.fail(f"temporary Kong did not forward the resolved token: {type(last_error).__name__}")
            finally:
                run(["docker", "rm", "-f", kong_container, echo_container], check=False)
                run(["docker", "network", "rm", network], check=False)


if __name__ == "__main__":
    unittest.main()
