"""api/routes/health.py — /health, /metrics, /debug endpoints."""

import gc
import logging
import time

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from api.cache import cache
from api.config import settings
from api.middleware import rate_bucket_stats
from api.models import DependencyStatus, HealthResponse

router = APIRouter()
logger = logging.getLogger("tourai.api")


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


async def _check_overpass() -> DependencyStatus:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                settings.overpass_local_url.replace("/api/interpreter", "/api/status")
            )
            return DependencyStatus(name="overpass", ok=resp.status_code < 500)
    except Exception as exc:
        return DependencyStatus(name="overpass", ok=False, detail=str(exc))


async def _check_osrm() -> DependencyStatus:
    import asyncio
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{settings.osrm_base_url}/nearest/v1/foot/-96.8,32.77",
                params={"number": 1},
            )
            ok = resp.status_code == 200 and resp.json().get("code") == "Ok"
            return DependencyStatus(name="osrm", ok=ok)
    except Exception as exc:
        return DependencyStatus(name="osrm", ok=False, detail=str(exc))


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    import asyncio
    if settings.geoapify_api_key:
        poi_dep = DependencyStatus(name="poi_source", ok=True, detail="geoapify")
        deps    = [poi_dep, await _check_osrm()]
    else:
        deps = list(await asyncio.gather(_check_overpass(), _check_osrm()))

    all_ok = all(d.ok for d in deps)
    any_ok = any(d.ok for d in deps)
    status = "healthy" if all_ok else ("degraded" if any_ok else "unhealthy")
    return HealthResponse(status=status, dependencies=deps, timestamp=_now_iso())


@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/debug", include_in_schema=False)
async def debug() -> dict:
    if not settings.debug:
        raise HTTPException(status_code=404, detail="Not found")

    import resource
    mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    gc.collect()

    return {
        "cache": {
            "entries":   cache.size,
            "hits":      cache.hits,
            "misses":    cache.misses,
            "hit_ratio": round(cache.hits / max(cache.hits + cache.misses, 1), 3),
        },
        "rate_limiter": rate_bucket_stats(),
        "process": {
            "memory_mb":  round(mem_mb, 1),
            "gc_objects": len(gc.get_objects()),
        },
        "settings": {
            "debug":           settings.debug,
            "rate_limit_rpm":  settings.rate_limit_rpm,
            "request_timeout": settings.request_timeout,
            "poi_cache_ttl":   settings.poi_cache_ttl,
            "vis_cache_ttl":   settings.vis_cache_ttl,
        },
        "timestamp": _now_iso(),
    }
