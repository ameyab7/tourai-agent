"""api/middleware.py — Observability middleware + sliding-window rate limiter."""

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict

from fastapi import Request, Response

from api.config import settings
from api.logging_setup import correlation_id
from api import metrics

logger = logging.getLogger("tourai.api")

_rate_buckets: dict[str, list[float]] = defaultdict(list)
_last_cleanup: float = 0.0


def check_rate_limit(ip: str) -> bool:
    global _last_cleanup
    now    = time.monotonic()
    window = 60.0

    # Purge IPs with no recent activity every 5 minutes
    if now - _last_cleanup > 300:
        stale = [k for k, v in _rate_buckets.items() if not v or now - v[-1] > window]
        for k in stale:
            del _rate_buckets[k]
        _last_cleanup = now

    _rate_buckets[ip] = [t for t in _rate_buckets[ip] if now - t < window]
    if len(_rate_buckets[ip]) >= settings.rate_limit_rpm:
        return False
    _rate_buckets[ip].append(now)
    return True


async def observability_middleware(request: Request, call_next) -> Response:
    cid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    correlation_id.set(cid)

    ip       = request.client.host if request.client else "unknown"
    path     = request.url.path
    method   = request.method
    endpoint = path.split("?")[0]

    logger.info(
        "request_received",
        extra={"method": method, "endpoint": endpoint, "ip": ip},
    )

    if not check_rate_limit(ip):
        logger.warning("rate_limit_exceeded", extra={"ip": ip, "endpoint": endpoint})
        metrics.request_count.labels(endpoint=endpoint, method=method, status="429").inc()
        metrics.errors_total.labels(endpoint=endpoint, error_type="rate_limited").inc()
        return Response(
            content=json.dumps({"detail": "Too many requests", "correlation_id": cid}),
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": "60", "X-Request-ID": cid},
        )

    start = time.perf_counter()

    try:
        response = await asyncio.wait_for(
            call_next(request), timeout=settings.request_timeout
        )
    except asyncio.TimeoutError:
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        logger.error(
            "request_timeout",
            extra={"endpoint": endpoint, "ip": ip, "elapsed_ms": elapsed_ms},
        )
        metrics.request_count.labels(endpoint=endpoint, method=method, status="504").inc()
        metrics.errors_total.labels(endpoint=endpoint, error_type="timeout").inc()
        return Response(
            content=json.dumps({"detail": "Request timed out", "correlation_id": cid}),
            status_code=504,
            media_type="application/json",
            headers={"X-Request-ID": cid},
        )

    elapsed    = time.perf_counter() - start
    elapsed_ms = round(elapsed * 1000)
    status     = str(response.status_code)

    metrics.request_count.labels(endpoint=endpoint, method=method, status=status).inc()
    metrics.request_duration.labels(endpoint=endpoint).observe(elapsed)

    if response.status_code >= 500:
        metrics.errors_total.labels(endpoint=endpoint, error_type="5xx").inc()
    elif response.status_code >= 400:
        metrics.errors_total.labels(endpoint=endpoint, error_type="4xx").inc()

    response.headers["X-Request-ID"] = cid

    logger.info(
        "request_completed",
        extra={
            "method":     method,
            "endpoint":   endpoint,
            "status":     response.status_code,
            "elapsed_ms": elapsed_ms,
            "ip":         ip,
        },
    )

    return response


def rate_bucket_stats() -> dict:
    """Return rate-limiter stats for /debug endpoint."""
    now = time.monotonic()
    return {
        "tracked_ips": len(_rate_buckets),
        "active_ips":  sum(
            1 for hits in _rate_buckets.values()
            if any(now - t < 60 for t in hits)
        ),
    }
