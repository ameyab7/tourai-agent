#!/usr/bin/env python3
"""
tests/test_skyline_detection.py

Unit tests for _get_building_height_meters and _is_skyline_poi.
Run:
    python tests/test_skyline_detection.py

All tests use synthetic POI dicts — no API calls needed.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Enable DEBUG logging so we see every decision
logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)-5s  %(name)s  %(message)s",
    stream=sys.stdout,
)
# Only show our visibility module logs (suppress noisy shapely/pyproj startup)
logging.getLogger("utils.visibility").setLevel(logging.DEBUG)
logging.getLogger("shapely").setLevel(logging.WARNING)
logging.getLogger("pyproj").setLevel(logging.WARNING)

from utils.visibility import _get_building_height_meters, _is_skyline_poi


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
results = []


def make_poi(name, tags=None, categories=None):
    return {
        "id": name.lower().replace(" ", "_"),
        "name": name,
        "lat": 32.78,
        "lon": -96.80,
        "tags": tags or {},
        "categories": categories or [],
        "geometry": [],
    }


def check_height(name, tags, expected_min, expected_max=None):
    """Assert _get_building_height_meters returns value in [expected_min, expected_max]."""
    result = _get_building_height_meters(tags)
    if expected_min is None:
        ok = result is None
        label = f"height({name}): expected None, got {result}"
    else:
        hi = expected_max if expected_max is not None else expected_min
        ok = result is not None and expected_min <= result <= hi
        label = f"height({name}): expected [{expected_min}, {hi}], got {result}"
    results.append((ok, label))
    print(f"  {'  OK' if ok else 'FAIL'}  {label}")


def check_skyline(poi_name, tags, categories, expected: bool):
    poi = make_poi(poi_name, tags, categories)
    result = _is_skyline_poi(poi)
    ok = result == expected
    label = (
        f"skyline({poi_name}): expected {'YES' if expected else 'NO'}, "
        f"got {'YES' if result else 'NO'}"
    )
    results.append((ok, label))
    print(f"  {'  OK' if ok else 'FAIL'}  {label}")


# ---------------------------------------------------------------------------
# Section 1 — Height parsing
# ---------------------------------------------------------------------------

print("\n" + "="*70)
print("SECTION 1 — _get_building_height_meters")
print("="*70)

print("\n── 1a: Explicit height tag ──")
check_height("plain number",         {"height": "100"},        99.9, 100.1)
check_height("number with m",        {"height": "100 m"},      99.9, 100.1)
check_height("feet string 328ft",    {"height": "328 ft"},     99.0, 100.5)   # 328ft ≈ 100m
check_height("feet apostrophe",      {"height": "328'"},       99.0, 100.5)
check_height("short building 15m",   {"height": "15"},         14.9, 15.1)
check_height("numeric float 45.5",   {"height": "45.5"},       45.4, 45.6)
check_height("garbage string",       {"height": "tall"},       None)
check_height("none tag",             {},                       None)

print("\n── 1b: building:levels estimation ──")
check_height("office 20 floors",     {"building:levels": "20", "building": "office"},
             76.0 - 0.1, 76.0 + 0.1)   # 20 × 3.8 = 76.0
check_height("residential 10",      {"building:levels": "10", "building": "apartments"},
             29.9, 30.1)               # 10 × 3.0 = 30.0
check_height("generic 15 floors",   {"building:levels": "15"},
             52.4, 52.6)               # 15 × 3.5 = 52.5
check_height("skyscraper 50 floors", {"building:levels": "50", "building": "skyscraper"},
             189.9, 190.1)             # 50 × 3.8 = 190.0

print("\n── 1c: roof:height fallback ──")
check_height("roof:height 30",      {"roof:height": "30"},    29.9, 30.1)
check_height("roof:height + levels", {"roof:height": "50", "building:levels": "10"},
             34.9, 35.1)               # levels (step2) beats roof:height (step3); 10×3.5=35

print("\n── 1d: tag priority (height > levels > roof) ──")
check_height("height beats levels",  {"height": "120", "building:levels": "5"},
             119.9, 120.1)             # height tag should win


# ---------------------------------------------------------------------------
# Section 2 — Skyline classification: TIER 1 (explicit tags)
# ---------------------------------------------------------------------------

print("\n" + "="*70)
print("SECTION 2 — TIER 1: Explicit OSM tags")
print("="*70)

check_skyline("Empire State Building",
              {"building": "skyscraper", "height": "443"},    [], True)
check_skyline("CN Tower",
              {"man_made": "tower", "height": "553"},         [], True)
check_skyline("Lighthouse",
              {"man_made": "lighthouse"},                     [], True)
check_skyline("Water Tower",
              {"man_made": "water_tower"},                    [], True)
check_skyline("Cooling Tower",
              {"man_made": "cooling_tower"},                  [], True)
check_skyline("Communications Tower",
              {"man_made": "communications_tower"},           [], True)
check_skyline("Observation Tower",
              {"tower:type": "observation"},                  [], True)
check_skyline("Bell Tower",
              {"tower:type": "bell_tower"},                   [], True)
check_skyline("Normal Office Building",
              {"building": "office"},                         [], False)  # not skyline without height


# ---------------------------------------------------------------------------
# Section 3 — Skyline classification: TIER 2 (height ≥ 80m)
# ---------------------------------------------------------------------------

print("\n" + "="*70)
print("SECTION 3 — TIER 2: Height ≥ 80m")
print("="*70)

check_skyline("Tall office (100m explicit)",
              {"building": "office", "height": "100"},        [], True)
check_skyline("Just under 80m (79m)",
              {"building": "office", "height": "79"},         [], False)
check_skyline("Exactly 80m",
              {"building": "office", "height": "80"},         [], True)
check_skyline("Tall via levels (25 office floors = 95m)",
              {"building": "office", "building:levels": "25"},[], True)   # 25×3.8=95
check_skyline("Residential 25 floors (75m)",
              {"building": "apartments", "building:levels": "25"}, [], False)  # 25×3.0=75m < 80
check_skyline("Residential 27 floors (81m)",
              {"building": "apartments", "building:levels": "27"}, [], True)   # 27×3.0=81m ≥ 80
check_skyline("Short office (5 floors = 19m)",
              {"building": "office", "building:levels": "5"}, [], False)
check_skyline("Tall in feet (330ft ≈ 100.6m)",
              {"building": "office", "height": "330 ft"},     [], True)


# ---------------------------------------------------------------------------
# Section 4 — Skyline classification: TIER 3 (prominence)
# ---------------------------------------------------------------------------

print("\n" + "="*70)
print("SECTION 4 — TIER 3: Prominence (architect/heritage/landmark + large size)")
print("="*70)

check_skyline("Cathedral with architect tag (large cat)",
              {"building": "cathedral", "architect": "Santiago Calatrava"},
              ["building.historic"], True)
check_skyline("Heritage government building",
              {"building": "civic", "heritage": "2"},
              ["building.public_and_civil"], True)
check_skyline("Small shop with architect tag (small size)",
              {"building": "shop", "architect": "Frank Lloyd Wright"},
              ["building"], False)   # size = medium/small — not large enough


# ---------------------------------------------------------------------------
# Section 5 — Skyline classification: TIER 4 (category fallback)
# ---------------------------------------------------------------------------

print("\n" + "="*70)
print("SECTION 5 — TIER 4: Category fallback (building.* + very_large)")
print("="*70)

check_skyline("Skyscraper category only",
              {},
              ["building.skyscraper"], True)   # _CAT_SIZE maps building.skyscraper → very_large
check_skyline("Generic building category",
              {},
              ["building"], False)              # _CAT_SIZE maps building → medium — not very_large


# ---------------------------------------------------------------------------
# Section 6 — Real-world POI scenarios
# ---------------------------------------------------------------------------

print("\n" + "="*70)
print("SECTION 6 — Real-world POI scenarios")
print("="*70)

# Should be skyline (tall enough):
check_skyline("Reunion Tower Dallas",
              {"man_made": "tower", "height": "171", "tourism": "attraction",
               "wikidata": "Q1371944"},
              ["man_made.tower", "tourism.sights"], True)

check_skyline("Bank of America Plaza Dallas",
              {"building": "skyscraper", "height": "281", "wikidata": "Q4857498"},
              ["building.skyscraper"], True)

check_skyline("Chase Tower Dallas (35 floors office)",
              {"building": "office", "building:levels": "35",
               "architect": "Philip Johnson", "wikidata": "Q5087024"},
              ["building.historic"], True)   # 35×3.8=133m → TIER2

# Should NOT be skyline (too short / generic):
check_skyline("The Victor residential (22 floors)",
              {"building": "apartments", "building:levels": "22"},
              [], False)   # 22×3.0=66m < 80

check_skyline("Manor House residential (20 floors)",
              {"building": "residential", "building:levels": "20"},
              [], False)   # 20×3.0=60m < 80

check_skyline("Small coffee shop",
              {"amenity": "cafe", "name": "Blue Bottle Coffee"},
              [], False)

check_skyline("Street artwork plaque",
              {"tourism": "artwork", "artwork_type": "plaque"},
              [], False)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print("\n" + "="*70)
passed = sum(1 for ok, _ in results if ok)
failed = sum(1 for ok, _ in results if not ok)
total  = len(results)
print(f"RESULTS: {passed}/{total} passed  ({failed} failed)")
if failed:
    print("\nFailed tests:")
    for ok, label in results:
        if not ok:
            print(f"  FAIL  {label}")
print("="*70)
sys.exit(0 if failed == 0 else 1)
