"""utils/google_places.py — Destination geocoding (Nominatim) + Google Places enrichment."""

import logging
from typing import Any

import httpx

logger = logging.getLogger("tourai.api")

_GEOAPIFY_GEOCODE = "https://api.geoapify.com/v1/geocode/search"
_NOMINATIM_URL   = "https://nominatim.openstreetmap.org/search"
_PLACES_SEARCH   = "https://maps.googleapis.com/maps/api/place/textsearch/json"
_PLACES_PHOTO    = "https://maps.googleapis.com/maps/api/place/photo"


async def geocode_destination(destination: str, api_key: str = "") -> dict[str, Any] | None:
    """Geocode a destination using Geoapify (primary) with Nominatim as fallback."""
    # 1. Geoapify — reliable, production-grade, uses our existing key
    if api_key:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    _GEOAPIFY_GEOCODE,
                    params={"text": destination, "apiKey": api_key, "limit": 1, "format": "json"},
                )
                resp.raise_for_status()
                results = resp.json().get("results", [])
                if results:
                    r = results[0]
                    return {
                        "lat":          float(r["lat"]),
                        "lon":          float(r["lon"]),
                        "display_name": r.get("formatted", destination),
                        "type":         r.get("result_type", ""),
                    }
        except Exception as exc:
            logger.warning("geoapify_geocode_failed", extra={"destination": destination, "error": str(exc)})

    # 2. Nominatim fallback
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                _NOMINATIM_URL,
                params={"q": destination, "format": "json", "limit": 1},
                headers={"User-Agent": "TourAI/1.0 (contact@tourai.app)"},
            )
            resp.raise_for_status()
            results = resp.json()
            if results:
                r = results[0]
                return {
                    "lat":          float(r["lat"]),
                    "lon":          float(r["lon"]),
                    "display_name": r.get("display_name", destination),
                    "type":         r.get("type", ""),
                }
    except Exception as exc:
        logger.warning("nominatim_geocode_failed", extra={"destination": destination, "error": str(exc)})

    return None


async def search_destinations(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Return up to `limit` geocoded suggestions for a partial destination query."""
    if len(query.strip()) < 2:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                _NOMINATIM_URL,
                params={
                    "q":       query,
                    "format":  "json",
                    "limit":   limit,
                    "featuretype": "city,state,country,attraction",
                },
                headers={"User-Agent": "TourAI/1.0 (tourai-app)"},
            )
            resp.raise_for_status()
            results = resp.json()
            return [
                {
                    "lat":          float(r["lat"]),
                    "lon":          float(r["lon"]),
                    "display_name": r.get("display_name", ""),
                    "short_name":   r.get("display_name", "").split(",")[0].strip(),
                }
                for r in results
            ]
    except Exception as exc:
        logger.warning("search_destinations_failed", extra={"query": query, "error": str(exc)})
        return []


async def get_place_photo_url(
    place_name: str,
    api_key: str,
    max_width: int = 800,
) -> str | None:
    """Search Google Places for a place and return a usable photo URL, or None."""
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            search = await client.get(
                _PLACES_SEARCH,
                params={"query": place_name, "key": api_key, "fields": "photos"},
            )
            search.raise_for_status()
            data = search.json()
            results = data.get("results", [])
            if not results:
                return None
            photos = results[0].get("photos", [])
            if not photos:
                return None
            photo_ref = photos[0].get("photo_reference")
            if not photo_ref:
                return None
            # Build a direct URL (redirects to actual image)
            return (
                f"{_PLACES_PHOTO}?maxwidth={max_width}"
                f"&photo_reference={photo_ref}&key={api_key}"
            )
    except Exception as exc:
        logger.warning("places_photo_failed", extra={"place": place_name, "error": str(exc)})
        return None
