"""
Lightweight observability: request ids and Prometheus metrics.

- Every request gets a correlation id (from the ``X-Request-ID`` header or a
  fresh uuid), echoed back in the response and attached to log records so a
  request can be traced across log lines.
- ``/metrics`` exposes request counts and latencies in Prometheus text format.
  Counters are per-process; under Gunicorn scrape each worker or enable
  prometheus_client multiprocess mode. Kept dependency-free on purpose.
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from threading import Lock

from flask import Flask, g, request

_lock = Lock()
_req_count: dict[tuple[str, int], int] = defaultdict(int)
_req_latency_sum: dict[str, float] = defaultdict(float)
_req_latency_count: dict[str, int] = defaultdict(int)


def init_observability(app: Flask) -> None:
    @app.before_request
    def _start_timer():
        g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        g.start_time = time.perf_counter()

    @app.after_request
    def _record(response):
        response.headers["X-Request-ID"] = getattr(g, "request_id", "")
        endpoint = request.endpoint or "unknown"
        if endpoint == "health.metrics":
            return response
        elapsed = time.perf_counter() - getattr(g, "start_time", time.perf_counter())
        with _lock:
            _req_count[(endpoint, response.status_code)] += 1
            _req_latency_sum[endpoint] += elapsed
            _req_latency_count[endpoint] += 1
        return response


def render_metrics() -> str:
    lines = [
        "# HELP autojob_requests_total Total HTTP requests by endpoint and status.",
        "# TYPE autojob_requests_total counter",
    ]
    with _lock:
        for (endpoint, status), count in sorted(_req_count.items()):
            lines.append(
                f'autojob_requests_total{{endpoint="{endpoint}",status="{status}"}} {count}'
            )
        lines.append("# HELP autojob_request_latency_seconds_avg Average latency per endpoint.")
        lines.append("# TYPE autojob_request_latency_seconds_avg gauge")
        for endpoint, total in sorted(_req_latency_sum.items()):
            n = _req_latency_count[endpoint] or 1
            lines.append(
                f'autojob_request_latency_seconds_avg{{endpoint="{endpoint}"}} {total / n:.6f}'
            )
    return "\n".join(lines) + "\n"
