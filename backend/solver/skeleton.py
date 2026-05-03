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

_INTEREST_TYPE_MAP: dict[str, frozenset[str]] = {
    "history":       frozenset({"museum", "monument", "castle", "ruins", "historic"}),
    "art":           frozenset({"gallery", "museum", "street_art", "sculpture"}),
    "nature":        frozenset({"park", "garden", "trail", "viewpoint", "beach", "waterfall"}),
    "food":          frozenset({"restaurant", "cafe", "market", "food_hall"}),
    "photography":   frozenset({"viewpoint", "park", "monument", "bridge", "skyline"}),
    "architecture":  frozenset({"monument", "castle", "cathedral", "bridge", "museum"}),
    "hiking":        frozenset({"trail", "park", "viewpoint", "beach", "hiking"}),
    "shopping":      frozenset({"shopping", "market", "mall"}),
    "culture":       frozenset({"museum", "gallery", "theatre", "library", "monument"}),
    "nightlife":     frozenset({"bar", "club", "rooftop", "entertainment"}),
    "beach":         frozenset({"beach", "waterfront", "marina"}),
    "sports":        frozenset({"stadium", "arena", "sports"}),
    "wellness":      frozenset({"spa", "park", "garden", "yoga"}),
}

_TYPE_BASE_SCORES: dict[str, float] = {
    "museum":     0.65,
    "monument":   0.60,
    "viewpoint":  0.60,
    "gallery":    0.58,
    "park":       0.55,
    "garden":     0.52,
    "castle":     0.62,
    "beach":      0.55,
    "trail":      0.50,
    "cafe":       0.40,
    "restaurant": 0.38,
    "shopping":   0.35,
}


def _heuristic_score(poi: dict, interests: list[str]) -> float:
    """Fallback when the LLM scorer isn't available.

    Uses semantic interest→type mapping for meaningful differentiation,
    then falls back to keyword overlap for unrecognised interests.
    Score range is intentionally wide (0.1–1.0) so ranking is useful.
    """
    poi_type = poi.get("poi_type", "")
    base = _TYPE_BASE_SCORES.get(poi_type, 0.25)

    if not interests:
        return base

    # Semantic boost: how many of the user's interests map to this POI type
    interest_hits = sum(
        1 for interest in interests
        if poi_type in _INTEREST_TYPE_MAP.get(interest.lower(), frozenset())
    )

    # Keyword overlap in name + tags as a secondary signal
    haystack = " ".join([
        poi.get("name", ""),
        poi_type,
        " ".join(str(v) for v in poi.get("tags", {}).values()),
    ]).lower()
    keyword_hits = sum(1 for i in interests if i.lower() in haystack)

    score = base + 0.15 * interest_hits + 0.08 * keyword_hits
    return min(1.0, score)


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
    restaurants: list[dict],
    hotels: list[dict],
    matrix: list[list[Leg]],
) -> None:
    """Sort stops by arrival_time and recompute transit_from_prev_min in-place.

    Called at the end of _schedule_day and by the re-plan mutator after
    modifying a day's stop list.

    Transit rules with full matrix (attractions + restaurants + hotels):
    - First stop: always "arrive", 0 min.
    - Going TO a meal: use matrix (restaurant index in matrix)
    - Coming FROM a meal TO an activity: matrix distance
    - Going TO hotel: use matrix (hotel index in matrix)
    - Coming FROM hotel TO first activity: matrix distance
    - Activity → Activity: real matrix distance
    """
    from prefetch.distance import _haversine_km, transit_mode_for as _transit_mode

    stops.sort(key=lambda s: s.arrival_time)

    attr_start = 0
    attr_count = len(attractions)
    rest_start = attr_count
    rest_count = len(restaurants)
    hotel_start = attr_count + rest_count
    hotel_count = len(hotels)

    attr_index = {p["poi_id"]: attr_start + i for i, p in enumerate(attractions)}
    rest_index = {r["name"]: rest_start + i for i, r in enumerate(restaurants)}
    hotel_index = {h["name"]: hotel_start + i for i, h in enumerate(hotels)}

    last_idx: int | None = None

    for i, cur in enumerate(stops):
        if i == 0:
            cur.transit_from_prev_min = 0
            cur.transit_mode = "arrive"
            if cur.poi_type == "accommodation":
                last_idx = hotel_index.get(cur.name, None)
            elif not cur.is_meal:
                last_idx = attr_index.get(cur.poi_id, None)
            continue

        if cur.is_meal:
            idx = rest_index.get(cur.name, None)
        elif cur.poi_type == "accommodation":
            idx = hotel_index.get(cur.name, None)
        else:
            idx = attr_index.get(cur.poi_id, None)

        if idx is not None and last_idx is not None and matrix:
            total_points = len(matrix)
            if idx < total_points and last_idx < total_points:
                cur.transit_from_prev_min = matrix[last_idx][idx].driving_min
                cur.transit_mode = _transit_mode(cur.transit_from_prev_min)
            else:
                cur.transit_from_prev_min = 0
                cur.transit_mode = "walk"
        else:
            cur.transit_from_prev_min = 0
            cur.transit_mode = "walk"

        if idx is not None:
            last_idx = idx


