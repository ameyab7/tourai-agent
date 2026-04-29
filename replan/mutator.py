"""tourai/replan/mutator.py

Constraint mutation for re-planning a single day.

Each handler receives the original (immutable) Skeleton, deep-copies it,
modifies only the affected day, and returns (new_skeleton, mutation_log).

Selection rule (applied consistently across all reasons):
  best candidate = highest _score_for_replacement, then lowest driving_min,
  then alphabetical name — deterministic, no randomness.
"""

from __future__ import annotations

import copy
import logging

from api.models import ReplanRequest
from prefetch.orchestrator import PrefetchBundle
from solver.skeleton import Skeleton, SkeletonStop, _finalize_day_ordering

logger = logging.getLogger("tourai.replan")


# ── POI type taxonomy ────────────────────────────────────────────────────────

_OUTDOOR_TYPES = frozenset({"park", "viewpoint", "beach", "garden", "trail", "hiking"})
_INDOOR_TYPES  = frozenset({"museum", "gallery", "cafe", "library", "shopping"})

# Loose type compatibility for place_closed swaps
_TYPE_COMPAT: dict[str, frozenset[str]] = {
    "museum":     frozenset({"museum", "gallery"}),
    "gallery":    frozenset({"gallery", "museum"}),
    "restaurant": frozenset({"restaurant", "cafe"}),
    "cafe":       frozenset({"cafe", "restaurant"}),
}

# Deterministic per-type scores — used when the LLM scorer is unavailable
_TYPE_SCORES: dict[str, float] = {
    "museum":     0.70,
    "gallery":    0.65,
    "monument":   0.60,
    "viewpoint":  0.60,
    "park":       0.55,
    "cafe":       0.50,
    "library":    0.50,
    "spa":        0.50,
    "beach":      0.50,
    "garden":     0.50,
    "shopping":   0.45,
    "trail":      0.45,
    "hiking":     0.45,
    "restaurant": 0.40,
}


def _score_for_replacement(attr: dict) -> float:
    return _TYPE_SCORES.get(attr.get("poi_type", ""), 0.30)


# ── Distance helpers ─────────────────────────────────────────────────────────

def _attr_index(bundle: PrefetchBundle) -> dict[str, int]:
    return {a["poi_id"]: i for i, a in enumerate(bundle.attractions)}


def _driving_min(
    from_id: str,
    to_id: str,
    poi_idx: dict[str, int],
    bundle: PrefetchBundle,
) -> int:
    fi = poi_idx.get(from_id)
    ti = poi_idx.get(to_id)
    if fi is None or ti is None:
        return 0
    return bundle.distance_matrix[fi][ti].driving_min


# ── Time helpers ─────────────────────────────────────────────────────────────

def _shift_time(arrival: str, delta_min: int, cap_min: int) -> str:
    """Add delta_min to an HH:MM string, clamped to [0, cap_min]."""
    h, m = map(int, arrival.split(":"))
    total = max(0, min(h * 60 + m + delta_min, cap_min))
    return f"{total // 60:02d}:{total % 60:02d}"


# ── BAD_WEATHER ───────────────────────────────────────────────────────────────

def _mutate_bad_weather(
    skeleton: Skeleton,
    bundle: PrefetchBundle,
    request: ReplanRequest,
) -> tuple[Skeleton, dict]:
    new_skel = copy.deepcopy(skeleton)
    day = new_skel.days[request.day_index]
    poi_idx = _attr_index(bundle)

    other_ids: set[str] = {
        s.poi_id
        for di, d in enumerate(new_skel.days)
        for s in d.stops
        if di != request.day_index
    }
    current_ids: set[str] = {s.poi_id for s in day.stops}

    swaps: list[dict] = []
    unswappable: list[str] = []

    for stop in day.stops:
        if stop.is_meal or stop.poi_type == "accommodation":
            continue
        if stop.poi_type not in _OUTDOOR_TYPES:
            continue

        used = other_ids | current_ids
        candidates: list[tuple[float, int, str, dict]] = []
        for attr in bundle.attractions:
            if attr["poi_id"] in used:
                continue
            if attr["poi_type"] not in _INDOOR_TYPES:
                continue
            score = _score_for_replacement(attr)
            if score < 0.4:
                continue
            d_min = _driving_min(stop.poi_id, attr["poi_id"], poi_idx, bundle)
            candidates.append((score, d_min, attr["name"], attr))

        if candidates:
            candidates.sort(key=lambda x: (-x[0], x[1], x[2]))
            _, _, _, best = candidates[0]

            current_ids.discard(stop.poi_id)
            current_ids.add(best["poi_id"])
            swaps.append({"out": stop.name, "in": best["name"]})

            stop.poi_id   = best["poi_id"]
            stop.name     = best["name"]
            stop.poi_type = best["poi_type"]
            stop.lat      = best["lat"]
            stop.lon      = best["lon"]
        else:
            unswappable.append(stop.name)
            stop.skip_if_rushed = True

    _finalize_day_ordering(day.stops, bundle.attractions, bundle.distance_matrix)
    return new_skel, {"reason": "bad_weather", "swaps": swaps, "unswappable": unswappable}


