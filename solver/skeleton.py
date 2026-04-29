"""tourai/solver/skeleton.py

Stage 2: build a structural skeleton (which POIs, which day, which order, what time).

This is deterministic. No LLM. The LLM did one thing well: scoring POIs against
interests in plain English. We let it do that ONCE upfront (cheap, fast model),
then a real algorithm handles spatial and temporal constraints.

Why this beats letting the LLM do scheduling:
  - LLMs are bad at spatial reasoning ("cluster these 12 points geographically")
  - Drive tolerance is a hard constraint; LLMs treat it as a suggestion
  - Determinism = cacheability + debuggability
  - We can hand the user a "why is this stop on day 2?" answer
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, time, timedelta

from prefetch.distance import Leg, transit_mode_for
from prefetch.orchestrator import PrefetchBundle

logger = logging.getLogger("tourai.solver")


# ── Types ────────────────────────────────────────────────────────────────────

@dataclass
class SkeletonStop:
    poi_id: str
    name: str
    poi_type: str
    lat: float
    lon: float
    arrival_time: str       # "HH:MM"
    duration_min: int
    is_meal: bool
    transit_from_prev_min: int
    transit_mode: str       # "arrive" | "walk" | "uber" | "drive"
    skip_if_rushed: bool


@dataclass
class SkeletonDay:
    date: str
    weekday: int
    stops: list[SkeletonStop]
    weather_is_clear: bool | None = None


@dataclass
class Skeleton:
    days: list[SkeletonDay]
    hotel: dict | None
    diagnostics: dict = field(default_factory=dict)


# ── Pace configuration ───────────────────────────────────────────────────────

PACE_CONFIG = {
    "relaxed":  {"activities": 2, "default_duration_min": 90},
    "balanced": {"activities": 3, "default_duration_min": 75},
    "packed":   {"activities": 4, "default_duration_min": 60},
}

# Meal anchors. We schedule activities AROUND these.
MEAL_SLOTS = [
    {"label": "breakfast", "time": time(8, 30),  "duration": 45},
    {"label": "lunch",     "time": time(12, 30), "duration": 60},
    {"label": "dinner",    "time": time(19, 0),  "duration": 90},
]


# ── Interest-aware POI scoring ────────────────────────────────────────────────
# Stub — wire to a cheap LLM call in scorer.py when ready.

def _heuristic_score(poi: dict, interests: list[str]) -> float:
    """Fallback when the LLM scorer isn't available. Overlap-based, 0..1."""
    if not interests:
        return 0.5
    haystack = " ".join([
        poi.get("name", ""),
        poi.get("poi_type", ""),
        " ".join(str(v) for v in poi.get("tags", {}).values()),
    ]).lower()
    hits = sum(1 for i in interests if i.lower() in haystack)
    type_boost = {
        "museum": 0.3, "gallery": 0.3, "monument": 0.3,
        "park": 0.2, "viewpoint": 0.3, "beach": 0.2,
    }.get(poi.get("poi_type", ""), 0.0)
    return min(1.0, 0.2 + 0.3 * hits + type_boost)


# ── Geographic clustering ────────────────────────────────────────────────────

def _cluster_by_proximity(
    attractions: list[dict],
    matrix: list[list[Leg]],
    num_days: int,
    drive_tol_min: int,
) -> list[list[int]]:
    """Partition POI indices into `num_days` geographic clusters.

    Algorithm: seed each cluster with the highest-scored unassigned POI that's
    far from existing seeds, then assign remaining POIs to the nearest seed
    that doesn't violate drive tolerance.
    """
    n = len(attractions)
    if n == 0 or num_days == 0:
        return [[] for _ in range(num_days)]

    seeds: list[int] = [0]
    while len(seeds) < min(num_days, n):
        best_idx = -1
        best_min_dist = -1.0
        for i in range(n):
            if i in seeds:
                continue
            min_to_seeds = min(matrix[i][s].driving_min for s in seeds)
            if min_to_seeds > best_min_dist:
                best_min_dist = min_to_seeds
                best_idx = i
        if best_idx == -1:
            break
        seeds.append(best_idx)

    clusters: list[list[int]] = [[s] for s in seeds]
    while len(clusters) < num_days:
        clusters.append([])

    assigned = set(seeds)
    for i in range(n):
        if i in assigned:
            continue
        best_cluster = -1
        best_dist = float("inf")
        for ci, seed_idx in enumerate(seeds):
            d = matrix[i][seed_idx].driving_min
            if d <= drive_tol_min and d < best_dist:
                best_dist = d
                best_cluster = ci
        if best_cluster == -1:
            continue
        clusters[best_cluster].append(i)
        assigned.add(i)

    return clusters


