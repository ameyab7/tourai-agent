"""utils/geoapify_places.py — POI fetching via Geoapify Places API.

Consolidated from the former utils/geoapify_places.py (category mapping +
fetch_pois) and utils/geoapify.py (OSM-tag enrichment filter). The public
API is unchanged so orchestrator.py requires no edits.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_PLACES_URL = "https://api.geoapify.com/v2/places"

# ── Geoapify category → our poi_type label ────────────────────────────────────

_CATEGORY_MAP: dict[str, str] = {
    "tourism.sights":                       "attraction",
    "tourism.sights.castle":                "castle",
    "tourism.sights.ruines":                "ruins",
    "tourism.sights.fort":                  "castle",
    "tourism.sights.archaeological_site":   "archaeological_site",
    "tourism.sights.memorial":              "memorial",
    "tourism.sights.tower":                 "tower",
    "tourism.sights.bridge":                "attraction",
    "tourism.sights.lighthouse":            "attraction",
    "tourism.sights.place_of_worship":      "attraction",
    "tourism.attraction":                   "attraction",
    "tourism.attraction.viewpoint":         "viewpoint",
    "tourism.attraction.artwork":           "artwork",
    "entertainment.museum":                 "museum",
    "entertainment.culture.gallery":        "art_gallery",
    "entertainment.culture.theatre":        "theatre",
    "entertainment.culture.arts_centre":    "culture",
    "entertainment.culture":                "culture",
    "entertainment.cinema":                 "cinema",
    "entertainment.theme_park":             "theme_park",
    "entertainment.aquarium":               "aquarium",
    "entertainment.zoo":                    "zoo",
    "leisure.park":                         "park",
    "leisure.park.nature_reserve":          "nature_reserve",
    "leisure.park.garden":                  "park",
    "national_park":                        "park",
    "natural.forest":                       "park",
    "natural.mountain.peak":                "peak",
    "beach":                                "beach",
    "beach.beach_resort":                   "beach",
    "catering.restaurant":                  "restaurant",
    "catering.cafe":                        "cafe",
    "catering.bar":                         "bar",
    "catering.pub":                         "pub",
    "catering.fast_food":                   "fast_food",
    "sport.stadium":                        "stadium",
    "sport.sports_centre":                  "sports_centre",
    "sport.swimming_pool":                  "swimming_pool",
    "production.winery":                    "winery",
    "production.brewery":                   "brewery",
}

# Expanded category list — includes the subcategories that _CATEGORY_MAP already
# handles but were never explicitly requested. Any single invalid string causes a
# 400 for the whole request, so this list must stay verified against the Geoapify
# category reference.
_CATEGORIES = ",".join([
    "tourism.sights",
    "tourism.sights.castle",
    "tourism.sights.ruines",
    "tourism.sights.fort",
    "tourism.sights.memorial",
    "tourism.sights.tower",
    "tourism.attraction",
    "tourism.attraction.viewpoint",
    "entertainment.museum",
    "entertainment.culture.gallery",
    "entertainment.culture.theatre",
    "entertainment.culture.arts_centre",
    "entertainment.cinema",
    "entertainment.theme_park",
    "entertainment.aquarium",
    "entertainment.zoo",
    "leisure.park",
    "leisure.park.nature_reserve",
    "national_park",
    "natural.mountain.peak",
    "beach",
    "catering.restaurant",
    "catering.cafe",
    "catering.bar",
    "catering.pub",
    "sport.stadium",
    "production.winery",
    "production.brewery",
])


# ── Enrichment filter (ported from the former utils/geoapify.py) ──────────────
# Drops POIs that have a generic tag (e.g. tourism=attraction) but carry no
# meaningful metadata — no wikidata, wikipedia, heritage, etc.  Keeps well-known
# places (Dealey Plaza, etc.) that have at least one enrichment tag.

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
_GENERIC_TOURISM_VALUES  = {"attraction", "artwork"}
_ENRICHMENT_TAGS         = {"wikipedia", "wikidata", "description", "heritage",
                             "architect", "start_date", "historic", "tourism"}
# Excludes "tourism" itself so tourism=attraction can't self-validate.
_TOURISM_ENRICHMENT_TAGS = _ENRICHMENT_TAGS - {"tourism"}


def _poi_type(tags: dict) -> str:
    """Return the primary OSM key present in a tag dict."""
    for key in _POI_TYPE_KEYS:
        if key in tags:
            return key
    return "unknown"


def _is_interesting(tags: dict) -> bool:
    """Return True if the POI's OSM tags indicate a genuinely visit-worthy place."""
    for key, allowed in _POI_VALUE_ALLOWLIST.items():
        val = tags.get(key)
        if val in allowed:
            if key == "building" and val in _GENERIC_BUILDING_VALUES:
                return any(t in tags for t in _ENRICHMENT_TAGS)
            if key == "tourism" and val in _GENERIC_TOURISM_VALUES:
                return any(t in tags for t in _TOURISM_ENRICHMENT_TAGS)
            return True
    return False


