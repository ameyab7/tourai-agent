# tourai/utils/geoutils.py
#
# Centralized geospatial helpers — single source of truth for the whole codebase.
#
# Install:  pip install geopy   (geographiclib is pulled in automatically as a
#           required geopy dependency, so both arrive together)
#
# Library mapping
# ───────────────
#   haversine_meters  →  geopy  geodesic((p1), (p2)).meters
#   bearing           →  geographiclib  Geodesic.WGS84.Inverse()["azi1"]
#                        Note: geopy.distance.geodesic computes *distances only* —
#                        it has no .initial_bearing attribute.  We go to the
#                        underlying geographiclib directly for the WGS84 Inverse
#                        solution (forward azimuth).
#   angle_diff        →  pure arithmetic — no library needed
#   project_endpoint  →  geopy  geodesic(meters=d).destination(Point, bearing)
#
# Asyncio note
# ────────────
# All four functions are synchronous, pure-math, zero-I/O computations.
# They are safe to call directly from async functions — they do not perform
# any blocking I/O and will not stall the event loop.  No asyncio.run(),
# loop.run_in_executor(), or await wrappers are needed.

import logging
import math

logger = logging.getLogger(__name__)

try:
    from geopy.distance import geodesic as _geodesic
    from geopy import Point as _Point
    from geographiclib.geodesic import Geodesic as _Geodesic
    _GEOPY_AVAILABLE = True
    logger.debug("geoutils: using geopy + geographiclib (WGS84 ellipsoid)")
except ImportError:
    _GEOPY_AVAILABLE = False
    logger.debug(
        "geoutils: geopy not installed — using manual spherical-math fallback. "
        "Run `pip install geopy` for higher-accuracy WGS84 calculations."
    )


# ---------------------------------------------------------------------------
# haversine_meters
# ---------------------------------------------------------------------------

def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two WGS84 lat/lon points.

    geopy path : geodesic((lat1, lon1), (lat2, lon2)).meters
    Fallback   : manual Haversine formula (spherical Earth, ~0.5% error max)
    """
    if _GEOPY_AVAILABLE:
        return _geodesic((lat1, lon1), (lat2, lon2)).meters
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# bearing
# ---------------------------------------------------------------------------

def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing in degrees [0, 360) from point 1 to point 2.

    0 = North, 90 = East, 180 = South, 270 = West.

    geographiclib path : Geodesic.WGS84.Inverse(lat1, lon1, lat2, lon2)["azi1"] % 360
        azi1 is the forward azimuth at point 1 on the WGS84 geodesic — the most
        accurate available bearing calculation.  geographiclib is a required
        geopy dependency and is always present when geopy is installed.
    Fallback           : standard spherical-trig formula (atan2 of cross/dot).
    """
    if _GEOPY_AVAILABLE:
        result = _Geodesic.WGS84.Inverse(lat1, lon1, lat2, lon2)
        return result["azi1"] % 360
    dlon = math.radians(lon2 - lon1)
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


# ---------------------------------------------------------------------------
# angle_diff
# ---------------------------------------------------------------------------

def angle_diff(a: float, b: float) -> float:
    """Smallest angular difference between two compass bearings, in [0, 180].

    Handles 360° wrap-around:  angle_diff(350, 10) == 20  ✓
    Pure arithmetic — no external library required regardless of geopy availability.
    """
    return min(abs(a - b) % 360, 360 - abs(a - b) % 360)


# ---------------------------------------------------------------------------
# project_endpoint
# ---------------------------------------------------------------------------

def project_endpoint(
    lat: float, lon: float, heading: float, distance_m: float
) -> tuple[float, float]:
    """Project a position distance_m metres ahead along a given compass heading.

    geopy path : geodesic(meters=distance_m).destination(Point(lat, lon), heading)
        Uses the WGS84 Vincenty Direct solution — accurate over long distances.
        Returns a geopy.Point; we extract .latitude / .longitude.
    Fallback   : spherical approximation (valid for distances < ~500 km).

    Returns:
        (dest_lat, dest_lon)
    """
    if _GEOPY_AVAILABLE:
        dest = _geodesic(meters=distance_m).destination(_Point(lat, lon), heading)
        return dest.latitude, dest.longitude
    R = 6_371_000
    d = distance_m / R
    heading_r = math.radians(heading)
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    dest_lat_r = math.asin(
        math.sin(lat_r) * math.cos(d)
        + math.cos(lat_r) * math.sin(d) * math.cos(heading_r)
    )
    dest_lon_r = lon_r + math.atan2(
        math.sin(heading_r) * math.sin(d) * math.cos(lat_r),
        math.cos(d) - math.sin(lat_r) * math.sin(dest_lat_r),
    )
    return math.degrees(dest_lat_r), math.degrees(dest_lon_r)
