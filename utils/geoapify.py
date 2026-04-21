# utils/geoapify.py
#
# Fetch nearby named POIs using the Geoapify Places API (OSM-backed).
#
# Public API — identical interface to utils/overpass.py:
#   search_nearby(lat, lon, radius) → list of POI dicts
#
# Each POI dict: id, name, lat, lon, tags, poi_type, geometry
#
# Notes:
#   - tags comes from datasource.raw — the actual OSM tag dict
#   - geometry is always [] (Geoapify returns point geometry only)
#     visibility.py handles this gracefully via tag-based size fallback
#   - Requires GEOAPIFY_API_KEY in environment

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.geoapify.com/v2/places"

# Shared client — one TCP connection pool for all requests
_http = httpx.AsyncClient(
    timeout=15,
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
)

# Geoapify categories that map to our POI allowlist.
# Using the broadest relevant buckets — we filter by OSM tags after fetching.
_CATEGORIES = ",".join([
    "tourism.sights",
    "tourism.attraction",
    "entertainment.museum",
    "entertainment.culture",
    "entertainment.zoo",
    "heritage",
    "natural",
    "building.historic",
    "sport.stadium",
])

_MAX_RESULTS = 100   # Geoapify max per request on free tier

# ---------------------------------------------------------------------------
# POI type + filtering — mirrors overpass.py logic exactly
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
# tourism values that are too generic on their own — only keep if the feature
# has at least one enrichment tag (wikidata, wikipedia, heritage, etc.)
# This filters indoor exhibits, minor plaques, and roadside attractions while
# keeping well-known places like Dealey Plaza that have wikidata entries.
_GENERIC_TOURISM_VALUES  = {"attraction", "artwork"}
_ENRICHMENT_TAGS = {"wikipedia", "wikidata", "description", "heritage",
                    "architect", "start_date", "historic", "tourism"}
# Same set minus "tourism" itself — used when checking tourism=attraction/artwork
# to avoid self-referential pass (the tourism tag is what triggered the check).
_TOURISM_ENRICHMENT_TAGS = _ENRICHMENT_TAGS - {"tourism"}


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
            if key == "tourism" and val in _GENERIC_TOURISM_VALUES:
                return (
                    any(t in tags for t in _TOURISM_ENRICHMENT_TAGS)
                    and bool(tags.get("wikidata"))   # hard requirement
                )
            return True
    return False


# Internal cache removed — main.py caches POI results for 1 hour, which is
# strictly better than the old 5-minute cache here. One source of truth.

# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_feature(feature: dict) -> dict | None:
    """Convert one Geoapify GeoJSON feature to our POI dict format."""
    props = feature.get("properties", {})
    name  = props.get("name", "").strip()
    if not name:
        return None

    # OSM tags live in datasource.raw — fall back to empty dict if missing
    raw  = props.get("datasource", {}).get("raw", {})
    tags = dict(raw)   # copy so we don't mutate the original

    # Geoapify sometimes strips the name from raw — put it back
    if "name" not in tags:
        tags["name"] = name

    if not _is_interesting(tags):
        return None

    lat = props.get("lat")
    lon = props.get("lon")
    if lat is None or lon is None:
        coords = feature.get("geometry", {}).get("coordinates", [])
        if len(coords) == 2:
            lon, lat = coords

    if lat is None or lon is None:
        return None

    return {
        "id":         props.get("place_id", f"geo_{name}_{lat}_{lon}"),
        "name":       name,
        "lat":        float(lat),
        "lon":        float(lon),
        "tags":       tags,
        "categories": props.get("categories", []),   # Geoapify category list for visibility sizing
        "poi_type":   _poi_type(tags),
        "geometry":   [],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def search_nearby(
    lat: float,
    lon: float,
    radius: float,
) -> list[dict]:
    """Search for named POIs near a GPS coordinate via Geoapify.

    Returns list of dicts: id, name, lat, lon, tags, poi_type, geometry.
    Returns [] on failure — never raises.
    Raises ValueError for invalid inputs.
    """
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180) or radius <= 0:
        raise ValueError(f"Invalid inputs: lat={lat}, lon={lon}, radius={radius}")

    api_key = os.environ.get("GEOAPIFY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEOAPIFY_API_KEY is not set")

    params = {
        "categories": _CATEGORIES,
        "filter":     f"circle:{lon},{lat},{int(radius)}",
        "limit":      _MAX_RESULTS,
        "apiKey":     api_key,
    }

    try:
        resp = await _http.get(_BASE_URL, params=params)

        if resp.status_code == 402:
            logger.error("geoapify_quota_exceeded")
            return []

        resp.raise_for_status()
        features = resp.json().get("features", [])

    except httpx.TimeoutException:
        logger.warning("geoapify_timeout", extra={"lat": lat, "lon": lon})
        return []
    except httpx.HTTPStatusError as e:
        logger.warning("geoapify_http_error", extra={"status": e.response.status_code})
        return []
    except Exception as e:
        logger.warning("geoapify_error", extra={"error": str(e)})
        return []

    pois = [p for f in features if (p := _parse_feature(f)) is not None]
    logger.info("geoapify_success", extra={"pois": len(pois), "features": len(features)})
    return pois


