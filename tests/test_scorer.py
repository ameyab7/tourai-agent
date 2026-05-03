"""Scorer integration test — runs the real prefetch + scorer pipeline.

Usage:
    python tests/test_scorer.py [destination] [interests...]

Examples:
    python tests/test_scorer.py "Las Vegas"
    python tests/test_scorer.py "Austin, TX" culture food architecture
"""
import asyncio
import os
import sys
from datetime import date, timedelta

_BACKEND = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
sys.path.insert(0, _BACKEND)

from dotenv import load_dotenv
load_dotenv(os.path.join(_BACKEND, ".env"))

from api.config import settings
from prefetch.orchestrator import prefetch_all, AmbiguousDestinationError
from solver.scorer import score_pois

DESTINATION = sys.argv[1] if len(sys.argv) > 1 else "Las Vegas, Nevada, United States"
INTERESTS   = sys.argv[2:] if len(sys.argv) > 2 else ["culture", "food", "architecture", "social"]

# 5-day window starting tomorrow
_start = date.today() + timedelta(days=1)
DATES = [(_start + timedelta(days=i)).isoformat() for i in range(5)]


async def main():
    print(f"Destination : {DESTINATION}")
    print(f"Interests   : {INTERESTS}")
    print(f"Dates       : {DATES[0]} → {DATES[-1]}")
    print()

    # Stage 1: prefetch
    print("── Stage 1: prefetch ──────────────────────────────")
    try:
        bundle = await prefetch_all(DESTINATION, DATES, INTERESTS, settings.geoapify_api_key)
    except AmbiguousDestinationError as e:
        first = e.options[0].get("display_name", "")
        print(f"  Ambiguous — auto-picking first result: {first!r}")
        bundle = await prefetch_all(first, DATES, INTERESTS, settings.geoapify_api_key)

    if bundle is None:
        print("ERROR: prefetch returned None — check destination name")
        return

    print(f"\n  Hotels ({len(bundle.hotels)}):")
    for h in bundle.hotels:
        print(f"    {'★' * int(h.get('stars') or 0) or '?'}  {h['name']}")

    print(f"\n  Attractions ({len(bundle.attractions)}):")
    for p in bundle.attractions:
        print(f"    [{p['poi_id']}]  {p['name']}  ({p['poi_type']})")

    print(f"\n  Restaurants ({len(bundle.restaurants)}):")
    for r in bundle.restaurants:
        cuisine = r.get("cuisine", "") or r.get("tags", {}).get("cuisine", "")
        print(f"    {r['name']}" + (f"  — {cuisine}" if cuisine else ""))

    print()

    # Stage 2: score
    print("── Stage 2: scorer ────────────────────────────────")
    scores = await score_pois(bundle.attractions, INTERESTS)

    if not scores:
        print("ERROR: scorer returned empty — check logs above")
        return

    name_map = {p["poi_id"]: p["name"] for p in bundle.attractions}
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    print(f"  {'score':>5}  name")
    print(f"  {'─'*5}  {'─'*40}")
    for poi_id, score in ranked:
        print(f"  {score:.2f}   {name_map.get(poi_id, poi_id)}")


asyncio.run(main())
