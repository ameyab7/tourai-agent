"""
tests/test_landmark_comparison.py

Compares AI landmark classification vs footprint area (Option 3) for the same POIs.

Run:
    python tests/test_landmark_comparison.py
"""

import asyncio
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import httpx
from groq import AsyncGroq

# ---------------------------------------------------------------------------
# Overpass — fetch POIs with full geometry
# ---------------------------------------------------------------------------

_QUERY = """
[out:json][timeout:20];
nw(around:{radius},{lat},{lon})
  [name]
  [~"^(tourism|historic|amenity|leisure|building|man_made|natural|railway)$"~"."];
out geom tags;
"""

async def fetch_pois_with_geometry(lat: float, lon: float, radius: float) -> list[dict]:
    query = _QUERY.format(lat=lat, lon=lon, radius=int(radius))
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post("http://localhost:12345/api/interpreter", data={"data": query})
        resp.raise_for_status()
    return resp.json().get("elements", [])


# ---------------------------------------------------------------------------
# Option 3 — footprint area via shoelace formula
# ---------------------------------------------------------------------------

def _polygon_area_m2(coords: list[dict]) -> float:
    """Shoelace formula on lat/lon points, converted to m²."""
    if len(coords) < 3:
        return 0.0
    # Convert to approximate metres using equirectangular projection
    lat0 = coords[0]["lat"]
    R = 6371000
    pts = [
        (
            math.radians(c["lon"] - coords[0]["lon"]) * R * math.cos(math.radians(lat0)),
            math.radians(c["lat"] - coords[0]["lat"]) * R,
        )
        for c in coords
    ]
    n = len(pts)
    area = abs(sum(
        pts[i][0] * pts[(i+1) % n][1] - pts[(i+1) % n][0] * pts[i][1]
        for i in range(n)
    )) / 2
    return area


_AREA_THRESHOLD = 2000  # m² — above this = physically large enough to see from other streets


def option3_is_landmark(element: dict) -> tuple[bool, float]:
    """Returns (is_landmark, area_m2)."""
    geom = element.get("geometry", [])
    if not geom:
        return False, 0.0
    area = _polygon_area_m2(geom)
    return area >= _AREA_THRESHOLD, area


# ---------------------------------------------------------------------------
# AI classification (Groq gpt-oss-120b)
# ---------------------------------------------------------------------------

_PROMPT = """\
You are helping a walking tour app decide which places are large enough to be \
visible from multiple surrounding streets.

Classify each place as:
- true  = LANDMARK (skyscraper, stadium, arena, large park, major cathedral, \
famous tower — physically visible from a block or more away)
- false = STREET-LEVEL (cafe, small shop, small church, office, \
theatre, museum — only visible if you are on that exact street)

Reply ONLY with a JSON object mapping each id to true or false.
Example: {{"111": true, "222": false}}

Places to classify:
{places}"""

_RELEVANT_TAGS = {"building", "leisure", "tourism", "historic", "amenity",
                  "building:levels", "height", "wikidata", "wikipedia"}


async def ai_classify(pois: list[dict]) -> tuple[dict[str, bool], float]:
    places_lines = []
    for p in pois:
        relevant = {k: v for k, v in p.get("tags", {}).items() if k in _RELEVANT_TAGS}
        places_lines.append(f'id={p["id"]} name="{p["name"]}" tags={json.dumps(relevant)}')

    prompt = _PROMPT.format(places="\n".join(places_lines))
    client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"].strip())

    t0 = time.perf_counter()
    response = await client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    elapsed = time.perf_counter() - t0

    raw = response.choices[0].message.content.strip()
    return json.loads(raw), elapsed


# ---------------------------------------------------------------------------
# POI filtering (same as overpass.py)
# ---------------------------------------------------------------------------

