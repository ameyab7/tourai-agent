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

import httpx

from providers.base import POIProvider

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

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


class OverpassPOIProvider(POIProvider):
    async def search_nearby(self, lat: float, lon: float, radius: float) -> list[dict]:
        query = QUERY_TEMPLATE.format(lat=lat, lon=lon, radius=radius)

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(OVERPASS_URL, data={"data": query})
            response.raise_for_status()

        elements = response.json().get("elements", [])
        pois = []

        for el in elements:
            tags = el.get("tags", {})
            name = tags.get("name")
            if not name:
                continue

            # Determine coordinates (ways use center)
            if el["type"] == "way":
                center = el.get("center", {})
                poi_lat = center.get("lat")
                poi_lon = center.get("lon")
            else:
                poi_lat = el.get("lat")
                poi_lon = el.get("lon")

            if poi_lat is None or poi_lon is None:
                continue

            # Determine poi_type from matched tag category
            if "tourism" in tags:
                poi_type = "tourism"
            elif "historic" in tags:
                poi_type = "historic"
            elif "amenity" in tags:
                poi_type = "amenity"
            elif "leisure" in tags:
                poi_type = "leisure"
            elif "building" in tags:
                poi_type = "building"
            elif "man_made" in tags:
                poi_type = "man_made"
            elif "natural" in tags:
                poi_type = "natural"
            else:
                poi_type = "unknown"

            pois.append({
                "id": el["id"],
                "name": name,
                "lat": poi_lat,
                "lon": poi_lon,
                "tags": tags,
                "poi_type": poi_type,
            })

        return pois
