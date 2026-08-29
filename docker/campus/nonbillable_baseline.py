#!/usr/bin/env python3
"""Measure the credential-free Campus HTTP baseline with paced virtual users."""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.client import HTTPConnection, HTTPSConnection
from threading import Event
from typing import TypedDict
from urllib.parse import SplitResult, urlsplit

ROUTES = (
    ("/health", 200),
    ("/portal/", 200),
    ("/", 302),
    ("/console/api/campus/session/access-check", 401),
)


@dataclass
class WorkerResult:
    attempts: Counter[str] = field(default_factory=Counter)
    statuses: Counter[str] = field(default_factory=Counter)
    failures: Counter[str] = field(default_factory=Counter)
    latencies_ms: dict[str, list[float]] = field(
        default_factory=lambda: {path: [] for path, _ in ROUTES}
    )


class EndpointMetrics(TypedDict):
    attempts: int
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_max_ms: float


class BaselineReport(TypedDict):
    concurrency: int
    duration_seconds: int
    requests_per_user: float
    target_rps: float
    response_rps: float
    attempts: int
    expected_attempts: int
    failures: int
    failure_rate_pct: float
    max_p99_ms: float
    worst_p99_ms: float
    statuses: dict[str, int]
    failure_types: dict[str, int]
    endpoints: dict[str, EndpointMetrics]
    passed: bool


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _connection(target: SplitResult) -> HTTPConnection:
    hostname = target.hostname
    if hostname is None:
        raise ValueError("target URL has no hostname")
    connection_type = HTTPSConnection if target.scheme == "https" else HTTPConnection
    return connection_type(hostname, target.port, timeout=5)


def _request_path(target: SplitResult, route: str) -> str:
    prefix = target.path.rstrip("/")
    return f"{prefix}{route}" or "/"


def meets_baseline_contract(
    *,
    expected_attempts: int,
    total_attempts: int,
    response_count: int,
    total_failures: int,
    worst_p99_ms: float,
    max_p99_ms: float,
) -> bool:
    """Require the exact workload, successful responses, and latency budget."""
    return (
        total_attempts == expected_attempts
        and response_count == expected_attempts
        and total_failures == 0
        and worst_p99_ms <= max_p99_ms
    )


def _run_worker(
    worker_id: int,
    *,
    target: SplitResult,
    requests_per_user: float,
    duration_seconds: int,
    starts_at: float,
    start_event: Event,
) -> WorkerResult:
    result = WorkerResult()
    interval = 1.0 / requests_per_user
    next_request_at = starts_at
    deadline = starts_at + duration_seconds
    request_index = worker_id
    connection: HTTPConnection | None = None
    start_event.wait()

    while next_request_at < deadline:
        wait_seconds = next_request_at - time.monotonic()
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        route, expected_status = ROUTES[request_index % len(ROUTES)]
        request_index += 1
        result.attempts[route] += 1
        started = time.perf_counter()
        try:
            if connection is None:
                connection = _connection(target)
            connection.request(
                "GET",
                _request_path(target, route),
                headers={
                    "Accept": "*/*",
                    "Connection": "keep-alive",
                    "User-Agent": "campus-nonbillable-baseline/1.0",
                },
            )
            response = connection.getresponse()
            response.read()
            result.latencies_ms[route].append((time.perf_counter() - started) * 1000)
            result.statuses[f"{route}:{response.status}"] += 1
            if response.status != expected_status:
                result.failures[f"unexpected_status:{route}:{response.status}"] += 1
            if response.will_close:
                connection.close()
                connection = None
        except Exception as error:  # noqa: BLE001 - the report groups transport failures by safe type name
            result.failures[f"{type(error).__name__}:{route}"] += 1
            if connection is not None:
                connection.close()
                connection = None
        next_request_at += interval
        if next_request_at < time.monotonic() - interval:
            next_request_at = time.monotonic()

    if connection is not None:
        connection.close()
    return result


