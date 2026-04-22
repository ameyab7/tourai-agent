# utils/overpass.py
#
# Fetch nearby named POIs from OpenStreetMap via the Overpass API.
#
# Public API:
#   search_nearby(lat, lon, radius) → list of POI dicts
#
# Each POI dict: id, name, lat, lon, tags, poi_type, geometry
#
# Reliability strategy:
#   - Per-mirror backoff: a mirror that 504s is cooled down for 60s before reuse
#   - Async rate-limiting: asyncio.sleep (never blocks the event loop)
#   - 5 attempts across all available mirrors, exponential backoff between cycles
#   - Simplified fallback query on the last attempt (no regex — faster to execute)
#   - localhost mirror is skipped automatically when unreachable (Railway / prod)

import asyncio
import logging
import time
import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mirrors — ordered by preference. localhost is first so local dev is fast;
# it is skipped automatically after the first ConnectError.
# ---------------------------------------------------------------------------

_ALL_MIRRORS = [
    "http://localhost:12345/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

# Per-mirror backoff: mirror_url → earliest time it may be used again
_mirror_backoff: dict[str, float] = {}
_MIRROR_COOLDOWN = 60.0   # seconds to cool a mirror after a 5xx / timeout


def _available_mirrors() -> list[str]:
    """Return mirrors not currently in their cooldown window."""
    now = time.monotonic()
    available = [m for m in _ALL_MIRRORS if now >= _mirror_backoff.get(m, 0)]
    # Always return at least one mirror so we never give up entirely
    return available if available else list(_ALL_MIRRORS)


def _cool_mirror(url: str) -> None:
    _mirror_backoff[url] = time.monotonic() + _MIRROR_COOLDOWN
    logger.warning("overpass_mirror_cooled", extra={"mirror": url, "cooldown_s": _MIRROR_COOLDOWN})


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

# Full query: nodes + ways with polygon geometry (needed for area calculation)
_QUERY_FULL = """\
[out:json][timeout:25];
nw(around:{radius},{lat},{lon})
  [name]
  [~"^(tourism|historic|amenity|leisure|building|man_made|natural|railway)$"~"."];
out geom tags;
"""

# Simplified fallback: nodes only, no regex — much faster on loaded servers
_QUERY_FALLBACK = """\
[out:json][timeout:20];
node(around:{radius},{lat},{lon})[name][tourism];
node(around:{radius},{lat},{lon})[name][historic];
out body;
"""

# Tall buildings query: ways with a name and building:levels >= min_levels
# Returns way centroid + tags. Used to supplement Geoapify with skyscrapers
# that have no tourism/heritage tags.
_QUERY_TALL_BUILDINGS = """\
[out:json][timeout:20];
way(around:{radius},{lat},{lon})[name]["building:levels"];
out center tags;
"""

# ---------------------------------------------------------------------------
# POI filtering
# ---------------------------------------------------------------------------

_POI_TYPE_KEYS = [
    "tourism", "historic", "amenity", "leisure",
    "building", "man_made", "railway", "aeroway", "natural",
]

_POI_VALUE_ALLOWLIST: dict[str, set[str]] = {
    "tourism":  {"attraction", "museum", "artwork", "viewpoint", "gallery", "theme_park", "zoo"},
    "historic": {"monument", "memorial", "castle", "ruins", "building", "church", "fort",
                 "battlefield", "archaeological_site", "manor", "palace", "ship", "wreck",
                 "wayside_cross", "wayside_shrine"},
    "amenity":  {"place_of_worship", "theatre", "library", "arts_centre", "cinema", "townhall",
                 "courthouse", "university", "college", "stadium", "concert_hall", "opera"},
    "leisure":  {"park", "garden", "stadium", "sports_centre", "marina", "nature_reserve"},
    "building": {"cathedral", "church", "chapel", "civic", "government", "skyscraper",
                 "commercial", "office", "stadium", "train_station", "synagogue", "mosque",
                 "temple", "public"},
    "man_made": {"lighthouse", "tower", "water_tower", "windmill", "bridge"},
    "natural":  {"peak", "cave_entrance", "waterfall", "hot_spring"},
    "railway":  {"station"},
    "aeroway":  {"terminal"},
}

_GENERIC_BUILDING_VALUES = {"commercial", "office"}
_ENRICHMENT_TAGS = {"wikipedia", "wikidata", "description", "heritage",
                    "architect", "start_date", "historic", "tourism"}


def _poi_type(tags: dict) -> str:
    for key in _POI_TYPE_KEYS:
        if key in tags:
            return key
    return "unknown"


def _is_interesting(tags: dict) -> bool:
    for key, allowed in _POI_VALUE_ALLOWLIST.items():
        val = tags.get(key)
        if val in allowed:
            if key == "building" and val in _GENERIC_BUILDING_VALUES:
                return any(t in tags for t in _ENRICHMENT_TAGS)
            return True
    return False


def _parse(elements: list[dict]) -> list[dict]:
    pois = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name or not _is_interesting(tags):
            continue
        geometry = el.get("geometry", [])
        if el["type"] in ("way", "relation"):
            if geometry:
                lat = sum(c["lat"] for c in geometry) / len(geometry)
                lon = sum(c["lon"] for c in geometry) / len(geometry)
            else:
                center = el.get("center", {})
                lat, lon = center.get("lat"), center.get("lon")
        else:
            lat, lon = el.get("lat"), el.get("lon")
        if lat is None or lon is None:
            continue
        pois.append({
            "id":       el["id"],
            "name":     name,
            "lat":      lat,
            "lon":      lon,
            "tags":     tags,
            "poi_type": _poi_type(tags),
            "geometry": geometry,
        })
    logger.debug("overpass_parsed", extra={"pois": len(pois), "elements": len(elements)})
    return pois


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache: dict[tuple, dict] = {}
_CACHE_TTL  = 300     # 5 minutes
_CACHE_GRID = 0.001   # ~111m grid cells


def _cache_key(lat: float, lon: float) -> tuple:
    return (
        round(lat / _CACHE_GRID) * _CACHE_GRID,
        round(lon / _CACHE_GRID) * _CACHE_GRID,
    )


# ---------------------------------------------------------------------------
# Async rate limiter  (one request at a time, min gap between requests)
# ---------------------------------------------------------------------------

_request_lock = asyncio.Lock()
_last_request_time: float = 0.0
_MIN_GAP = 3.0   # seconds between outbound Overpass requests


async def _acquire_slot() -> None:
    """Async rate gate — never blocks the event loop."""
    global _last_request_time
    async with _request_lock:
        gap = time.monotonic() - _last_request_time
        if gap < _MIN_GAP:
            await asyncio.sleep(_MIN_GAP - gap)
        _last_request_time = time.monotonic()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def search_nearby(
    lat: float,
    lon: float,
    radius: float,
) -> list[dict]:
    """Search for named POIs near a GPS coordinate.

    Returns list of dicts: id, name, lat, lon, tags, poi_type, geometry.
    Returns [] after all retries fail — never raises.
    Raises ValueError for invalid inputs.
    """
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180) or radius <= 0:
        raise ValueError(f"Invalid inputs: lat={lat}, lon={lon}, radius={radius}")

    ck     = _cache_key(lat, lon)
    cached = _cache.get(ck)
    if cached and (time.monotonic() - cached["ts"]) < _CACHE_TTL:
        logger.debug("overpass_cache_hit")
        return cached["pois"]

    await _acquire_slot()

    query    = _QUERY_FULL.format(lat=lat, lon=lon, radius=int(radius))
    fallback = _QUERY_FALLBACK.format(lat=lat, lon=lon, radius=int(radius))

    max_attempts = 5

    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(max_attempts):
            is_last    = attempt == max_attempts - 1
            mirrors    = _available_mirrors()
            url        = mirrors[attempt % len(mirrors)]
            use_query  = fallback if is_last else query

            logger.info(
                "overpass_attempt",
                extra={"attempt": attempt + 1, "mirror": url, "fallback": is_last},
            )

            try:
                resp = await client.post(url, data={"data": use_query})

                if resp.status_code == 429:
                    _cool_mirror(url)
                    wait = 30 + attempt * 10
                    logger.warning("overpass_rate_limited", extra={"wait_s": wait})
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 500:
                    _cool_mirror(url)
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )

                resp.raise_for_status()
                pois = _parse(resp.json().get("elements", []))
                _cache[ck] = {"pois": pois, "ts": time.monotonic()}
                logger.info("overpass_success", extra={"attempt": attempt + 1, "pois": len(pois)})
                return pois

            except httpx.TimeoutException:
                _cool_mirror(url)
                logger.warning("overpass_timeout", extra={"attempt": attempt + 1, "mirror": url})
            except httpx.ConnectError:
                # localhost not running — cool it permanently for this process lifetime
                _mirror_backoff[url] = time.monotonic() + 3600
                logger.info("overpass_mirror_unreachable", extra={"mirror": url})
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:
                    return []   # 4xx — bad query, no point retrying
                logger.warning("overpass_http_error",
                               extra={"status": e.response.status_code, "attempt": attempt + 1})
            except Exception as e:
                logger.warning("overpass_error", extra={"error": str(e), "attempt": attempt + 1})

            if not is_last:
                backoff = min(2 ** attempt, 16)   # 1s, 2s, 4s, 8s — capped at 16s
                logger.info("overpass_backoff", extra={"wait_s": backoff})
                await asyncio.sleep(backoff)

    logger.warning("overpass_all_failed", extra={"lat": lat, "lon": lon})
    return []


