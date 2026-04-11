# utils/overpass.py
#
# Standalone async function for fetching nearby POIs from OpenStreetMap
# via the Overpass API. No class wrapper — just call search_nearby() directly.
#
# Each returned POI dict contains:
#   id        — unique OpenStreetMap element ID
#   name      — human-readable place name
#   lat/lon   — coordinates (center point for polygon elements like buildings)
#   tags      — full OSM tag dict (opening_hours, website, description, etc.)
#   poi_type  — matched tag category: tourism, historic, amenity, leisure,
#               building, man_made, or natural

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

_OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
TIMEOUT_SECONDS = 8
MAX_RETRIES = 3
RETRY_BACKOFF = [2, 5, 10]

QUERY_TEMPLATE = """
[out:json][timeout:10];
(
  node(around:{radius},{lat},{lon})[tourism~"attraction|museum|artwork|viewpoint|gallery|hotel"];
  way(around:{radius},{lat},{lon})[tourism~"attraction|museum|artwork|viewpoint|gallery|hotel"];
  node(around:{radius},{lat},{lon})[historic~"monument|memorial|castle|ruins|building|church"];
  way(around:{radius},{lat},{lon})[historic~"monument|memorial|castle|ruins|building|church"];
  node(around:{radius},{lat},{lon})[amenity~"place_of_worship|theatre|library|arts_centre|cinema"];
  way(around:{radius},{lat},{lon})[amenity~"place_of_worship|theatre|library|arts_centre|cinema"];
  node(around:{radius},{lat},{lon})[leisure~"park|garden"];
  way(around:{radius},{lat},{lon})[leisure~"park|garden"];
  node(around:{radius},{lat},{lon})[building~"cathedral|church|civic|government|skyscraper|office|commercial"];
  way(around:{radius},{lat},{lon})[building~"cathedral|church|civic|government|skyscraper|office|commercial"];
  node(around:{radius},{lat},{lon})[man_made~"lighthouse"];
  way(around:{radius},{lat},{lon})[man_made~"lighthouse"];
  node(around:{radius},{lat},{lon})[natural~"peak"];
  way(around:{radius},{lat},{lon})[natural~"peak"];
);
out center tags;
"""

_POI_TYPE_KEYS = ["tourism", "historic", "amenity", "leisure", "building", "man_made", "natural"]


class OverpassError(Exception):
    """Raised when the Overpass API call fails."""


def _validate_inputs(lat: float, lon: float, radius: float) -> None:
    if not (-90 <= lat <= 90):
        raise ValueError(f"Latitude must be between -90 and 90, got {lat}")
    if not (-180 <= lon <= 180):
        raise ValueError(f"Longitude must be between -180 and 180, got {lon}")
    if radius <= 0:
        raise ValueError(f"Radius must be positive, got {radius}")


def _extract_coordinates(element: dict) -> tuple[float | None, float | None]:
    if element["type"] == "way":
        center = element.get("center", {})
        return center.get("lat"), center.get("lon")
    return element.get("lat"), element.get("lon")


def _resolve_poi_type(tags: dict) -> str:
    for key in _POI_TYPE_KEYS:
        if key in tags:
            return key
    return "unknown"


async def search_nearby(lat: float, lon: float, radius: float) -> list[dict]:
    """Search for POIs near a GPS coordinate using the Overpass/OSM API.

    Args:
        lat: Latitude (-90 to 90).
        lon: Longitude (-180 to 180).
        radius: Search radius in meters (must be positive).

    Returns:
        List of POI dicts with keys: id, name, lat, lon, tags, poi_type.

    Raises:
        ValueError: If coordinates or radius are invalid.
        OverpassError: If the API call fails after all retries.
    """
    _validate_inputs(lat, lon, radius)

    query = QUERY_TEMPLATE.format(lat=lat, lon=lon, radius=int(radius))
    logger.debug("Querying Overpass at (%.6f, %.6f) radius=%dm", lat, lon, radius)

    response = None
    last_error: Exception | None = None

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        for attempt in range(MAX_RETRIES):
            url = _OVERPASS_MIRRORS[attempt % len(_OVERPASS_MIRRORS)]
            try:
                response = await client.post(url, data={"data": query})
                response.raise_for_status()
                break
            except httpx.TimeoutException as e:
                last_error = e
                logger.warning("Overpass timeout (attempt %d/%d) [%s]", attempt + 1, MAX_RETRIES, url)
            except httpx.ConnectError as e:
                last_error = e
                logger.warning("Overpass connect error (attempt %d/%d) [%s]: %s", attempt + 1, MAX_RETRIES, url, e)
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code < 500:
                    raise OverpassError(
                        f"Overpass API returned HTTP {e.response.status_code}"
                    ) from e
                logger.warning(
                    "Overpass HTTP %d on custom query (attempt %d/%d)\n  Response body: %s",
                    e.response.status_code, attempt + 1, MAX_RETRIES,
                    e.response.text[:500],
                )

            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF[attempt]
                logger.info("Retrying Overpass in %ds on next mirror...", wait)
                await asyncio.sleep(wait)

    if response is None or not response.is_success:
        raise OverpassError(
            f"Overpass API failed after {MAX_RETRIES} attempts for ({lat}, {lon}): {last_error}"
        )

    try:
        elements = response.json().get("elements", [])
    except Exception as e:
        raise OverpassError(f"Failed to parse Overpass response as JSON: {e}") from e

    pois = []
    skipped = 0

    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            skipped += 1
            continue

        poi_lat, poi_lon = _extract_coordinates(el)
        if poi_lat is None or poi_lon is None:
            logger.warning("Skipping element %s — missing coordinates", el.get("id"))
            skipped += 1
            continue

        pois.append({
            "id": el["id"],
            "name": name,
            "lat": poi_lat,
            "lon": poi_lon,
            "tags": tags,
            "poi_type": _resolve_poi_type(tags),
        })

    logger.debug(
        "Overpass returned %d elements — %d named POIs, %d skipped",
        len(elements), len(pois), skipped,
    )
    return pois
