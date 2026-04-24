"""api/routes/pois.py — /v1/visible-pois and /v1/current-street endpoints."""

import asyncio
import logging
import os
import time
import traceback
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from api import metrics
from api.cache import cache, poi_cache_key, vis_cache_key, area_cache_key
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

# Source for named POIs
import os as _os
if _os.environ.get("GEOAPIFY_API_KEY"):
    from utils import geoapify as poi_source
    _HAS_GEOAPIFY = True
else:
    from utils import overpass as poi_source   # type: ignore[no-redef]
    _HAS_GEOAPIFY = False

# Obstacle buildings always come from Overpass (free, complete polygon coverage)
from utils.overpass import fetch_obstacle_buildings as _fetch_obstacle_buildings

# Tall-buildings enrichment from Overpass
try:
    from utils.overpass import search_tall_buildings as _search_tall_buildings
    _HAS_TALL = True
except ImportError:
    _HAS_TALL = False

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


async def _fetch_area_buildings(lat: float, lon: float) -> dict:
    """
    Fetch all building footprint polygons for a ~333m grid cell via Overpass.

    Returns {"buildings": {osm_way_id: (name, wgs84_geom, utm_geom)}} or {}.

    UTM geometries are pre-projected once at fetch time and stored in the
    1-hour area cache.  filter_visible finds them already projected and skips
    the 21ms re-projection loop that was previously run on every request.

    Uses Overpass (free) — returns ~150-600 building polygons vs ~10 from
    the old Geoapify approach.
    """
    from utils.visibility import _get_utm_transformer, _project_geom

    # 500m radius: large-block cities (Dallas) have ~83 buildings vs 19 at 300m.
    buildings = await _fetch_obstacle_buildings(lat, lon, radius=500)
    if not buildings:
        return {"buildings": {}}

    # Project all WGS84 polygons to UTM once, using the grid-cell centre.
    # Within any 500m cell the UTM zone is constant, so these projections are
    # valid for any user position inside the cell.
    xfm = _get_utm_transformer(lat, lon)
    projected = {}
    for pid, (name, wgs84_geom) in buildings.items():
        utm_geom = _project_geom(wgs84_geom, xfm) if wgs84_geom is not None else None
        projected[pid] = (name, wgs84_geom, utm_geom)

    logger.info("area_buildings_projected", extra={
        "lat": round(lat, 4), "lon": round(lon, 4),
        "total": len(projected),
        "with_utm": sum(1 for _, _, u in projected.values() if u is not None),
    })
    return {"buildings": projected}


@router.post("/v1/visible-pois", response_model=VisiblePoisResponse)
async def get_visible_pois(body: VisiblePoisRequest) -> VisiblePoisResponse:
    cid = correlation_id.get("-")
    t0  = time.perf_counter()

    # 1. Visibility cache (per heading bucket)
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

    # 2. POI cache
    p_key = poi_cache_key(body.latitude, body.longitude, body.radius)
    pois  = await cache.get(p_key)

    # 3. Area buildings cache (obstacle polygons for ray casting)
    a_key     = area_cache_key(body.latitude, body.longitude)
    area_data = await cache.get(a_key)

    # ── Parallel fetch for anything that missed cache ──────────────────────────
    async def _fetch_pois():
        async with metrics.timed("overpass"):
            return await poi_source.search_nearby(body.latitude, body.longitude, body.radius)

    async def _fetch_street():
        try:
            async with metrics.timed("osrm"):
                return await osrm.get_current_street(body.latitude, body.longitude)
        except Exception:
            logger.warning("osrm_street_failed")
            return None

    async def _fetch_tall():
        if not _HAS_TALL:
            return []
        try:
            return await asyncio.wait_for(
                _search_tall_buildings(body.latitude, body.longitude, radius=1500, min_levels=15),
                timeout=5.0,
            )
        except Exception:
            return []

    needs_pois = pois is None
    needs_area = area_data is None

    tasks: list = []
    if needs_pois:
        tasks.append(_fetch_pois())
        tasks.append(_fetch_street())
        tasks.append(_fetch_tall())
    else:
        tasks.append(_fetch_street())

    if needs_area:
        tasks.append(_fetch_area_buildings(body.latitude, body.longitude))

    try:
        results = await asyncio.gather(*tasks, return_exceptions=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logger.error("fetch_error", extra={"exc": traceback.format_exc()})
        metrics.errors_total.labels(endpoint="/v1/visible-pois", error_type="fetch").inc()
        raise HTTPException(status_code=502, detail="POI data temporarily unavailable")

    # Unpack results
    idx = 0
    if needs_pois:
        pois   = results[idx];   idx += 1
        street = results[idx];   idx += 1
        tall   = results[idx];   idx += 1

        existing_names = {p["name"].lower() for p in pois}
        new_tall = [p for p in tall if p["name"].lower() not in existing_names]
        if new_tall:
            logger.info("tall_buildings_merged", extra={"added": len(new_tall)})
        pois = pois + new_tall

        await cache.set(p_key, pois, ttl=settings.poi_cache_ttl)
        metrics.cache_misses.labels(cache_type="poi").inc()
    else:
        street = results[idx];   idx += 1
        metrics.cache_hits.labels(cache_type="poi").inc()

    if needs_area:
        area_data = results[idx]
        await cache.set(a_key, area_data, ttl=settings.area_cache_ttl)

    # 4. Visibility filter with ray casting
    buildings = (area_data or {}).get("buildings") or None

    visible, rejected = filter_visible(
        pois,
        user_lat     = body.latitude,
        user_lon     = body.longitude,
        user_heading = body.heading,
        buildings    = buildings,
        user_street  = street,
    )

    # 5. Cache and return
    result_payload = {
        "visible_pois":  [_poi_to_out(p).model_dump() for p in visible],
        "rejected_pois": [_poi_to_out(p).model_dump() for p in rejected],
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
        "ray_casting":   buildings is not None and len(buildings) > 0,
        "buildings_used": len(buildings) if buildings else 0,
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
