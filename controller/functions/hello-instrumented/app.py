#!/usr/bin/env python3

import json
import os
import socket
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


PORT = int(os.getenv("PORT", "8080"))
MAX_WORK_MS = int(os.getenv("MAX_WORK_MS", "5000"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def perform_cpu_work(work_ms: int) -> int:
    """Perform bounded CPU work and return the number of loop iterations."""
    if work_ms <= 0:
        return 0

    deadline = time.perf_counter() + (work_ms / 1000.0)
    iterations = 0
    accumulator = 1

    while time.perf_counter() < deadline:
        accumulator = (
            accumulator * 1_664_525 + 1_013_904_223
        ) & 0xFFFFFFFF
        iterations += 1

    return iterations


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "InstrumentedHello/1.0"

    def _send_json(
        self,
        status_code: int,
        payload: dict,
        request_id: str | None = None,
    ) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))

        if request_id:
            self.send_header("X-Request-ID", request_id)

        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))

        if content_length <= 0:
            return {}

        raw_body = self.rfile.read(content_length)

        try:
            value = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

        return value if isinstance(value, dict) else {}

    def _parse_work_ms(self, body: dict) -> int:
        query = parse_qs(urlparse(self.path).query)

        value = query.get("work_ms", [None])[0]

        if value is None:
            value = body.get("work_ms", 0)

        try:
            work_ms = int(value)
        except (TypeError, ValueError):
            work_ms = 0

        return max(0, min(work_ms, MAX_WORK_MS))

    def _handle_request(self) -> None:
        parsed_path = urlparse(self.path)

        if parsed_path.path in {"/healthz", "/readyz"}:
            self._send_json(
                200,
                {
                    "status": "ok",
                    "timestamp": utc_now(),
                },
            )
            return

        request_id = (
            self.headers.get("X-Request-ID")
            or str(uuid.uuid4())
        )

        body = self._read_json_body()
        work_ms = self._parse_work_ms(body)

        started_at = utc_now()
        started_perf = time.perf_counter()

        iterations = perform_cpu_work(work_ms)

        duration_ms = round(
            (time.perf_counter() - started_perf) * 1000,
            3,
        )
        finished_at = utc_now()

        payload = {
            "message": "Hello from the instrumented Knative function",
            "request_id": request_id,
            "cluster": os.getenv("CLUSTER_NAME", "unknown"),
            "node": os.getenv("NODE_NAME", "unknown"),
            "pod": os.getenv(
                "POD_NAME",
                socket.gethostname(),
            ),
            "namespace": os.getenv("POD_NAMESPACE", "unknown"),
            "pod_ip": os.getenv("POD_IP", "unknown"),
            "host_ip": os.getenv("HOST_IP", "unknown"),
            "pod_uid": os.getenv("POD_UID", "unknown"),
            "knative_service": os.getenv("K_SERVICE", "unknown"),
            "knative_revision": os.getenv("K_REVISION", "unknown"),
            "knative_configuration": os.getenv(
                "K_CONFIGURATION",
                "unknown",
            ),
            "started_at": started_at,
            "finished_at": finished_at,
            "work_requested_ms": work_ms,
            "work_duration_ms": duration_ms,
            "work_iterations": iterations,
        }

        self._send_json(200, payload, request_id)

    def do_GET(self) -> None:
        self._handle_request()

    def do_POST(self) -> None:
        self._handle_request()

    def log_message(self, format_string: str, *args: object) -> None:
        print(
            f"{self.address_string()} "
            f"[{self.log_date_time_string()}] "
            f"{format_string % args}",
            flush=True,
        )


if __name__ == "__main__":
    address = ("0.0.0.0", PORT)

    print(
        f"Starting instrumented hello server on port {PORT}",
        flush=True,
    )

    server = ThreadingHTTPServer(address, RequestHandler)
    server.serve_forever()