_POI_VALUE_ALLOWLIST = {
    "tourism":  {"attraction", "museum", "artwork", "viewpoint", "gallery", "theme_park", "zoo"},
    "historic": {"monument", "memorial", "castle", "ruins", "building", "church", "fort",
                 "battlefield", "archaeological_site", "manor", "palace", "ship"},
    "amenity":  {"place_of_worship", "theatre", "library", "arts_centre", "cinema", "townhall",
                 "courthouse", "university", "college", "stadium", "concert_hall", "opera"},
    "leisure":  {"park", "garden", "stadium", "sports_centre", "marina", "nature_reserve"},
    "building": {"cathedral", "church", "chapel", "civic", "government", "skyscraper",
                 "commercial", "office", "stadium", "train_station", "synagogue", "mosque",
                 "temple", "public"},
    "man_made": {"lighthouse", "tower", "water_tower", "windmill"},
    "natural":  {"peak", "cave_entrance", "waterfall"},
    "railway":  {"station"},
}

def _is_interesting(tags: dict) -> bool:
    for key, allowed in _POI_VALUE_ALLOWLIST.items():
        if tags.get(key) in allowed:
            return True
    return False


# ---------------------------------------------------------------------------
# Main comparison
# ---------------------------------------------------------------------------

async def compare(lat: float, lon: float, radius: float = 200.0):
    print(f"\nFetching POIs at ({lat}, {lon}) radius={radius:.0f}m ...")
    elements = await fetch_pois_with_geometry(lat, lon, radius)

    # Filter to interesting named POIs
    pois = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name or not _is_interesting(tags):
            continue
        # Get center
        if el["type"] in ("way", "relation"):
            geom = el.get("geometry", [])
            if not geom:
                continue
            clat = sum(c["lat"] for c in geom) / len(geom)
            clon = sum(c["lon"] for c in geom) / len(geom)
        else:
            clat, clon = el.get("lat", lat), el.get("lon", lon)
        pois.append({
            "id": el["id"],
            "name": name,
            "lat": clat,
            "lon": clon,
            "tags": tags,
            "geometry": el.get("geometry", []),
            "type": el["type"],
        })

    if not pois:
        print("No POIs found.")
        return

    print(f"Found {len(pois)} POIs. Running AI classification ...")

    # AI
    ai_results, ai_time = await ai_classify(pois)

    # Option 3
    opt3_results = {}
    opt3_areas   = {}
    for p in pois:
        is_lm, area = option3_is_landmark(p)
        opt3_results[str(p["id"])] = is_lm
        opt3_areas[str(p["id"])]   = area

    # Print comparison
    print(f"\n{'POI':<45} {'TYPE':<6} {'AREA m²':>8}   {'AI':^6} {'AREA':^6} {'MATCH':^6}")
    print("─" * 85)

    agree = disagree = 0
    for p in pois:
        pid  = str(p["id"])
        ai   = ai_results.get(pid, False)
        opt3 = opt3_results.get(pid, False)
        area = opt3_areas.get(pid, 0.0)
        match = "✓" if ai == opt3 else "✗"
        if ai == opt3:
            agree += 1
        else:
            disagree += 1
        print(
            f"  {p['name'][:43]:<43} {p['type'][:4]:<6} {area:>8.0f}"
            f"   {'LM' if ai else 'st':^6} {'LM' if opt3 else 'st':^6} {match:^6}"
        )

    print("─" * 85)
    print(f"  Agreement: {agree}/{agree+disagree}  |  AI time: {ai_time:.2f}s  |  Area threshold: {_AREA_THRESHOLD}m²")
    print(f"\n  Disagreements (these are the interesting cases):")
    for p in pois:
        pid  = str(p["id"])
        ai   = ai_results.get(pid, False)
        opt3 = opt3_results.get(pid, False)
        if ai != opt3:
            area = opt3_areas.get(pid, 0.0)
            print(f"    {p['name']}: AI={'LM' if ai else 'st'}, Area={'LM' if opt3 else 'st'} ({area:.0f}m²)")
            print(f"      tags: { {k:v for k,v in p['tags'].items() if k in _RELEVANT_TAGS} }")


if __name__ == "__main__":
    # Arts District walk area
    asyncio.run(compare(32.7905, -96.7975, radius=300))
