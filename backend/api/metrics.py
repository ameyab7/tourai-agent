"""api/metrics.py — Prometheus metrics + async external-call timer."""

import logging
import time
from contextlib import asynccontextmanager

from prometheus_client import Counter, Histogram

logger = logging.getLogger("tourai.api")

request_count = Counter(
    "tourai_requests_total",
    "Total HTTP requests",
    ["endpoint", "method", "status"],
)

request_duration = Histogram(
    "tourai_request_duration_seconds",
    "HTTP request duration",
    ["endpoint"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

cache_hits = Counter(
    "tourai_cache_hits_total",
    "Cache hits by type",
    ["cache_type"],
)

cache_misses = Counter(
    "tourai_cache_misses_total",
    "Cache misses by type",
    ["cache_type"],
)

external_duration = Histogram(
    "tourai_external_call_duration_seconds",
    "External API call duration",
    ["service"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

errors_total = Counter(
    "tourai_errors_total",
    "Application errors by endpoint and type",
    ["endpoint", "error_type"],
)


@asynccontextmanager
async def timed(service: str):
    """Time an external API call and record it to the histogram + logs."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - t0
        external_duration.labels(service=service).observe(elapsed)
        logger.info(
            "external_call",
            extra={"service": service, "duration_ms": round(elapsed * 1000)},
        )
