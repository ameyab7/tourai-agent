"""Tests for replan/mutator.py — one test per mutation reason."""

import os
import sys

os.environ.setdefault("GROQ_API_KEY", "test-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from prefetch.distance import HaversineProvider, Leg
from prefetch.orchestrator import PrefetchBundle
from api.models import ReplanRequest
from replan.mutator import mutate_constraints
from solver.skeleton import Skeleton, SkeletonDay, SkeletonStop


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_stop(
    poi_id: str,
    name: str,
    poi_type: str,
    lat: float,
    lon: float,
    arrival_time: str,
    duration_min: int = 60,
    skip_if_rushed: bool = False,
    is_meal: bool = False,
    transit_from_prev_min: int = 0,
    transit_mode: str = "walk",
) -> SkeletonStop:
    return SkeletonStop(
        poi_id=poi_id,
        name=name,
        poi_type=poi_type,
        lat=lat,
        lon=lon,
        arrival_time=arrival_time,
        duration_min=duration_min,
        is_meal=is_meal,
        transit_from_prev_min=transit_from_prev_min,
        transit_mode=transit_mode,
        skip_if_rushed=skip_if_rushed,
    )


def _matrix(attrs: list[dict]) -> list[list[Leg]]:
    points = [(a["lat"], a["lon"]) for a in attrs]
    return HaversineProvider().matrix(points)


# ── test_bad_weather_swaps_outdoor_stops ──────────────────────────────────────

def test_bad_weather_swaps_outdoor_stops():
    """Two park stops are replaced with indoor alternatives; the museum stays."""
    attrs = [
        {"poi_id": "park1",   "name": "City Park",    "poi_type": "park",    "lat": 0.0,  "lon": 0.0,  "tags": {}},
        {"poi_id": "park2",   "name": "River Park",   "poi_type": "park",    "lat": 0.0,  "lon": 0.1,  "tags": {}},
        {"poi_id": "museum1", "name": "Art Museum",   "poi_type": "museum",  "lat": 0.1,  "lon": 0.0,  "tags": {}},
        {"poi_id": "gallery1","name": "City Gallery", "poi_type": "gallery", "lat": 0.0,  "lon": 0.05, "tags": {}},
        {"poi_id": "cafe1",   "name": "Corner Cafe",  "poi_type": "cafe",    "lat": 0.0,  "lon": 0.15, "tags": {}},
    ]
    bundle = PrefetchBundle(
        lat=0.0, lon=0.0, display_name="TestCity",
        attractions=attrs, restaurants=[], hotels=[],
        weather=[], distance_matrix=_matrix(attrs), cache_hits={},
    )
    stops = [
        _make_stop("park1",   "City Park",  "park",   0.0, 0.0, "10:00"),
        _make_stop("park2",   "River Park", "park",   0.0, 0.1, "12:00", skip_if_rushed=True),
        _make_stop("museum1", "Art Museum", "museum", 0.1, 0.0, "14:00"),
    ]
    skeleton = Skeleton(days=[SkeletonDay("2025-06-01", 6, stops)], hotel=None)

    new_skel, log = mutate_constraints(
        skeleton, bundle, ReplanRequest(reason="bad_weather", day_index=0)
    )

    final_ids  = {s.poi_id for s in new_skel.days[0].stops}
    final_types = {s.poi_type for s in new_skel.days[0].stops}

    # Both parks replaced
    assert "park1" not in final_ids
    assert "park2" not in final_ids
    # Museum preserved (already indoor)
    assert "museum1" in final_ids
    # Replacements are indoor types
    assert final_types <= {"museum", "gallery", "cafe", "library", "shopping"}
    # Mutation log
    assert log["reason"] == "bad_weather"
    assert len(log["swaps"]) == 2
    assert log["unswappable"] == []

    # Selection is deterministic: gallery1 wins for park1 (higher score beats cafe),
    # cafe1 wins for park2 (only remaining indoor candidate)
    assert "gallery1" in final_ids
    assert "cafe1"    in final_ids


# ── test_running_late_drops_skippable ────────────────────────────────────────

def test_running_late_drops_skippable():
    """Last skip_if_rushed stop is removed; remaining stops shift forward +30 min."""
    attrs = [
        {"poi_id": "s_a", "name": "Stop A", "poi_type": "viewpoint", "lat": 0.0, "lon": 0.0, "tags": {}},
        {"poi_id": "s_b", "name": "Stop B", "poi_type": "museum",    "lat": 0.0, "lon": 0.1, "tags": {}},
        {"poi_id": "s_c", "name": "Stop C", "poi_type": "park",      "lat": 0.1, "lon": 0.0, "tags": {}},
        {"poi_id": "s_d", "name": "Stop D", "poi_type": "gallery",   "lat": 0.1, "lon": 0.1, "tags": {}},
    ]
    bundle = PrefetchBundle(
        lat=0.0, lon=0.0, display_name="TestCity",
        attractions=attrs, restaurants=[], hotels=[],
        weather=[], distance_matrix=_matrix(attrs), cache_hits={},
    )
    stops = [
        _make_stop("s_a", "Stop A", "viewpoint", 0.0, 0.0, "10:00"),
        _make_stop("s_b", "Stop B", "museum",    0.0, 0.1, "12:00"),
        _make_stop("s_c", "Stop C", "park",      0.1, 0.0, "14:00"),
        _make_stop("s_d", "Stop D", "gallery",   0.1, 0.1, "16:00", skip_if_rushed=True),
    ]
    skeleton = Skeleton(days=[SkeletonDay("2025-06-01", 6, stops)], hotel=None)

    new_skel, log = mutate_constraints(
        skeleton, bundle,
        ReplanRequest(reason="running_late", day_index=0, from_stop_index=0),
    )

    by_id = {s.poi_id: s for s in new_skel.days[0].stops}

    assert "s_d" not in by_id, "skip_if_rushed stop must be dropped"
    assert by_id["s_a"].arrival_time == "10:30"
    assert by_id["s_b"].arrival_time == "12:30"
    assert by_id["s_c"].arrival_time == "14:30"

    assert log["reason"] == "running_late"
    assert "Stop D" in log["dropped"]
    assert log["shifted_by_min"] == 30


# ── test_tired_drops_late_activities ─────────────────────────────────────────

def test_tired_drops_late_activities():
    """Last 1-2 activities are dropped and at least one activity remains."""
    attrs = [
        {"poi_id": "act1", "name": "Activity 1", "poi_type": "museum",    "lat": 0.0, "lon": 0.0, "tags": {}},
        {"poi_id": "act2", "name": "Activity 2", "poi_type": "viewpoint", "lat": 0.0, "lon": 0.1, "tags": {}},
        {"poi_id": "act3", "name": "Activity 3", "poi_type": "park",      "lat": 0.1, "lon": 0.0, "tags": {}},
        {"poi_id": "act4", "name": "Activity 4", "poi_type": "gallery",   "lat": 0.1, "lon": 0.1, "tags": {}},
    ]
    bundle = PrefetchBundle(
        lat=0.0, lon=0.0, display_name="TestCity",
        attractions=attrs, restaurants=[], hotels=[],
        weather=[], distance_matrix=_matrix(attrs), cache_hits={},
    )
    stops = [
        _make_stop("act1", "Activity 1", "museum",    0.0, 0.0, "10:00"),
        _make_stop("act2", "Activity 2", "viewpoint", 0.0, 0.1, "12:30"),
        _make_stop("act3", "Activity 3", "park",      0.1, 0.0, "14:30"),
        _make_stop("act4", "Activity 4", "gallery",   0.1, 0.1, "16:00", skip_if_rushed=True),
    ]
    skeleton = Skeleton(days=[SkeletonDay("2025-06-01", 6, stops)], hotel=None)

    new_skel, log = mutate_constraints(
        skeleton, bundle, ReplanRequest(reason="tired", day_index=0)
    )

    remaining = new_skel.days[0].stops
    activities = [s for s in remaining if not s.is_meal and s.poi_type != "accommodation"]

    assert len(activities) >= 1, "must keep at least one activity"
    assert len(log["dropped"]) >= 1
    assert log["reason"] == "tired"
    assert log["added_rest"] is True

    # The two last activities (act3, act4) are dropped; act1 and act2 survive
    remaining_ids = {s.poi_id for s in remaining}
    assert "act1" in remaining_ids
    assert "act2" in remaining_ids
    assert "act3" not in remaining_ids
    assert "act4" not in remaining_ids

    # A rest stop is inserted (synthetic, since no cafe/park/spa in bundle)
    assert any(s.poi_id.startswith("rest-") or s.poi_type in {"cafe", "park", "spa", "rest"}
               for s in remaining)


# ── test_place_closed_swaps_specific_id ──────────────────────────────────────

def test_place_closed_swaps_specific_id():
    """Closed POI is replaced with a compatible-type alternative from the bundle."""
    attrs = [
        {"poi_id": "museum1",  "name": "Art Museum",  "poi_type": "museum",  "lat": 0.0, "lon": 0.0, "tags": {}},
        {"poi_id": "gallery1", "name": "Old Gallery", "poi_type": "gallery", "lat": 0.0, "lon": 0.1, "tags": {}},
        {"poi_id": "park1",    "name": "City Park",   "poi_type": "park",    "lat": 0.1, "lon": 0.0, "tags": {}},
        {"poi_id": "gallery2", "name": "New Gallery", "poi_type": "gallery", "lat": 0.1, "lon": 0.1, "tags": {}},
    ]
    bundle = PrefetchBundle(
        lat=0.0, lon=0.0, display_name="TestCity",
        attractions=attrs, restaurants=[], hotels=[],
        weather=[], distance_matrix=_matrix(attrs), cache_hits={},
    )
    stops = [
        _make_stop("museum1",  "Art Museum",  "museum",  0.0, 0.0, "10:00"),
        _make_stop("gallery1", "Old Gallery", "gallery", 0.0, 0.1, "12:00"),
        _make_stop("park1",    "City Park",   "park",    0.1, 0.0, "14:00", skip_if_rushed=True),
    ]
    skeleton = Skeleton(days=[SkeletonDay("2025-06-01", 6, stops)], hotel=None)

    new_skel, log = mutate_constraints(
        skeleton, bundle,
        ReplanRequest(reason="place_closed", day_index=0, closed_poi_ids=["gallery1"]),
    )

    final_ids = {s.poi_id for s in new_skel.days[0].stops}

    assert "gallery1" not in final_ids, "closed POI must be removed"
    assert "gallery2" in final_ids,     "compatible replacement must be inserted"
    assert "museum1"  in final_ids,     "unaffected stops must remain"
    assert "park1"    in final_ids,     "unaffected stops must remain"

    assert log["reason"] == "place_closed"
    assert len(log["swaps"]) == 1
    assert log["swaps"][0] == {"out": "Old Gallery", "in": "New Gallery"}
    assert log["dropped"] == []


# ── free_text stub ───────────────────────────────────────────────────────────

def test_free_text_returns_skeleton_unchanged():
    """FREE_TEXT reason returns the original skeleton and implemented=False."""
    attrs = [{"poi_id": "a0", "name": "Place", "poi_type": "museum",
              "lat": 0.0, "lon": 0.0, "tags": {}}]
    bundle = PrefetchBundle(
        lat=0.0, lon=0.0, display_name="T",
        attractions=attrs, restaurants=[], hotels=[],
        weather=[], distance_matrix=_matrix(attrs), cache_hits={},
    )
    stops = [_make_stop("a0", "Place", "museum", 0.0, 0.0, "10:00")]
    skeleton = Skeleton(days=[SkeletonDay("2025-06-01", 6, stops)], hotel=None)

    new_skel, log = mutate_constraints(
        skeleton, bundle, ReplanRequest(reason="free_text", day_index=0)
    )

    assert new_skel is skeleton
    assert log == {"reason": "free_text", "implemented": False}
