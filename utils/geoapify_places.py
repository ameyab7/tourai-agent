"""utils/geoapify_places.py — POI fetching via Geoapify Places API."""

import logging
from typing import Any

import httpx

logger = logging.getLogger("tourai.api")

_PLACES_URL = "https://api.geoapify.com/v2/places"

# Geoapify category → our poi_type label
# Only confirmed-valid Geoapify categories listed here.
# Historical sights (castles, ruins, monuments) are under tourism.sights.* — NOT historic.*
_CATEGORY_MAP = {
    "tourism.sights":            "attraction",
    "tourism.sights.castle":     "castle",
    "tourism.sights.monument":   "monument",
    "tourism.sights.memorial":   "memorial",
    "tourism.sights.ruins":      "ruins",
    "tourism.sights.archaeological_site": "archaeological_site",
    "tourism.sights.viewpoint":  "viewpoint",
    "tourism.sights.tower":      "tower",
    "tourism.sights.fort":       "castle",
    "tourism.attraction":        "attraction",
    "entertainment.museum":      "museum",
    "entertainment.art_gallery": "art_gallery",
    "entertainment.cinema":      "cinema",
    "entertainment.theme_park":  "theme_park",
    "entertainment.aquarium":    "aquarium",
    "entertainment.zoo":         "zoo",
    "natural.park":              "park",
    "natural.forest":            "park",
    "natural.beach":             "beach",
    "natural.peak":              "peak",
    "catering.restaurant":       "restaurant",
    "catering.cafe":             "cafe",
    "catering.bar":              "bar",
    "catering.pub":              "pub",
    "catering.fast_food":        "fast_food",
    "commercial.shopping_mall":  "mall",
    "commercial.market":         "marketplace",
    "sport.stadium":             "stadium",
    "sport.sports_centre":       "sports_centre",
    "sport.swimming_pool":       "swimming_pool",
    "production.winery":         "winery",
    "production.brewery":        "brewery",
}

# Only confirmed-valid Geoapify category strings.
# Any invalid entry makes the entire request return 400 — keep this list conservative.
_CATEGORIES = ",".join([
    "tourism.sights",
    "tourism.attraction",
    "entertainment.museum",
    "entertainment.art_gallery",
    "entertainment.cinema",
    "entertainment.theme_park",
    "entertainment.aquarium",
    "entertainment.zoo",
    "natural.park",
    "natural.beach",
    "catering.restaurant",
    "catering.cafe",
    "catering.bar",
    "catering.pub",
    "sport.stadium",
])


def _geoapify_to_poi(feature: dict[str, Any]) -> dict[str, Any] | None:
    props = feature.get("properties", {})
    name  = props.get("name", "").strip()
    if not name:
        return None
    coords = feature.get("geometry", {}).get("coordinates", [])
    if len(coords) < 2:
        return None
    lon, lat = coords[0], coords[1]

    # Pick the most specific category label
    cats     = props.get("categories", [])
    poi_type = "place"
    for cat in cats:
        if cat in _CATEGORY_MAP:
            poi_type = _CATEGORY_MAP[cat]
            break
        # Partial match on prefix (e.g. "historic.ruins.abc" → "ruins")
        for key, val in _CATEGORY_MAP.items():
            if cat.startswith(key):
                poi_type = val
                break

    # Build a minimal tags dict so scoring code can reuse OSM-style keys
    tags: dict[str, Any] = {
        "name":        name,
        "description": props.get("datasource", {}).get("raw", {}).get("description", ""),
        "website":     props.get("website", ""),
        "opening_hours": props.get("opening_hours", ""),
        "addr:street":   props.get("address_line1", ""),
        "addr:city":     props.get("city", ""),
    }
    # Expose the primary category under the OSM-style key that matches poi_type
    osm_key = (
        "tourism"  if poi_type in {"attraction", "museum", "art_gallery", "theme_park", "aquarium", "zoo",
                                    "castle", "monument", "memorial", "ruins", "archaeological_site",
                                    "viewpoint", "tower", "winery", "brewery"}
        else "leisure"  if poi_type in {"park", "beach", "peak", "sports_centre", "swimming_pool", "stadium"}
        else "amenity"  if poi_type in {"restaurant", "cafe", "bar", "pub", "fast_food", "cinema", "mall", "marketplace"}
        else "leisure"
    )
    tags[osm_key] = poi_type

    return {
        "id":       props.get("place_id", f"{lat},{lon}"),
        "name":     name,
        "lat":      lat,
        "lon":      lon,
        "poi_type": poi_type,
        "tags":     tags,
    }


async def fetch_pois(
    lat: float,
    lon: float,
    radius_m: int,
    api_key: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch nearby POIs from Geoapify Places API."""
    if not api_key:
        logger.warning("geoapify_no_key")
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                _PLACES_URL,
                params={
                    "categories": _CATEGORIES,
                    "filter":     f"circle:{lon},{lat},{radius_m}",
                    "limit":      limit,
                    "apiKey":     api_key,
                },
            )
            resp.raise_for_status()
            features = resp.json().get("features", [])
            pois = [p for f in features if (p := _geoapify_to_poi(f)) is not None]
            logger.info("geoapify_pois_fetched", extra={"count": len(pois), "lat": lat, "lon": lon})
            return pois
    except Exception as exc:
        logger.error("geoapify_fetch_failed", extra={"error": str(exc)})
        return []
