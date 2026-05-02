"""utils/geoapify_buildings.py — Live Walk POI search + visibility obstacle buildings.

Moved from utils/geoapify.py. Contains three functions used by the visibility
pipeline and Live Walk routes:
  search_nearby()             — named POI search for ask.py / pois.py
  search_obstacle_buildings() — anonymous building fetch for ray casting
  fetch_building_geometry()   — building footprint polygon fetch

Enrichment filtering reuses _is_interesting / _poi_type from geoapify_places.
"""

import logging
import os

import httpx

from utils.geoapify_places import _is_interesting, _poi_type

logger = logging.getLogger(__name__)

_BASE_URL    = "https://api.geoapify.com/v2/places"
_DETAILS_URL = "https://api.geoapify.com/v2/place-details"

_http = httpx.AsyncClient(
    timeout=15,
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
)

# In-process cache for building footprint polygons — polygons don't change,
# so this survives the process lifetime at zero extra API credits.
_bldg_geom_cache: dict[str, object] = {}

# Category list for Live Walk POI search (broader than itinerary — includes
# heritage, natural, historic building categories used by the visibility system).
_SEARCH_CATEGORIES = ",".join([
    "tourism.sights",
    "tourism.attraction",
    "entertainment.museum",
    "entertainment.culture",
    "entertainment.zoo",
    "heritage",
    "natural",
    "building.historic",
    "sport.stadium",
    "man_made.tower",
    "man_made.bridge",
])

_MAX_RESULTS = 100


# ── Feature parsing ───────────────────────────────────────────────────────────

def _parse_feature(feature: dict) -> dict | None:
    """Convert one Geoapify GeoJSON feature to the Live Walk POI dict format."""
    props = feature.get("properties", {})
    name  = props.get("name", "").strip()
    if not name:
        return None

    raw  = dict(props.get("datasource", {}).get("raw", {}))
    if "name" not in raw:
        raw["name"] = name

    if not _is_interesting(raw):
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
        "tags":       raw,
        "categories": props.get("categories", []),
        "poi_type":   _poi_type(raw),
        "geometry":   [],
    }


# ── Public API ────────────────────────────────────────────────────────────────

async def search_nearby(lat: float, lon: float, radius: float) -> list[dict]:
    """Search for named POIs near a GPS coordinate via Geoapify.

    Returns list of dicts: id, name, lat, lon, tags, poi_type, geometry.
    Returns [] on failure — never raises.
    Raises ValueError for invalid inputs.
    """
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180) or radius <= 0:
        raise ValueError(f"Invalid inputs: lat={lat}, lon={lon}, radius={radius}")

    api_key = os.environ.get("GEOAPIFY_API_KEY", "").strip().lstrip("=").strip()
    if not api_key:
        raise RuntimeError("GEOAPIFY_API_KEY is not set")

    params = {
        "categories": _SEARCH_CATEGORIES,
        "filter":     f"circle:{lon},{lat},{int(radius)}",
        "limit":      _MAX_RESULTS,
        "apiKey":     api_key,
    }

    try:
        resp = await _http.get(_BASE_URL, params=params)
        if resp.status_code == 402:
            logger.error("Geoapify API quota exceeded (402) — returning empty POI list")
            return []
        resp.raise_for_status()
        features = resp.json().get("features", [])
    except httpx.TimeoutException:
        logger.warning(f"Geoapify request timed out at ({lat}, {lon}) — returning empty list")
        return []
    except httpx.HTTPStatusError as e:
        logger.warning(f"Geoapify returned HTTP {e.response.status_code} — returning empty list")
        return []
    except Exception as e:
        logger.warning(f"Geoapify request failed — {e}")
        return []

    pois = [p for f in features if (p := _parse_feature(f)) is not None]
    logger.info(f"Fetched {len(pois)} POIs from Geoapify ({len(features)} raw features)")
    return pois


async def search_obstacle_buildings(
    lat:    float,
    lon:    float,
    radius: float = 200,
) -> list[dict]:
    """Fetch all buildings within radius metres for ray-cast obstacles.

    Returns list of {id, name, lat, lon}. Returns [] on failure.
    Cost: 1 Geoapify credit per call.
    """
    api_key = os.environ.get("GEOAPIFY_API_KEY", "").strip().lstrip("=").strip()
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
            logger.error("Geoapify quota exceeded fetching obstacle buildings (402)")
            return []
        resp.raise_for_status()
        features = resp.json().get("features", [])
    except Exception as e:
        logger.warning(f"Geoapify obstacle building fetch failed — {e}")
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

    logger.info(f"Fetched {len(buildings)} obstacle buildings from Geoapify")
    return buildings


async def fetch_building_geometry(place_id: str) -> object:
    """Fetch the building footprint polygon (Polygon or MultiPolygon) for a place_id.

    Returns a shapely geometry or None if only a Point is available.
    Results are cached in-process indefinitely.
    Cost: 1 Geoapify credit per uncached place_id.
    """
    if place_id in _bldg_geom_cache:
        return _bldg_geom_cache[place_id]

    api_key = os.environ.get("GEOAPIFY_API_KEY", "").strip().lstrip("=").strip()
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
            logger.error("Geoapify quota exceeded fetching building geometry (402)")
            _bldg_geom_cache[place_id] = None
            return None
        resp.raise_for_status()
        features = resp.json().get("features", [])
    except Exception as e:
        logger.warning(f"Geoapify geometry fetch failed for place {place_id[:12]} — {e}")
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
        logger.warning(f"Failed to parse Geoapify building geometry for place {place_id[:12]} — {e}")
        _bldg_geom_cache[place_id] = None
        return None
