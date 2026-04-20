"""api/routes/pois.py — /v1/visible-pois and /v1/current-street endpoints."""

import asyncio
import logging
import time
import traceback
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from api import metrics
from api.cache import cache, poi_cache_key, vis_cache_key
from api.config import settings
from api.logging_setup import correlation_id
from api.models import (
    CurrentStreetResponse,
    PoiOut,
    VisiblePoisRequest,
    VisiblePoisResponse,
)
from utils import osrm
from utils.visibility import filter_visible
from utils.overpass import search_tall_buildings

import os
if os.environ.get("GEOAPIFY_API_KEY"):
    from utils import geoapify as poi_source
else:
    from utils import overpass as poi_source  # type: ignore[no-redef]

router  = APIRouter()
logger  = logging.getLogger("tourai.api")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _poi_to_out(p: dict) -> PoiOut:
    return PoiOut(
        id=p["id"],
        name=p["name"],
        lat=p["lat"],
        lon=p["lon"],
        poi_type=p.get("poi_type", "unknown"),
        distance_m=p.get("distance_m", 0.0),
        angle_deg=p.get("angle_deg", 0.0),
        tags=p.get("tags", {}),
    )


@router.post("/v1/visible-pois", response_model=VisiblePoisResponse)
async def get_visible_pois(body: VisiblePoisRequest) -> VisiblePoisResponse:
    cid = correlation_id.get("-")
    t0  = time.perf_counter()

    # 1. Visibility cache
    vis_key = vis_cache_key(body.latitude, body.longitude, body.heading)
    cached  = await cache.get(vis_key)
    if cached:
        metrics.cache_hits.labels(cache_type="visibility").inc()
        logger.info("visible_pois", extra={
            "lat":           round(body.latitude, 5),
            "lon":           round(body.longitude, 5),
            "heading":       round(body.heading, 1),
            "street":        cached.get("street_name"),
            "visible_count": len(cached.get("visible_pois", [])),
            "total_checked": cached.get("total_checked", 0),
            "cache_hit":     True,
            "elapsed_ms":    round((time.perf_counter() - t0) * 1000),
        })
        return VisiblePoisResponse(**cached, cache_hit=True, correlation_id=cid, timestamp=_now_iso())

    metrics.cache_misses.labels(cache_type="visibility").inc()

    # 2. POI fetch + street name in parallel
    p_key = poi_cache_key(body.latitude, body.longitude, body.radius)
    pois  = await cache.get(p_key)

    async def _fetch_pois():
        async with metrics.timed("overpass"):
            return await poi_source.search_nearby(body.latitude, body.longitude, body.radius)

    async def _fetch_street():
        try:
            async with metrics.timed("osrm"):
                return await osrm.get_current_street(body.latitude, body.longitude)
        except Exception:
            logger.warning("osrm_street_failed", extra={"lat": body.latitude, "lon": body.longitude})
            return None

    if pois is None:
        metrics.cache_misses.labels(cache_type="poi").inc()
        async def _fetch_tall():
            try:
                return await asyncio.wait_for(
                    search_tall_buildings(body.latitude, body.longitude, radius=1500, min_levels=15),
                    timeout=5.0,
                )
            except (asyncio.TimeoutError, Exception):
                logger.info("tall_buildings_skipped", extra={"reason": "timeout or error"})
                return []

        try:
            (pois, street, tall) = await asyncio.gather(
                _fetch_pois(),
                _fetch_street(),
                _fetch_tall(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception:
            logger.error("overpass_error", extra={"exc": traceback.format_exc()})
            metrics.errors_total.labels(endpoint="/v1/visible-pois", error_type="overpass").inc()
            raise HTTPException(status_code=502, detail="POI data temporarily unavailable")

        # Merge tall buildings — skip any already returned by Geoapify (match by name)
        existing_names = {p["name"].lower() for p in pois}
        new_tall = [p for p in tall if p["name"].lower() not in existing_names]
        if new_tall:
            logger.info("tall_buildings_merged", extra={"added": len(new_tall)})
        pois = pois + new_tall

        await cache.set(p_key, pois, ttl=settings.poi_cache_ttl)
    else:
        metrics.cache_hits.labels(cache_type="poi").inc()
        street = await _fetch_street()

    # 3. Visibility filter (pure Python — no I/O)
    visible, _ = filter_visible(
        pois,
        user_lat=body.latitude,
        user_lon=body.longitude,
        user_heading=body.heading,
        user_street=street,
    )

    # 4. Cache and return
    result_payload = {
        "visible_pois":  [_poi_to_out(p).model_dump() for p in visible],
        "street_name":   street,
        "total_checked": len(pois),
    }
    await cache.set(vis_key, result_payload, ttl=settings.vis_cache_ttl)

    logger.info("visible_pois", extra={
        "lat":           round(body.latitude, 5),
        "lon":           round(body.longitude, 5),
        "heading":       round(body.heading, 1),
        "street":        street,
        "visible_count": len(visible),
        "total_checked": len(pois),
        "cache_hit":     False,
        "elapsed_ms":    round((time.perf_counter() - t0) * 1000),
        "poi_names":     [p["name"] for p in result_payload["visible_pois"]],
    })

    return VisiblePoisResponse(
        **result_payload,
        cache_hit=False,
        correlation_id=cid,
        timestamp=_now_iso(),
    )


@router.get("/v1/current-street", response_model=CurrentStreetResponse)
async def get_current_street(
    lat: float = Query(..., ge=-90,  le=90,  description="WGS84 latitude"),
    lon: float = Query(..., ge=-180, le=180, description="WGS84 longitude"),
) -> CurrentStreetResponse:
    try:
        async with metrics.timed("osrm"):
            street = await osrm.get_current_street(lat, lon)
    except Exception:
        logger.error("osrm_error", extra={"exc": traceback.format_exc()})
        metrics.errors_total.labels(endpoint="/v1/current-street", error_type="osrm").inc()
        raise HTTPException(status_code=502, detail="Street lookup temporarily unavailable")

    return CurrentStreetResponse(street_name=street, latitude=lat, longitude=lon)
