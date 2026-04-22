"""api/routes/route.py — GET /v1/route

Walking route between two points using Geoapify Routing API.
Used by the mobile walk simulator (replaces the unreliable OSRM public demo).
"""

import logging
import os

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()
logger = logging.getLogger("tourai.api")

_GEOAPIFY_ROUTING = "https://api.geoapify.com/v1/routing"


@router.get("/v1/route")
async def get_route(
    from_lat: float = Query(..., ge=-90,  le=90),
    from_lon: float = Query(..., ge=-180, le=180),
    to_lat:   float = Query(..., ge=-90,  le=90),
    to_lon:   float = Query(..., ge=-180, le=180),
):
    """
    Return a walking GeoJSON route between two points.
    Response matches the shape the mobile SimulateWalk expects:
      { code, routes: [{ distance, duration, geometry: { coordinates: [[lon,lat],...] } }] }
    """
    api_key = os.environ.get("GEOAPIFY_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="Routing not configured (no GEOAPIFY_API_KEY)")

    waypoints = f"{from_lat},{from_lon}|{to_lat},{to_lon}"
    params = {
        "waypoints": waypoints,
        "mode":      "walk",
        "format":    "geojson",
        "apiKey":    api_key,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_GEOAPIFY_ROUTING, params=params)
        if resp.status_code != 200:
            logger.error("geoapify_routing_http_error", extra={
                "status": resp.status_code,
                "body":   resp.text[:500],
            })
            raise HTTPException(status_code=502, detail=f"Routing service returned {resp.status_code}: {resp.text[:200]}")
        resp.raise_for_status()
        data = resp.json()
    except HTTPException:
        raise
    except httpx.TimeoutException:
        logger.warning("geoapify_routing_timeout")
        raise HTTPException(status_code=504, detail="Routing service timed out")
    except Exception as exc:
        logger.error("geoapify_routing_error", extra={"err": str(exc)})
        raise HTTPException(status_code=502, detail=f"Routing service error: {exc}")

    # Geoapify GeoJSON: features[0].geometry.coordinates = [[lon,lat],...]
    # features[0].properties has distance + time
    features = data.get("features", [])
    if not features:
        raise HTTPException(status_code=404, detail="No route found")

    feat  = features[0]
    props = feat.get("properties", {})
    coords = feat["geometry"]["coordinates"]

    # Flatten MultiLineString to LineString if needed
    if feat["geometry"]["type"] == "MultiLineString":
        flat = []
        for segment in coords:
            flat.extend(segment)
        coords = flat

    # Return in the same shape as OSRM so SimulateWalk needs minimal changes
    return {
        "code": "Ok",
        "routes": [{
            "distance": props.get("distance", 0),
            "duration": props.get("time", 0),
            "geometry": {
                "type":        "LineString",
                "coordinates": coords,
            },
        }],
    }
