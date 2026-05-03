"""
run.py — TourAI

Usage:
    # Single GPS point (lat,lon,heading)
    python run.py 32.791514,-96.795706 225

    # Walk simulation (start end steps)
    python run.py 32.791514,-96.795706 32.787654,-96.800159 5
"""

import asyncio
import sys

from dotenv import load_dotenv
load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

def _parse_coord(s: str) -> tuple[float, float]:
    parts = s.split(",")
    if len(parts) != 2:
        print(f"ERROR: expected 'lat,lon', got {s!r}")
        sys.exit(1)
    return float(parts[0]), float(parts[1])

args = sys.argv[1:]

# Detect mode: single point = 2 args (coord + heading), walk = 3 args (start + end + steps)
if len(args) == 2:
    MODE        = "single"
    SINGLE_POS  = _parse_coord(args[0])
    SINGLE_HDG  = float(args[1])
else:
    MODE        = "walk"
    WALK_START  = _parse_coord(args[0]) if len(args) > 0 else (32.791514, -96.795706)
    WALK_END    = _parse_coord(args[1]) if len(args) > 1 else (32.787654, -96.800159)
    WALK_STEPS  = int(args[2])          if len(args) > 2 else 5

SEARCH_RADIUS = 150.0   # metres


# ── Display helpers ───────────────────────────────────────────────────────────

def section(title: str) -> None:
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print(f"{'═' * 60}")

def log(msg: str) -> None:
    print(f"  {msg}")


# ── Core: what can the user see right now? ────────────────────────────────────

async def what_can_i_see(
    lat: float,
    lon: float,
    heading: float,
) -> tuple[list[dict], str | None]:
    """Given a GPS position and heading, return visible nearby places.

    1. OSRM: what street is the user on?
    2. Overpass: find all named POIs within SEARCH_RADIUS (with geometry)
    3. Visibility score filter: prominence + distance + angle

    Returns (visible_pois, user_street)
    """
    from utils.osrm import get_street_ahead
    from utils.overpass import search_nearby
    from utils.visibility import filter_visible

    # 1. Street name (kept for display — no longer used for filtering)
    user_street = await get_street_ahead(lat, lon, heading)

    # 2. Raw POIs from OSM
    pois = await search_nearby(lat, lon, radius=SEARCH_RADIUS)

    # 3. AI visibility filter
    visible, _ = await filter_visible(pois, lat, lon, heading, user_street)

    return visible, user_street


# ── Simulation: walk A→B in N steps ──────────────────────────────────────────

async def build_walk_points(
    start: tuple[float, float],
    end:   tuple[float, float],
    steps: int,
) -> list[tuple[float, float, float]]:
    """Sample N evenly-spaced (lat, lon, heading) positions along the OSRM route."""
    from utils.osrm import walking_route
    from utils.geoutils import haversine_meters, bearing

    def _linear() -> list[tuple[float, float, float]]:
        brg = bearing(start[0], start[1], end[0], end[1])
        return [
            (
                start[0] + (i / max(steps - 1, 1)) * (end[0] - start[0]),
                start[1] + (i / max(steps - 1, 1)) * (end[1] - start[1]),
                round(brg, 1),
            )
            for i in range(steps)
        ]

    log("Fetching OSRM walking route …")
    try:
        route = await walking_route(start[0], start[1], end[0], end[1])
    except Exception as e:
        log(f"OSRM unavailable ({e}) — straight-line fallback")
        return _linear()

    if not route or len(route) < 2:
        log("Empty route — straight-line fallback")
        return _linear()

    from utils.geoutils import haversine_meters
    total_m = sum(
        haversine_meters(route[i][0], route[i][1], route[i+1][0], route[i+1][1])
        for i in range(len(route) - 1)
    )
    log(f"Route: {len(route)} waypoints, ~{total_m:.0f}m")

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
        # Last step has no next waypoint — inherit heading from previous step
        if seg + 1 < len(route):
            hdg = bearing(la, lo, route[seg+1][0], route[seg+1][1])
        elif points:
            hdg = points[-1][2]   # copy last known heading
        else:
            hdg = bearing(start[0], start[1], end[0], end[1])
        points.append((la, lo, round(hdg, 1)))

    return points


