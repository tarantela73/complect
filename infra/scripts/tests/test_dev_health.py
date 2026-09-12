"""The actual Compose probe must time out even if the HTTP worker stops replying."""

from __future__ import annotations

import os
import signal
import socketserver
import subprocess
import threading
import time
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


class HealthProbeTests(unittest.TestCase):
    def test_probe_bounds_a_nonresponding_worker_without_orphaning_curl(self):
        connected = threading.Event()
        finish = threading.Event()

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                connected.set()
                finish.wait(10)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True

        with Server(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            probe = yaml.safe_load((ROOT / "docker-compose.dev.yml").read_text())["services"]["backend"]["healthcheck"][
                "test"
            ]
            replacement = f"http://127.0.0.1:{server.server_address[1]}/health/"
            values = [value.replace("http://localhost:8010/health/", replacement) for value in probe[1:]]
            command = ["sh", "-c", values[0]] if probe[0] == "CMD-SHELL" else values
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            started = time.monotonic()
            try:
                process.communicate(timeout=5)
                self.assertTrue(connected.is_set(), "Probe did not reach the HTTP boundary")
                self.assertNotEqual(process.returncode, 0)
                self.assertLess(time.monotonic() - started, 4.5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                self.fail("Health probe has no bounded HTTP timeout; worker stalls leave live curl processes")
            finally:
                finish.set()
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()