# ── RUNNING_LATE ──────────────────────────────────────────────────────────────

def _mutate_running_late(
    skeleton: Skeleton,
    bundle: PrefetchBundle,
    request: ReplanRequest,
) -> tuple[Skeleton, dict]:
    new_skel = copy.deepcopy(skeleton)
    day = new_skel.days[request.day_index]

    from_idx = request.from_stop_index if request.from_stop_index is not None else 0
    from_idx = max(0, min(from_idx, len(day.stops)))

    pre       = day.stops[:from_idx]
    remaining = day.stops[from_idx:]
    dropped: list[str] = []

    # Step 1: drop last skip_if_rushed non-meal activity
    step1_dropped = False
    for i in range(len(remaining) - 1, -1, -1):
        s = remaining[i]
        if not s.is_meal and s.poi_type != "accommodation" and s.skip_if_rushed:
            dropped.append(s.name)
            remaining.pop(i)
            step1_dropped = True
            break

    # Step 2: if step 1 found nothing to drop, drop the lowest-scored activity
    # ("still too tight" — no skippable stop existed to shed time)
    if not step1_dropped:
        non_meal_rem = [
            (i, s) for i, s in enumerate(remaining)
            if not s.is_meal and s.poi_type != "accommodation"
        ]
        if len(non_meal_rem) > 1:  # keep at least 1
            score_map = {a["poi_id"]: _score_for_replacement(a) for a in bundle.attractions}
            non_meal_rem.sort(key=lambda x: (score_map.get(x[1].poi_id, 0.0), x[1].name))
            worst_i, worst_stop = non_meal_rem[0]
            dropped.append(worst_stop.name)
            remaining.pop(worst_i)

    # Step 3: shift all remaining stops forward by 30 min, with per-type caps
    _SHIFT        = 30
    _CAP_ACTIVITY = 20 * 60       # 20:00
    _CAP_MEAL     = 21 * 60 + 30  # 21:30

    for stop in remaining:
        cap = _CAP_MEAL if stop.is_meal else _CAP_ACTIVITY
        stop.arrival_time = _shift_time(stop.arrival_time, _SHIFT, cap)

    day.stops = pre + remaining
    _finalize_day_ordering(day.stops, bundle.attractions, bundle.distance_matrix)
    return new_skel, {"reason": "running_late", "dropped": dropped, "shifted_by_min": _SHIFT}


# ── TIRED ─────────────────────────────────────────────────────────────────────