# ── Schedule a single day ────────────────────────────────────────────────────

def _schedule_day(
    day_date: date,
    activity_indices: list[int],
    attractions: list[dict],
    restaurants: list[dict],
    hotels: list[dict],
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

    CHECKIN_TIME  = time(14, 0)
    CHECKOUT_TIME = time(11, 0)

    meals_today = list(MEAL_SLOTS)
    if is_first_day:
        # Drop any meal that falls before hotel check-in (breakfast 8:30, lunch 12:30)
        meals_today = [m for m in meals_today if m["time"] >= CHECKIN_TIME]
    if is_last_day:
        # Drop dinner — guests check out before it
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

    # Last day: drop any non-meal activity scheduled at or after checkout
    if is_last_day:
        stops = [
            s for s in stops
            if s.is_meal or s.poi_type == "accommodation" or s.arrival_time < "11:00"
        ]

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

    _finalize_day_ordering(stops, attractions, restaurants, hotels, matrix)
    return SkeletonDay(date=day_date.isoformat(), weekday=day_date.weekday(), stops=stops)


# ── Type-diversity enforcement ───────────────────────────────────────────────

def _enforce_type_diversity(
    clusters: list[list[int]],
    attractions: list[dict],
) -> list[list[int]]:
    """Prevent the same POI type dominating every day.

    For each day, cap any single type at 2 stops. Excess stops are moved to
    days that have the fewest stops and don't already have that type overloaded.
    Falls back to appending if no ideal day exists (better than dropping).
    """
    _TYPE_CAP = 2

    def _type(idx: int) -> str:
        return attractions[idx].get("poi_type", "unknown")

    for day_i, cluster in enumerate(clusters):
        type_counts: dict[str, int] = {}
        overflow: list[int] = []
        kept: list[int] = []

        for idx in cluster:
            t = _type(idx)
            type_counts[t] = type_counts.get(t, 0) + 1
            if type_counts[t] > _TYPE_CAP:
                overflow.append(idx)
            else:
                kept.append(idx)

        clusters[day_i] = kept

        for idx in overflow:
            t = _type(idx)
            # Move to the day with fewest stops that isn't already over cap for this type
            best_day = -1
            best_size = float("inf")
            for other_i, other_cluster in enumerate(clusters):
                if other_i == day_i:
                    continue
                other_type_count = sum(1 for oi in other_cluster if _type(oi) == t)
                if other_type_count < _TYPE_CAP and len(other_cluster) < best_size:
                    best_size = len(other_cluster)
                    best_day = other_i
            if best_day != -1:
                clusters[best_day].append(idx)
            else:
                clusters[day_i].append(idx)  # no better home — keep it

    return clusters


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
    restaurants = bundle.restaurants
    hotels = bundle.hotels
    matrix = bundle.distance_matrix

    if poi_scores is None:
        poi_scores = {p["poi_id"]: _heuristic_score(p, interests) for p in attractions}

    idx = bundle.matrix_index
    num_attr = idx.attractions_count

    order = sorted(range(len(attractions)), key=lambda i: -poi_scores.get(attractions[i]["poi_id"], 0))
    sorted_attractions = [attractions[i] for i in order]
    old_to_new = {order[i]: i for i in range(len(order))}

    sorted_matrix = []
    for i in range(len(order)):
        row = []
        for j in range(len(order)):
            old_i, old_j = order[i], order[j]
            matrix_i = idx.attractions_start + old_i
            matrix_j = idx.attractions_start + old_j
            if matrix and matrix_i < len(matrix) and matrix_j < len(matrix[0]):
                row.append(matrix[matrix_i][matrix_j])
            else:
                from prefetch.distance import Leg
                row.append(Leg(km=0.0, walking_min=0, driving_min=0))
        sorted_matrix.append(row)

    hotel = None
    if hotels:
        rated = [h for h in hotels if str(h.get("stars", "")).strip().isdigit()]
        hotel = max(rated, key=lambda h: int(h["stars"])) if rated else hotels[0]

    clusters = _cluster_by_proximity(sorted_attractions, sorted_matrix, num_days, drive_tol_min)
    clusters = _enforce_type_diversity(clusters, sorted_attractions)
    ordered_clusters = [_order_within_cluster(c, sorted_matrix) for c in clusters]

    weather_by_date = {w.get("date"): w for w in bundle.weather}
    days: list[SkeletonDay] = []
    for i, cluster_indices in enumerate(ordered_clusters):
        day_date = d0 + timedelta(days=i)
        day = _schedule_day(
            day_date=day_date,
            activity_indices=cluster_indices,
            attractions=sorted_attractions,
            restaurants=restaurants,
            hotels=hotels,
            matrix=matrix,
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
    logger.info(
        f"Skeleton built — {diagnostics['poi_count']} POIs across {len(days)} days, "
        f"hotel: {diagnostics['hotel_picked'] or 'none picked'}, "
        f"drive tolerance: {drive_tol_min}min",
        extra=diagnostics,
    )
    return Skeleton(days=days, hotel=hotel, diagnostics=diagnostics)
