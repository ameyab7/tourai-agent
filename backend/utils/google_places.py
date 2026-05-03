"""utils/google_places.py — Destination geocoding (Nominatim) + Google Places enrichment."""

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger("tourai.api")

_GEOAPIFY_GEOCODE = "https://api.geoapify.com/v1/geocode/search"
_NOMINATIM_URL   = "https://nominatim.openstreetmap.org/search"
_PLACES_SEARCH   = "https://maps.googleapis.com/maps/api/place/textsearch/json"
_PLACES_PHOTO    = "https://maps.googleapis.com/maps/api/place/photo"

_COUNTRY_CODE_MAP = {
    "united states": "us", "usa": "us", "america": "us",
    "united kingdom": "gb", "uk": "gb", "britain": "gb", "england": "gb", "scotland": "gb", "wales": "gb",
    "canada": "ca",
    "mexico": "mx",
    "france": "fr",
    "germany": "de",
    "italy": "it",
    "spain": "es",
    "japan": "jp",
    "australia": "au",
    "brazil": "br",
    "india": "in",
}

_US_STATE_MAP = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca",
    "colorado": "co", "connecticut": "ct", "delaware": "de", "florida": "fl", "georgia": "ga",
    "hawaii": "hi", "idaho": "id", "illinois": "il", "indiana": "in", "iowa": "ia",
    "kansas": "ks", "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn", "mississippi": "ms", "missouri": "mo",
    "montana": "mt", "nebraska": "ne", "nevada": "nv", "new hampshire": "nh", "new jersey": "nj",
    "new mexico": "nm", "new york": "ny", "north carolina": "nc", "north dakota": "nd", "ohio": "oh",
    "oklahoma": "ok", "oregon": "or", "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc",
    "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut", "vermont": "vt",
    "virginia": "va", "washington": "wa", "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy",
    "district of columbia": "dc",
}


def _extract_region_hints(query: str) -> dict[str, str]:
    """Extract country/state hints from user query for validation."""
    hints = {"country": "", "state": "", "raw": query.lower()}
    q = query.lower()

    for country, code in _COUNTRY_CODE_MAP.items():
        if country in q:
            hints["country"] = code
            break

    state_pattern = r'\b([a-z]{2})\b'
    matches = re.findall(state_pattern, q)
    for abbr in matches:
        if abbr in ["tx", "ca", "ny", "fl", "nv", "co", "az", "ut", "wa", "or"]:
            hints["state"] = abbr
            break
    if not hints["state"]:
        for state, abbr in _US_STATE_MAP.items():
            if state in q:
                hints["state"] = abbr
                break

    return hints


def _matches_hints(result: dict, hints: dict) -> bool:
    """Check if geocode result matches user-specified country/state hints."""
    if not hints["country"] and not hints["state"]:
        return True

    result_country = result.get("country_code", "").lower()
    result_state = result.get("state", "").lower()

    if hints["country"] and result_country:
        if hints["country"] != result_country:
            return False
    if hints["state"] and result_state:
        state_abbr = _US_STATE_MAP.get(result_state, "")
        if hints["state"] != state_abbr and hints["state"] != result_state:
            return False

    return True


_MULTI_CITY_PATTERNS = [
    r'\band\b', r'\s&\s', r'\bto\b', r',\s+', r'—', r'\s-\s',
]
_MULTI_CITY_KEYWORDS = [
    "bay area", "wine country", "southern california", "northern california",
    "tuscany", "provence", "amalfi coast", "cottage country",
    "blue ridge", "gold coast", "adriatic coast",
]


def _is_multi_city_query(destination: str) -> tuple[bool, list[str]]:
    """Detect if destination is a multi-city/region trip.

    Returns (is_multi, split_parts).
    """
    d = destination.lower().strip()

    for kw in _MULTI_CITY_KEYWORDS:
        if kw in d:
            return True, [destination]

    for pat in _MULTI_CITY_PATTERNS:
        parts = re.split(pat, d)
        if len(parts) > 1:
            cleaned = [p.strip() for p in parts if p.strip() and len(p.strip()) > 2]
            if len(cleaned) > 1:
                return True, cleaned

    return False, []


def _wrap_result(result: dict, is_multi: bool, multi_parts: list[str]) -> dict:
    """Add multi-city warning to geocode result."""
    if is_multi:
        result["warning"] = result.get("warning", "") + " Multi-city trip detected. Using first location only."
        result["multi_city"] = True
        result["multi_city_parts"] = multi_parts
    return result


