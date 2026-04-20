"""
tests/test_visibility_validation.py

Runs multiple walks across Dallas, collects all visibility decisions,
and prints a review table so we can manually validate accuracy.

Run:
    python tests/test_visibility_validation.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from utils.osrm import get_street_ahead, walking_route
from utils.overpass import search_nearby
from utils.visibility import filter_visible
from utils.geoutils import haversine_meters, bearing

# ---------------------------------------------------------------------------
# Test walks — diverse areas of Dallas
# ---------------------------------------------------------------------------

WALKS = [
    {
        "name": "Arts District (Flora St)",
        "start": (32.791514, -96.795706),
        "end":   (32.787654, -96.800159),
        "steps": 5,
    },
    {
        "name": "Downtown Elm St",
        "start": (32.781103, -96.800852),
        "end":   (32.779825, -96.806815),
        "steps": 5,
    },
    {
        "name": "Ross Ave / Uptown",
        "start": (32.791387, -96.795831),
        "end":   (32.788371, -96.800982),
        "steps": 5,
    },
    {
        "name": "Victory Park / AAC",
        "start": (32.795554, -96.813877),
        "end":   (32.789339, -96.808841),
        "steps": 5,
    },
]

SEARCH_RADIUS = 150.0

# ---------------------------------------------------------------------------
# Walk simulation
# ---------------------------------------------------------------------------

async def build_walk_points(start, end, steps):
    from utils.geoutils import bearing as _bearing

    def _linear():
        brg = _bearing(start[0], start[1], end[0], end[1])
        return [
            (
                start[0] + (i / max(steps - 1, 1)) * (end[0] - start[0]),
                start[1] + (i / max(steps - 1, 1)) * (end[1] - start[1]),
                round(brg, 1),
            )
            for i in range(steps)
        ]

    try:
        route = await walking_route(start[0], start[1], end[0], end[1])
    except Exception:
        return _linear()

    if not route or len(route) < 2:
        return _linear()

    cumulative = [0.0]
    for i in range(1, len(route)):
        cumulative.append(
            cumulative[-1] + haversine_meters(
                route[i-1][0], route[i-1][1], route[i][0], route[i][1]
            )
        )

    points = []
    for i in range(steps):
        target = (i / max(steps - 1, 1)) * cumulative[-1]
        seg = len(route) - 2
        for j in range(len(cumulative) - 1):
            if cumulative[j] <= target <= cumulative[j + 1]:
                seg = j
                break
        seg_len = cumulative[seg + 1] - cumulative[seg]
        frac = (target - cumulative[seg]) / seg_len if seg_len > 0 else 0.0
        la = route[seg][0] + frac * (route[seg+1][0] - route[seg][0])
        lo = route[seg][1] + frac * (route[seg+1][1] - route[seg][1])
        if seg + 1 < len(route):
            hdg = _bearing(la, lo, route[seg+1][0], route[seg+1][1])
        elif points:
            hdg = points[-1][2]
        else:
            hdg = _bearing(start[0], start[1], end[0], end[1])
        points.append((la, lo, round(hdg, 1)))

    return points


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run_walk(walk: dict) -> list[dict]:
    """Run one walk, return all visibility decisions."""
    points = await build_walk_points(walk["start"], walk["end"], walk["steps"])
    decisions = []
    seen_ids = set()

    for step, (lat, lon, heading) in enumerate(points, 1):
        street = await get_street_ahead(lat, lon, heading)
        pois   = await search_nearby(lat, lon, radius=SEARCH_RADIUS)
        visible, rejected = await filter_visible(pois, lat, lon, heading, street)

        for p in visible + rejected:
            pid = str(p["id"])
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            decisions.append({
                "walk":       walk["name"],
                "step":       step,
                "poi_name":   p["name"],
                "poi_type":   p.get("poi_type", "?"),
                "distance_m": p.get("distance_m", 0),
                "angle_deg":  p.get("angle_deg", 0),
                "street":     street or "unknown",
                "visible":    p in visible,
                "reason":     p.get("filtered_reason", "AI: visible"),
            })

    return decisions


async def main():
    all_decisions = []

    for walk in WALKS:
        print(f"\n{'='*60}")
        print(f"  Walk: {walk['name']}")
        print(f"{'='*60}")
        decisions = await run_walk(walk)
        all_decisions.extend(decisions)

    # ---------------------------------------------------------------------------
    # Summary table
    # ---------------------------------------------------------------------------
    visible  = [d for d in all_decisions if d["visible"]]
    rejected = [d for d in all_decisions if not d["visible"]]

    print(f"\n\n{'═'*80}")
    print(f"  VALIDATION SUMMARY — {len(all_decisions)} unique POIs across {len(WALKS)} walks")
    print(f"{'═'*80}")
    print(f"  Visible : {len(visible)}")
    print(f"  Rejected: {len(rejected)}")

    print(f"\n{'─'*80}")
    print(f"  VISIBLE POIs (review these — should all make sense)")
    print(f"{'─'*80}")
    print(f"  {'POI':<45} {'TYPE':<12} {'DIST':>6} {'ANGLE':>6}  {'WALK'}")
    print(f"  {'-'*75}")
    for d in sorted(visible, key=lambda x: (x["walk"], x["distance_m"])):
        print(
            f"  {d['poi_name'][:44]:<45} {d['poi_type'][:11]:<12}"
            f" {d['distance_m']:>5.0f}m {d['angle_deg']:>5.0f}°"
            f"  {d['walk']}"
        )

    print(f"\n{'─'*80}")
    print(f"  REJECTED POIs (review these — should all make sense)")
    print(f"{'─'*80}")
    print(f"  {'POI':<45} {'TYPE':<12} {'DIST':>6} {'ANGLE':>6}  {'WALK'}")
    print(f"  {'-'*75}")
    for d in sorted(rejected, key=lambda x: (x["walk"], x["distance_m"])):
        print(
            f"  {d['poi_name'][:44]:<45} {d['poi_type'][:11]:<12}"
            f" {d['distance_m']:>5.0f}m {d['angle_deg']:>5.0f}°"
            f"  {d['walk']}"
        )

    # Save to JSON for deeper analysis
    out_path = os.path.join(os.path.dirname(__file__), "visibility_validation_results.json")
    with open(out_path, "w") as f:
        json.dump(all_decisions, f, indent=2)
    print(f"\n  Full results saved to: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
