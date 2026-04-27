"""utils/geoapify_places.py — POI fetching via Geoapify Places API."""

import logging
from typing import Any

import httpx

logger = logging.getLogger("tourai.api")

_PLACES_URL = "https://api.geoapify.com/v2/places"

# Geoapify category → our poi_type label
# Derived from the confirmed supported category list from the Geoapify API.
_CATEGORY_MAP = {
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

# Confirmed-valid Geoapify category strings (verified from API error response).
# Any single invalid entry causes 400 for the entire request.
_CATEGORIES = ",".join([
    "tourism.sights",
    "tourism.attraction",
    "entertainment.museum",
    "entertainment.culture.gallery",
    "entertainment.culture.theatre",
    "entertainment.cinema",
    "entertainment.theme_park",
    "entertainment.aquarium",
    "entertainment.zoo",
    "leisure.park",
    "leisure.park.nature_reserve",
    "national_park",
    "beach",
    "catering.restaurant",
    "catering.cafe",
    "catering.bar",
    "catering.pub",
    "sport.stadium",
    "production.winery",
    "production.brewery",
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
                                    "castle", "memorial", "ruins", "archaeological_site", "viewpoint",
                                    "tower", "artwork", "culture", "theatre", "winery", "brewery"}
        else "leisure"  if poi_type in {"park", "nature_reserve", "beach", "peak", "sports_centre",
                                         "swimming_pool", "stadium"}
        else "amenity"  if poi_type in {"restaurant", "cafe", "bar", "pub", "fast_food", "cinema"}
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
                logger.error("geoapify_400", extra={"body": resp.text, "categories": _CATEGORIES})
                return []
            resp.raise_for_status()
            features = resp.json().get("features", [])
            pois = [p for f in features if (p := _geoapify_to_poi(f)) is not None]
            logger.info("geoapify_pois_fetched", extra={"count": len(pois), "lat": lat, "lon": lon})
            return pois
    except Exception as exc:
        logger.error("geoapify_fetch_failed", extra={"error": str(exc)})
        return []