async def main() -> None:
    if MODE == "single":
        await _run_single()
    else:
        await _run_walk()


async def _run_single() -> None:
    lat, lon = SINGLE_POS
    heading  = SINGLE_HDG

    section(f"TourAI — single point")
    log(f"Position      : ({lat}, {lon})")
    log(f"Heading       : {heading}°")
    log(f"Search radius : {SEARCH_RADIUS:.0f}m")

    visible, user_street = await what_can_i_see(lat, lon, heading)

    log(f"Street        : {user_street or 'unknown'}")
    print()

    if not visible:
        log("Nothing visible at this position.")
        return

    section(f"Visible places ({len(visible)})")
    for i, p in enumerate(visible, 1):
        tags = p.get("tags", {})
        kind = (tags.get("tourism") or tags.get("historic") or
                tags.get("amenity") or tags.get("leisure") or
                tags.get("building") or p.get("poi_type", ""))
        log(f"  {i}. {p['name']}  [{kind}]  {p['distance_m']:.0f}m  {p['angle_deg']:.0f}° off heading")


async def _run_walk() -> None:
    from utils.geoutils import haversine_meters

    straight_m = haversine_meters(WALK_START[0], WALK_START[1], WALK_END[0], WALK_END[1])

    section(f"TourAI — {WALK_START} → {WALK_END}")
    log(f"Steps         : {WALK_STEPS}")
    log(f"Distance      : ~{straight_m:.0f}m")
    log(f"Search radius : {SEARCH_RADIUS:.0f}m")

    section("Route")
    walk_points = await build_walk_points(WALK_START, WALK_END, WALK_STEPS)
    for i, (la, lo, hdg) in enumerate(walk_points, 1):
        log(f"  {i}. ({la:.5f}, {lo:.5f})  heading={hdg}°")

    section("Walking …")
    seen_ids: set = set()
    all_visible: list[dict] = []

    # OPTION 1 — production approach (commented out):
    # If street is unknown, skip the step and wait for the next GPS ping.
    #
    # for i, (lat, lon, heading) in enumerate(walk_points, 1):
    #     visible, user_street = await what_can_i_see(lat, lon, heading)
    #     if user_street is None:
    #         log(f"Step {i}: unknown street — waiting for next ping")
    #         continue
    #     ...

    # OPTION 2 — active: project 25m ahead if street is unknown
    for i, (lat, lon, heading) in enumerate(walk_points, 1):
        log(f"\nStep {i}/{len(walk_points)}  ({lat:.5f}, {lon:.5f})  heading={heading}°")
        visible, user_street = await what_can_i_see(lat, lon, heading)
        log(f"  Street: {user_street or 'unknown'}")

        new = [p for p in visible if str(p.get("id", "")) not in seen_ids]
        for p in new:
            seen_ids.add(str(p.get("id", "")))
            all_visible.append(p)

        if new:
            for p in new:
                tags = p.get("tags", {})
                kind = (tags.get("tourism") or tags.get("historic") or
                        tags.get("amenity") or tags.get("leisure") or
                        tags.get("building") or p.get("poi_type", ""))
                log(f"    ✓ {p['name']}  [{kind}]  {p['distance_m']:.0f}m  {p['angle_deg']:.0f}° off heading")
        else:
            log(f"    — nothing new visible")

    section("Places seen on this walk")
    if not all_visible:
        log("No places found. Try a larger search radius or different route.")
    else:
        for i, p in enumerate(all_visible, 1):
            tags = p.get("tags", {})
            kind = (tags.get("tourism") or tags.get("historic") or
                    tags.get("amenity") or tags.get("leisure") or
                    tags.get("building") or p.get("poi_type", ""))
            log(f"  {i}. {p['name']}  [{kind}]")


if __name__ == "__main__":
    asyncio.run(main())