async def geocode_destination(destination: str, api_key: str = "") -> dict[str, Any] | None:
    """Geocode a destination using Geoapify with validation, disambiguation, and region hints."""
    is_multi, multi_parts = _is_multi_city_query(destination)
    if is_multi:
        logger.info(f"Multi-city query detected: {destination!r} -> {multi_parts}")

    hints = _extract_region_hints(destination)

    if api_key:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    _GEOAPIFY_GEOCODE,
                    params={"text": destination, "apiKey": api_key, "limit": 5, "format": "json"},
                )
                resp.raise_for_status()
                results = resp.json().get("results", [])

                if not results:
                    logger.warning(f"Geoapify returned no results for {destination!r}")
                    return None

                countries = set(r.get("country_code", "").upper() for r in results if r.get("country_code"))
                states = set(r.get("state", "").lower() for r in results if r.get("state"))

                has_ambiguity = len(countries) > 1 or (len(states) > 1 and not hints.get("state"))

                if has_ambiguity and not (hints.get("country") or hints.get("state")):
                    logger.info(f"Ambiguous destination {destination!r} — multiple results: {len(results)}")
                    disambiguation = [
                        {
                            "display_name": r.get("formatted", ""),
                            "country_code": r.get("country_code", ""),
                            "state": r.get("state", ""),
                            "lat": float(r["lat"]),
                            "lon": float(r["lon"]),
                        }
                        for r in results[:5]
                    ]
                    return {"disambiguation": disambiguation, "ambiguous": True}

                valid_results = [r for r in results if _matches_hints(r, hints)]

                if valid_results:
                    best = valid_results[0]
                    result = {
                        "lat": float(best["lat"]),
                        "lon": float(best["lon"]),
                        "display_name": best.get("formatted", destination),
                        "type": best.get("result_type", ""),
                        "country_code": best.get("country_code", ""),
                        "state": best.get("state", ""),
                    }
                    return _wrap_result(result, is_multi, multi_parts)

                if len(results) > 1 and countries:
                    logger.warning(
                        f"Ambiguous destination {destination!r} — multiple countries/states found: countries={countries}"
                    )
                    disambiguation = [
                        {
                            "display_name": r.get("formatted", ""),
                            "country_code": r.get("country_code", ""),
                            "state": r.get("state", ""),
                            "lat": float(r["lat"]),
                            "lon": float(r["lon"]),
                        }
                        for r in results[:5]
                    ]
                    return {"disambiguation": disambiguation, "ambiguous": True}

                r = results[0]
                logger.warning(
                    f"Geocode result for {destination!r} doesn't match hints {hints} — "
                    f"country={r.get('country_code')}, state={r.get('state')}"
                )
                result = {
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"]),
                    "display_name": r.get("formatted", destination),
                    "type": r.get("result_type", ""),
                    "country_code": r.get("country_code", ""),
                    "state": r.get("state", ""),
                    "warning": f"Result may not match expected region",
                }
                return _wrap_result(result, is_multi, multi_parts)
        except Exception as exc:
            logger.warning(f"Geoapify geocode failed for {destination!r} — {exc}")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                _NOMINATIM_URL,
                params={"q": destination, "format": "json", "limit": 5},
                headers={"User-Agent": "TourAI/1.0 (contact@tourai.app)"},
            )
            resp.raise_for_status()
            results = resp.json()

            if not results:
                logger.warning(f"Nominatim returned no results for {destination!r}")
                return None

            valid_results = [r for r in results if _matches_hints(r, hints)]

            if valid_results:
                best = valid_results[0]
                result = {
                    "lat": float(best["lat"]),
                    "lon": float(best["lon"]),
                    "display_name": best.get("display_name", destination),
                    "type": best.get("type", ""),
                }
                return _wrap_result(result, is_multi, multi_parts)

            r = results[0]
            result = {
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "display_name": r.get("display_name", destination),
                "type": r.get("type", ""),
                "warning": "Result may not match expected region",
            }
            return _wrap_result(result, is_multi, multi_parts)
    except Exception as exc:
        logger.warning(f"Nominatim geocode failed for {destination!r} — {exc}")

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
        logger.warning(f"Destination search failed for query {query!r} — {exc}")
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
        logger.warning(f"Google Places photo lookup failed for {place_name!r} — {exc}")
        return None