def run_baseline(
    *,
    base_url: str,
    concurrency: int,
    requests_per_user: float,
    duration_seconds: int,
    max_p99_ms: float,
) -> tuple[BaselineReport, bool]:
    target = urlsplit(base_url)
    if target.scheme not in {"http", "https"} or target.hostname is None:
        raise ValueError("base_url must be an absolute HTTP or HTTPS URL")
    if (
        concurrency < 1
        or requests_per_user <= 0
        or duration_seconds < 1
        or max_p99_ms <= 0
    ):
        raise ValueError(
            "concurrency, request rate, duration, and p99 limit must be positive"
        )

    start_event = Event()
    starts_at = time.monotonic() + 1.0
    wall_started = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(
                _run_worker,
                worker_id,
                target=target,
                requests_per_user=requests_per_user,
                duration_seconds=duration_seconds,
                starts_at=starts_at,
                start_event=start_event,
            )
            for worker_id in range(concurrency)
        ]
        start_event.set()
        worker_results = [future.result() for future in futures]
    elapsed = time.monotonic() - wall_started

    attempts: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    latencies_ms: dict[str, list[float]] = {path: [] for path, _ in ROUTES}
    for worker_result in worker_results:
        attempts.update(worker_result.attempts)
        statuses.update(worker_result.statuses)
        failures.update(worker_result.failures)
        for path, _ in ROUTES:
            latencies_ms[path].extend(worker_result.latencies_ms[path])

    endpoint_metrics: dict[str, EndpointMetrics] = {}
    worst_p99_ms = 0.0
    for path, _ in ROUTES:
        values = latencies_ms[path]
        p99_ms = _percentile(values, 0.99)
        worst_p99_ms = max(worst_p99_ms, p99_ms)
        endpoint_metrics[path] = {
            "attempts": attempts[path],
            "latency_p50_ms": round(_percentile(values, 0.50), 3),
            "latency_p95_ms": round(_percentile(values, 0.95), 3),
            "latency_p99_ms": round(p99_ms, 3),
            "latency_max_ms": round(max(values), 3) if values else 0.0,
        }

    total_attempts = sum(attempts.values())
    total_failures = sum(failures.values())
    response_count = sum(statuses.values())
    target_rps = concurrency * requests_per_user
    expected_attempts = concurrency * math.ceil(duration_seconds * requests_per_user)
    measured_seconds = max(duration_seconds, elapsed - 1.0)
    response_rps = response_count / measured_seconds
    passed = meets_baseline_contract(
        expected_attempts=expected_attempts,
        total_attempts=total_attempts,
        response_count=response_count,
        total_failures=total_failures,
        worst_p99_ms=worst_p99_ms,
        max_p99_ms=max_p99_ms,
    )
    report: BaselineReport = {
        "concurrency": concurrency,
        "duration_seconds": duration_seconds,
        "requests_per_user": requests_per_user,
        "target_rps": round(target_rps, 2),
        "response_rps": round(response_rps, 2),
        "attempts": total_attempts,
        "expected_attempts": expected_attempts,
        "failures": total_failures,
        "failure_rate_pct": round(100 * total_failures / max(1, total_attempts), 5),
        "max_p99_ms": max_p99_ms,
        "worst_p99_ms": round(worst_p99_ms, 3),
        "statuses": dict(sorted(statuses.items())),
        "failure_types": dict(sorted(failures.items())),
        "endpoints": endpoint_metrics,
        "passed": passed,
    }
    return report, passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--requests-per-user", type=float, default=1.0)
    parser.add_argument("--duration-seconds", type=int, default=300)
    parser.add_argument("--max-p99-ms", type=float, default=100.0)
    arguments = parser.parse_args()
    report, passed = run_baseline(
        base_url=arguments.base_url,
        concurrency=arguments.concurrency,
        requests_per_user=arguments.requests_per_user,
        duration_seconds=arguments.duration_seconds,
        max_p99_ms=arguments.max_p99_ms,
    )
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