def _mutate_tired(
    skeleton: Skeleton,
    bundle: PrefetchBundle,
    request: ReplanRequest,
) -> tuple[Skeleton, dict]:
    new_skel = copy.deepcopy(skeleton)
    day = new_skel.days[request.day_index]

    non_meal = [
        (i, s) for i, s in enumerate(day.stops)
        if not s.is_meal and s.poi_type != "accommodation"
    ]
    # Drop last 1-2, keeping at least 1
    to_drop = min(2, max(0, len(non_meal) - 1))

    dropped: list[str] = []
    dropped_arrivals: list[str] = []
    dropped_ids: set[str] = set()

    for _ in range(to_drop):
        non_meal = [
            (i, s) for i, s in enumerate(day.stops)
            if not s.is_meal and s.poi_type != "accommodation"
        ]
        if not non_meal:
            break
        drop_i, drop_stop = non_meal[-1]
        dropped.append(drop_stop.name)
        dropped_arrivals.append(drop_stop.arrival_time)
        dropped_ids.add(drop_stop.poi_id)
        day.stops.pop(drop_i)

    rest_arrival = (
        min(dropped_arrivals) if dropped_arrivals
        else (day.stops[-1].arrival_time if day.stops else "15:00")
    )

    # Prefer a real cafe/park/spa POI; fall back to synthetic hotel rest.
    # Exclude both already-used and just-dropped POIs so a dropped park
    # cannot immediately re-appear as the rest destination.
    used_ids = {s.poi_id for d in new_skel.days for s in d.stops} | dropped_ids
    rest_candidates: list[tuple[float, str, dict]] = [
        (_score_for_replacement(a), a["name"], a)
        for a in bundle.attractions
        if a["poi_id"] not in used_ids and a["poi_type"] in {"cafe", "park", "spa"}
    ]
    rest_candidates.sort(key=lambda x: (-x[0], x[1]))

    added_rest = True
    if rest_candidates:
        _, _, best = rest_candidates[0]
        day.stops.append(SkeletonStop(
            poi_id=best["poi_id"],
            name=best["name"],
            poi_type=best["poi_type"],
            lat=best["lat"],
            lon=best["lon"],
            arrival_time=rest_arrival,
            duration_min=90,
            is_meal=False,
            transit_from_prev_min=0,
            transit_mode="walk",
            skip_if_rushed=False,
        ))
    else:
        hotel = new_skel.hotel
        day.stops.append(SkeletonStop(
            poi_id=f"rest-{day.date}",
            name="Rest at hotel",
            poi_type="rest",
            lat=hotel["lat"] if hotel and "lat" in hotel else 0.0,
            lon=hotel["lon"] if hotel and "lon" in hotel else 0.0,
            arrival_time=rest_arrival,
            duration_min=120,
            is_meal=False,
            transit_from_prev_min=0,
            transit_mode="walk",
            skip_if_rushed=False,
        ))

    _finalize_day_ordering(day.stops, bundle.attractions, bundle.distance_matrix)
    return new_skel, {"reason": "tired", "dropped": dropped, "added_rest": added_rest}


# ── PLACE_CLOSED ──────────────────────────────────────────────────────────────

def _mutate_place_closed(
    skeleton: Skeleton,
    bundle: PrefetchBundle,
    request: ReplanRequest,
) -> tuple[Skeleton, dict]:
    new_skel = copy.deepcopy(skeleton)
    day = new_skel.days[request.day_index]
    poi_idx = _attr_index(bundle)

    closed_ids = set(request.closed_poi_ids)
    other_ids: set[str] = {
        s.poi_id
        for di, d in enumerate(new_skel.days)
        for s in d.stops
        if di != request.day_index
    }
    current_ids: set[str] = {s.poi_id for s in day.stops}

    swaps: list[dict] = []
    dropped: list[str] = []

    i = 0
    while i < len(day.stops):
        stop = day.stops[i]
        if stop.poi_id not in closed_ids:
            i += 1
            continue

        compat = _TYPE_COMPAT.get(stop.poi_type, frozenset({stop.poi_type}))
        used = other_ids | current_ids

        candidates: list[tuple[float, int, str, dict]] = []
        for attr in bundle.attractions:
            if attr["poi_id"] in used:
                continue
            if attr["poi_type"] not in compat:
                continue
            score = _score_for_replacement(attr)
            d_min = _driving_min(stop.poi_id, attr["poi_id"], poi_idx, bundle)
            candidates.append((score, d_min, attr["name"], attr))

        if candidates:
            candidates.sort(key=lambda x: (-x[0], x[1], x[2]))
            _, _, _, best = candidates[0]

            current_ids.discard(stop.poi_id)
            current_ids.add(best["poi_id"])
            swaps.append({"out": stop.name, "in": best["name"]})

            day.stops[i] = SkeletonStop(
                poi_id=best["poi_id"],
                name=best["name"],
                poi_type=best["poi_type"],
                lat=best["lat"],
                lon=best["lon"],
                arrival_time=stop.arrival_time,
                duration_min=stop.duration_min,
                is_meal=stop.is_meal,
                transit_from_prev_min=stop.transit_from_prev_min,
                transit_mode=stop.transit_mode,
                skip_if_rushed=stop.skip_if_rushed,
            )
            i += 1
        else:
            # No replacement — drop and shift subsequent stops earlier
            dropped.append(stop.name)
            freed = stop.duration_min
            current_ids.discard(stop.poi_id)
            day.stops.pop(i)
            for j in range(i, len(day.stops)):
                day.stops[j].arrival_time = _shift_time(
                    day.stops[j].arrival_time, -freed, 23 * 60 + 59
                )

    _finalize_day_ordering(day.stops, bundle.attractions, bundle.distance_matrix)
    return new_skel, {"reason": "place_closed", "swaps": swaps, "dropped": dropped}


