"""utils/geoapify_places.py — POI fetching via Geoapify Places API."""

import logging
from typing import Any

import httpx

logger = logging.getLogger("tourai.api")

_PLACES_URL = "https://api.geoapify.com/v2/places"

# Geoapify category → our poi_type label
_CATEGORY_MAP = {
    "tourism.sights":             "attraction",
    "tourism.attraction":         "attraction",
    "entertainment.museum":       "museum",
    "entertainment.art_gallery":  "art_gallery",
    "entertainment.gallery":      "gallery",
    "entertainment.culture":      "culture",
    "entertainment.theme_park":   "theme_park",
    "entertainment.aquarium":     "aquarium",
    "entertainment.zoo":          "zoo",
    "entertainment.cinema":       "cinema",
    "entertainment.theatre":      "theatre",
    "natural.park":               "park",
    "natural.national_park":      "park",
    "natural.nature_reserve":     "nature_reserve",
    "natural.forest":             "park",
    "natural.beach":              "beach",
    "natural.peak":               "peak",
    "catering.restaurant":        "restaurant",
    "catering.cafe":              "cafe",
    "catering.bar":               "bar",
    "catering.pub":               "pub",
    "catering.bakery":            "bakery",
    "historic":                   "historic",
    "historic.monument":          "monument",
    "historic.memorial":          "memorial",
    "historic.castle":            "castle",
    "historic.ruins":             "ruins",
    "historic.archaeological_site": "archaeological_site",
    "commercial.shopping_mall":   "mall",
    "commercial.market":          "marketplace",
    "sport":                      "sports_centre",
    "sport.stadium":              "stadium",
    "sport.swimming_pool":        "swimming_pool",
}

# Categories sent to Geoapify — must be valid subcategories (no bare top-level like "historic")
_CATEGORIES = ",".join([
    "tourism.sights",
    "tourism.attraction",
    "entertainment.museum",
    "entertainment.art_gallery",
    "entertainment.culture",
    "entertainment.theme_park",
    "entertainment.aquarium",
    "entertainment.zoo",
    "entertainment.cinema",
    "entertainment.theatre",
    "natural.park",
    "natural.national_park",
    "natural.nature_reserve",
    "natural.beach",
    "catering.restaurant",
    "catering.cafe",
    "catering.bar",
    "catering.pub",
    "historic.monument",
    "historic.memorial",
    "historic.castle",
    "historic.ruins",
    "historic.archaeological_site",
    "commercial.market",
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
        "tourism" if poi_type in {"attraction", "museum", "art_gallery", "gallery", "theme_park", "aquarium", "zoo"}
        else "leisure" if poi_type in {"park", "nature_reserve", "beach", "peak", "sports_centre", "swimming_pool"}
        else "historic" if poi_type in {"historic", "monument", "memorial", "castle", "ruins", "archaeological_site"}
        else "amenity" if poi_type in {"restaurant", "cafe", "bar", "pub", "bakery", "cinema", "theatre", "culture"}
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