# ── POI parsing ───────────────────────────────────────────────────────────────

def _geoapify_to_poi(feature: dict[str, Any]) -> dict[str, Any] | None:
    props = feature.get("properties", {})
    name  = props.get("name", "").strip()
    if not name:
        return None
    coords = feature.get("geometry", {}).get("coordinates", [])
    if len(coords) < 2:
        return None
    lon, lat = coords[0], coords[1]

    # Real OSM tags from datasource.raw — used for enrichment filtering and scoring.
    raw = dict(props.get("datasource", {}).get("raw", {}))
    if "name" not in raw:
        raw["name"] = name

    if not _is_interesting(raw):
        return None

    # Most-specific Geoapify category wins for poi_type label.
    cats     = props.get("categories", [])
    poi_type = "place"
    for cat in cats:
        if cat in _CATEGORY_MAP:
            poi_type = _CATEGORY_MAP[cat]
            break
        for key, val in _CATEGORY_MAP.items():
            if cat.startswith(key):
                poi_type = val
                break

    return {
        "id":       props.get("place_id", f"{lat},{lon}"),
        "name":     name,
        "lat":      lat,
        "lon":      lon,
        "poi_type": poi_type,
        "tags":     raw,
    }


# ── Deduplication ─────────────────────────────────────────────────────────────

def _dedupe_pois(pois: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop duplicate POIs that share the same name and location (4 d.p.)."""
    seen: set[tuple] = set()
    result: list[dict[str, Any]] = []
    for poi in pois:
        key = (round(poi["lat"], 4), round(poi["lon"], 4), poi["name"].lower().strip())
        if key not in seen:
            seen.add(key)
            result.append(poi)
    return result


# ── Public API ────────────────────────────────────────────────────────────────

async def fetch_pois(
    lat: float,
    lon: float,
    radius_m: int,
    api_key: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch nearby POIs from Geoapify Places API."""
    if not api_key:
        logger.warning("Geoapify API key not configured — skipping POI fetch")
        return []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                _PLACES_URL,
                params={
                    "categories": _CATEGORIES,
                    "filter":     f"circle:{lon},{lat},{radius_m}",
                    "limit":      limit,
                    "apiKey":     api_key,
                },
            )
            if resp.status_code == 400:
                logger.error(f"Geoapify returned 400 Bad Request — check the category list: {resp.text[:200]}")
                return []
            resp.raise_for_status()
            features = resp.json().get("features", [])
            pois = [p for f in features if (p := _geoapify_to_poi(f)) is not None]
            pois = _dedupe_pois(pois)
            logger.info(f"Fetched {len(pois)} POIs from Geoapify at ({lat}, {lon})")
            return pois
    except Exception as exc:
        logger.error(f"Geoapify POI fetch failed — {exc}")
        return []
