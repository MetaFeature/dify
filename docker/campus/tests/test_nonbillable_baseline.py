#!/usr/bin/env python3
from __future__ import annotations

import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import nonbillable_baseline  # noqa: E402


class BaselineHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    statuses = {
        "/health": 200,
        "/portal/": 200,
        "/": 302,
        "/console/api/campus/session/access-check": 401,
    }

    def do_GET(self) -> None:
        self.send_response(self.statuses.get(self.path, 404))
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args: object) -> None:
        return


class NonbillableBaselineTest(unittest.TestCase):
    def setUp(self) -> None:
        BaselineHandler.statuses = {
            "/health": 200,
            "/portal/": 200,
            "/": 302,
            "/console/api/campus/session/access-check": 401,
        }
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BaselineHandler)
        self.server_thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.server_thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)

    def run_baseline(self) -> tuple[dict[str, object], bool]:
        return nonbillable_baseline.run_baseline(
            base_url=f"http://127.0.0.1:{self.server.server_port}",
            concurrency=4,
            requests_per_user=2,
            duration_seconds=1,
            max_p99_ms=500,
        )

    def test_exact_request_contract_passes(self) -> None:
        report, passed = self.run_baseline()

        self.assertTrue(passed, report)
        self.assertEqual(report["attempts"], 8)
        self.assertEqual(report["failures"], 0)

    def test_unexpected_status_fails(self) -> None:
        BaselineHandler.statuses["/"] = 200

        report, passed = self.run_baseline()

        self.assertFalse(passed)
        self.assertGreater(cast(int, report["failures"]), 0)

    def test_ninety_five_percent_of_required_attempts_fails(self) -> None:
        self.assertFalse(
            nonbillable_baseline.meets_baseline_contract(
                expected_attempts=100,
                total_attempts=95,
                response_count=95,
                total_failures=0,
                worst_p99_ms=5,
                max_p99_ms=100,
            )
        )

    def test_percentile_uses_nearest_rank(self) -> None:
        self.assertEqual(nonbillable_baseline._percentile([1, 2, 3, 4], 0.50), 2)
        self.assertEqual(nonbillable_baseline._percentile([1, 2, 3, 4], 0.99), 4)

    def test_transport_error_fails(self) -> None:
        with socket.socket() as unavailable_server:
            unavailable_server.bind(("127.0.0.1", 0))
            unavailable_port = unavailable_server.getsockname()[1]

        report, passed = nonbillable_baseline.run_baseline(
            base_url=f"http://127.0.0.1:{unavailable_port}",
            concurrency=1,
            requests_per_user=1,
            duration_seconds=1,
            max_p99_ms=500,
        )

        self.assertFalse(passed)
        self.assertEqual(report["failures"], 1)


if __name__ == "__main__":
    unittest.main()