# ---------------------------------------------------------------------------
# Obstacle building fetch + geometry (used by visibility pipeline)
# ---------------------------------------------------------------------------

_DETAILS_URL = "https://api.geoapify.com/v2/place-details"

# Module-level geometry cache — building polygons don't change; survives the
# process lifetime so repeated requests for the same cell cost 0 extra credits.
_bldg_geom_cache: dict[str, object] = {}   # place_id → shapely geom | None


async def search_obstacle_buildings(
    lat:    float,
    lon:    float,
    radius: float = 200,
) -> list[dict]:
    """
    Fetch all buildings within `radius` metres — these are anonymous structures
    (offices, garages, apartment blocks) used as ray-cast obstacles.
    Returns list of {id, name, lat, lon}.  Returns [] on failure.
    Cost: 1 Geoapify credit per call.
    """
    api_key = os.environ.get("GEOAPIFY_API_KEY", "").strip()
    if not api_key:
        return []

    params = {
        "categories": "building",
        "filter":     f"circle:{lon},{lat},{int(radius)}",
        "limit":      100,
        "apiKey":     api_key,
    }
    try:
        resp = await _http.get(_BASE_URL, params=params)
        if resp.status_code == 402:
            logger.error("geoapify_quota_exceeded_obstacles")
            return []
        resp.raise_for_status()
        features = resp.json().get("features", [])
    except Exception as e:
        logger.warning("geoapify_obstacle_error", extra={"error": str(e)})
        return []

    buildings = []
    for feat in features:
        props  = feat.get("properties", {})
        geom   = feat.get("geometry", {})
        coords = geom.get("coordinates", [0.0, 0.0])
        buildings.append({
            "id":   props.get("place_id", ""),
            "name": props.get("name") or props.get("formatted") or "building",
            "lat":  coords[1],
            "lon":  coords[0],
        })

    logger.info("geoapify_obstacles", extra={"count": len(buildings)})
    return buildings


async def fetch_building_geometry(place_id: str) -> object:
    """
    Fetch the building footprint polygon (Polygon or MultiPolygon) for a
    Geoapify place_id.  Returns a shapely geometry or None if only a Point
    is available.  Results are cached in-process indefinitely.
    Cost: 1 Geoapify credit per uncached place_id.
    """
    if place_id in _bldg_geom_cache:
        return _bldg_geom_cache[place_id]

    api_key = os.environ.get("GEOAPIFY_API_KEY", "").strip()
    if not api_key:
        _bldg_geom_cache[place_id] = None
        return None

    try:
        from shapely.geometry import shape as _shape
    except ImportError:
        _bldg_geom_cache[place_id] = None
        return None

    params = {
        "id":       place_id,
        "features": "details,geometry",
        "apiKey":   api_key,
    }
    try:
        resp = await _http.get(_DETAILS_URL, params=params)
        if resp.status_code == 402:
            logger.error("geoapify_quota_exceeded_geometry")
            _bldg_geom_cache[place_id] = None
            return None
        resp.raise_for_status()
        features = resp.json().get("features", [])
    except Exception as e:
        logger.warning("geoapify_geometry_error", extra={"place_id": place_id[:12], "error": str(e)})
        _bldg_geom_cache[place_id] = None
        return None

    if not features:
        _bldg_geom_cache[place_id] = None
        return None

    geom_data = features[0].get("geometry")
    if not geom_data or geom_data.get("type") == "Point":
        _bldg_geom_cache[place_id] = None
        return None

    try:
        geom = _shape(geom_data)
        _bldg_geom_cache[place_id] = geom
        return geom
    except Exception as e:
        logger.warning("geoapify_geometry_parse_error", extra={"place_id": place_id[:12], "error": str(e)})
        _bldg_geom_cache[place_id] = None
        return None