def _order_within_cluster(indices: list[int], matrix: list[list[Leg]]) -> list[int]:
    """Order stops within a day to minimize total travel (nearest-neighbour TSP).

    Good enough for ≤5 stops. Swap to OR-tools if you need more.
    """
    if len(indices) <= 1:
        return indices
    remaining = set(indices)
    current = indices[0]
    ordered = [current]
    remaining.remove(current)
    while remaining:
        nxt = min(remaining, key=lambda i: matrix[current][i].driving_min)
        ordered.append(nxt)
        remaining.remove(nxt)
        current = nxt
    return ordered


# ── Chronological sort + transit recomputation ────────────────────────────────

def _finalize_day_ordering(
    stops: list[SkeletonStop],
    attractions: list[dict],
    matrix: list[list[Leg]],
) -> None:
    """Sort stops by arrival_time and recompute transit_from_prev_min in-place.

    Called at the end of _schedule_day and by the re-plan mutator after
    modifying a day's stop list.
    """
    stops.sort(key=lambda s: s.arrival_time)
    poi_index = {p["poi_id"]: i for i, p in enumerate(attractions)}
    for i in range(1, len(stops)):
        prev, cur = stops[i - 1], stops[i]
        if (cur.is_meal or cur.poi_type == "accommodation"
                or prev.is_meal or prev.poi_type == "accommodation"):
            cur.transit_from_prev_min = 0
            cur.transit_mode = "walk"
        else:
            pi = poi_index.get(prev.poi_id)
            ci = poi_index.get(cur.poi_id)
            if pi is not None and ci is not None:
                cur.transit_from_prev_min = matrix[pi][ci].driving_min
                cur.transit_mode = transit_mode_for(cur.transit_from_prev_min)
    if stops:
        stops[0].transit_from_prev_min = 0
        stops[0].transit_mode = "arrive"


# ── Schedule a single day ────────────────────────────────────────────────────

