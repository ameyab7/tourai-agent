# utils/osrm.py
#
# OSRM helpers for street-aware navigation.
#
# Functions:
#   get_current_street  — returns the street name the user is currently on
#   walking_route       — full route polyline between two points (for simulation)

import logging

import httpx

from utils.geoutils import haversine_meters as _haversine
from utils.geoutils import bearing as _bearing
from utils.geoutils import project_endpoint as _project

logger = logging.getLogger(__name__)

_BASE    = "http://router.project-osrm.org"
_TIMEOUT = 8

# Shared client — avoids a new TCP+TLS handshake on every request
_http = httpx.AsyncClient(
    base_url=_BASE,
    timeout=_TIMEOUT,
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
)


async def _snap_street(lat: float, lon: float) -> str | None:
    """Raw OSRM snap — returns street name or None if unnamed / error."""
    url    = f"/nearest/v1/foot/{lon},{lat}"
    params = {"number": 1, "generate_hints": "false"}
    try:
        resp = await _http.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("_snap_street failed: %s", e)
        return None
    if data.get("code") != "Ok" or not data.get("waypoints"):
        return None
    name = data["waypoints"][0].get("name", "").strip()
    return name if name else None


async def get_current_street(lat: float, lon: float) -> str | None:
    """Snap GPS point to nearest road and return the street name.

    Returns None if on an unnamed road (e.g. driveways, plazas).

    NOTE — Option 1 (production approach):
    If this returns None, the right thing to do is WAIT for the next GPS ping.
    Once the user has moved 10-20m, they'll be on a named street and we'll
    have a reliable answer. Don't try to guess from a single unknown point.
    See the walk loop in run.py for the commented-out Option 1 implementation.
    """
    return await _snap_street(lat, lon)


async def get_street_ahead(
    lat: float,
    lon: float,
    heading: float,
    project_m: float = 25.0,
) -> str | None:
    """Return street name, projecting ahead if the current point is unnamed.

    Option 2 — when the user is on an unnamed driveway/plaza, project
    project_m metres forward in their heading direction and snap there instead.
    That projected point usually lands on the named street they're heading toward.

    Falls back to None if both snaps fail.
    """
    street = await _snap_street(lat, lon)
    if street:
        return street

    # Current point is unnamed — project ahead and try again
    proj_lat, proj_lon = _project(lat, lon, heading, distance_m=project_m)
    logger.debug(
        "get_street_ahead: unnamed at (%.5f, %.5f), projecting %.0fm to (%.5f, %.5f)",
        lat, lon, project_m, proj_lat, proj_lon,
    )
    return await _snap_street(proj_lat, proj_lon)


async def get_drive_time(
    from_lat: float, from_lon: float,
    to_lat:   float, to_lon:   float,
) -> dict:
    """Return driving duration and distance between two coordinates via OSRM.

    Returns: {duration_min, distance_km}  or  {duration_min: None, distance_km: None} on error.
    """
    url    = f"/route/v1/driving/{from_lon},{from_lat};{to_lon},{to_lat}"
    params = {"overview": "false", "steps": "false"}
    try:
        resp = await _http.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok":
            raise ValueError(data.get("message", "OSRM error"))
        route = data["routes"][0]
        return {
            "duration_min": round(route["duration"] / 60),
            "distance_km":  round(route["distance"] / 1000, 1),
        }
    except Exception as e:
        logger.warning("get_drive_time failed: %s", e)
        return {"duration_min": None, "distance_km": None}


async def walking_route(
    start_lat: float, start_lon: float,
    end_lat:   float, end_lon:   float,
) -> list[tuple[float, float]] | None:
    """Full walking route polyline between two points (used by simulation).

    Returns list of (lat, lon) tuples, or None on error.
    """
    url    = f"/route/v1/foot/{start_lon},{start_lat};{end_lon},{end_lat}"
    params = {"overview": "full", "geometries": "geojson", "steps": "false"}

    try:
        resp = await _http.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("walking_route failed: %s", e)
        return None

    if data.get("code") != "Ok":
        return None

    coords = data["routes"][0]["geometry"]["coordinates"]
    return [(lat, lon) for lon, lat in coords]
