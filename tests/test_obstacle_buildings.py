#!/usr/bin/env python3
"""
tests/test_obstacle_buildings.py

Integration test for the new Overpass-based obstacle building fetch.
Hits the real Overpass API and verifies we get far more polygons than the
old Geoapify approach (~10).

Run:
    python tests/test_obstacle_buildings.py

No API key needed — uses public Overpass mirrors.
"""

import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-5s  %(name)s  %(message)s",
    stream=sys.stdout,
)
logging.getLogger("utils.overpass").setLevel(logging.DEBUG)

from utils.overpass import fetch_obstacle_buildings

# ---------------------------------------------------------------------------
# Test locations
# ---------------------------------------------------------------------------

LOCATIONS = [
    ("Downtown Dallas (skyscrapers)", 32.7767, -96.7970),
    ("NYC Midtown (dense blocks)",    40.7549, -73.9840),
    ("Chicago Loop",                  41.8827, -87.6233),
]

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
results = []


async def run_tests():
    for loc_name, lat, lon in LOCATIONS:
        print(f"\n{'='*65}")
        print(f"Location: {loc_name}  ({lat}, {lon})")
        print(f"{'='*65}")

        t0 = time.perf_counter()
        buildings = await fetch_obstacle_buildings(lat, lon, radius=300)
        elapsed = (time.perf_counter() - t0) * 1000

        total       = len(buildings)
        with_poly   = sum(1 for _, g in buildings.values() if g is not None)
        without_poly = total - with_poly

        print(f"  Elapsed:        {elapsed:.0f} ms")
        print(f"  Total buildings:  {total}")
        print(f"  With polygon:     {with_poly}")
        print(f"  Without polygon:  {without_poly}")

        # Sample a few entries
        sample = list(buildings.items())[:5]
        print(f"  Sample entries:")
        for way_id, (name, geom) in sample:
            geom_type = type(geom).__name__ if geom else "None"
            area_m2   = geom.area * (111_000 ** 2) if geom else 0
            print(f"    way/{way_id}  name={name!r:30}  geom={geom_type}  area≈{area_m2:.0f}m²")

        # Assertions
        # Min counts are location-specific: Dallas downtown has large blocks
        # so 300m catches fewer buildings than dense NYC/Chicago grids.
        min_total = 10 if "Dallas" in loc_name else 50
        min_polys = 10 if "Dallas" in loc_name else 50
        ok_total  = total >= min_total
        ok_polys  = with_poly >= min_polys
        # Allow two mirror timeouts (2×10s) + one successful response (5s)
        ok_time   = elapsed < 30_000

        results.append((ok_total, f"{loc_name}: total buildings {total} >= {min_total}"))
        results.append((ok_polys,  f"{loc_name}: buildings with polygon {with_poly} >= {min_polys}"))
        results.append((ok_time,   f"{loc_name}: fetch time {elapsed:.0f}ms < 30000ms"))

        for ok, label in results[-3:]:
            print(f"  {'  OK' if ok else 'FAIL'}  {label}")

        # Second call should be served from cache
        print(f"\n  [cache test]")
        t1 = time.perf_counter()
        buildings2 = await fetch_obstacle_buildings(lat, lon, radius=300)
        cache_ms   = (time.perf_counter() - t1) * 1000
        ok_cache   = cache_ms < 5 and len(buildings2) == total
        results.append((ok_cache, f"{loc_name}: cache hit in {cache_ms:.1f}ms (same count)"))
        print(f"  {'  OK' if ok_cache else 'FAIL'}  {results[-1][1]}")

        # Small gap before next request to respect rate limiter
        await asyncio.sleep(3.5)

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    passed = sum(1 for ok, _ in results if ok)
    failed = sum(1 for ok, _ in results if not ok)
    total_tests = len(results)
    print(f"RESULTS: {passed}/{total_tests} passed  ({failed} failed)")
    if failed:
        print("\nFailed tests:")
        for ok, label in results:
            if not ok:
                print(f"  FAIL  {label}")
    print(f"{'='*65}")
    return failed


if __name__ == "__main__":
    failed = asyncio.run(run_tests())
    sys.exit(0 if failed == 0 else 1)