# ── Dispatcher ────────────────────────────────────────────────────────────────

_HANDLERS = {
    "bad_weather":  _mutate_bad_weather,
    "running_late": _mutate_running_late,
    "tired":        _mutate_tired,
    "place_closed": _mutate_place_closed,
}


def summarize_mutation(mutation_log: dict) -> str:
    """One-line human-readable summary of what changed.

    Used in replan narration prompts and SSE event payloads.
    """
    reason = mutation_log.get("reason", "")

    if reason == "bad_weather":
        swaps = mutation_log.get("swaps", [])
        unswappable = mutation_log.get("unswappable", [])
        parts: list[str] = []
        if swaps:
            n = len(swaps)
            parts.append(f"Swapped {n} outdoor stop{'s' if n != 1 else ''} for indoor alternatives")
        if unswappable:
            m = len(unswappable)
            parts.append(f"{m} stop{'s' if m != 1 else ''} flagged as optional (no indoor replacement found)")
        return "; ".join(parts) or "Switched to indoor-friendly stops"

    if reason == "running_late":
        dropped = mutation_log.get("dropped", [])
        shift = mutation_log.get("shifted_by_min", 30)
        base = f"Compressed schedule, shifted times by {shift} min"
        if dropped:
            n = len(dropped)
            return f"{base}, dropped {n} stop{'s' if n != 1 else ''}"
        return base

    if reason == "tired":
        dropped = mutation_log.get("dropped", [])
        parts = ["Lighter day"]
        if dropped:
            n = len(dropped)
            parts.append(f"dropped {n} stop{'s' if n != 1 else ''}")
        if mutation_log.get("added_rest"):
            parts.append("added rest period")
        return ", ".join(parts)

    if reason == "place_closed":
        swaps = mutation_log.get("swaps", [])
        dropped = mutation_log.get("dropped", [])
        parts = []
        if swaps:
            n = len(swaps)
            parts.append(f"Swapped {n} closed venue{'s' if n != 1 else ''}")
        if dropped:
            n = len(dropped)
            parts.append(f"removed {n} stop{'s' if n != 1 else ''} with no replacement")
        return "; ".join(parts) or "Replaced closed venue"

    return "Plan adjusted"


def mutate_constraints(
    skeleton: Skeleton,
    bundle: PrefetchBundle,
    request: ReplanRequest,
) -> tuple[Skeleton, dict]:
    """Return (new_skeleton, mutation_log). Never mutates the input skeleton."""
    if request.reason == "free_text":
        return skeleton, {"reason": "free_text", "implemented": False}

    new_skel, log = _HANDLERS[request.reason](skeleton, bundle, request)

    # Safety: never leave a day with zero activities
    day = new_skel.days[request.day_index]
    if not any(not s.is_meal and s.poi_type != "accommodation" for s in day.stops):
        fallback = next(
            (copy.deepcopy(s) for s in skeleton.days[request.day_index].stops
             if not s.is_meal and s.poi_type != "accommodation"),
            None,
        )
        if fallback:
            day.stops.append(fallback)
            _finalize_day_ordering(day.stops, bundle.attractions, bundle.distance_matrix)

    return new_skel, log
