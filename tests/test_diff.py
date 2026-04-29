"""Tests for replan/diff.py — compute_day_diff and summarize_diff."""

import os
import sys

os.environ.setdefault("GROQ_API_KEY", "test-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from replan.diff import compute_day_diff, summarize_diff
from validation.validator import FinalDay, FinalStop


# ── Helpers ───────────────────────────────────────────────────────────────────

def _stop(poi_id: str, name: str, arrival_time: str, is_meal: bool = False) -> FinalStop:
    return FinalStop(
        poi_id=poi_id,
        name=name,
        poi_type="restaurant" if is_meal else "museum",
        arrival_time=arrival_time,
        duration_min=60,
        is_meal=is_meal,
        lat=0.0,
        lon=0.0,
    )


def _day(stops: list[FinalStop]) -> FinalDay:
    return FinalDay(date="2025-06-01", day_label="Day 1", stops=stops)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_pure_swap():
    """Two stops swapped at identical times → swapped list, others empty."""
    before = _day([
        _stop("museum1", "Old Museum", "10:00"),
        _stop("park1", "Old Park", "14:00"),
    ])
    after = _day([
        _stop("gallery1", "New Gallery", "10:00"),
        _stop("spa1", "New Spa", "14:00"),
    ])

    diff = compute_day_diff(before, after)

    assert len(diff["swapped"]) == 2
    assert diff["dropped"] == []
    assert diff["added"] == []
    assert diff["time_shifted"] == []

    before_names = {s["before"]["name"] for s in diff["swapped"]}
    after_names  = {s["after"]["name"]  for s in diff["swapped"]}
    assert "Old Museum" in before_names
    assert "New Gallery" in after_names


def test_dropped_stop():
    """One stop present in before but absent from after → dropped."""
    before = _day([
        _stop("museum1", "Art Museum", "10:00"),
        _stop("park1", "City Park", "14:00"),
    ])
    after = _day([
        _stop("museum1", "Art Museum", "10:00"),
    ])

    diff = compute_day_diff(before, after)

    assert diff["dropped"] == [{"name": "City Park", "poi_id": "park1"}]
    assert diff["added"] == []
    assert diff["swapped"] == []
    assert diff["time_shifted"] == []


def test_added_rest():
    """New rest stop in after with no counterpart in before → added."""
    before = _day([
        _stop("museum1", "Art Museum", "10:00"),
    ])
    after = _day([
        _stop("museum1", "Art Museum", "10:00"),
        _stop("rest-2025-06-01", "Rest Stop", "15:00"),
    ])

    diff = compute_day_diff(before, after)

    assert diff["dropped"] == []
    assert diff["swapped"] == []
    assert diff["time_shifted"] == []
    assert len(diff["added"]) == 1
    assert diff["added"][0]["poi_id"] == "rest-2025-06-01"
    assert diff["added"][0]["name"] == "Rest Stop"


def test_time_shift():
    """Same stops shifted 30 min later → time_shifted list, others empty."""
    before = _day([
        _stop("museum1", "Art Museum", "09:00"),
        _stop("park1", "City Park", "11:00"),
        _stop("gallery1", "Art Gallery", "14:00"),
    ])
    after = _day([
        _stop("museum1", "Art Museum", "09:30"),
        _stop("park1", "City Park", "11:30"),
        _stop("gallery1", "Art Gallery", "14:30"),
    ])

    diff = compute_day_diff(before, after)

    assert diff["swapped"] == []
    assert diff["dropped"] == []
    assert diff["added"] == []
    assert len(diff["time_shifted"]) == 3

    shifted_ids = {e["poi_id"] for e in diff["time_shifted"]}
    assert shifted_ids == {"museum1", "park1", "gallery1"}

    for entry in diff["time_shifted"]:
        # before time is 30 min earlier than after time
        bh, bm = map(int, entry["before"].split(":"))
        ah, am = map(int, entry["after"].split(":"))
        assert (ah * 60 + am) - (bh * 60 + bm) == 30


def test_meal_match_by_type():
    """Meal with same poi_id but different restaurant name → swapped, not dropped+added."""
    before = _day([
        _stop("meal-breakfast-2025-06-01", "Old Diner", "08:00", is_meal=True),
    ])
    after = _day([
        _stop("meal-breakfast-2025-06-01", "New Cafe", "08:00", is_meal=True),
    ])

    diff = compute_day_diff(before, after)

    assert diff["dropped"] == []
    assert diff["added"] == []
    assert diff["time_shifted"] == []
    assert len(diff["swapped"]) == 1
    assert diff["swapped"][0]["before"]["name"] == "Old Diner"
    assert diff["swapped"][0]["after"]["name"] == "New Cafe"


# ── summarize_diff ────────────────────────────────────────────────────────────

def test_summarize_diff_no_changes():
    diff = {"swapped": [], "dropped": [], "added": [], "time_shifted": []}
    assert summarize_diff(diff) == "No changes"


def test_summarize_diff_mixed():
    diff = {
        "swapped": [{}],
        "dropped": [{}, {}],
        "added": [{}],
        "time_shifted": [{}, {}, {}],
    }
    summary = summarize_diff(diff)
    assert "1" in summary   # swapped
    assert "2" in summary   # dropped
    assert "3" in summary   # shifted