# ---------------------------------------------------------------------------
# Obstacle buildings — all building polygons in a radius for ray casting
# ---------------------------------------------------------------------------

# Shapely import (optional — graceful no-op when not installed)
try:
    from shapely.geometry import Polygon as _Polygon
    _SHAPELY_OK = True
except ImportError:
    _SHAPELY_OK = False

# Query: all building ways with polygon geometry, no name filter.
# `out geom` returns the full node list so we can build Shapely polygons
# without a second API call.
_QUERY_OBSTACLE_BUILDINGS = """\
[out:json][timeout:15];
way(around:{radius},{lat},{lon})[building];
out geom;
"""

# Module-level cache: grid_key → {"buildings": dict, "ts": float}
_obstacle_cache: dict[tuple, dict] = {}
_OBSTACLE_CACHE_TTL  = 3600   # 1 hour — building footprints don't change
_OBSTACLE_CACHE_GRID = 0.003  # ~333m grid cells (same as area_cache in api/cache.py)


def _obstacle_cache_key(lat: float, lon: float) -> tuple:
    g = _OBSTACLE_CACHE_GRID
    return (round(lat / g) * g, round(lon / g) * g)


async def fetch_obstacle_buildings(
    lat:    float,
    lon:    float,
    radius: float = 500,
) -> dict:
    """
    Return all building polygons within *radius* metres as a dict suitable
    for passing directly to ``filter_visible(buildings=...)``.

    Format: ``{osm_way_id_str: (name, shapely_Polygon_or_None)}``

    - Uses the free Overpass API — zero credit cost.
    - Returns ~150–300 polygons vs ~10 from the old Geoapify approach.
    - Results are cached for 1 hour per ~333m grid cell.
    - Returns {} on failure — never raises.
    - Shares the module-level rate limiter with search_nearby.
    """
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180) or radius <= 0:
        return {}

    ck     = _obstacle_cache_key(lat, lon)
    cached = _obstacle_cache.get(ck)
    if cached and (time.monotonic() - cached["ts"]) < _OBSTACLE_CACHE_TTL:
        logger.debug("obstacle_cache_hit", extra={"key": ck, "count": len(cached["buildings"])})
        return cached["buildings"]

    await _acquire_slot()

    query = _QUERY_OBSTACLE_BUILDINGS.format(lat=lat, lon=lon, radius=int(radius))

    # overpass-api.de (Apache front-end) returns 406 if Accept is not set explicitly.
    _hdrs = {"Accept": "application/json, */*", "User-Agent": "TourAI/1.0"}

    # 10s per-mirror timeout: aggressive enough to fail fast to the next mirror,
    # generous enough for a 300m building query on an uncongested server.
    async with httpx.AsyncClient(timeout=10, headers=_hdrs) as client:
        for mirror in _available_mirrors():
            try:
                resp = await client.post(mirror, data={"data": query})

                if resp.status_code == 429:
                    _cool_mirror(mirror)
                    continue
                if resp.status_code >= 500:
                    _cool_mirror(mirror)
                    continue
                if resp.status_code >= 400:
                    # Don't abort — 406 from one mirror may be transient; try the next
                    logger.warning("obstacle_buildings_http_error",
                                   extra={"status": resp.status_code, "mirror": mirror})
                    continue

                elements = resp.json().get("elements", [])
                buildings: dict = {}

                for el in elements:
                    way_id = str(el.get("id", ""))
                    if not way_id:
                        continue
                    tags    = el.get("tags", {})
                    name    = tags.get("name") or tags.get("building") or "building"
                    geom_pts = el.get("geometry", [])

                    polygon = None
                    if _SHAPELY_OK and len(geom_pts) >= 3:
                        try:
                            coords  = [(pt["lon"], pt["lat"]) for pt in geom_pts]
                            polygon = _Polygon(coords)
                            if not polygon.is_valid:
                                polygon = polygon.buffer(0)   # fix self-intersections
                        except Exception:
                            polygon = None

                    buildings[way_id] = (name, polygon)

                _obstacle_cache[ck] = {"buildings": buildings, "ts": time.monotonic()}
                logger.info(
                    "obstacle_buildings_fetched",
                    extra={
                        "lat":    round(lat, 4),
                        "lon":    round(lon, 4),
                        "mirror": mirror,
                        "total":  len(elements),
                        "with_polygon": sum(1 for _, g in buildings.values() if g is not None),
                    },
                )
                return buildings

            except httpx.TimeoutException:
                _cool_mirror(mirror)
                logger.warning("obstacle_buildings_timeout", extra={"mirror": mirror})
            except httpx.ConnectError:
                _mirror_backoff[mirror] = time.monotonic() + 3600
                logger.info("obstacle_buildings_mirror_unreachable", extra={"mirror": mirror})
            except Exception as e:
                logger.warning("obstacle_buildings_error",
                               extra={"mirror": mirror, "error": str(e)})

    logger.warning("obstacle_buildings_all_failed", extra={"lat": lat, "lon": lon})
    return {}