def _schedule_day(
    day_date: date,
    activity_indices: list[int],
    attractions: list[dict],
    matrix: list[list[Leg]],
    pace: str,
    is_first_day: bool,
    is_last_day: bool,
    hotel: dict | None,
) -> SkeletonDay:
    cfg = PACE_CONFIG.get(pace, PACE_CONFIG["balanced"])
    max_activities = cfg["activities"]
    default_duration = cfg["default_duration_min"]

    activity_indices = activity_indices[:max_activities]

    stops: list[SkeletonStop] = []

    if is_first_day and hotel:
        stops.append(SkeletonStop(
            poi_id="hotel-checkin",
            name=hotel["name"],
            poi_type="accommodation",
            lat=hotel.get("lat", 0.0), lon=hotel.get("lon", 0.0),
            arrival_time="14:00",
            duration_min=30,
            is_meal=False,
            transit_from_prev_min=0,
            transit_mode="arrive",
            skip_if_rushed=False,
        ))

    meals_today = list(MEAL_SLOTS)
    if is_first_day:
        meals_today = [m for m in meals_today if m["label"] != "breakfast"]
    if is_last_day:
        meals_today = [m for m in meals_today if m["label"] != "dinner"]

    timeline: list[tuple[time, str, object]] = []
    for m in meals_today:
        timeline.append((m["time"], "meal", m))

    activity_window_start = time(10, 0) if not is_first_day else time(15, 0)
    activity_window_end   = time(18, 0) if not is_last_day  else time(11, 0)

    if activity_indices and activity_window_start < activity_window_end:
        start_min = activity_window_start.hour * 60 + activity_window_start.minute
        end_min   = activity_window_end.hour * 60 + activity_window_end.minute
        slot_size = (end_min - start_min) / max(len(activity_indices), 1)
        for k, ai in enumerate(activity_indices):
            t_min = int(start_min + k * slot_size)
            t = time(hour=t_min // 60, minute=t_min % 60)
            timeline.append((t, "activity", ai))

    timeline.sort(key=lambda x: x[0])

    prev_idx: int | None = None
    for t, kind, payload in timeline:
        clock = f"{t.hour:02d}:{t.minute:02d}"
        if kind == "meal":
            m = payload  # type: ignore
            stops.append(SkeletonStop(
                poi_id=f"meal-{m['label']}-{day_date.isoformat()}",
                name=f"({m['label'].title()})",
                poi_type="meal",
                lat=0.0, lon=0.0,
                arrival_time=clock,
                duration_min=m["duration"],
                is_meal=True,
                transit_from_prev_min=0,
                transit_mode="walk",
                skip_if_rushed=False,
            ))
        else:
            ai = payload  # type: ignore
            poi = attractions[ai]
            transit_min = matrix[prev_idx][ai].driving_min if prev_idx is not None else 0
            stops.append(SkeletonStop(
                poi_id=poi["poi_id"],
                name=poi["name"],
                poi_type=poi["poi_type"],
                lat=poi["lat"], lon=poi["lon"],
                arrival_time=clock,
                duration_min=default_duration,
                is_meal=False,
                transit_from_prev_min=transit_min,
                transit_mode=transit_mode_for(transit_min),
                skip_if_rushed=False,
            ))
            prev_idx = ai

    # Mark the last non-meal, non-hotel stop as skippable
    for s in reversed(stops):
        if not s.is_meal and s.poi_type != "accommodation":
            s.skip_if_rushed = True
            break

    if is_last_day and hotel:
        stops.append(SkeletonStop(
            poi_id="hotel-checkout",
            name=hotel["name"],
            poi_type="accommodation",
            lat=hotel.get("lat", 0.0), lon=hotel.get("lon", 0.0),
            arrival_time="11:00",
            duration_min=30,
            is_meal=False,
            transit_from_prev_min=0,
            transit_mode="arrive",
            skip_if_rushed=False,
        ))

    _finalize_day_ordering(stops, attractions, matrix)
    return SkeletonDay(date=day_date.isoformat(), weekday=day_date.weekday(), stops=stops)


# ── Public entry point ───────────────────────────────────────────────────────

def build_skeleton(
    bundle: PrefetchBundle,
    start_date: str,
    end_date: str,
    interests: list[str],
    pace: str,
    drive_tol_hrs: float,
    poi_scores: dict[str, float] | None = None,
) -> Skeleton:
    """Build the structural plan. Pure function — no I/O, deterministic.

    poi_scores: optional dict from poi_id → 0..1 score. If absent, falls back
    to the keyword heuristic. Wire your LLM scorer to populate this.
    """
    d0 = date.fromisoformat(start_date)
    d1 = date.fromisoformat(end_date)
    num_days = (d1 - d0).days + 1
    drive_tol_min = int(drive_tol_hrs * 60)

    attractions = bundle.attractions
    matrix = bundle.distance_matrix

    if poi_scores is None:
        poi_scores = {p["poi_id"]: _heuristic_score(p, interests) for p in attractions}

    # Sort by score desc; reindex matrix accordingly
    order = sorted(range(len(attractions)), key=lambda i: -poi_scores.get(attractions[i]["poi_id"], 0))
    sorted_attractions = [attractions[i] for i in order]
    sorted_matrix = [[matrix[order[i]][order[j]] for j in range(len(order))] for i in range(len(order))]

    hotel = None
    if bundle.hotels:
        rated = [h for h in bundle.hotels if str(h.get("stars", "")).strip().isdigit()]
        hotel = max(rated, key=lambda h: int(h["stars"])) if rated else bundle.hotels[0]

    clusters = _cluster_by_proximity(sorted_attractions, sorted_matrix, num_days, drive_tol_min)
    ordered_clusters = [_order_within_cluster(c, sorted_matrix) for c in clusters]

    weather_by_date = {w.get("date"): w for w in bundle.weather}
    days: list[SkeletonDay] = []
    for i, cluster_indices in enumerate(ordered_clusters):
        day_date = d0 + timedelta(days=i)
        day = _schedule_day(
            day_date=day_date,
            activity_indices=cluster_indices,
            attractions=sorted_attractions,
            matrix=sorted_matrix,
            pace=pace,
            is_first_day=(i == 0),
            is_last_day=(i == num_days - 1),
            hotel=hotel,
        )
        wx = weather_by_date.get(day.date)
        if wx is not None:
            day.weather_is_clear = wx.get("is_clear")
        days.append(day)

    diagnostics = {
        "poi_count":        len(attractions),
        "cluster_sizes":    [len(c) for c in ordered_clusters],
        "drive_tol_min":    drive_tol_min,
        "hotel_picked":     hotel["name"] if hotel else None,
    }
    logger.info("skeleton_built", extra=diagnostics)
    return Skeleton(days=days, hotel=hotel, diagnostics=diagnostics)
