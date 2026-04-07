# OverpassPOIProvider
#
# Fetches nearby points of interest (POIs) from OpenStreetMap using the
# Overpass API — a free, public API that lets you query OSM map data.
#
# What it does:
#   1. Takes a GPS coordinate (lat, lon) and a search radius in meters
#   2. Builds an Overpass QL query targeting tourist, historic, amenity, leisure,
#      building, man_made, and natural places
#   3. POSTs that query to the Overpass API and waits for the response
#   4. Parses the raw OSM data, skips unnamed places, and returns a clean list of dicts
#
# Each returned POI dict contains:
#   id        — unique OpenStreetMap element ID
#   name      — human-readable place name
#   lat/lon   — coordinates (uses center point for polygon elements like buildings)
#   tags      — full OSM tag dict (e.g. opening_hours, website, description)
#   poi_type  — which tag category matched: "tourism", "historic", "amenity",
#               "leisure", "building", "man_made", or "natural"

import logging

import httpx

from providers.base import POIProvider, POIProviderError

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
TIMEOUT_SECONDS = 10

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

# Tag categories in priority order for poi_type resolution
_POI_TYPE_KEYS = ["tourism", "historic", "amenity", "leisure", "building", "man_made", "natural"]


def _validate_inputs(lat: float, lon: float, radius: float) -> None:
    if not (-90 <= lat <= 90):
        raise ValueError(f"Latitude must be between -90 and 90, got {lat}")
    if not (-180 <= lon <= 180):
        raise ValueError(f"Longitude must be between -180 and 180, got {lon}")
    if radius <= 0:
        raise ValueError(f"Radius must be positive, got {radius}")


def _extract_coordinates(element: dict) -> tuple[float | None, float | None]:
    """Extract lat/lon from a node or way element."""
    if element["type"] == "way":
        center = element.get("center", {})
        return center.get("lat"), center.get("lon")
    return element.get("lat"), element.get("lon")


def _resolve_poi_type(tags: dict) -> str:
    for key in _POI_TYPE_KEYS:
        if key in tags:
            return key
    return "unknown"


class OverpassPOIProvider(POIProvider):
    async def search_nearby(self, lat: float, lon: float, radius: float) -> list[dict]:
        _validate_inputs(lat, lon, radius)

        query = QUERY_TEMPLATE.format(lat=lat, lon=lon, radius=int(radius))
        logger.debug("Querying Overpass at (%.6f, %.6f) radius=%dm", lat, lon, radius)

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.post(OVERPASS_URL, data={"data": query})
                response.raise_for_status()
        except httpx.TimeoutException:
            raise POIProviderError(
                f"Overpass API timed out after {TIMEOUT_SECONDS}s for ({lat}, {lon})"
            )
        except httpx.ConnectError as e:
            raise POIProviderError(f"Could not connect to Overpass API: {e}") from e
        except httpx.HTTPStatusError as e:
            raise POIProviderError(
                f"Overpass API returned HTTP {e.response.status_code}"
            ) from e

        try:
            elements = response.json().get("elements", [])
        except Exception as e:
            raise POIProviderError(f"Failed to parse Overpass response as JSON: {e}") from e

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