async def search_tall_buildings(
    lat: float,
    lon: float,
    radius: float = 1500,
    min_levels: int = 15,
) -> list[dict]:
    """Fetch named buildings with building:levels >= min_levels within radius.

    Returns the same POI dict format as search_nearby.
    Used to supplement Geoapify with skyscrapers that lack tourism/heritage tags.
    Returns [] on failure — never raises.
    """
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180) or radius <= 0:
        return []

    query = _QUERY_TALL_BUILDINGS.format(
        lat=lat, lon=lon,
        radius=int(radius),
        min_levels=min_levels,
    )

    async with httpx.AsyncClient(timeout=20) as client:
        for mirror in _available_mirrors():
            try:
                resp = await client.post(mirror, data={"data": query})
                if resp.status_code >= 400:
                    continue
                elements = resp.json().get("elements", [])
                pois = []
                for el in elements:
                    tags = el.get("tags", {})
                    name = tags.get("name", "").strip()
                    if not name:
                        continue
                    try:
                        levels = int(tags.get("building:levels", 0))
                    except (ValueError, TypeError):
                        continue
                    if levels < min_levels:
                        continue
                    center = el.get("center", {})
                    elat, elon = center.get("lat"), center.get("lon")
                    if elat is None or elon is None:
                        continue
                    pois.append({
                        "id":       f"tb_{el['id']}",
                        "name":     name,
                        "lat":      elat,
                        "lon":      elon,
                        "tags":     tags,
                        "poi_type": "building",
                        "geometry": [],
                    })
                logger.info("overpass_tall_buildings", extra={"count": len(pois), "mirror": mirror})
                return pois
            except Exception as e:
                logger.warning("overpass_tall_buildings_error", extra={"mirror": mirror, "error": str(e)})

    return []
