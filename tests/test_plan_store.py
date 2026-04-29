"""Tests for storage/plan_store.py"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime, timezone

import pytest

from api.models import ItineraryRequest
from prefetch.distance import Leg
from prefetch.orchestrator import PrefetchBundle
from storage.plan_store import (
    PlanSnapshot,
    PlanStore,
    _deserialize_bundle,
    _serialize_bundle,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_bundle() -> PrefetchBundle:
    return PrefetchBundle(
        lat=32.77,
        lon=-96.80,
        display_name="Dallas",
        attractions=[{
            "poi_id": "a0", "name": "Reunion Tower",
            "poi_type": "viewpoint", "lat": 32.77, "lon": -96.80, "tags": {},
        }],
        restaurants=[{"name": "Local Grill", "cuisine": "american", "lat": 32.77, "lon": -96.80}],
        hotels=[{"name": "Hyatt", "stars": "4", "lat": 32.77, "lon": -96.80}],
        weather=[{"date": "2025-06-01", "is_clear": True, "description": "Sunny",
                  "temp_high_c": 32, "temp_low_c": 22}],
        distance_matrix=[[Leg(km=0.0, walking_min=0, driving_min=0)]],
        cache_hits={},
    )


def _make_snapshot(plan_id: str) -> PlanSnapshot:
    return PlanSnapshot(
        plan_id=plan_id,
        user_id=None,
        created_at=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        request=ItineraryRequest(
            destination="Dallas",
            start_date="2025-06-01",
            end_date="2025-06-03",
            interests=["history"],
            travel_style="solo",
            pace="balanced",
            drive_tolerance_hrs=2.0,
        ),
        skeleton_dict={"days": [], "hotel": None, "diagnostics": {}},
        bundle_dict=_serialize_bundle(_make_bundle()),
        final_plan={
            "title": "Dallas Weekend",
            "summary": "A great weekend in Dallas.",
            "destination": "Dallas",
            "start_date": "2025-06-01",
            "end_date": "2025-06-03",
            "highlights": [],
            "accommodation": {},
            "budget": {},
            "days": [],
        },
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_save_and_load():
    store = PlanStore()
    plan_id = "abc123"
    snapshot = _make_snapshot(plan_id)

    await store.save(plan_id, snapshot)
    loaded = await store.load(plan_id)

    assert loaded is not None
    assert loaded.plan_id == plan_id
    assert loaded.final_plan["title"] == "Dallas Weekend"


async def test_delete():
    store = PlanStore()
    plan_id = "xyz789"

    await store.save(plan_id, _make_snapshot(plan_id))
    await store.delete(plan_id)

    assert await store.load(plan_id) is None


async def test_load_missing_returns_none():
    store = PlanStore()
    assert await store.load("does-not-exist") is None


async def test_ttl_expiry():
    store = PlanStore(ttl_seconds=0)  # expire immediately
    plan_id = "expired"

    await store.save(plan_id, _make_snapshot(plan_id))
    assert await store.load(plan_id) is None


def test_bundle_round_trip():
    bundle = _make_bundle()
    recovered = _deserialize_bundle(_serialize_bundle(bundle))

    assert recovered.lat == bundle.lat
    assert recovered.lon == bundle.lon
    assert recovered.display_name == bundle.display_name
    assert recovered.attractions == bundle.attractions
    assert recovered.restaurants == bundle.restaurants
    assert recovered.hotels == bundle.hotels
    assert recovered.weather == bundle.weather
    assert recovered.distance_matrix == bundle.distance_matrix
    assert recovered.cache_hits == bundle.cache_hits
